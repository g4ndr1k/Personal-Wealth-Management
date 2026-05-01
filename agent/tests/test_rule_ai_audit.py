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
                "mail_rule_ai_draft_audit",
                "mail_rule_ai_golden_probe_runs",
            )
        }


def _audit_rows(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM mail_rule_ai_draft_audit ORDER BY id"
            )
        ]


def _fake_alert_draft(request_text, account_id=None, settings=None):
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
        warnings=["This is a draft only."],
        safety_status="safe_local_alert_draft",
        requires_user_confirmation=True,
        status="draft",
        saveable=True,
        provider="ollama",
        model="qwen2.5:7b-instruct-q4_K_M",
    )


def test_draft_endpoint_records_saveable_sender_audit_without_rule_rows(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _counts(db_path)

    response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Add abcd@efcf.com to the spam list", "mode": "sender_suppression"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft_audit_id"] == 1
    counts = _counts(db_path)
    assert counts["mail_rules"] == before["mail_rules"]
    assert counts["mail_rule_conditions"] == before["mail_rule_conditions"]
    assert counts["mail_rule_actions"] == before["mail_rule_actions"]
    assert counts["mail_rule_ai_draft_audit"] == before["mail_rule_ai_draft_audit"] + 1

    row = _audit_rows(db_path)[0]
    assert row["mode"] == "sender_suppression"
    assert row["status"] == "draft"
    assert row["saveable"] == 1
    assert row["safety_status"] == "safe_local_suppression"
    assert row["request_hash"]
    assert row["request_preview"] == "Add abcd@efcf.com to the spam list"
    assert row["normalized_intent"] == "Suppress alerts from abcd@efcf.com"
    assert row["rule_name"] == "Suppress sender abcd@efcf.com"
    assert row["condition_count"] == 1
    assert row["action_count"] == 2


def test_draft_endpoint_records_unsupported_audit(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Remember abcd@efcf.com for later", "mode": "sender_suppression"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unsupported"
    assert body["saveable"] is False

    row = _audit_rows(db_path)[0]
    assert row["status"] == "unsupported"
    assert row["saveable"] == 0
    assert row["safety_status"] == "unsupported_intent"
    assert row["rule_name"] is None
    assert row["condition_count"] == 0
    assert row["action_count"] == 0


def test_alert_draft_records_provider_model_and_actual_domain(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch, rule_ai_enabled=True)
    monkeypatch.setattr(api_mail, "draft_alert_rule_with_local_llm", _fake_alert_draft)

    response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={
            "request_text": "If BCA emails me about suspicious transaction, notify me.",
            "mode": "alert_rule",
        },
    )
    assert response.status_code == 200, response.text
    row = _audit_rows(db_path)[0]
    assert row["provider"] == "ollama"
    assert row["model"] == "qwen2.5:7b-instruct-q4_K_M"
    assert row["safety_status"] == "safe_local_alert_draft"
    assert row["actual_domain"] == "bca.co.id"
    assert row["condition_count"] == 3
    assert row["action_count"] == 1


def test_audit_privacy_truncates_preview_and_stores_no_raw_model_output(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    long_request = "Add abcd@efcf.com to the spam list " + ("please " * 80)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": long_request, "mode": "sender_suppression"},
    )
    assert response.status_code == 200, response.text
    row = _audit_rows(db_path)[0]
    assert row["request_hash"]
    assert len(row["request_preview"]) == 160
    assert row["request_preview"] in long_request
    assert long_request not in row.values()
    assert "raw_model_output" not in row
    assert row["raw_model_error"] is None


