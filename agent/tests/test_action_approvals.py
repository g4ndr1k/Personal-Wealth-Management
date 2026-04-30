from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail
from agent.app.state import AgentState


def _settings(tmp_path: Path, *, mode="draft_only", mutations_enabled=False, dry_run=True) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "settings.toml"
    path.write_text(
        f"""
[agent]
mode = "{mode}"
safe_default = "draft_only"

[mail.approvals]
enabled = true
require_approval_for_ai_actions = true
approval_expiry_hours = 72
allow_bulk_approve = false

[mail.imap_mutations]
enabled = {str(mutations_enabled).lower()}
allow_create_folder = false
allow_copy_delete_fallback = false
dry_run_default = {str(dry_run).lower()}

[mail.imap]
accounts = [
  {{ id = "acct", name = "acct", email = "acct@example.com", provider = "gmail", host = "imap.example.com", auth_source = "file" }}
]
""",
        encoding="utf-8",
    )
    return path


def _client(tmp_path, monkeypatch, *, mode="draft_only", mutations_enabled=False, dry_run=True):
    db_path = tmp_path / "agent.db"
    settings_path = _settings(
        tmp_path,
        mode=mode,
        mutations_enabled=mutations_enabled,
        dry_run=dry_run,
    )
    monkeypatch.setenv("AGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("SETTINGS_FILE", str(settings_path))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")
    state = AgentState(str(db_path))
    app = FastAPI()
    app.include_router(api_mail.router, prefix="/api/mail")
    return TestClient(app), state


def _classification(**overrides):
    payload = {
        "category": "payment_due",
        "urgency_score": 8,
        "confidence": 0.9,
        "summary": "Payment is due tomorrow.",
        "needs_reply": True,
        "reason": "Payment reminder requires review.",
    }
    payload.update(overrides)
    return payload


def _trigger_payload(actions=None):
    return {
        "name": "Urgent payment",
        "enabled": True,
        "priority": 10,
        "conditions_json": {
            "match_type": "ALL",
            "conditions": [
                {"field": "category", "operator": "equals", "value": "payment_due"},
            ],
        },
        "actions_json": actions or [
            {"action_type": "move_to_folder", "target": "Bills"}
        ],
        "cooldown_seconds": 3600,
    }


def _queue_message(state: AgentState):
    return state.enqueue_ai_work({
        "bridge_id": "imap-acct-INBOX-42",
        "message_key": "mkey42",
        "imap_account": "acct",
        "imap_folder": "INBOX",
        "imap_uid": 42,
        "imap_uidvalidity": 7,
        "sender_email": "billing@example.com",
        "subject": "Payment due",
        "date_received": "2026-04-30T00:00:00+00:00",
        "body_text": "Payment due tomorrow.",
    })


def _approval_id(state: AgentState):
    approvals = state.list_action_approvals()
    assert approvals
    return approvals[0]["approval_id"]


def _events(state: AgentState):
    with state._connect() as conn:
        return conn.execute(
            "SELECT event_type, outcome FROM mail_processing_events ORDER BY id"
        ).fetchall()


def _create_approval(
        state: AgentState, *, action_type="mark_read", message_key="m1",
        source_id="trigger", target=None):
    state.create_action_approval(
        source_type="ai_trigger",
        source_id=source_id,
        message={
            "message_key": message_key,
            "account_id": "acct",
            "folder": "INBOX",
            "uidvalidity": 7,
            "imap_uid": 42,
        },
        action={"action_type": action_type, "target": target},
        classification=_classification(),
    )
    return _approval_id(state)


