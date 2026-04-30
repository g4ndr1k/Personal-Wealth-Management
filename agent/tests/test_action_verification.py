from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.app.action_execution import evaluate_execution_gate
from agent.app.action_verification import (
    FakeReadOnlyMailboxAdapter,
    FinalVerificationRequest,
    verify_action_plan_readonly,
)


NOW = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)


def _settings():
    return {
        "mail": {
            "imap_mutations": {
                "enabled": True,
                "dry_run_default": False,
                "allow_mark_read": True,
                "allow_mark_unread": True,
                "allow_add_label": False,
                "allow_move_to_folder": False,
                "require_uidvalidity_match": True,
                "require_capability_cache": True,
            },
            "approvals": {"approval_expiry_hours": 72},
        }
    }


def _approval(**overrides):
    payload = {
        "approval_id": "approval-1",
        "status": "approved",
        "requested_at": (NOW - timedelta(hours=1)).isoformat(),
        "archived_at": None,
        "account_id": "acct",
        "folder": "INBOX",
        "uidvalidity": "7",
        "imap_uid": 42,
        "message_key": "<m42@example.test>",
        "subject": "Payment due",
        "sender": "billing@example.test",
        "received_at": "Fri, 01 May 2026 07:00:00 +0700",
        "proposed_action_type": "mark_read",
        "proposed_target": None,
    }
    payload.update(overrides)
    return payload


def _plan(**overrides):
    payload = {
        "action_type": "mark_read",
        "account_id": "acct",
        "folder": "INBOX",
        "uidvalidity": "7",
        "uid": 42,
        "target": None,
        "operation": r"STORE +FLAGS.SILENT (\Seen)",
        "dry_run": True,
        "would_mutate": False,
        "reversible": True,
        "before_state": {"seen": False},
    }
    payload.update(overrides)
    return payload


def _gate(approval=None, plan=None):
    return evaluate_execution_gate(
        approval=approval or _approval(),
        settings=_settings(),
        account={"enabled": True},
        folder_state={"uidvalidity": "7", "uid_exists": True},
        capability_cache={
            "uidvalidity": "7",
            "supports_store_flags": True,
            "status": "ok",
        },
        dry_run_plan=plan or _plan(),
        now=NOW,
        mock_mode=True,
    )


def _adapter(**overrides):
    message = {
        "identity": {
            "message_id": "<m42@example.test>",
            "subject": "Payment due",
            "from": "billing@example.test",
            "date": "Fri, 01 May 2026 07:00:00 +0700",
        },
        "flags": {"seen": False},
    }
    message.update(overrides.pop("message", {}))
    return FakeReadOnlyMailboxAdapter(
        folder_exists=overrides.pop("folder_exists", True),
        uidvalidity=overrides.pop("uidvalidity", "7"),
        messages=overrides.pop("messages", {"42": message}),
    )


def _verify(approval=None, gate=None, adapter=None):
    gate = gate or _gate(approval=approval)
    request = FinalVerificationRequest(
        approval=approval or _approval(),
        gate_result=gate,
        plan_hash=gate.plan_hash,
        idempotency_key=gate.idempotency_key,
    )
    return verify_action_plan_readonly(request, adapter or _adapter())


def _codes(result):
    return [blocker["code"] for blocker in result.to_dict()["blockers"]]


def test_verification_passes_when_identity_matches():
    result = _verify()

    assert result.status == "verified"
    assert result.safe_to_execute is True
    assert result.mailbox_identity["uidvalidity_current"] == "7"
    assert result.message_identity["message_id_current"] == "m42@example.test"
    assert result.current_flags["seen"] is False


def test_blocks_when_uidvalidity_changed():
    result = _verify(adapter=_adapter(uidvalidity="99"))

    assert result.status == "blocked"
    assert "uidvalidity_mismatch" in _codes(result)


def test_blocks_when_uid_missing():
    result = _verify(adapter=FakeReadOnlyMailboxAdapter(messages={}))

    assert result.status == "blocked"
    assert "uid_missing" in _codes(result)


def test_blocks_when_message_id_mismatch():
    result = _verify(adapter=_adapter(message={
        "identity": {
            "message_id": "<other@example.test>",
            "subject": "Payment due",
            "from": "billing@example.test",
            "date": "Fri, 01 May 2026 07:00:00 +0700",
        },
    }))

    assert result.status == "blocked"
    assert "message_id_mismatch" in _codes(result)


def test_blocks_when_folder_missing():
    result = _verify(adapter=_adapter(folder_exists=False))

    assert result.status == "blocked"
    assert "folder_missing" in _codes(result)


def test_mark_read_already_read_is_noop_warning():
    result = _verify(adapter=_adapter(message={"flags": {"seen": True}}))

    assert result.status == "verified"
    assert "mark_read_noop_message_already_read" in result.warnings


def test_mark_unread_already_unread_is_noop_warning():
    approval = _approval(proposed_action_type="mark_unread")
    plan = _plan(
        action_type="mark_unread",
        operation=r"STORE -FLAGS.SILENT (\Seen)",
        before_state={"seen": True},
    )
    gate = _gate(approval=approval, plan=plan)
    result = _verify(approval=approval, gate=gate, adapter=_adapter(message={"flags": {"seen": False}}))

    assert result.status == "verified"
    assert "mark_unread_noop_message_already_unread" in result.warnings


def test_missing_optional_headers_warn_without_blocking():
    approval = _approval(message_key="mkey:42", subject=None, sender=None, received_at=None)
    gate = _gate(approval=approval)
    result = _verify(approval=approval, gate=gate, adapter=_adapter(message={
        "identity": {"message_id": None, "subject": None, "from": None, "date": None},
    }))

    assert result.status == "verified"
    assert "message_id_expected_missing" in result.warnings
    assert "subject_expected_missing" in result.warnings


def test_adapter_exceptions_return_structured_blocker():
    class BrokenAdapter:
        def select_folder_readonly(self, account_id, folder):
            raise RuntimeError("read-only probe failed")

        def get_uidvalidity(self):
            raise AssertionError("not reached")

        def fetch_message_identity_by_uid(self, uid):
            raise AssertionError("not reached")

        def fetch_flags_by_uid(self, uid):
            raise AssertionError("not reached")

        def close(self):
            return None

    result = _verify(adapter=BrokenAdapter())

    assert result.status == "blocked"
    assert "verification_error" in _codes(result)
