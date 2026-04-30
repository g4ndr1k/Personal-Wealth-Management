from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail
from agent.app.action_verification import FakeReadOnlyMailboxAdapter
from agent.app.state import AgentState


def _settings(
        tmp_path: Path, *, mode="draft_only", mutations_enabled=False,
        dry_run=True, allow=False, require_capability_cache=True) -> Path:
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
	started_stale_after_minutes = 30
	allow_bulk_approve = false

[mail.imap_mutations]
enabled = {str(mutations_enabled).lower()}
dry_run_default = {str(dry_run).lower()}
allow_mark_read = {str(allow).lower()}
allow_mark_unread = {str(allow).lower()}
allow_add_label = {str(allow).lower()}
allow_move_to_folder = {str(allow).lower()}
require_uidvalidity_match = true
require_capability_cache = {str(require_capability_cache).lower()}
allow_create_folder = false
allow_copy_delete_fallback = false

[mail.imap]
accounts = [
  {{ id = "acct", name = "acct", email = "acct@example.com", provider = "gmail", host = "imap.example.com", auth_source = "file" }}
]
""",
        encoding="utf-8",
    )
    return path


def _client(
        tmp_path, monkeypatch, *, mode="draft_only",
        mutations_enabled=False, dry_run=True, allow=False,
        require_capability_cache=True):
    db_path = tmp_path / "agent.db"
    settings_path = _settings(
        tmp_path,
        mode=mode,
        mutations_enabled=mutations_enabled,
        dry_run=dry_run,
        allow=allow,
        require_capability_cache=require_capability_cache,
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


def _set_execution_started_at(state: AgentState, approval_id: str, started_at: str):
    with state._connect() as conn:
        conn.execute("""
            UPDATE mail_action_approvals
            SET execution_started_at = ?
            WHERE approval_id = ?
        """, (started_at, approval_id))
        conn.commit()


def _set_approval_times(
        state: AgentState, approval_id: str, when: str,
        *, archived_at: str | None = None):
    with state._connect() as conn:
        conn.execute("""
            UPDATE mail_action_approvals
            SET requested_at = ?,
                created_at = ?,
                updated_at = ?,
                decided_at = CASE WHEN decided_at IS NULL THEN NULL ELSE ? END,
                executed_at = CASE WHEN executed_at IS NULL THEN NULL ELSE ? END,
                archived_at = COALESCE(?, archived_at)
            WHERE approval_id = ?
        """, (when, when, when, when, when, archived_at, approval_id))
        conn.commit()


def _age_approval(state: AgentState, approval_id: str, *, days=0, hours=0):
    old = (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).isoformat()
    _set_approval_times(state, approval_id, old)
    return old


def _create_approval(
        state: AgentState, *, action_type="mark_read", message_key="m1",
        source_id="trigger", target=None, imap_uid=42, uidvalidity=7,
        account_id="acct", folder="INBOX"):
    state.create_action_approval(
        source_type="ai_trigger",
        source_id=source_id,
        message={
            "message_key": message_key,
            "account_id": account_id,
            "folder": folder,
            "uidvalidity": uidvalidity,
            "imap_uid": imap_uid,
        },
        action={"action_type": action_type, "target": target},
        classification=_classification(),
    )
    return _approval_id(state)


def _terminal_approval(client, state, *, status="rejected", message_key="terminal"):
    approval_id = _create_approval(state, message_key=message_key, source_id=message_key)
    if status == "rejected":
        client.post(
            f"/api/mail/approvals/{approval_id}/reject",
            headers={"X-Api-Key": "secret"},
            json={},
        )
    elif status == "expired":
        client.post(
            f"/api/mail/approvals/{approval_id}/expire",
            headers={"X-Api-Key": "secret"},
        )
    elif status == "executed":
        client.post(
            f"/api/mail/approvals/{approval_id}/approve",
            headers={"X-Api-Key": "secret"},
            json={},
        )
        state.mark_approval_execution_started(approval_id)
        state.finish_action_approval_execution(
            approval_id,
            status="executed",
            execution_status="completed",
            result={"status": "executed"},
        )
    else:
        raise ValueError(status)
    return approval_id


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
    assert body[0]["preview_title"]
    assert body[0]["preview_summary"]
    assert body[0]["risk_level"] == "caution"
    assert body[0]["would_be_blocked_now"] is True
    assert body[0]["current_gate_preview"]["gate"] == "mode_blocked"
    assert body[0]["message_context"]["folder"] == "INBOX"
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


def test_approved_mailbox_action_does_not_call_live_mutation_executor(tmp_path, monkeypatch):
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
        tmp_path, monkeypatch, mode="live", mutations_enabled=True, dry_run=False, allow=True
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
    assert executed.json()["execution_status"] == "folder_state_missing"
    assert calls == []


def _create_verifiable_approval(state: AgentState, *, action_type="mark_read"):
    state.set_imap_folder_state("acct", "INBOX", 42, 7)
    state.upsert_imap_capability_cache(
        account_id="acct",
        folder="INBOX",
        uidvalidity=7,
        capabilities=["IMAP4REV1"],
        supports_store_flags=True,
        source="test",
    )
    state.create_action_approval(
        source_type="ai_trigger",
        source_id=f"verifiable-{action_type}",
        message={
            "message_key": "<m42@example.test>",
            "account_id": "acct",
            "folder": "INBOX",
            "uidvalidity": 7,
            "imap_uid": 42,
            "subject": "Payment due",
            "sender": "billing@example.test",
            "received_at": "Fri, 01 May 2026 07:00:00 +0700",
        },
        action={
            "action_type": action_type,
            "value": {"before_state": {"seen": action_type == "mark_unread"}},
        },
        classification=_classification(),
    )
    return _approval_id(state)


def _fake_adapter(*, uidvalidity="7", seen=False):
    return FakeReadOnlyMailboxAdapter(
        uidvalidity=uidvalidity,
        messages={
            "42": {
                "identity": {
                    "message_id": "<m42@example.test>",
                    "subject": "Payment due",
                    "from": "billing@example.test",
                    "date": "Fri, 01 May 2026 07:00:00 +0700",
                },
                "flags": {"seen": seen},
            }
        },
    )


def test_approval_execute_runs_final_verifier_before_mock_executor(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_mail,
        "_readonly_mailbox_adapter",
        lambda settings, approval: calls.append(approval["approval_id"]) or _fake_adapter(),
    )
    monkeypatch.setattr(
        api_mail,
        "_mutation_executor",
        lambda settings: (_ for _ in ()).throw(RuntimeError("live executor must not be built")),
    )
    client, state = _client(
        tmp_path, monkeypatch, mode="live",
        mutations_enabled=True, dry_run=False, allow=True,
    )
    approval_id = _create_verifiable_approval(state)
    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    executed = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )

    body = executed.json()
    assert executed.status_code == 200
    assert calls == [approval_id]
    assert body["status"] == "executed"
    assert body["execution_status"] == "mock_executed"
    assert body["execution_result"]["execution_mode"] == "mock"
    assert body["execution_result"]["final_verification"]["status"] == "verified"
    with state._connect() as conn:
        assert conn.execute(
            "SELECT status FROM mail_action_executions"
        ).fetchone()[0] == "mock_executed"


def test_verifier_block_prevents_mock_execution_and_writes_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        api_mail,
        "_readonly_mailbox_adapter",
        lambda settings, approval: _fake_adapter(uidvalidity="99"),
    )
    client, state = _client(
        tmp_path, monkeypatch, mode="live",
        mutations_enabled=True, dry_run=False, allow=True,
    )
    approval_id = _create_verifiable_approval(state)
    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    executed = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )

    body = executed.json()
    assert executed.status_code == 200
    assert body["status"] == "blocked"
    assert body["execution_status"] == "final_verification_blocked"
    assert body["execution_result"]["final_verification"]["blockers"][0]["code"] == "uidvalidity_mismatch"
    with state._connect() as conn:
        rows = conn.execute(
            "SELECT status FROM mail_action_executions"
        ).fetchall()
        events = conn.execute(
            "SELECT event_type FROM mail_action_execution_events ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == ["blocked"]
    assert [event[0] for event in events] == ["final_verification_blocked"]


def test_bulk_execution_endpoint_does_not_exist(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/mail/approvals/bulk-execute",
        headers={"X-Api-Key": "secret"},
        json={"approval_ids": []},
    )

    assert response.status_code in {404, 405}


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
    detail_no_auth = client.get(f"/api/mail/approvals/{_approval_id(state)}")
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
    assert detail_no_auth.status_code in {401, 403}
    assert invalid.status_code == 404
    assert invalid.json()["detail"] == "Approval not found"
    assert invalid_execute.status_code == 404
    assert empty.status_code == 200
    assert empty.json() == []


def test_approval_detail_includes_result_and_timeline(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    state.create_ai_trigger(_trigger_payload(
        actions=[{"action_type": "add_to_needs_reply", "value": {"note": "review"}}]
    ))
    state.complete_ai_item(_queue_message(state), _classification())
    approval_id = _approval_id(state)
    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={"decision_note": "Looks correct"},
    )
    executed = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )

    assert executed.status_code == 200, executed.text
    detail = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    )

    body = detail.json()
    assert detail.status_code == 200
    assert body["approval_id"] == approval_id
    assert body["action_type"] == "add_to_needs_reply"
    assert body["message_id"] == "mkey:mkey42"
    assert body["trigger_id"]
    assert body["approved_at"]
    assert body["execution_started_at"]
    assert body["execution_finished_at"]
    assert body["execution_state"] == "executed"
    assert body["execution_result"]["result"]["event_written"] is True
    assert body["proposed_value"] == {"note": "review"}
    assert body["message_context"] == {
        "sender": "billing@example.com",
        "subject": "Payment due",
        "received_at": "2026-04-30T00:00:00+00:00",
        "account_id": "acct",
        "account_label": "acct",
        "folder": "INBOX",
        "imap_uid": 42,
        "uidvalidity": "7",
        "classification_category": "payment_due",
        "ai_summary": "Phase 4C.3A preview-only",
        "urgency_score": 8,
        "confidence": 0.9,
    }
    assert body["trigger_context"]["trigger_name"] == "Urgent payment"
    assert body["trigger_context"]["matched_category"] == "payment_due"
    assert body["trigger_context"]["planned_action"]["action_type"] == "add_to_needs_reply"
    assert body["rule_context"] is None
    assert body["current_gate_preview"]["gate"] == "terminal"
    assert body["audit_event_ids"] == [event["id"] for event in body["events"]]
    assert [event["id"] for event in body["events"]] == sorted(
        event["id"] for event in body["events"])
    assert "body_text" not in str(body)
    assert "Payment due tomorrow." not in str(body)
    assert "secret" not in str(body).lower()


def test_pending_preview_is_read_only_and_explains_dry_run_block(tmp_path, monkeypatch):
    client, state = _client(
        tmp_path, monkeypatch, mode="live", mutations_enabled=True, dry_run=True
    )
    approval_id = _create_approval(state, action_type="mark_read")
    before = _events(state)

    detail = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    )
    after = state.get_action_approval(approval_id)

    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "pending"
    assert body["execution_status"] is None
    assert after["status"] == "pending"
    assert after["execution_status"] is None
    assert _events(state) == before
    assert body["current_gate_preview"]["gate"] == "dry_run"
    assert body["current_gate_preview"]["reason"] == "mail.imap_mutations.dry_run_default=true"
    assert body["would_execute_now"] is False
    assert body["would_be_blocked_now"] is True
    assert body["risk_level"] == "safe_reversible"
    plan = body["current_gate_preview"]["dry_run_plan"]
    assert plan["action_type"] == "mark_read"
    assert plan["operation"] == r"STORE +FLAGS.SILENT (\Seen)"
    assert plan["would_mutate"] is False
    assert plan["rollback_hint"] == r"mark_unread using STORE -FLAGS.SILENT (\Seen)"


def test_config_defaults_keep_imap_mutations_conservative(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    approval_id = _create_approval(state, action_type="mark_read")

    body = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    ).json()
    gates = body["current_gate_preview"]["dry_run_plan"]["safety_gates"]

    assert body["current_gate_preview"]["mutation_enabled"] is False
    assert body["current_gate_preview"]["dry_run_default"] is True
    assert body["current_gate_preview"]["allow_mark_read"] is False
    assert body["current_gate_preview"]["allow_mark_unread"] is False
    assert body["current_gate_preview"]["allow_add_label"] is False
    assert body["current_gate_preview"]["allow_move_to_folder"] is False
    assert {"gate": "imap_mutations_enabled", "status": "blocked", "reason": "false"} in gates


def test_reversible_action_previews_return_dry_run_plans(tmp_path, monkeypatch):
    for action_type, target, operation in [
        ("mark_read", None, r"STORE +FLAGS.SILENT (\Seen)"),
        ("mark_unread", None, r"STORE -FLAGS.SILENT (\Seen)"),
        ("add_label", "Receipts", "STORE +X-GM-LABELS.SILENT (Receipts)"),
        ("move_to_folder", "Archive", "UID MOVE/COPY to Archive"),
    ]:
        client, state = _client(tmp_path / action_type, monkeypatch)
        approval_id = _create_approval(
            state,
            action_type=action_type,
            target=target,
            message_key=action_type,
            source_id=action_type,
        )
        body = client.get(
            f"/api/mail/approvals/{approval_id}",
            headers={"X-Api-Key": "secret"},
        ).json()
        plan = body["current_gate_preview"]["dry_run_plan"]
        assert plan["action_type"] == action_type
        assert plan["operation"] == operation
        assert plan["dry_run"] is True
        assert plan["would_mutate"] is False
        assert plan["reversible"] is True
        assert body["would_execute_now"] is False


def test_preview_blocks_missing_uid_and_uidvalidity(tmp_path, monkeypatch):
    for kwargs, expected_gate in [
        ({"imap_uid": None}, "identity_incomplete"),
        ({"uidvalidity": None}, "identity_incomplete"),
    ]:
        client, state = _client(
            tmp_path / expected_gate / str(kwargs),
            monkeypatch,
            mode="live",
            mutations_enabled=True,
            dry_run=False,
            allow=True,
            require_capability_cache=False,
        )
        state.set_imap_folder_state("acct", "INBOX", 42, 7)
        approval_id = _create_approval(
            state,
            message_key=str(kwargs),
            source_id=str(kwargs),
            **kwargs,
        )
        body = client.get(
            f"/api/mail/approvals/{approval_id}",
            headers={"X-Api-Key": "secret"},
        ).json()
        assert body["current_gate_preview"]["gate"] == expected_gate
        assert body["would_execute_now"] is False


def test_preview_blocks_uidvalidity_mismatch(tmp_path, monkeypatch):
    client, state = _client(
        tmp_path,
        monkeypatch,
        mode="live",
        mutations_enabled=True,
        dry_run=False,
        allow=True,
        require_capability_cache=False,
    )
    state.set_imap_folder_state("acct", "INBOX", 42, 99)
    approval_id = _create_approval(state, action_type="mark_read", uidvalidity=7)

    body = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    ).json()

    assert body["current_gate_preview"]["gate"] == "uidvalidity_mismatch"
    assert "cached=99 approval=7" == body["current_gate_preview"]["reason"]


def test_capability_cache_store_read_is_idempotent(tmp_path, monkeypatch):
    _, state = _client(tmp_path, monkeypatch)

    first = state.upsert_imap_capability_cache(
        account_id="acct",
        folder="INBOX",
        uidvalidity=7,
        capabilities=["IMAP4REV1", "MOVE"],
        supports_store_flags=True,
        supports_move=True,
        supports_create_folder=False,
        supports_gmail_labels=None,
        source="test",
    )
    second = state.upsert_imap_capability_cache(
        account_id="acct",
        folder="INBOX",
        uidvalidity=7,
        capabilities=["IMAP4REV1", "MOVE"],
        supports_store_flags=True,
        supports_move=True,
        supports_create_folder=False,
        supports_gmail_labels=None,
        source="test",
    )

    assert first["capabilities"] == ["IMAP4REV1", "MOVE"]
    assert second["supports_store_flags"] is True
    with state._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM imap_capability_cache"
        ).fetchone()[0] == 1


def test_preview_explains_imap_mutation_disabled_block(tmp_path, monkeypatch):
    client, state = _client(
        tmp_path, monkeypatch, mode="live", mutations_enabled=False, dry_run=False
    )
    approval_id = _create_approval(state, action_type="mark_read")

    body = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    ).json()

    assert body["current_gate_preview"]["gate"] == "mutation_disabled"
    assert body["current_gate_preview"]["reason"] == "mail.imap_mutations.enabled=false"
    assert body["would_be_blocked_now"] is True


def test_preview_blocks_missing_capability_cache_when_required(tmp_path, monkeypatch):
    client, state = _client(
        tmp_path, monkeypatch, mode="live", mutations_enabled=True, dry_run=False, allow=True
    )
    state.set_imap_folder_state("acct", "INBOX", 42, 7)
    approval_id = _create_approval(state, action_type="mark_read")

    body = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    ).json()

    assert body["current_gate_preview"]["gate"] == "capability_cache_missing"
    assert body["current_gate_preview"]["capability"] == "missing"
    assert body["current_gate_preview"]["dry_run_plan"]["would_mutate"] is False
    assert body["would_execute_now"] is False


def test_preview_readiness_can_reach_ready_but_still_says_live_disabled(tmp_path, monkeypatch):
    client, state = _client(
        tmp_path,
        monkeypatch,
        mode="live",
        mutations_enabled=True,
        dry_run=False,
        allow=True,
    )
    state.set_imap_folder_state("acct", "INBOX", 42, 7)
    state.upsert_imap_capability_cache(
        account_id="acct",
        folder="INBOX",
        uidvalidity=7,
        capabilities=["IMAP4REV1"],
        supports_store_flags=True,
        supports_move=True,
        supports_gmail_labels=None,
        source="test",
    )
    approval_id = _create_approval(state, action_type="mark_read")

    body = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    ).json()

    assert body["current_gate_preview"]["gate"] == "ready"
    assert body["current_gate_preview"]["capability"] == "known"
    assert body["would_execute_now"] is False
    assert "Live mutation disabled" in " ".join(body["current_gate_preview"]["notes"])


def test_preview_blocks_unsupported_and_dangerous_aliases(tmp_path, monkeypatch):
    for action_type, expected_risk in [
        ("delete", "dangerous_blocked"),
        ("expunge", "dangerous_blocked"),
        ("reply", "dangerous_blocked"),
        ("forward", "dangerous_blocked"),
        ("webhook", "dangerous_blocked"),
        ("send_imessage", "dangerous_blocked"),
        ("unknown_action", "unsupported_blocked"),
    ]:
        client, state = _client(tmp_path / action_type, monkeypatch, mode="live", mutations_enabled=True, dry_run=False)
        approval_id = _create_approval(state, action_type=action_type, message_key=action_type, source_id=action_type)

        body = client.get(
            f"/api/mail/approvals/{approval_id}",
            headers={"X-Api-Key": "secret"},
        ).json()

        assert body["current_gate_preview"]["gate"] == "unsupported"
        assert body["would_execute_now"] is False
        assert body["would_be_blocked_now"] is True
        assert body["risk_level"] == expected_risk


def test_terminal_and_stale_preview_states_cannot_execute_now(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    rejected_id = _create_approval(state, message_key="rejected", source_id="rejected")
    client.post(f"/api/mail/approvals/{rejected_id}/reject", headers={"X-Api-Key": "secret"}, json={})
    expired_id = _create_approval(state, message_key="expired", source_id="expired")
    client.post(f"/api/mail/approvals/{expired_id}/expire", headers={"X-Api-Key": "secret"})
    executed_id = _create_approval(state, message_key="executed", source_id="executed", action_type="add_to_needs_reply")
    client.post(f"/api/mail/approvals/{executed_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    client.post(f"/api/mail/approvals/{executed_id}/execute", headers={"X-Api-Key": "secret"})
    stale_id = _create_approval(state, message_key="stale", source_id="stale")
    client.post(f"/api/mail/approvals/{stale_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    state.mark_approval_execution_started(stale_id)
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    _set_execution_started_at(state, stale_id, old)

    expected = {
        rejected_id: ("rejected", "Approval was rejected."),
        expired_id: ("expired", "Approval is expired."),
        executed_id: ("terminal", "Approval already reached terminal status 'executed'."),
        stale_id: ("manual_review_required", "execution_status='started' is stale"),
    }
    for approval_id, (gate, reason) in expected.items():
        body = client.get(
            f"/api/mail/approvals/{approval_id}",
            headers={"X-Api-Key": "secret"},
        ).json()
        assert body["current_gate_preview"]["gate"] == gate
        assert body["current_gate_preview"]["reason"] == reason
        assert body["would_execute_now"] is False
        assert body["would_be_blocked_now"] is True


def test_preview_handles_missing_trigger_and_rule_context(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    ai_id = _create_approval(state, source_id="missing-trigger")
    state.create_action_approval(
        source_type="rule_preview",
        source_id="rule-7",
        message={"message_key": "rule-message", "account_id": "acct"},
        action={"action_type": "add_to_needs_reply"},
        classification=None,
    )
    rule_id = [
        row["approval_id"] for row in state.list_action_approvals(status=None)
        if row["source_type"] == "rule_preview"
    ][0]

    ai_detail = client.get(
        f"/api/mail/approvals/{ai_id}",
        headers={"X-Api-Key": "secret"},
    )
    rule_detail = client.get(
        f"/api/mail/approvals/{rule_id}",
        headers={"X-Api-Key": "secret"},
    )

    assert ai_detail.status_code == 200
    assert ai_detail.json()["trigger_context"]["trigger_id"] == "missing-trigger"
    assert rule_detail.status_code == 200
    assert rule_detail.json()["trigger_context"] is None
    assert rule_detail.json()["rule_context"]["rule_id"] == "rule-7"


def test_approval_events_endpoint_auth_and_order(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    approval_id = _create_approval(state, action_type="add_to_needs_reply")

    no_auth = client.get(f"/api/mail/approvals/{approval_id}/events")
    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )
    events = client.get(
        f"/api/mail/approvals/{approval_id}/events",
        headers={"X-Api-Key": "secret"},
    )

    assert no_auth.status_code in {401, 403}
    assert events.status_code == 200
    ids = [event["id"] for event in events.json()]
    assert ids == sorted(ids)
    event_types = [event["event_type"] for event in events.json()]
    assert "approval_created" in event_types
    assert "approval_execution_started" in event_types
    assert "approval_executed" in event_types


def test_blocked_execution_detail_records_gate_reason(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch, mode="live", mutations_enabled=True, dry_run=True)
    approval_id = _create_approval(state, action_type="mark_read")
    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    executed = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )

    body = executed.json()
    assert executed.status_code == 200
    assert body["status"] == "blocked"
    assert body["execution_state"] == "blocked"
    assert body["execution_status"] == "dry_run"
    assert body["blocked_reason"] == "dry_run"
    assert body["gate_result"]["status"] == "dry_run"


def test_live_executor_failure_path_is_not_reachable_from_approval(tmp_path, monkeypatch):
    def raise_executor(settings):
        def run(*args, **kwargs):
            raise RuntimeError("simulated IMAP failure")
        return run

    monkeypatch.setattr(api_mail, "_mutation_executor", raise_executor)
    client, state = _client(
        tmp_path,
        monkeypatch,
        mode="live",
        mutations_enabled=True,
        dry_run=False,
        allow=True,
    )
    approval_id = _create_approval(state, action_type="mark_read")
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
    assert executed.json()["execution_state"] == "blocked"
    assert executed.json()["execution_status"] == "folder_state_missing"
    assert executed.json()["execution_error"] is None


def test_started_approval_stuck_detection_and_mark_failed(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    approval_id = _create_approval(state)
    client.post(
        f"/api/mail/approvals/{approval_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    state.mark_approval_execution_started(approval_id)
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    _set_execution_started_at(state, approval_id, old)

    detail = client.get(
        f"/api/mail/approvals/{approval_id}",
        headers={"X-Api-Key": "secret"},
    )
    execute_again = client.post(
        f"/api/mail/approvals/{approval_id}/execute",
        headers={"X-Api-Key": "secret"},
    )
    marked = client.post(
        f"/api/mail/approvals/{approval_id}/mark-failed",
        headers={"X-Api-Key": "secret"},
        json={"reason": "Operator verified crashed worker"},
    )

    assert detail.status_code == 200
    assert detail.json()["execution_state"] == "stuck"
    assert detail.json()["is_stuck"] is True
    assert execute_again.status_code == 409
    assert marked.status_code == 200
    assert marked.json()["status"] == "failed"
    assert marked.json()["execution_state"] == "failed"
    assert marked.json()["execution_error"] == "Operator verified crashed worker"
    assert "approval_marked_failed" in [
        event["event_type"] for event in marked.json()["events"]]


def test_non_stale_started_and_terminal_derived_states(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    started_id = _create_approval(state, message_key="started", source_id="started")
    client.post(
        f"/api/mail/approvals/{started_id}/approve",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    state.mark_approval_execution_started(started_id)
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _set_execution_started_at(state, started_id, fresh)

    rejected_id = _create_approval(state, message_key="rejected", source_id="rejected")
    client.post(
        f"/api/mail/approvals/{rejected_id}/reject",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    expired_id = _create_approval(state, message_key="expired", source_id="expired")
    client.post(
        f"/api/mail/approvals/{expired_id}/expire",
        headers={"X-Api-Key": "secret"},
    )

    assert client.get(
        f"/api/mail/approvals/{started_id}",
        headers={"X-Api-Key": "secret"},
    ).json()["execution_state"] == "started"
    assert client.post(
        f"/api/mail/approvals/{started_id}/mark-failed",
        headers={"X-Api-Key": "secret"},
        json={},
    ).status_code == 409
    assert client.get(
        f"/api/mail/approvals/{rejected_id}",
        headers={"X-Api-Key": "secret"},
    ).json()["execution_state"] == "rejected"
    assert client.get(
        f"/api/mail/approvals/{expired_id}",
        headers={"X-Api-Key": "secret"},
    ).json()["execution_state"] == "expired"


def test_mark_failed_only_for_stale_started_terminal_items_rejected(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    pending_id = _create_approval(state, message_key="pending", source_id="pending")
    executed_id = _create_approval(state, message_key="executed", source_id="executed", action_type="add_to_needs_reply")
    rejected_id = _create_approval(state, message_key="rejected", source_id="rejected")
    expired_id = _create_approval(state, message_key="expired", source_id="expired")
    client.post(f"/api/mail/approvals/{executed_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    client.post(f"/api/mail/approvals/{executed_id}/execute", headers={"X-Api-Key": "secret"})
    client.post(f"/api/mail/approvals/{rejected_id}/reject", headers={"X-Api-Key": "secret"}, json={})
    client.post(f"/api/mail/approvals/{expired_id}/expire", headers={"X-Api-Key": "secret"})

    for approval_id in [pending_id, executed_id, rejected_id, expired_id]:
        response = client.post(
            f"/api/mail/approvals/{approval_id}/mark-failed",
            headers={"X-Api-Key": "secret"},
            json={},
        )
        assert response.status_code == 409


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


def test_cleanup_preview_is_read_only_and_excludes_started(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    old_pending = _create_approval(state, message_key="old-pending", source_id="old-pending")
    _age_approval(state, old_pending, hours=30)
    old_terminal = _terminal_approval(client, state, status="rejected", message_key="old-terminal")
    _age_approval(state, old_terminal, days=45)
    started = _create_approval(state, message_key="started-cleanup", source_id="started-cleanup")
    client.post(f"/api/mail/approvals/{started}/approve", headers={"X-Api-Key": "secret"}, json={})
    state.mark_approval_execution_started(started)
    _set_execution_started_at(
        state,
        started,
        (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(),
    )
    before_events = _events(state)

    preview = client.get(
        "/api/mail/approvals/cleanup/preview",
        headers={"X-Api-Key": "secret"},
    )

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["cleanup_enabled"] is False
    assert body["would_expire_pending"] == 1
    assert body["would_archive_terminal"] == 1
    assert body["would_hard_delete"] == 0
    assert body["stuck_or_started_excluded"] == 1
    assert state.get_action_approval(old_pending)["status"] == "pending"
    assert state.get_action_approval(old_terminal)["archived_at"] is None
    assert state.get_action_approval(started)["execution_status"] == "started"
    assert _events(state) == before_events


def test_cleanup_disabled_then_forced_expires_and_archives_only_safe_rows(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    old_pending = _create_approval(state, message_key="cleanup-pending", source_id="cleanup-pending")
    _age_approval(state, old_pending, hours=30)
    fresh_pending = _create_approval(state, message_key="fresh-pending", source_id="fresh-pending")
    old_terminal = _terminal_approval(client, state, status="expired", message_key="cleanup-terminal")
    _age_approval(state, old_terminal, days=45)
    started = _create_approval(state, message_key="cleanup-started", source_id="cleanup-started")
    client.post(f"/api/mail/approvals/{started}/approve", headers={"X-Api-Key": "secret"}, json={})
    state.mark_approval_execution_started(started)

    disabled = client.post(
        "/api/mail/approvals/cleanup",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert disabled.status_code == 200
    assert disabled.json()["cleanup_ran"] is False
    assert state.get_action_approval(old_pending)["status"] == "pending"

    forced = client.post(
        "/api/mail/approvals/cleanup",
        headers={"X-Api-Key": "secret"},
        json={"force": True},
    )

    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["cleanup_ran"] is True
    assert body["expired_ids"] == [old_pending]
    assert body["archived_ids"] == [old_terminal]
    assert body["hard_deleted_ids"] == []
    assert state.get_action_approval(old_pending)["status"] == "expired"
    assert state.get_action_approval(old_terminal)["archived_at"]
    assert state.get_action_approval(fresh_pending)["status"] == "pending"
    assert state.get_action_approval(started)["execution_status"] == "started"
    event_types = [event[0] for event in _events(state)]
    assert "approval_cleanup_expired" in event_types
    assert "approval_cleanup_archived" in event_types


def test_archive_unarchive_visibility_and_invalid_transitions(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    pending_id = _create_approval(state, message_key="archive-pending", source_id="archive-pending")
    terminal_id = _terminal_approval(client, state, status="rejected", message_key="archive-terminal")
    started_id = _create_approval(state, message_key="archive-started", source_id="archive-started")
    client.post(f"/api/mail/approvals/{started_id}/approve", headers={"X-Api-Key": "secret"}, json={})
    state.mark_approval_execution_started(started_id)

    assert client.post(
        f"/api/mail/approvals/{pending_id}/archive",
        headers={"X-Api-Key": "secret"},
        json={},
    ).status_code == 409
    assert client.post(
        f"/api/mail/approvals/{started_id}/archive",
        headers={"X-Api-Key": "secret"},
        json={},
    ).status_code == 409

    archived = client.post(
        f"/api/mail/approvals/{terminal_id}/archive",
        headers={"X-Api-Key": "secret"},
        json={"decided_by": "tester"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"]
    assert archived.json()["is_archived"] is True
    hidden = client.get(
        "/api/mail/approvals?status=rejected",
        headers={"X-Api-Key": "secret"},
    ).json()
    visible = client.get(
        "/api/mail/approvals?status=rejected&include_archived=true",
        headers={"X-Api-Key": "secret"},
    ).json()
    assert terminal_id not in [row["approval_id"] for row in hidden]
    assert terminal_id in [row["approval_id"] for row in visible]

    unarchived = client.post(
        f"/api/mail/approvals/{terminal_id}/unarchive",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert unarchived.status_code == 200
    assert unarchived.json()["archived_at"] is None
    restored = client.get(
        "/api/mail/approvals?status=rejected",
        headers={"X-Api-Key": "secret"},
    ).json()
    assert terminal_id in [row["approval_id"] for row in restored]
    event_types = [event[0] for event in _events(state)]
    assert "approval_archived" in event_types
    assert "approval_unarchived" in event_types


def test_approval_export_sanitized_filters_and_archived(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    state.create_ai_trigger(_trigger_payload())
    state.complete_ai_item(_queue_message(state), _classification())
    approval_id = _approval_id(state)
    client.post(
        f"/api/mail/approvals/{approval_id}/reject",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    client.post(
        f"/api/mail/approvals/{approval_id}/archive",
        headers={"X-Api-Key": "secret"},
        json={},
    )

    hidden = client.get(
        "/api/mail/approvals/export?status=rejected&include_events=true",
        headers={"X-Api-Key": "secret"},
    )
    exported = client.get(
        "/api/mail/approvals/export?status=rejected&include_archived=true&include_events=true",
        headers={"X-Api-Key": "secret"},
    )

    assert hidden.status_code == 200
    assert hidden.json()["count"] == 0
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["count"] == 1
    assert body["approvals"][0]["approval"]["approval_id"] == approval_id
    assert body["approvals"][0]["events"]
    text = str(body)
    assert "body_text" not in text
    assert "Payment due tomorrow." not in text
    assert "secret" not in text.lower()


def test_approval_list_filters_and_auth(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    pending_id = _create_approval(state, message_key="filter-pending", source_id="filter-pending")
    executed_id = _terminal_approval(client, state, status="executed", message_key="filter-executed")

    no_auth = client.get("/api/mail/approvals/cleanup/preview")
    invalid_status = client.get(
        "/api/mail/approvals?status=weird",
        headers={"X-Api-Key": "secret"},
    )
    invalid_state = client.get(
        "/api/mail/approvals?execution_state=weird",
        headers={"X-Api-Key": "secret"},
    )
    pending = client.get(
        "/api/mail/approvals?status=pending&limit=500",
        headers={"X-Api-Key": "secret"},
    )
    executed = client.get(
        "/api/mail/approvals?execution_state=executed&status=executed",
        headers={"X-Api-Key": "secret"},
    )

    assert no_auth.status_code in {401, 403}
    assert invalid_status.status_code == 400
    assert invalid_state.status_code == 400
    assert pending.status_code == 200
    assert [row["approval_id"] for row in pending.json()] == [pending_id]
    assert executed.status_code == 200
    assert [row["approval_id"] for row in executed.json()] == [executed_id]
