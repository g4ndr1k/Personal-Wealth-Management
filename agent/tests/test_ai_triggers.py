from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail
from agent.app.state import AgentState


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


def _trigger_payload(**overrides):
    payload = {
        "name": "Urgent payment",
        "enabled": True,
        "priority": 10,
        "conditions_json": {
            "match_type": "ALL",
            "conditions": [
                {"field": "category", "operator": "equals", "value": "payment_due"},
                {"field": "urgency_score", "operator": ">=", "value": 7},
                {"field": "confidence", "operator": ">=", "value": 0.8},
            ],
        },
        "actions_json": [
            {"action_type": "send_imessage"},
            {"action_type": "move_to_folder", "target": "Bills"},
            {"action_type": "mark_flagged"},
        ],
        "cooldown_seconds": 3600,
    }
    payload.update(overrides)
    return payload


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "agent.db"
    monkeypatch.setenv("AGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")
    AgentState(str(db_path))
    app = FastAPI()
    app.include_router(api_mail.router, prefix="/api/mail")
    return TestClient(app), AgentState(str(db_path))


def test_trigger_crud_accepts_valid_deterministic_conditions(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    headers = {"X-Api-Key": "secret"}

    created = client.post("/api/mail/ai/triggers", headers=headers, json=_trigger_payload())

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["trigger_id"]
    assert body["conditions_json"]["match_type"] == "ALL"
    assert body["actions_json"][0]["dry_run"] is True

    listed = client.get("/api/mail/ai/triggers", headers=headers)
    assert listed.status_code == 200
    assert [t["trigger_id"] for t in listed.json()] == [body["trigger_id"]]

    patched = client.patch(
        f"/api/mail/ai/triggers/{body['trigger_id']}",
        headers=headers,
        json={"enabled": False, "priority": 20},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(
        f"/api/mail/ai/triggers/{body['trigger_id']}",
        headers=headers,
    )
    assert deleted.status_code == 200


def test_trigger_crud_rejects_unknown_fields_operators_and_dangerous_actions(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    headers = {"X-Api-Key": "secret"}

    bad_field = _trigger_payload(conditions_json={
        "match_type": "ALL",
        "conditions": [{"field": "sender", "operator": "equals", "value": "a"}],
    })
    assert client.post("/api/mail/ai/triggers", headers=headers, json=bad_field).status_code == 400

    bad_operator = _trigger_payload(conditions_json={
        "match_type": "ALL",
        "conditions": [{"field": "urgency_score", "operator": "contains", "value": 7}],
    })
    assert client.post("/api/mail/ai/triggers", headers=headers, json=bad_operator).status_code == 400

    dangerous = _trigger_payload(actions_json=[{"action_type": "delete"}])
    resp = client.post("/api/mail/ai/triggers", headers=headers, json=dangerous)
    assert resp.status_code == 400
    assert "Dangerous" in resp.json()["detail"]


def test_preview_returns_matched_trigger_and_dry_run_actions(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    headers = {"X-Api-Key": "secret"}
    client.post("/api/mail/ai/triggers", headers=headers, json=_trigger_payload())

    preview = client.post(
        "/api/mail/ai/triggers/preview",
        headers=headers,
        json={"classification": _classification()},
    )

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["matched"] is True
    assert body["planned_actions"][0]["action_type"] == "send_imessage"
    assert all(action["dry_run"] is True for action in body["planned_actions"])
    assert all(action["would_execute"] is False for action in body["planned_actions"])


def test_ai_completion_evaluates_enabled_triggers_and_writes_audit(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    first = state.create_ai_trigger(_trigger_payload(priority=20, name="Second"))
    second = state.create_ai_trigger(_trigger_payload(priority=10, name="First"))
    queue_id = state.enqueue_ai_work({
        "bridge_id": "imap-acct-INBOX-1",
        "message_key": "mkey1",
        "imap_account": "acct",
        "imap_folder": "INBOX",
        "imap_uid": 1,
        "imap_uidvalidity": 9,
        "body_text": "Payment due tomorrow.",
    })

    state.complete_ai_item(queue_id, _classification())

    with state._connect() as conn:
        events = conn.execute(
            "SELECT event_type, outcome, details_json "
            "FROM mail_processing_events ORDER BY id"
        ).fetchall()
    trigger_events = [event for event in events if event[0] == "ai_trigger_matched"]
    approval_events = [event for event in events if event[0] == "approval_created"]
    assert [event[0] for event in trigger_events] == [
        "ai_trigger_matched",
        "ai_trigger_matched",
    ]
    assert len(approval_events) == 6
    assert [state.preview_ai_triggers(_classification())[i]["trigger_id"] for i in range(2)] == [
        second["trigger_id"],
        first["trigger_id"],
    ]
    assert trigger_events[0][1] == "dry_run"
    assert "Phase 4C.3A preview-only" in trigger_events[0][2]


def test_trigger_evaluation_failure_does_not_fail_ai_classification(tmp_path, monkeypatch):
    state = AgentState(str(tmp_path / "agent.db"))
    queue_id = state.enqueue_ai_work({"body_text": "Payment due tomorrow."})

    def fail(_classification):
        raise RuntimeError("trigger engine down")

    monkeypatch.setattr(state, "evaluate_ai_triggers_for_queue", lambda *_: fail({}))
    state.complete_ai_item(queue_id, _classification())

    with state._connect() as conn:
        status = conn.execute(
            "SELECT status FROM mail_ai_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()[0]
    assert status == "completed"


def test_disabled_triggers_do_not_match(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    state.create_ai_trigger(_trigger_payload(enabled=False))
    queue_id = state.enqueue_ai_work({"body_text": "Payment due tomorrow."})

    state.complete_ai_item(queue_id, _classification())

    with state._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM mail_processing_events "
            "WHERE event_type = 'ai_trigger_matched'"
        ).fetchone()[0]
    assert count == 0


def test_ai_triggers_do_not_call_imap_or_bridge_helpers(tmp_path, monkeypatch):
    from agent.app import imap_source

    state = AgentState(str(tmp_path / "agent.db"))
    state.create_ai_trigger(_trigger_payload(actions_json=[
        {"action_type": "send_imessage"},
        {"action_type": "move_to_folder", "target": "Bills"},
        {"action_type": "mark_read"},
    ]))
    calls = []
    monkeypatch.setattr(
        imap_source,
        "move_message_by_uid",
        lambda *args, **kwargs: calls.append(("move", args, kwargs)),
    )
    monkeypatch.setattr(
        imap_source,
        "store_flags_by_uid",
        lambda *args, **kwargs: calls.append(("store", args, kwargs)),
    )

    queue_id = state.enqueue_ai_work({"body_text": "Payment due tomorrow."})
    state.complete_ai_item(queue_id, _classification())

    assert calls == []
