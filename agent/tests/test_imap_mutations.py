import tomllib

from agent.app.action_verification import FakeReadOnlyMailboxAdapter
from agent.app.imap_source import (
    IMAPPoller,
    _capability_names,
    _permanent_flags,
)
from agent.app.rules import evaluate_message
from agent.app.state import AgentState


class FakeImap:
    def __init__(self, *, uidvalidity=7, capabilities=None):
        self.uidvalidity = uidvalidity
        self.capabilities = capabilities or (b"IMAP4rev1", b"UIDPLUS")
        self.untagged_responses = {
            "PERMANENTFLAGS": [b"(\\Answered \\Flagged \\Seen \\*)"]
        }
        self.uid_calls = []
        self.selected = []

    def login(self, email, password):
        return "OK", [b"logged in"]

    def logout(self):
        return "OK", [b"logged out"]

    def capability(self):
        return "OK", [b"IMAP4rev1 UIDPLUS MOVE"]

    def list(self, directory=None, pattern=None):
        if pattern:
            exists = pattern in {"Archive", "INBOX"}
            return "OK", [f'(\\HasNoChildren) "/" "{pattern}"'.encode()] if exists else []
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, folder, readonly=False):
        self.selected.append((folder, readonly))
        return "OK", [b"1"]

    def status(self, folder, items):
        return "OK", [f'"{folder}" (UIDVALIDITY {self.uidvalidity})'.encode()]

    def create(self, folder):
        return "OK", [b"created"]

    def uid(self, *args):
        self.uid_calls.append(args)
        return "OK", [b"done"]


def _poller(fake):
    poller = IMAPPoller(
        {
            "name": "acct1",
            "email": "acct@example.com",
            "host": "imap.example.com",
            "auth_source": "file",
        },
        state=None,
        imap_cfg={},
    )
    poller._password = "pw"
    poller._connect = lambda: fake
    return poller


def test_capability_parsing():
    fake = FakeImap(capabilities=[b"IMAP4rev1 UIDPLUS MOVE"])
    caps = _poller(fake).probe_capabilities("INBOX", "Archive")

    assert caps.supports_move is True
    assert caps.supports_uidplus is True
    assert caps.flag_support is True
    assert "\\Seen" in caps.permanent_flags
    assert caps.target_exists is True
    assert _capability_names(fake) == ["IMAP4REV1", "MOVE", "UIDPLUS"]
    assert _permanent_flags(fake.untagged_responses) == ["\\Flagged", "\\Seen"]


def test_uidvalidity_mismatch_refuses_mutation():
    fake = FakeImap(uidvalidity=99, capabilities=[b"IMAP4rev1 MOVE"])
    result = _poller(fake).store_flags_by_uid(
        "INBOX",
        7,
        42,
        add_flags=["\\Seen"],
        dry_run=False,
        mutation_cfg={"enabled": True},
    )

    assert result.status == "uidvalidity_mismatch"
    assert fake.uid_calls == []


def test_move_unsupported_returns_unsupported():
    fake = FakeImap(capabilities=[b"IMAP4rev1 UIDPLUS"])
    poller = _poller(fake)
    poller.probe_capabilities = lambda folder, target: type(
        "Caps",
        (),
        {
            "supports_move": False,
            "target_exists": True,
            "target_can_be_created": False,
            "to_dict": lambda self: {"supports_move": False},
        },
    )()

    result = poller.move_message_by_uid(
        "INBOX",
        7,
        42,
        "Archive",
        dry_run=False,
        mutation_cfg={"enabled": True, "allow_copy_delete_fallback": False},
    )

    assert result.status == "unsupported"
    assert not any(call[0] == "MOVE" for call in fake.uid_calls)


def test_uid_store_seen_and_flagged_uses_uid_commands():
    fake = FakeImap(uidvalidity=7)
    result = _poller(fake).store_flags_by_uid(
        "INBOX",
        7,
        42,
        add_flags=["\\Seen", "\\Flagged"],
        dry_run=False,
        mutation_cfg={"enabled": True},
    )

    assert result.status == "completed"
    assert fake.uid_calls == [
        ("STORE", "42", "+FLAGS.SILENT", "(\\Seen \\Flagged)")
    ]


def _insert_mutation_rule(state, action_type="mark_read", target=None):
    now = state._now()
    with state._connect() as conn:
        cur = conn.execute(
            "INSERT INTO mail_rules "
            "(name, priority, enabled, match_type, created_at, updated_at) "
            "VALUES ('mutate', 1, 1, 'ALL', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO mail_rule_actions "
            "(rule_id, action_type, target, stop_processing) "
            "VALUES (?, ?, ?, 0)",
            (cur.lastrowid, action_type, target),
        )
        conn.commit()


