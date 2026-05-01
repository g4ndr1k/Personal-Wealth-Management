import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail
from agent.app.rule_ai_builder import RuleActionDraft, RuleConditionDraft, RuleDraft, RuleDraftResult
from agent.app.rule_ai_golden_probe import (
    DRAFT_PATH,
    SAVE_RULE_PATH,
    GoldenPrompt,
    load_golden_prompts,
    run_golden_probe,
    select_golden_prompts,
    validate_golden_response,
)
from agent.app.state import AgentState
from scripts import mail_rule_ai_golden_probe as cli_probe


FIXTURE = Path("agent/tests/fixtures/rule_ai_golden_prompts.json")


def _case(**overrides):
    item = {
        "id": "bca_suspicious_transaction",
        "prompt": "If BCA emails me about suspicious transaction, notify me.",
        "mode": "alert_rule",
        "expected_domain": "bca.co.id",
        "expected_safety_status": "safe_local_alert_draft",
        "expected_action_type": "mark_pending_alert",
        "expected_target": "imessage",
        "expected_keywords_any": ["suspicious", "mencurigakan", "transaction", "transaksi"],
    }
    item.update(overrides)
    return GoldenPrompt.from_dict(item, 0)


def _valid_response(**overrides):
    payload = {
        "intent_summary": "Notify me for BCA suspicious transaction emails",
        "confidence": 0.89,
        "status": "draft",
        "saveable": True,
        "safety_status": "safe_local_alert_draft",
        "requires_user_confirmation": True,
        "rule": {
            "name": "BCA suspicious transaction alert",
            "account_id": None,
            "match_type": "ALL",
            "conditions": [
                {"field": "from_domain", "operator": "contains", "value": "bca.co.id"},
                {"field": "subject", "operator": "contains", "value": "suspicious transaction"},
            ],
            "actions": [
                {
                    "action_type": "mark_pending_alert",
                    "target": "imessage",
                    "value_json": {"template": "BCA suspicious transaction email detected."},
                    "stop_processing": False,
                }
            ],
        },
        "explanation": ["This rule matches BCA suspicious transaction emails."],
        "warnings": [
            "This is a draft only.",
            "This does not mutate Gmail.",
        ],
    }
    payload.update(overrides)
    return payload


def test_fixture_loads_correctly():
    cases = load_golden_prompts(FIXTURE)
    assert len(cases) == 10
    assert {case.id for case in cases} == {
        "bca_suspicious_transaction",
        "cimb_credit_card_confirmation",
        "maybank_security_alert",
        "permata_kartu_kredit_confirmation",
        "klikbca_login_security",
        "mandiri_otp",
        "bni_failed_transaction",
        "bri_payment_due",
        "ocbc_suspicious_login",
        "jenius_account_security",
    }
    assert all(case.mode == "alert_rule" for case in cases)


def test_valid_response_passes():
    result = validate_golden_response(_case(), _valid_response(), 200)
    assert result.passed is True
    assert result.errors == []
    assert result.actual_domain == "bca.co.id"
    assert result.action_type == "mark_pending_alert"


def test_unsupported_response_fails():
    result = validate_golden_response(
        _case(),
        {
            "status": "unsupported",
            "saveable": False,
            "safety_status": "llm_draft_failed",
            "rule": None,
            "warnings": ["No rule was saved."],
        },
        200,
    )
    assert result.passed is False
    assert "status_not_draft:unsupported" in result.errors
    assert "saveable_not_true" in result.errors
    assert "missing_rule" in result.errors


def test_wrong_domain_fails():
    payload = _valid_response(rule={
        **_valid_response()["rule"],
        "conditions": [
            {"field": "from_domain", "operator": "contains", "value": "example.com"},
            {"field": "subject", "operator": "contains", "value": "suspicious transaction"},
        ],
    })
    result = validate_golden_response(_case(), payload, 200)
    assert result.passed is False
    assert "missing_expected_domain:bca.co.id" in result.errors


def test_missing_content_condition_fails():
    payload = _valid_response(rule={
        **_valid_response()["rule"],
        "conditions": [
            {"field": "from_domain", "operator": "contains", "value": "bca.co.id"},
        ],
    })
    result = validate_golden_response(_case(), payload, 200)
    assert result.passed is False
    assert "missing_content_condition" in result.errors


