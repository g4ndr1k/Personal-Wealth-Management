import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app import api_mail
from agent.app.rule_ai_builder import (
    MAX_RULE_AI_REQUEST_CHARS,
    RuleActionDraft,
    RuleConditionDraft,
    RuleDraft,
    RuleDraftResult,
    draft_alert_rule_with_local_llm,
    draft_sender_suppression_rule,
    validate_alert_rule_draft,
    validate_sender_suppression_draft,
)
from agent.app.state import AgentState


def _client(tmp_path, monkeypatch, api_key="secret"):
    db_path = tmp_path / "agent.db"
    monkeypatch.setenv("AGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("FINANCE_API_KEY", api_key)
    AgentState(str(db_path))
    app = FastAPI()
    app.include_router(api_mail.router, prefix="/api/mail")
    return TestClient(app), db_path


def _draft(text, account_id=None):
    return draft_sender_suppression_rule(text, account_id=account_id).to_dict()


def _alert_settings(**overrides):
    cfg = {
        "enabled": True,
        "provider": "ollama",
        "base_url": "http://ollama.test",
        "model": "fake-local",
        "timeout_seconds": 1,
        "temperature": 0.0,
        "max_request_chars": 1000,
    }
    cfg.update(overrides)
    return cfg


def _fake_alert_payload(**overrides):
    payload = {
        "intent_summary": "Notify me for Permata credit card clarification emails",
        "confidence": 0.84,
        "rule": {
            "name": "Permata credit card clarification alert",
            "account_id": None,
            "match_type": "ALL",
            "conditions": [
                {"field": "from_domain", "operator": "contains", "value": "not-the-domain.example"},
                {"field": "subject", "operator": "contains", "value": "clarification"},
                {"field": "body", "operator": "contains", "value": "credit card"},
            ],
            "actions": [
                {
                    "action_type": "mark_pending_alert",
                    "target": "imessage",
                    "value_json": {
                        "template": "Permata credit card clarification email detected."
                    },
                    "stop_processing": False,
                }
            ],
        },
        "explanation": [
            "This rule matches messages from Permata Bank.",
            "It looks for clarification and credit card wording.",
        ],
        "warnings": ["This is a draft only."],
        "safety_status": "safe_local_alert_draft",
        "requires_user_confirmation": True,
    }
    payload.update(overrides)
    return payload


def _assert_suppression_draft(payload, email):
    assert payload["intent_summary"] == f"Suppress alerts from {email}"
    assert payload["confidence"] == 0.95
    assert payload["safety_status"] == "safe_local_suppression"
    assert payload["requires_user_confirmation"] is True
    assert payload["rule"]["name"] == f"Suppress sender {email}"
    assert payload["rule"]["match_type"] == "ALL"
    assert payload["rule"]["conditions"] == [
        {"field": "from_email", "operator": "equals", "value": email}
    ]
    assert payload["rule"]["actions"] == [
        {
            "action_type": "skip_ai_inference",
            "target": None,
            "value_json": None,
            "stop_processing": False,
        },
        {
            "action_type": "stop_processing",
            "target": None,
            "value_json": None,
            "stop_processing": True,
        },
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Add abcd@efcf.com to the spam list",
        "Block alerts from abcd@efcf.com",
        "Stop processing email from abcd@efcf.com",
        "suppress abcd@efcf.com",
        "mute abcd@efcf.com",
        "ignore abcd@efcf.com",
        'Block "John Doe" <john@example.com>',
        "Block John Doe john@example.com",
    ],
)
def test_drafts_sender_suppression_rules(text):
    expected = "john@example.com" if "john@" in text else "abcd@efcf.com"
    _assert_suppression_draft(_draft(text), expected)


def test_lowercases_email_and_carries_account_id():
    payload = _draft("Mute Alerts@Example.COM", account_id="acct_1")
    _assert_suppression_draft(payload, "alerts@example.com")
    assert payload["rule"]["account_id"] == "acct_1"


def test_rejects_empty_request():
    with pytest.raises(ValueError, match="request_text is required"):
        draft_sender_suppression_rule("   ")


def test_rejects_non_string_request():
    with pytest.raises(ValueError, match="request_text must be a string"):
        draft_sender_suppression_rule(123)  # type: ignore[arg-type]


def test_rejects_very_long_request():
    with pytest.raises(ValueError, match="characters or fewer"):
        draft_sender_suppression_rule(
            f"Block alerts from abcd@efcf.com {'x' * MAX_RULE_AI_REQUEST_CHARS}"
        )


def test_rejects_no_email():
    with pytest.raises(ValueError, match="Exactly one sender email"):
        draft_sender_suppression_rule("Block alerts from this sender")