def _message():
    return {
        "bridge_id": "imap-acct1-INBOX-42",
        "message_id": "<m42@example.test>",
        "message_key": "mkey42",
        "imap_account": "acct1",
        "imap_folder": "INBOX",
        "imap_uid": 42,
        "imap_uidvalidity": 7,
        "subject": "Hello",
    }


def _event_outcomes(state):
    with state._connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT outcome FROM mail_processing_events ORDER BY id"
            ).fetchall()
        ]


def test_observe_and_draft_only_block_mutation(tmp_path):
    for mode in ("observe", "draft_only"):
        state = AgentState(str(tmp_path / f"{mode}.db"))
        _insert_mutation_rule(state)
        result = evaluate_message(
            state,
            _message(),
            mutation_context={
                "mode": mode,
                "config": {"enabled": True, "dry_run_default": False},
            },
        )
        assert result.actions_executed[0]["status"] == "mode_blocked"
        assert _event_outcomes(state) == ["planned", "mode_blocked"]


def test_live_blocks_when_mutations_disabled(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_mutation_rule(state)
    result = evaluate_message(
        state,
        _message(),
        mutation_context={
            "mode": "live",
            "config": {"enabled": False, "dry_run_default": False},
        },
    )

    assert result.actions_executed[0]["status"] == "mutation_disabled"
    assert _event_outcomes(state) == ["planned", "mutation_disabled"]


def test_dry_run_audits_without_calling_executor(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_mutation_rule(state)
    calls = []
    result = evaluate_message(
        state,
        _message(),
        mutation_context={
            "mode": "live",
            "config": {"enabled": True, "dry_run_default": True},
            "executor": lambda *args, **kwargs: calls.append(args),
        },
    )

    assert result.actions_executed[0]["status"] == "dry_run"
    assert calls == []
    assert _event_outcomes(state) == ["planned", "dry_run"]


def test_rule_actions_recognize_mutation_action_types_but_respect_gates(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_mutation_rule(state, "move_to_folder", "Archive")
    result = evaluate_message(
        state,
        _message(),
        mutation_context={"mode": "observe", "config": {"enabled": True}},
    )

    assert result.planned_actions[0]["action_type"] == "move_to_folder"
    assert result.planned_actions[0]["target"] == "Archive"
    assert result.actions_executed[0]["status"] == "mode_blocked"


def test_live_enabled_rule_managed_mutation_uses_executor_and_audits_result(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_mutation_rule(state, "move_to_folder", "Archive")
    calls = []

    def executor(action_type, message, target, *, dry_run):
        calls.append((action_type, message["imap_uid"], target, dry_run))
        return {
            "status": "completed",
            "operation": action_type,
            "uid": message["imap_uid"],
            "target": target,
        }

    result = evaluate_message(
        state,
        _message(),
        mutation_context={
            "mode": "live",
            "config": {
                "enabled": True,
                "dry_run_default": False,
                "allow_move_to_folder": True,
            },
            "dry_run": False,
            "executor": executor,
        },
    )

    assert calls == [("move_to_folder", 42, "Archive", False)]
    assert result.actions_executed[0]["status"] == "completed"
    with state._connect() as conn:
        rows = conn.execute(
            "SELECT outcome, details_json FROM mail_processing_events ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == ["planned", "completed"]
    assert '"mutation_result"' in rows[-1][1]


def test_read_only_adapter_exposes_no_mutating_methods():
    adapter = FakeReadOnlyMailboxAdapter()

    for name in (
        "store",
        "move",
        "copy",
        "create",
        "expunge",
        "store_flags_by_uid",
        "move_message_by_uid",
    ):
        assert not hasattr(adapter, name)


def test_config_defaults_still_disable_mutations():
    with open("config/settings.toml", "rb") as f:
        cfg = tomllib.load(f)
    mutation_cfg = cfg["mail"]["imap_mutations"]

    assert mutation_cfg["enabled"] is False
    assert mutation_cfg["dry_run_default"] is True
    assert mutation_cfg["allow_mark_read"] is False
    assert mutation_cfg["allow_mark_unread"] is False
    assert mutation_cfg["allow_add_label"] is False
    assert mutation_cfg["allow_move_to_folder"] is False
    assert mutation_cfg["require_uidvalidity_match"] is True
    assert mutation_cfg["require_capability_cache"] is True
