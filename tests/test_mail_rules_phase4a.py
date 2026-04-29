import os
import sqlite3
import sys
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app.api_mail import router
from agent.app.rules import evaluate_message, message_audit_id
from agent.app.state import AgentState


def _message(**overrides):
    msg = {
        "bridge_id": "imap-gmail-INBOX-42",
        "message_id": "<m42@example.test>",
        "message_key": "mkey42",
        "fallback_message_key": "fkey42",
        "imap_account": "acct1",
        "imap_folder": "INBOX",
        "imap_uid": 42,
        "imap_uidvalidity": 7,
        "sender_email": "billing@example.com",
        "subject": "Invoice needs attention",
        "body_text": "Please review this invoice and reply.",
        "snippet": "Please review this invoice and reply.",
        "attachments": [{"filename": "statement.pdf"}],
    }
    msg.update(overrides)
    return msg


def _insert_rule(
    state,
    *,
    account_id=None,
    name="rule",
    priority=10,
    enabled=True,
    match_type="ALL",
    conditions=None,
    actions=None,
):
    now = state._now()
    with state._connect() as conn:
        cur = conn.execute(
            "INSERT INTO mail_rules "
            "(account_id, name, priority, enabled, match_type, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, name, priority, int(enabled), match_type, now, now),
        )
        rule_id = cur.lastrowid
        for condition in conditions or []:
            conn.execute(
                "INSERT INTO mail_rule_conditions "
                "(rule_id, field, operator, value, value_json, case_sensitive) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    rule_id,
                    condition["field"],
                    condition["operator"],
                    condition.get("value"),
                    condition.get("value_json"),
                ),
            )
        for action in actions or []:
            conn.execute(
                "INSERT INTO mail_rule_actions "
                "(rule_id, action_type, target, value_json, stop_processing) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    rule_id,
                    action["action_type"],
                    action.get("target"),
                    action.get("value_json"),
                    int(action.get("stop_processing", False)),
                ),
            )
        conn.commit()
        return rule_id


def test_state_pragmas_and_schema_are_idempotent(tmp_path):
    db_path = tmp_path / "agent.db"
    AgentState(str(db_path))
    AgentState(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "mail_rules",
            "mail_rule_conditions",
            "mail_rule_actions",
            "mail_processing_events",
            "mail_needs_reply",
            "mail_ai_queue",
            "mail_ai_classifications",
            "mail_ai_categories",
            "mail_ai_trigger_rules",
        }.issubset(tables)
    finally:
        conn.close()


def test_rule_engine_priority_any_all_stop_and_audit(tmp_path, caplog):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_rule(
        state,
        account_id="acct1",
        name="first",
        priority=5,
        match_type="ALL",
        conditions=[
            {"field": "sender_email", "operator": "domain_equals", "value": "example.com"},
            {"field": "subject", "operator": "contains", "value": "invoice"},
        ],
        actions=[
            {"action_type": "add_to_needs_reply"},
            {"action_type": "skip_ai_inference"},
        ],
    )
    _insert_rule(
        state,
        account_id=None,
        name="global stop",
        priority=6,
        match_type="ANY",
        conditions=[
            {"field": "subject", "operator": "contains", "value": "attention"},
            {"field": "body_text", "operator": "contains", "value": "missing"},
        ],
        actions=[
            {"action_type": "notify_dashboard", "stop_processing": True},
        ],
    )
    _insert_rule(
        state,
        account_id="acct1",
        name="never reached",
        priority=7,
        conditions=[{"field": "subject", "operator": "contains", "value": "invoice"}],
        actions=[{"action_type": "mark_pending_alert"}],
    )

    caplog.set_level(logging.INFO, logger="agent.rules")
    result = evaluate_message(state, _message())

    assert [a["action_type"] for a in result.actions_executed] == [
        "add_to_needs_reply",
        "skip_ai_inference",
        "notify_dashboard",
        "stop_processing",
    ]
    assert result.would_skip_ai is True
    assert result.continue_to_classifier is True
    assert result.stopped is True

    with state._connect() as conn:
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM mail_processing_events WHERE message_id = ?",
            (message_audit_id(_message()),),
        ).fetchone()[0]
        needs_reply_count = conn.execute(
            "SELECT COUNT(*) FROM mail_needs_reply WHERE message_id = ?",
            (message_audit_id(_message()),),
        ).fetchone()[0]
    assert audit_count == 4
    assert needs_reply_count == 1
    assert "phase4a_event_inserted message_id=mkey42" in caplog.text
    assert "action_type=add_to_needs_reply" in caplog.text
    assert "phase4a_needs_reply_inserted message_id=mkey42" in caplog.text
    assert "Please review this invoice and reply." not in caplog.text


