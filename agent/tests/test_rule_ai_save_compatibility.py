import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail
from agent.app.rule_ai_builder import (
    RuleActionDraft,
    RuleConditionDraft,
    RuleDraft,
    RuleDraftResult,
)
from agent.app.rules import ACTIVE_ACTIONS, ALLOWED_OPERATORS, MUTATION_ACTIONS
from agent.app.state import AgentState


HEADERS = {"X-Api-Key": "secret"}


def _client(tmp_path, monkeypatch, *, rule_ai_enabled: bool = False):
    db_path = tmp_path / "agent.db"
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        f"""
[agent]
mode = "draft_only"
safe_default = "draft_only"

[mail]
source = "gmail"

[mail.rule_ai]
enabled = {str(rule_ai_enabled).lower()}
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen2.5:7b-instruct-q4_K_M"
temperature = 0.0
timeout_seconds = 1
low_confidence_threshold = 0.35

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
            )
        }


def _rows(db_path, table):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def _fake_bca_alert_draft(request_text, account_id=None, settings=None):
    return RuleDraftResult(
        intent_summary="Notify me for BCA suspicious transaction emails",
        confidence=0.91,
        rule=RuleDraft(
            name="BCA suspicious transaction alert",
            account_id=account_id,
            match_type="ALL",
            conditions=[
                RuleConditionDraft("from_domain", "contains", "bca.co.id"),
                RuleConditionDraft("subject", "contains", "suspicious"),
                RuleConditionDraft("body", "contains", "transaction"),
            ],
            actions=[
                RuleActionDraft(
                    "mark_pending_alert",
                    target="imessage",
                    value_json={"template": "BCA suspicious transaction email detected."},
                    stop_processing=False,
                )
            ],
        ),
        explanation=["This is a local alert draft."],
        warnings=[
            "This is a draft only.",
            "This does not send an iMessage now.",
            "This does not mutate Gmail.",
        ],
        safety_status="safe_local_alert_draft",
        requires_user_confirmation=True,
        status="draft",
        saveable=True,
        provider="ollama",
        model="qwen2.5:7b-instruct-q4_K_M",
    )


def _save_rule(client, draft_rule, priority=10):
    response = client.post(
        "/api/mail/rules",
        headers=HEADERS,
        json={**draft_rule, "priority": priority, "enabled": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_sender_suppression_draft_saves_and_previews_deterministically(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)

    before = _counts(db_path)
    draft_response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={
            "request_text": "Add abcd@efcf.com to the spam list",
            "mode": "sender_suppression",
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    assert _counts(db_path) == before
    draft = draft_response.json()
    assert draft["status"] == "draft"
    assert draft["saveable"] is True
    assert draft["rule"]["conditions"] == [
        {"field": "from_email", "operator": "equals", "value": "abcd@efcf.com"}
    ]
    assert [action["action_type"] for action in draft["rule"]["actions"]] == [
        "skip_ai_inference",
        "stop_processing",
    ]

    saved = _save_rule(client, draft["rule"])
    assert _counts(db_path) == {
        "mail_rules": 1,
        "mail_rule_conditions": 1,
        "mail_rule_actions": 2,
    }
    assert _rows(db_path, "mail_rule_conditions")[0] | {"id": 0, "rule_id": 0} == {
        "id": 0,
        "rule_id": 0,
        "field": "from_email",
        "operator": "equals",
        "value": "abcd@efcf.com",
        "value_json": None,
        "case_sensitive": 0,
    }
    action_types = [row["action_type"] for row in _rows(db_path, "mail_rule_actions")]
    assert action_types == ["skip_ai_inference", "stop_processing"]
    assert not (set(action_types) & MUTATION_ACTIONS)

    preview_response = client.post(
        "/api/mail/rules/preview",
        headers=HEADERS,
        json={
            "message": {
                "message_id": "m-suppress-1",
                "imap_account": saved["account_id"],
                "sender_email": "abcd@efcf.com",
                "subject": "Anything",
                "body_text": "Body",
            }
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["matched_conditions"][0]["matched"] is True
    assert preview["would_skip_ai"] is True
    assert [action["action_type"] for action in preview["planned_actions"]] == [
        "skip_ai_inference",
        "stop_processing",
    ]
    assert all(not action.get("mutation") for action in preview["planned_actions"])


def test_alert_rule_draft_saves_and_from_domain_previews_deterministically(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch, rule_ai_enabled=True)
    monkeypatch.setattr(api_mail, "draft_alert_rule_with_local_llm", _fake_bca_alert_draft)

    draft_response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={
            "request_text": "If BCA emails me about suspicious transaction, notify me.",
            "mode": "alert_rule",
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    assert draft["status"] == "draft"
    assert draft["saveable"] is True
    assert draft["safety_status"] == "safe_local_alert_draft"
    assert {"field": "from_domain", "operator": "contains", "value": "bca.co.id"} in draft["rule"]["conditions"]
    assert any(c["field"] in {"subject", "body"} for c in draft["rule"]["conditions"])
    assert draft["rule"]["actions"] == [
        {
            "action_type": "mark_pending_alert",
            "target": "imessage",
            "value_json": {"template": "BCA suspicious transaction email detected."},
            "stop_processing": False,
        }
    ]

    _save_rule(client, draft["rule"])
    assert _counts(db_path) == {
        "mail_rules": 1,
        "mail_rule_conditions": 3,
        "mail_rule_actions": 1,
    }
    assert _rows(db_path, "mail_rule_actions")[0]["action_type"] == "mark_pending_alert"

    preview_response = client.post(
        "/api/mail/rules/preview",
        headers=HEADERS,
        json={
            "message": {
                "message_id": "m-alert-1",
                "sender_email": "alerts@bca.co.id",
                "subject": "Suspicious transaction warning",
                "body_text": "A suspicious transaction was detected.",
            }
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["matched_conditions"][0]["matched"] is True
    assert preview["planned_actions"] == [
        {
            "rule_id": 1,
            "action_type": "mark_pending_alert",
            "target": "imessage",
            "value": {"template": "BCA suspicious transaction email detected."},
        }
    ]
    assert preview["would_skip_ai"] is False
    assert all(not action.get("mutation") for action in preview["planned_actions"])


def test_draft_and_golden_probe_endpoints_do_not_write_rule_rows(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch, rule_ai_enabled=True)
    monkeypatch.setattr(api_mail, "draft_alert_rule_with_local_llm", _fake_bca_alert_draft)
    before = _counts(db_path)

    draft_response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Block abcd@efcf.com", "mode": "sender_suppression"},
    )
    assert draft_response.status_code == 200, draft_response.text
    assert _counts(db_path) == before

    probe_response = client.post(
        "/api/mail/rules/ai/golden-probe",
        headers=HEADERS,
        json={"prompt_ids": ["bca_suspicious_transaction"], "fail_fast": False},
    )
    assert probe_response.status_code == 200, probe_response.text
    assert _counts(db_path) == before

    _save_rule(client, draft_response.json()["rule"])
    assert _counts(db_path)["mail_rules"] == before["mail_rules"] + 1


def test_save_endpoint_rejects_blocked_actions_from_draft_like_payload(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules",
        headers=HEADERS,
        json={
            "name": "Malicious draft-like payload",
            "account_id": None,
            "priority": 10,
            "enabled": True,
            "match_type": "ALL",
            "conditions": [{"field": "from_email", "operator": "equals", "value": "abcd@efcf.com"}],
            "actions": [{"action_type": "send_imessage", "target": "imessage", "stop_processing": False}],
        },
    )
    assert response.status_code == 400
    assert "Unsupported mail rule action_type" in response.json()["detail"]


def test_ai_generated_fields_operators_and_actions_match_engine_vocabulary(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, rule_ai_enabled=True)
    monkeypatch.setattr(api_mail, "draft_alert_rule_with_local_llm", _fake_bca_alert_draft)

    suppression = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Mute abcd@efcf.com", "mode": "sender_suppression"},
    ).json()["rule"]
    alert = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "If BCA emails me about suspicious transaction, notify me.", "mode": "alert_rule"},
    ).json()["rule"]

    engine_fields = {"from_email", "from_domain", "sender_email", "sender_domain", "subject", "body"}
    for rule in (suppression, alert):
        assert rule["match_type"] == "ALL"
        for condition in rule["conditions"]:
            assert condition["field"] in engine_fields
            assert condition["operator"] in ALLOWED_OPERATORS
        for action in rule["actions"]:
            assert action["action_type"] in ACTIVE_ACTIONS
            assert action["action_type"] not in {
                "send_imessage",
                "delete",
                "archive",
                "forward",
                "reply",
                "unsubscribe",
                "webhook",
            }

    _save_rule(client, alert)
    preview_response = client.post(
        "/api/mail/rules/preview",
        headers=HEADERS,
        json={
            "message": {
                "message_id": "domain-derived",
                "sender_email": "security@bca.co.id",
                "subject": "Suspicious login and transaction",
                "body_text": "Transaction security alert",
            }
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    assert preview_response.json()["matched_conditions"][0]["matched"] is True