def test_wrong_action_fails():
    payload = _valid_response(rule={
        **_valid_response()["rule"],
        "actions": [
            {
                "action_type": "mark_pending_alert",
                "target": "dashboard",
                "value_json": {"template": "x"},
                "stop_processing": False,
            }
        ],
    })
    result = validate_golden_response(_case(), payload, 200)
    assert result.passed is False
    assert "target_mismatch:dashboard" in result.errors


def test_blocked_action_fails():
    payload = _valid_response(rule={
        **_valid_response()["rule"],
        "actions": [
            {
                "action_type": "send_imessage",
                "target": "imessage",
                "value_json": {"template": "x"},
                "stop_processing": False,
            }
        ],
    })
    result = validate_golden_response(_case(), payload, 200)
    assert result.passed is False
    assert "blocked_action:send_imessage" in result.errors


def test_non_200_response_fails():
    result = validate_golden_response(_case(), _valid_response(), 503)
    assert result.passed is False
    assert "http_status_not_200:503" in result.errors


def test_json_report_mode_returns_structured_results():
    summary = run_golden_probe(
        [_case(), _case(id="bad")],
        draft_fn=lambda prompt: _valid_response(status="unsupported") if prompt.id == "bad" else _valid_response(),
    )
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1
    assert summary["results"][0]["id"] == "bca_suspicious_transaction"


def test_run_probe_calls_only_draft_endpoint_and_never_save_rule_endpoint(monkeypatch):
    called_urls = []

    def fake_post(url, payload, api_key, timeout):
        called_urls.append(url)
        assert url.endswith(DRAFT_PATH)
        assert not url.endswith(SAVE_RULE_PATH)
        assert payload == {
            "request_text": "If BCA emails me about suspicious transaction, notify me.",
            "mode": "alert_rule",
        }
        assert api_key == "secret"
        assert timeout == 12
        return 200, _valid_response()

    monkeypatch.setattr(cli_probe, "_post_json", fake_post)
    summary = cli_probe.run_http_probe(
        [_case()],
        api_base="http://127.0.0.1:8090",
        api_key="secret",
        timeout=12,
    )

    assert summary["summary"]["passed"] == 1
    assert called_urls == ["http://127.0.0.1:8090/api/mail/rules/ai/draft"]


def test_main_json_uses_mocked_probe_without_network(tmp_path, monkeypatch, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps([_case().to_dict()]))
    monkeypatch.setenv("FINANCE_API_KEY", "secret")

    def fake_run_probe(cases, api_base, api_key, timeout, fail_fast=False):
        assert cases[0].id == "bca_suspicious_transaction"
        assert api_base == "http://api.test"
        assert api_key == "secret"
        assert timeout == 5
        assert fail_fast is True
        return run_golden_probe([_case()], draft_fn=lambda prompt: _valid_response())

    monkeypatch.setattr(cli_probe, "run_http_probe", fake_run_probe)
    code = cli_probe.main([
        "--api-base",
        "http://api.test",
        "--fixture",
        str(fixture),
        "--timeout",
        "5",
        "--fail-fast",
        "--json",
    ])

    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["passed"] == 1
    assert output["summary"]["failed"] == 0


def test_main_requires_api_key(monkeypatch, capsys):
    monkeypatch.delenv("FINANCE_API_KEY", raising=False)
    assert cli_probe.main(["--fixture", str(FIXTURE)]) == 2
    assert "FINANCE_API_KEY is required" in capsys.readouterr().err


def test_filter_cases_rejects_unknown_prompt_id():
    with pytest.raises(ValueError, match="Unknown golden prompt id"):
        select_golden_prompts([_case()], ["missing"])


def test_successful_summary():
    summary = run_golden_probe([_case()], draft_fn=lambda prompt: _valid_response())
    assert summary["status"] == "passed"
    assert summary["summary"] == {"total": 1, "passed": 1, "failed": 0, "skipped": 0}


def test_one_failing_prompt_produces_failed_summary():
    summary = run_golden_probe(
        [_case(), _case(id="bad")],
        draft_fn=lambda prompt: _valid_response(status="unsupported") if prompt.id == "bad" else _valid_response(),
    )
    assert summary["status"] == "failed"
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1