def test_rule_preview_is_side_effect_free(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_rule(
        state,
        name="preview",
        priority=1,
        conditions=[{"field": "subject", "operator": "contains", "value": "invoice"}],
        actions=[{"action_type": "mark_pending_alert"}],
    )

    result = evaluate_message(state, _message(), preview=True)

    assert [a["action_type"] for a in result.planned_actions] == [
        "mark_pending_alert"
    ]
    with state._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mail_processing_events"
        ).fetchone()[0] == 0


def test_unsupported_stored_action_is_deferred_without_audit(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_rule(
        state,
        name="future action",
        priority=1,
        conditions=[{"field": "subject", "operator": "contains", "value": "invoice"}],
        actions=[{"action_type": "move_to_folder"}],
    )

    result = evaluate_message(state, _message())

    assert result.planned_actions == [
        {"rule_id": 1, "action_type": "move_to_folder", "status": "deferred"}
    ]
    assert result.actions_executed == []
    with state._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mail_processing_events"
        ).fetchone()[0] == 0


def test_disabled_rules_do_not_match_or_write_audit(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_rule(
        state,
        enabled=False,
        name="disabled",
        priority=1,
        conditions=[{"field": "subject", "operator": "contains", "value": "invoice"}],
        actions=[{"action_type": "mark_pending_alert"}],
    )

    result = evaluate_message(state, _message())

    assert result.matched_conditions == []
    assert result.planned_actions == []
    assert result.actions_executed == []
    with state._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mail_processing_events"
        ).fetchone()[0] == 0


def test_needs_reply_existing_row_logs_skipped_without_duplicate(tmp_path, caplog):
    state = AgentState(str(tmp_path / "agent.db"))
    _insert_rule(
        state,
        name="needs reply",
        priority=1,
        conditions=[{"field": "subject", "operator": "contains", "value": "invoice"}],
        actions=[{"action_type": "add_to_needs_reply"}],
    )

    evaluate_message(state, _message())
    caplog.clear()
    caplog.set_level(logging.INFO, logger="agent.rules")

    result = evaluate_message(state, _message())

    assert result.needs_reply_written == 0
    assert result.events_written == 1
    assert "phase4a_needs_reply_skipped_existing message_id=mkey42" in caplog.text
    with state._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mail_needs_reply WHERE message_id = ?",
            ("mkey42",),
        ).fetchone()[0] == 1


