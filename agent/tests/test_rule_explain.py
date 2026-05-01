import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail, rules as rules_module
from agent.app.state import AgentState


HEADERS = {"X-Api-Key": "secret"}


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "agent.db"
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        """
[agent]
mode = "draft_only"
safe_default = "draft_only"

[mail]
source = "gmail"

[mail.imap_mutations]
enabled = false
dry_run_default = true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("SETTINGS_FILE", str(settings_path))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")
    AgentState(str(db_path))
    app = FastAPI()
    app.include_router(api_mail.router, prefix="/api/mail")
    return TestClient(app), db_path


def _counts(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "mail_rules",
                "mail_rule_conditions",
                "mail_rule_actions",
                "mail_processing_events",
                "mail_needs_reply",
                "mail_rule_ai_draft_audit",
                "mail_action_executions",
            )
        }


def _create_rule(client, payload):
    response = client.post("/api/mail/rules", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _message(**overrides):
    payload = {
        "sender_email": "alerts@bca.co.id",
        "subject": "Suspicious transaction alert",
        "body_text": "We detected suspicious transaction activity.",
        "imap_account": "gmail_g4ndr1k",
        "imap_folder": "INBOX",
        "has_attachment": False,
    }
    payload.update(overrides)
    return payload


def test_explain_matching_alert_rule_is_read_only_with_condition_details(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    rule = _create_rule(client, {
        "name": "BCA suspicious transaction alert",
        "account_id": None,
        "priority": 10,
        "enabled": True,
        "match_type": "ALL",
        "conditions": [
            {"field": "from_domain", "operator": "contains", "value": "bca.co.id"},
            {"field": "subject", "operator": "contains", "value": "suspicious"},
        ],
        "actions": [
            {
                "action_type": "mark_pending_alert",
                "target": "imessage",
                "value_json": {"template": "BCA suspicious transaction email detected."},
            }
        ],
    })
    before = _counts(db_path)

    response = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message(), "rule_id": rule["rule_id"]},
    )

    assert response.status_code == 200, response.text
    assert _counts(db_path) == before
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["preview"] is True
    assert payload["matched_rule_count"] == 1
    assert payload["message_summary"]["sender_domain"] == "bca.co.id"
    assert payload["rules"][0]["matched"] is True
    assert payload["rules"][0]["conditions"] == [
        {
            "field": "from_domain",
            "operator": "contains",
            "expected": "bca.co.id",
            "actual": "bca.co.id",
            "matched": True,
            "case_sensitive": False,
        },
        {
            "field": "subject",
            "operator": "contains",
            "expected": "suspicious",
            "actual": "Suspicious transaction alert",
            "matched": True,
            "case_sensitive": False,
        },
    ]
    action = payload["planned_actions"][0]
    assert action["action_type"] == "mark_pending_alert"
    assert action["target"] == "imessage"
    assert action["mutation"] is False
    assert action["would_execute"] is False
    assert "local pending alert" in action["explanation"]
    assert payload["safety"] | {
        "wrote_rule_rows": False,
        "wrote_approval_rows": False,
        "called_ollama": False,
        "called_cloud_llm": False,
    } == {
        "read_only": True,
        "sent_imessage": False,
        "called_bridge": False,
        "called_imap": False,
        "mutated_gmail": False,
        "mutated_imap": False,
        "wrote_events": False,
        "wrote_rule_rows": False,
        "wrote_approval_rows": False,
        "called_ollama": False,
        "called_cloud_llm": False,
    }


def test_explain_non_matching_rule_shows_failed_condition(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    _create_rule(client, {
        "name": "BCA only",
        "priority": 10,
        "enabled": True,
        "match_type": "ALL",
        "conditions": [
            {"field": "from_domain", "operator": "contains", "value": "bca.co.id"},
            {"field": "subject", "operator": "contains", "value": "suspicious"},
        ],
        "actions": [{"action_type": "notify_dashboard"}],
    })
    before = _counts(db_path)

    response = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message(sender_email="alerts@notbca.example")},
    )

    assert response.status_code == 200, response.text
    assert _counts(db_path) == before
    payload = response.json()
    assert payload["matched_rule_count"] == 0
    conditions = payload["rules"][0]["conditions"]
    assert conditions[0]["actual"] == "notbca.example"
    assert conditions[0]["matched"] is False
    assert conditions[1]["matched"] is True
    assert payload["planned_actions"] == []


def test_sender_suppression_explanation_shows_skip_ai_and_stop(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    _create_rule(client, {
        "name": "Suppress spam sender",
        "priority": 10,
        "enabled": True,
        "match_type": "ALL",
        "conditions": [
            {"field": "from_email", "operator": "equals", "value": "spam@example.com"}
        ],
        "actions": [
            {"action_type": "skip_ai_inference"},
            {"action_type": "stop_processing"},
        ],
    })
    before = _counts(db_path)

    response = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message(sender_email="spam@example.com", subject="hello")},
    )

    assert response.status_code == 200, response.text
    assert _counts(db_path) == before
    payload = response.json()
    assert payload["would_skip_ai"] is True
    assert payload["enqueue_ai"] is False
    assert payload["stopped"] is True
    assert [a["action_type"] for a in payload["planned_actions"]] == [
        "skip_ai_inference",
        "stop_processing",
    ]
    assert all(a["would_execute"] is False for a in payload["planned_actions"])


def test_mutation_rule_is_preview_only_and_does_not_execute(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    called = {"execute": False, "mutation": False}

    def fail_execute(*args, **kwargs):
        called["execute"] = True
        raise AssertionError("execute action must not run")

    def fail_mutation(*args, **kwargs):
        called["mutation"] = True
        raise AssertionError("mutation action must not run")

    monkeypatch.setattr(rules_module, "_execute_action", fail_execute)
    monkeypatch.setattr(rules_module, "_execute_mutation_action", fail_mutation)
    _create_rule(client, {
        "name": "Mark read sample",
        "priority": 10,
        "enabled": True,
        "match_type": "ALL",
        "conditions": [{"field": "subject", "operator": "contains", "value": "alert"}],
        "actions": [{"action_type": "mark_read"}],
    })
    before = _counts(db_path)

    response = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message()},
    )

    assert response.status_code == 200, response.text
    assert _counts(db_path) == before
    assert called == {"execute": False, "mutation": False}
    action = response.json()["planned_actions"][0]
    assert action["action_type"] == "mark_read"
    assert action["mutation"] is True
    assert action["would_execute"] is False
    assert action["dry_run"] is True
    assert action["gate_status"] in {"mode_blocked", "preview_only"}


def test_rule_id_filter_and_disabled_rule_options(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    first = _create_rule(client, {
        "name": "First",
        "priority": 10,
        "enabled": True,
        "match_type": "ALL",
        "conditions": [{"field": "subject", "operator": "contains", "value": "Suspicious"}],
        "actions": [{"action_type": "notify_dashboard"}],
    })
    second = _create_rule(client, {
        "name": "Second disabled",
        "priority": 20,
        "enabled": False,
        "match_type": "ALL",
        "conditions": [{"field": "subject", "operator": "contains", "value": "Suspicious"}],
        "actions": [{"action_type": "notify_dashboard"}],
    })
    before = _counts(db_path)

    only_first = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message(), "rule_id": first["rule_id"]},
    )
    default_rules = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message()},
    )
    with_disabled = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message(), "include_disabled": True},
    )
    only_disabled_excluded = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={"message": _message(), "rule_id": second["rule_id"]},
    )
    only_disabled_included = client.post(
        "/api/mail/rules/explain",
        headers=HEADERS,
        json={
            "message": _message(),
            "rule_id": second["rule_id"],
            "include_disabled": True,
        },
    )

    assert only_first.status_code == 200, only_first.text
    assert default_rules.status_code == 200, default_rules.text
    assert with_disabled.status_code == 200, with_disabled.text
    assert only_disabled_excluded.status_code == 200, only_disabled_excluded.text
    assert only_disabled_included.status_code == 200, only_disabled_included.text
    assert _counts(db_path) == before
    assert [r["rule_id"] for r in only_first.json()["rules"]] == [first["rule_id"]]
    assert [r["rule_id"] for r in default_rules.json()["rules"]] == [first["rule_id"]]
    assert [r["rule_id"] for r in with_disabled.json()["rules"]] == [
        first["rule_id"],
        second["rule_id"],
    ]
    assert only_disabled_excluded.json()["rules"] == []
    assert [r["rule_id"] for r in only_disabled_included.json()["rules"]] == [second["rule_id"]]