def test_prompt_ids_filters_the_run():
    prompts = [_case(), _case(id="maybank_security_alert", expected_domain="maybank.co.id")]
    selected = select_golden_prompts(prompts, ["maybank_security_alert"])
    assert [prompt.id for prompt in selected] == ["maybank_security_alert"]


def test_fail_fast_stops_after_first_failure():
    calls = []

    def draft_fn(prompt):
        calls.append(prompt.id)
        return _valid_response(status="unsupported")

    summary = run_golden_probe([_case(), _case(id="second")], draft_fn=draft_fn, fail_fast=True)
    assert calls == ["bca_suspicious_transaction"]
    assert summary["summary"] == {"total": 2, "passed": 0, "failed": 1, "skipped": 1}


def _client(tmp_path, monkeypatch, api_key="secret"):
    db_path = tmp_path / "agent.db"
    monkeypatch.setenv("AGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("FINANCE_API_KEY", api_key)
    AgentState(str(db_path))
    app = FastAPI()
    app.include_router(api_mail.router, prefix="/api/mail")
    return TestClient(app), db_path


def _rule_table_counts(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "mail_rules",
                "mail_rule_conditions",
                "mail_rule_actions",
            )
        }


def _safe_draft_result():
    return RuleDraftResult(
        intent_summary="Notify me for BCA suspicious transaction emails",
        confidence=0.9,
        rule=RuleDraft(
            name="BCA suspicious transaction alert",
            account_id=None,
            match_type="ALL",
            conditions=[
                RuleConditionDraft("from_domain", "contains", "bca.co.id"),
                RuleConditionDraft("subject", "contains", "suspicious transaction"),
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
        explanation=["This rule matches BCA suspicious transaction emails."],
        warnings=["This is a draft only.", "This does not mutate Gmail."],
        safety_status="safe_local_alert_draft",
        requires_user_confirmation=True,
        status="draft",
        saveable=True,
        provider="ollama",
        model="fake-local",
    )


def test_api_endpoint_returns_disabled_when_rule_ai_disabled(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _rule_table_counts(db_path)
    monkeypatch.setattr(api_mail, "_get_settings", lambda: {"mail": {"rule_ai": {"enabled": False}}})

    response = client.post(
        "/api/mail/rules/ai/golden-probe",
        headers={"X-Api-Key": "secret"},
        json={},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["summary"] == {"total": 10, "passed": 0, "failed": 0, "skipped": 10}
    assert "Local Rule AI is disabled" in payload["warnings"][0]
    assert payload["safety"]["saved_rules"] is False
    assert _rule_table_counts(db_path) == before


def test_api_endpoint_returns_results_using_fake_builder_when_enabled(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _rule_table_counts(db_path)
    calls = []
    monkeypatch.setattr(api_mail, "_get_settings", lambda: {"mail": {"rule_ai": {
        "enabled": True,
        "provider": "ollama",
        "model": "fake-local",
        "timeout_seconds": 1,
    }}})

    def fake_builder(request_text, account_id=None, settings=None):
        calls.append((request_text, account_id, settings["timeout_seconds"]))
        return _safe_draft_result()

    monkeypatch.setattr(api_mail, "draft_alert_rule_with_local_llm", fake_builder)
    response = client.post(
        "/api/mail/rules/ai/golden-probe",
        headers={"X-Api-Key": "secret"},
        json={"prompt_ids": ["bca_suspicious_transaction"], "timeout_seconds": 7},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["summary"] == {"total": 1, "passed": 1, "failed": 0, "skipped": 0}
    assert payload["rule_ai"]["model"] == "fake-local"
    assert payload["results"][0]["actual_domain"] == "bca.co.id"
    assert payload["results"][0]["action_type"] == "mark_pending_alert"
    assert payload["safety"] == {
        "saved_rules": False,
        "sent_imessage": False,
        "mutated_gmail": False,
        "mutated_imap": False,
    }
    assert calls == [("If BCA emails me about suspicious transaction, notify me.", None, 7)]
    assert _rule_table_counts(db_path) == before


def test_api_endpoint_unknown_prompt_id_returns_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(api_mail, "_get_settings", lambda: {"mail": {"rule_ai": {"enabled": True}}})
    response = client.post(
        "/api/mail/rules/ai/golden-probe",
        headers={"X-Api-Key": "secret"},
        json={"prompt_ids": ["missing"]},
    )
    assert response.status_code == 400
    assert "Unknown golden prompt id" in response.json()["detail"]