@pytest.mark.parametrize(
    "text",
    [
        "Block john@example..com",
        "Block john@-example.com",
        "Block john@example.com\nBcc: boss@example.com",
    ],
)
def test_rejects_malformed_or_header_injection_email(text):
    with pytest.raises(ValueError):
        draft_sender_suppression_rule(text)


def test_rejects_multiple_emails():
    with pytest.raises(ValueError, match="Only one sender email"):
        draft_sender_suppression_rule("Block a@example.com and b@example.com")


def test_rejects_repeated_email_mentions():
    with pytest.raises(ValueError, match="Only one sender email"):
        draft_sender_suppression_rule("Block a@example.com and a@example.com")


@pytest.mark.parametrize(
    "text",
    [
        "Block a@example.com, b@example.com",
        "Block a@example.com; b@example.com",
    ],
)
def test_rejects_comma_or_semicolon_separated_emails(text):
    with pytest.raises(ValueError, match="Only one sender email"):
        draft_sender_suppression_rule(text)


@pytest.mark.parametrize(
    "text,status",
    [
        ("Delete email from abcd@efcf.com", "unsupported_live_mailbox_action"),
        ("Move abcd@efcf.com to spam", "unsupported_live_mailbox_action"),
        ("archive emails from abcd@efcf.com", "unsupported_live_mailbox_action"),
        ("mark emails from abcd@efcf.com read", "unsupported_live_mailbox_action"),
        ("mark emails from abcd@efcf.com unread", "unsupported_live_mailbox_action"),
        ("label emails from abcd@efcf.com as receipts", "unsupported_live_mailbox_action"),
        ("forward emails from abcd@efcf.com to me@example.com", "unsupported_live_mailbox_action"),
        ("reply to abcd@efcf.com", "unsupported_live_mailbox_action"),
        ("unsubscribe from abcd@efcf.com", "unsupported_live_mailbox_action"),
    ],
)
def test_blocks_explicit_live_mailbox_requests(text, status):
    payload = _draft(text)
    assert payload["rule"] is None
    assert payload["safety_status"] == status
    assert any("live Gmail or mailbox action" in warning for warning in payload["warnings"])


@pytest.mark.parametrize(
    "text",
    [
        "do something with abcd@efcf.com",
        "handle abcd@efcf.com",
        "make a rule for abcd@efcf.com",
    ],
)
def test_ambiguous_requests_are_unsupported(text):
    payload = _draft(text)
    assert payload["rule"] is None
    assert payload["safety_status"] == "unsupported_intent"


def test_spam_list_warning_explains_local_suppression_not_gmail_spam():
    payload = _draft("Add abcd@efcf.com to the spam list")
    assert any("not Gmail Spam" in warning for warning in payload["warnings"])
    assert any("will not move existing or future emails" in warning for warning in payload["warnings"])


def test_blocked_words_inside_email_address_do_not_block_safe_request():
    payload = _draft("Block alerts from reply@example.com")
    _assert_suppression_draft(payload, "reply@example.com")


def test_alert_mode_calls_fake_llm_and_returns_safe_alert_draft():
    payload = draft_alert_rule_with_local_llm(
        "If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification",
        settings=_alert_settings(),
        client=lambda request: {"message": {"content": __import__("json").dumps(_fake_alert_payload())}},
    ).to_dict()

    assert payload["status"] == "draft"
    assert payload["saveable"] is True
    assert payload["safety_status"] == "safe_local_alert_draft"
    assert payload["requires_user_confirmation"] is True
    assert payload["provider"] == "ollama"
    assert payload["model"] == "fake-local"
    assert payload["rule"]["match_type"] == "ALL"
    assert {"field": "from_domain", "operator": "contains", "value": "permatabank.co.id"} in payload["rule"]["conditions"]
    assert any(c["field"] in {"subject", "body"} and c["operator"] == "contains" for c in payload["rule"]["conditions"])
    assert payload["rule"]["actions"] == [
        {
            "action_type": "mark_pending_alert",
            "target": "imessage",
            "value_json": {"template": "Permata credit card clarification email detected."},
            "stop_processing": False,
        }
    ]


@pytest.mark.parametrize("action_type", ["delete", "move_to_folder", "send_imessage"])
def test_alert_validation_blocks_dangerous_actions(action_type):
    payload = _fake_alert_payload(rule={
        **_fake_alert_payload()["rule"],
        "actions": [{"action_type": action_type, "target": "INBOX", "stop_processing": False}],
    })
    result = draft_alert_rule_with_local_llm(
        "Permata Bank clarification credit card notification",
        settings=_alert_settings(),
        client=lambda request: payload,
    ).to_dict()
    assert result["rule"] is None
    assert result["saveable"] is False
    assert result["safety_status"] == "llm_draft_failed"