def test_ai_trigger_creates_approval_item_instead_of_executing(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    state.create_ai_trigger(_trigger_payload())
    state.complete_ai_item(_queue_message(state), _classification())

    approvals = client.get(
        "/api/mail/approvals?status=pending",
        headers={"X-Api-Key": "secret"},
    )

    assert approvals.status_code == 200
    body = approvals.json()
    assert len(body) == 1
    assert body[0]["source_type"] == "ai_trigger"
    assert body[0]["proposed_action_type"] == "move_to_folder"
    assert body[0]["status"] == "pending"
    assert [event[0] for event in _events(state)] == [
        "ai_trigger_matched",
        "approval_created",
    ]


def test_duplicate_pending_approval_is_deduped(tmp_path, monkeypatch):
    _, state = _client(tmp_path, monkeypatch)
    trigger = state.create_ai_trigger(_trigger_payload())
    queue_id = _queue_message(state)
    state.complete_ai_item(queue_id, _classification())
    message = {
        "message_key": "mkey:mkey42",
        "account_id": "acct",
        "folder": "INBOX",
        "uidvalidity": 7,
        "imap_uid": 42,
    }
    state.create_action_approval(
        source_type="ai_trigger",
        source_id=trigger["trigger_id"],
        message=message,
        action={"action_type": "move_to_folder", "target": "Bills"},
        classification=_classification(),
    )

    assert len(state.list_action_approvals()) == 1


def test_approval_can_be_approved_or_rejected(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    state.create_ai_trigger(_trigger_payload())
    state.complete_ai_item(_queue_message(state), _classification())
    approval_id = _approval_id(state)

    approved = client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={"decision_note": "Looks correct"},
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    state.create_action_approval(
        source_type="ai_trigger",
        source_id="second",
        message={"message_key": "m2"},
        action={"action_type": "mark_read"},
        classification=_classification(),
    )
    second = _approval_id(state)
    rejected = client.post(
        f"/api/mail/approvals/{second}/reject",
        headers={"X-Api-Key": "secret"},
        json={"decision_note": "No"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_pending_rejected_and_expired_items_cannot_execute(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    state.create_action_approval(
        source_type="ai_trigger",
        source_id="t1",
        message={"message_key": "m1"},
        action={"action_type": "mark_read"},
        classification=_classification(),
    )
    approval_id = _approval_id(state)

    pending_execute = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )
    assert pending_execute.status_code == 409

    client.post(
        f"/api/mail/approvals/{approval_id}/reject",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    rejected_execute = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )
    assert rejected_execute.status_code == 409

    state.create_action_approval(
        source_type="ai_trigger",
        source_id="t2",
        message={"message_key": "m2"},
        action={"action_type": "mark_read"},
        classification=_classification(),
    )
    expired_id = _approval_id(state)
    client.post(
        f"/api/mail/approvals/{expired_id}/expire",
        headers={"X-Api-Key": "secret"},
    )
    expired_approve = client.post(
        f"/api/mail/approvals/{expired_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert expired_approve.status_code == 409

    expired_execute = client.post(
        f"/api/mail/approvals/{expired_id}/execute",
        headers={"X-Api-Key": "secret"},
    )
    assert expired_execute.status_code == 409


def test_repeated_and_conflicting_decisions_return_conflict(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    approval_id = _create_approval(state)

    approved = client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert approved.status_code == 200

    approve_again = client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    reject_after_approve = client.post(
        f"/api/mail/approvals/{approval_id}/reject",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert approve_again.status_code == 409
    assert reject_after_approve.status_code == 409

    second_id = _create_approval(state, message_key="m2", source_id="trigger-2")
    rejected = client.post(
        f"/api/mail/approvals/{second_id}/reject",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    approve_after_reject = client.post(
        f"/api/mail/approvals/{second_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert rejected.status_code == 200
    assert approve_after_reject.status_code == 409


def test_execute_already_terminal_item_fails(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    approval_id = _create_approval(state, action_type="add_to_needs_reply")

    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    first = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )
    second = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "executed"
    assert second.status_code == 409


def test_approved_action_does_not_bypass_draft_only_or_mutation_disabled(tmp_path, monkeypatch):
    for mode, enabled, expected in [
        ("draft_only", True, "mode_blocked"),
        ("live", False, "mutation_disabled"),
    ]:
        client, state = _client(
            tmp_path / mode,
            monkeypatch,
            mode=mode,
            mutations_enabled=enabled,
            dry_run=False,
        )
        state.create_action_approval(
            source_type="ai_trigger",
            source_id=f"trigger-{mode}",
            message={
                "message_key": f"m-{mode}",
                "account_id": "acct",
                "folder": "INBOX",
                "uidvalidity": 7,
                "imap_uid": 42,
            },
            action={"action_type": "mark_read"},
            classification=_classification(),
        )
        approval_id = _approval_id(state)
        client.post(
            f"/api/mail/approvals/{approval_id}/approve",
            headers={"X-Api-Key": "secret"},
            json={},
        )
        executed = client.post(
            f"/api/mail/approvals/{approval_id}/execute",
            headers={"X-Api-Key": "secret"},
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "blocked"
        assert executed.json()["execution_status"] == expected


def test_approved_action_does_not_bypass_dry_run_default(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_mail,
        "_mutation_executor",
        lambda settings: lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    client, state = _client(
        tmp_path, monkeypatch, mode="live", mutations_enabled=True, dry_run=True
    )
    state.create_action_approval(
        source_type="ai_trigger",
        source_id="trigger",
        message={
            "message_key": "m1",
            "account_id": "acct",
            "folder": "INBOX",
            "uidvalidity": 7,
            "imap_uid": 42,
        },
        action={"action_type": "mark_read"},
        classification=_classification(),
    )
    approval_id = _approval_id(state)
    client.post(f"/api/mail/approvals/{approval_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    executed = client.post(f"/api/mail/approvals/{approval_id}/execute", headers={"X-Api-Key": "secret"})

    assert executed.status_code == 200
    assert executed.json()["status"] == "blocked"
    assert executed.json()["execution_status"] == "dry_run"
    assert calls == []


def test_approved_mailbox_action_uses_existing_gated_mutation_path(tmp_path, monkeypatch):
    class Result:
        def to_dict(self):
            return {"status": "completed"}

    calls = []
    monkeypatch.setattr(
        api_mail,
        "_mutation_executor",
        lambda settings: lambda *args, **kwargs: calls.append((args, kwargs)) or Result(),
    )
    client, state = _client(
        tmp_path, monkeypatch, mode="live", mutations_enabled=True, dry_run=False
    )
    state.create_action_approval(
        source_type="ai_trigger",
        source_id="trigger",
        message={
            "message_key": "m1",
            "account_id": "acct",
            "folder": "INBOX",
            "uidvalidity": 7,
            "imap_uid": 42,
        },
        action={"action_type": "mark_read"},
        classification=_classification(),
    )
    approval_id = _approval_id(state)
    client.post(f"/api/mail/approvals/{approval_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    executed = client.post(f"/api/mail/approvals/{approval_id}/execute", headers={"X-Api-Key": "secret"})

    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"
    assert executed.json()["execution_status"] == "completed"
    assert calls


def test_unsupported_and_dangerous_actions_are_blocked_or_rejected(tmp_path, monkeypatch):
    blocked_actions = [
        "send_imessage",
        "reply",
        "forward",
        "delete",
        "expunge",
        "unsubscribe",
        "webhook",
        "external_webhook",
    ]
    for action_type in blocked_actions:
        client, state = _client(
            tmp_path / action_type,
            monkeypatch,
            mode="live",
            mutations_enabled=True,
            dry_run=False,
        )
        approval_id = _create_approval(
            state,
            action_type=action_type,
            message_key=f"m-{action_type}",
            source_id=f"trigger-{action_type}",
        )
        client.post(
            f"/api/mail/approvals/{approval_id}/approve",
            headers={"X-Api-Key": "secret"},
            json={},
        )
        executed = client.post(
            f"/api/mail/approvals/{approval_id}/execute",
            headers={"X-Api-Key": "secret"},
        )
        assert executed.status_code == 200
        assert executed.json()["status"] == "blocked"
        assert executed.json()["execution_status"] == "unsupported"

    dangerous = client.post(
        "/api/mail/ai/triggers",
        headers={"X-Api-Key": "secret"},
        json=_trigger_payload(actions=[{"action_type": "delete"}]),
    )
    assert dangerous.status_code == 400


def test_api_auth_and_invalid_ids_are_structured(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    _create_approval(state)

    no_auth = client.get("/api/mail/approvals?status=pending")
    invalid = client.post(
        "/api/mail/approvals/not-found/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    invalid_execute = client.post(
        "/api/mail/approvals/not-found/execute",
        headers={"X-Api-Key": "secret"},
    )
    empty = client.get(
        "/api/mail/approvals?status=executed",
        headers={"X-Api-Key": "secret"},
    )

    assert no_auth.status_code in {401, 403}
    assert invalid.status_code == 404
    assert invalid.json()["detail"] == "Approval not found"
    assert invalid_execute.status_code == 404
    assert empty.status_code == 200
    assert empty.json() == []


def test_audit_events_and_no_bridge_send_alert(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    state.create_ai_trigger(_trigger_payload(actions=[{"action_type": "add_to_needs_reply"}]))
    state.complete_ai_item(_queue_message(state), _classification())
    approval_id = _approval_id(state)
    client.post(f"/api/mail/approvals/{approval_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    client.post(f"/api/mail/approvals/{approval_id}/execute", headers={"X-Api-Key": "secret"})

    event_types = [event[0] for event in _events(state)]
    assert "approval_created" in event_types
    assert "approval_approved" in event_types
    assert "approval_execution_started" in event_types
    assert "approval_executed" in event_types
    with state._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mail_needs_reply").fetchone()[0] == 1