def test_golden_probe_endpoint_records_aggregate_run_without_rule_rows(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch, rule_ai_enabled=True)
    monkeypatch.setattr(api_mail, "draft_alert_rule_with_local_llm", _fake_alert_draft)
    before = _counts(db_path)

    response = client.post(
        "/api/mail/rules/ai/golden-probe",
        headers=HEADERS,
        json={"prompt_ids": ["bca_suspicious_transaction"], "fail_fast": False},
    )
    assert response.status_code == 200, response.text
    counts = _counts(db_path)
    assert counts["mail_rules"] == before["mail_rules"]
    assert counts["mail_rule_conditions"] == before["mail_rule_conditions"]
    assert counts["mail_rule_actions"] == before["mail_rule_actions"]
    assert counts["mail_rule_ai_golden_probe_runs"] == before["mail_rule_ai_golden_probe_runs"] + 1

    runs = client.get(
        "/api/mail/rules/ai/golden-probe/runs?limit=5",
        headers=HEADERS,
    )
    assert runs.status_code == 200, runs.text
    run = runs.json()["items"][0]
    assert run["status"] == "passed"
    assert run["total"] == 1
    assert run["passed"] == 1
    assert run["results"][0]["id"] == "bca_suspicious_transaction"
    assert run["results"][0]["expected_domain"] == "bca.co.id"


def test_audit_read_endpoints_are_read_only_and_summary_counts(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Add abcd@efcf.com to the spam list", "mode": "sender_suppression"},
    )
    client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Remember wxyz@efcf.com", "mode": "sender_suppression"},
    )
    before = _counts(db_path)

    recent = client.get("/api/mail/rules/ai/audit/recent?limit=5", headers=HEADERS)
    summary = client.get("/api/mail/rules/ai/audit/summary", headers=HEADERS)
    assert recent.status_code == 200, recent.text
    assert summary.status_code == 200, summary.text
    assert _counts(db_path) == before
    assert len(recent.json()["items"]) == 2
    payload = summary.json()
    assert payload["total_draft_attempts"] == 2
    assert payload["saveable_count"] == 1
    assert payload["unsupported_count"] == 1
    assert payload["saveable_rate"] == 0.5
    assert payload["by_mode"] == {"sender_suppression": 2}


def test_audit_write_failure_does_not_break_draft_endpoint(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)

    def fail_audit(self, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AgentState, "record_rule_ai_draft_audit", fail_audit)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Add abcd@efcf.com to the spam list", "mode": "sender_suppression"},
    )
    assert response.status_code == 200, response.text
    assert "draft_audit_id" not in response.json()
    assert _counts(db_path)["mail_rule_ai_draft_audit"] == 0


def test_source_draft_audit_id_cannot_bypass_rule_validation(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    draft_response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Add abcd@efcf.com to the spam list", "mode": "sender_suppression"},
    )
    audit_id = draft_response.json()["draft_audit_id"]
    response = client.post(
        "/api/mail/rules",
        headers=HEADERS,
        json={
            "name": "Invalid linked rule",
            "account_id": None,
            "priority": 10,
            "enabled": True,
            "match_type": "ALL",
            "source_draft_audit_id": audit_id,
            "conditions": [{"field": "from_email", "operator": "equals", "value": "abcd@efcf.com"}],
            "actions": [{"action_type": "send_imessage", "target": "imessage"}],
        },
    )
    assert response.status_code == 400
    assert _counts(db_path)["mail_rules"] == 0
    assert _audit_rows(db_path)[0]["saved_rule_id"] is None


def test_source_draft_audit_id_links_after_valid_human_save(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    draft_response = client.post(
        "/api/mail/rules/ai/draft",
        headers=HEADERS,
        json={"request_text": "Add abcd@efcf.com to the spam list", "mode": "sender_suppression"},
    )
    draft = draft_response.json()
    response = client.post(
        "/api/mail/rules",
        headers=HEADERS,
        json={
            **draft["rule"],
            "priority": 10,
            "enabled": True,
            "source_draft_audit_id": draft["draft_audit_id"],
        },
    )
    assert response.status_code == 200, response.text
    assert _audit_rows(db_path)[0]["saved_rule_id"] == response.json()["rule_id"]