def test_alert_validation_blocks_missing_sender_domain():
    payload = _fake_alert_payload(rule={
        **_fake_alert_payload()["rule"],
        "conditions": [{"field": "subject", "operator": "contains", "value": "clarification"}],
    })
    result = draft_alert_rule_with_local_llm(
        "Notify for credit card clarification",
        settings=_alert_settings(),
        client=lambda request: payload,
    ).to_dict()
    assert result["rule"] is None
    assert any("from_domain or from_email" in warning for warning in result["warnings"])


def test_alert_validation_blocks_missing_content_condition():
    payload = _fake_alert_payload(rule={
        **_fake_alert_payload()["rule"],
        "conditions": [{"field": "from_domain", "operator": "contains", "value": "permatabank.co.id"}],
    })
    result = draft_alert_rule_with_local_llm(
        "Notify for Permata Bank",
        settings=_alert_settings(),
        client=lambda request: payload,
    ).to_dict()
    assert result["rule"] is None
    assert any("subject or body" in warning for warning in result["warnings"])


def test_alert_non_json_output_is_blocked_and_sanitized():
    result = draft_alert_rule_with_local_llm(
        "Permata Bank clarification credit card notification",
        settings=_alert_settings(),
        client=lambda request: "x" * 5000,
    ).to_dict()
    assert result["rule"] is None
    assert result["safety_status"] == "llm_draft_failed"
    assert len(result["raw_model_error"]) <= 240


def test_alert_timeout_or_unreachable_model_is_handled():
    def boom(_request):
        raise TimeoutError("ollama timed out")

    result = draft_alert_rule_with_local_llm(
        "Permata Bank clarification credit card notification",
        settings=_alert_settings(),
        client=boom,
    ).to_dict()
    assert result["rule"] is None
    assert result["safety_status"] == "llm_draft_failed"
    assert "timed out" in result["raw_model_error"]


def test_alert_llm_disabled_returns_unsupported():
    result = draft_alert_rule_with_local_llm(
        "Permata Bank clarification credit card notification",
        settings=_alert_settings(enabled=False),
        client=lambda request: _fake_alert_payload(),
    ).to_dict()
    assert result["rule"] is None
    assert result["safety_status"] == "local_llm_disabled"


@pytest.mark.parametrize(
    "action_type",
    [
        "delete",
        "move_to_folder",
        "add_label",
        "mark_read",
        "mark_unread",
        "move_to_spam",
        "notify_dashboard",
        "mark_pending_alert",
        "send_imessage",
        "forward",
        "auto_reply",
        "external_webhook",
    ],
)
def test_validation_blocks_any_non_suppression_action(action_type):
    result = RuleDraftResult(
        intent_summary="Unsafe",
        confidence=0.9,
        rule=RuleDraft(
            name="Unsafe",
            account_id=None,
            match_type="ALL",
            conditions=[
                RuleConditionDraft(
                    field="from_email",
                    operator="equals",
                    value="abcd@efcf.com",
                )
            ],
            actions=[
                RuleActionDraft(action_type="skip_ai_inference"),
                RuleActionDraft(action_type=action_type, stop_processing=True),
            ],
        ),
        explanation=[],
        warnings=[],
        safety_status="safe_local_suppression",
        requires_user_confirmation=True,
    )
    payload = validate_sender_suppression_draft(result).to_dict()
    assert payload["rule"] is None
    assert payload["safety_status"] == "blocked_validation_failed"


def test_validation_blocks_targets_and_value_json_on_saveable_actions():
    result = RuleDraftResult(
        intent_summary="Unsafe",
        confidence=0.9,
        rule=RuleDraft(
            name="Unsafe",
            account_id=None,
            match_type="ALL",
            conditions=[
                RuleConditionDraft(
                    field="from_email",
                    operator="equals",
                    value="abcd@efcf.com",
                )
            ],
            actions=[
                RuleActionDraft(action_type="skip_ai_inference", target="INBOX"),
                RuleActionDraft(action_type="stop_processing", stop_processing=True),
            ],
        ),
        explanation=[],
        warnings=[],
        safety_status="safe_local_suppression",
        requires_user_confirmation=True,
    )
    assert validate_sender_suppression_draft(result).rule is None


def test_builder_does_not_write_to_rule_tables(tmp_path):
    db_path = tmp_path / "agent.db"
    AgentState(str(db_path))
    before = _rule_table_counts(db_path)
    _assert_suppression_draft(
        _draft("Block alerts from abcd@efcf.com"),
        "abcd@efcf.com",
    )
    assert _rule_table_counts(db_path) == before