def test_rules_crud_api_requires_key_and_preview_has_no_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")

    app = FastAPI()
    app.include_router(router, prefix="/api/mail")
    client = TestClient(app)

    denied = client.get("/api/mail/rules")
    assert denied.status_code == 401

    headers = {"X-Api-Key": "secret"}
    created = client.post(
        "/api/mail/rules",
        headers=headers,
        json={
            "name": "api rule",
            "priority": 1,
            "match_type": "ALL",
            "conditions": [
                {
                    "field": "sender_email",
                    "operator": "equals",
                    "value": "billing@example.com",
                }
            ],
            "actions": [{"action_type": "mark_pending_alert"}],
        },
    )
    assert created.status_code == 200, created.text
    rule = created.json()
    assert rule["rule_id"] > 0
    assert rule["actions"][0]["action_type"] == "mark_pending_alert"

    preview = client.post(
        "/api/mail/rules/preview",
        headers=headers,
        json={"message": _message()},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["planned_actions"][0]["action_type"] == "mark_pending_alert"

    events = client.get(
        f"/api/mail/messages/{message_audit_id(_message())}/processing-events",
        headers=headers,
    )
    assert events.status_code == 200
    assert events.json() == []

    recent_events = client.get(
        "/api/mail/processing-events?limit=10",
        headers=headers,
    )
    assert recent_events.status_code == 200
    assert recent_events.json() == []

    patched = client.patch(
        f"/api/mail/rules/{rule['rule_id']}",
        headers=headers,
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(f"/api/mail/rules/{rule['rule_id']}", headers=headers)
    assert deleted.status_code == 200


def test_duplicate_priority_fails_and_invalid_rule_payloads_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")

    app = FastAPI()
    app.include_router(router, prefix="/api/mail")
    client = TestClient(app)
    headers = {"X-Api-Key": "secret"}

    base_payload = {
        "name": "first",
        "priority": 1,
        "match_type": "ALL",
        "conditions": [
            {"field": "subject", "operator": "contains", "value": "invoice"}
        ],
        "actions": [{"action_type": "mark_pending_alert"}],
    }
    first = client.post("/api/mail/rules", headers=headers, json=base_payload)
    assert first.status_code == 200, first.text

    duplicate = client.post(
        "/api/mail/rules",
        headers=headers,
        json={**base_payload, "name": "duplicate"},
    )
    assert duplicate.status_code == 409

    invalid_operator = client.post(
        "/api/mail/rules",
        headers=headers,
        json={
            **base_payload,
            "name": "invalid operator",
            "priority": 2,
            "conditions": [
                {"field": "subject", "operator": "near", "value": "invoice"}
            ],
        },
    )
    assert invalid_operator.status_code == 400
    assert "Unsupported rule operator" in invalid_operator.json()["detail"]

    invalid_action = client.post(
        "/api/mail/rules",
        headers=headers,
        json={
            **base_payload,
            "name": "invalid action",
            "priority": 3,
            "actions": [{"action_type": "move_to_folder"}],
        },
    )
    assert invalid_action.status_code == 400
    assert "Unsupported Phase 4A action_type" in invalid_action.json()["detail"]


def test_mail_run_unmatched_rules_still_reach_existing_classifier(
    tmp_path, monkeypatch, caplog
):
    agent_root = Path(__file__).resolve().parents[1] / "agent"
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))

    from app.orchestrator import Orchestrator
    from app.schemas import Classification

    state = AgentState(str(tmp_path / "agent.db"))
    _insert_rule(
        state,
        name="non matching",
        priority=1,
        conditions=[{"field": "subject", "operator": "contains", "value": "nope"}],
        actions=[{"action_type": "mark_pending_alert"}],
    )
    message = _message(subject="Unmatched ordinary mail", attachments=[])

    class DummyIntake:
        def poll_all(self):
            return [message]

    class DummyClassifier:
        def __init__(self):
            self.calls = []

        def classify(self, item):
            self.calls.append(item)
            return Classification(
                category="not_financial",
                urgency="low",
                summary="legacy classifier reached",
                requires_action=False,
                provider="dummy_legacy",
            )

    class DummyStats:
        def __init__(self):
            self.values = {}

        def incr(self, key, value=1):
            self.values[key] = self.values.get(key, 0) + value

        def update(self, **kwargs):
            self.values.update(kwargs)

    classifier = DummyClassifier()
    orch = object.__new__(Orchestrator)
    orch.imap_intake = DummyIntake()
    orch.state = state
    orch.classifier = classifier
    orch.stats = DummyStats()
    orch.commands = type("Commands", (), {"quiet": True})()
    orch.settings = {"agent": {"alert_on_categories": []}}
    orch.mode = "draft_only"
    orch.bridge_ok = False
    orch.pdf_router = None

    class FakeResponse:
        def json(self):
            return {"queued": True, "scanned": True}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, params):
            assert url == "http://mail-agent.test/trigger"
            assert params == {"force": "1"}
            assert orch._scan_imap_once() is True
            return FakeResponse()

    import httpx

    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")
    monkeypatch.setenv("AGENT_INTERNAL_URL", "http://mail-agent.test")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    app = FastAPI()
    app.include_router(router, prefix="/api/mail")
    client = TestClient(app)
    caplog.set_level(logging.INFO, logger="agent.orchestrator")
    response = client.post(
        "/api/mail/run?force=true",
        headers={"X-Api-Key": "secret"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"queued": True, "scanned": True}
    assert "phase4a_evaluator message_key=mkey42" in caplog.text
    assert "message_id=<m42@example.test>" in caplog.text
    assert "account_id=acct1" in caplog.text
    assert "matched_rule_count=0" in caplog.text
    assert "planned_action_count=0" in caplog.text
    assert "continue_to_classifier=True" in caplog.text
    assert "route_to_pdf_pipeline=False" in caplog.text
    assert "skipped_by_dedup=False" in caplog.text
    assert "subject=Unmatched ordinary mail" in caplog.text
    assert "sender_email=billing@example.com" in caplog.text
    assert "phase4a_scan_summary messages_seen=1" in caplog.text
    assert "messages_dedup_skipped=0" in caplog.text
    assert "messages_evaluator_ran=1" in caplog.text
    assert "rules_matched=0" in caplog.text
    assert "events_written=0" in caplog.text
    assert "needs_reply_written=0" in caplog.text
    assert classifier.calls == [message]
    with state._connect() as conn:
        row = conn.execute(
            "SELECT provider, summary FROM processed_messages WHERE bridge_id = ?",
            (message["bridge_id"],),
        ).fetchone()
    assert row == ("dummy_legacy", "legacy classifier reached")