def test_api_draft_endpoint_returns_draft_and_does_not_create_rule_rows(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _rule_table_counts(db_path)

    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={"request_text": "Block alerts from ABCD@EFCF.COM", "account_id": None},
    )

    assert response.status_code == 200, response.text
    _assert_suppression_draft(response.json(), "abcd@efcf.com")
    assert _rule_table_counts(db_path) == before


def test_api_sender_suppression_mode_remains_deterministic(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={
            "request_text": "Block alerts from ABCD@EFCF.COM",
            "mode": "sender_suppression",
        },
    )
    assert response.status_code == 200, response.text
    _assert_suppression_draft(response.json(), "abcd@efcf.com")


def test_api_alert_rule_mode_returns_disabled_without_rule_rows(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _rule_table_counts(db_path)
    monkeypatch.setattr(api_mail, "_get_settings", lambda: {"mail": {"rule_ai": {"enabled": False}}})

    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={
            "request_text": "If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification",
            "mode": "alert_rule",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["rule"] is None
    assert response.json()["safety_status"] == "local_llm_disabled"
    assert _rule_table_counts(db_path) == before


def test_api_alert_rule_mode_with_fake_enabled_llm_returns_safe_draft(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _rule_table_counts(db_path)
    monkeypatch.setattr(api_mail, "_get_settings", lambda: {"mail": {"rule_ai": _alert_settings()}})
    monkeypatch.setattr(
        api_mail,
        "draft_alert_rule_with_local_llm",
        lambda request_text, account_id=None, settings=None: draft_alert_rule_with_local_llm(
            request_text,
            account_id=account_id,
            settings=settings,
            client=lambda request: _fake_alert_payload(),
        ),
    )

    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={
            "request_text": "If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification",
            "mode": "alert_rule",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["safety_status"] == "safe_local_alert_draft"
    assert payload["saveable"] is True
    assert {"field": "from_domain", "operator": "contains", "value": "permatabank.co.id"} in payload["rule"]["conditions"]
    assert [action["action_type"] for action in payload["rule"]["actions"]] == ["mark_pending_alert"]
    assert _rule_table_counts(db_path) == before


def test_api_invalid_mode_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={"request_text": "Block alerts from abcd@efcf.com", "mode": "wat"},
    )
    assert response.status_code == 422


def test_api_draft_rule_can_be_saved_through_existing_rule_create_and_evaluated(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)

    draft_response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={"request_text": "Block alerts from ABCD@EFCF.COM"},
    )
    assert draft_response.status_code == 200, draft_response.text
    rule_payload = {
        **draft_response.json()["rule"],
        "priority": 10,
        "enabled": True,
    }

    create_response = client.post(
        "/api/mail/rules",
        headers={"X-Api-Key": "secret"},
        json=rule_payload,
    )
    assert create_response.status_code == 200, create_response.text
    assert _rule_table_counts(db_path) == {
        "mail_rules": 1,
        "mail_rule_conditions": 1,
        "mail_rule_actions": 2,
    }

    preview_response = client.post(
        "/api/mail/rules/preview",
        headers={"X-Api-Key": "secret"},
        json={
            "message": {
                "message_id": "m1",
                "imap_account": "acct",
                "sender_email": "abcd@efcf.com",
                "subject": "hello",
            }
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["would_skip_ai"] is True
    assert [a["action_type"] for a in preview["planned_actions"]] == [
        "skip_ai_inference",
        "stop_processing",
    ]


def test_api_draft_endpoint_requires_auth(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "wrong"},
        json={"request_text": "Block alerts from abcd@efcf.com"},
    )
    assert response.status_code == 401


def test_api_invalid_request_returns_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={"request_text": "No sender here"},
    )
    assert response.status_code == 200
    assert response.json()["rule"] is None
    assert response.json()["safety_status"] == "local_llm_disabled"


def test_api_missing_request_text_is_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={},
    )
    assert response.status_code == 422


def test_api_non_string_request_text_is_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={"request_text": 123},
    )
    assert response.status_code == 422


def test_api_blocked_request_returns_no_saveable_rule_and_no_rows(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    before = _rule_table_counts(db_path)
    response = client.post(
        "/api/mail/rules/ai/draft",
        headers={"X-Api-Key": "secret"},
        json={"request_text": "Move abcd@efcf.com to spam"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rule"] is None
    assert response.json()["safety_status"] == "unsupported_live_mailbox_action"
    assert _rule_table_counts(db_path) == before


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
