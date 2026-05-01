from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable

import httpx


MAX_RULE_AI_REQUEST_CHARS = 1000
MAX_RAW_MODEL_ERROR_CHARS = 240

EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![A-Z0-9._%+-])",
    re.IGNORECASE,
)

SUPPORTED_INTENT_RE = re.compile(
    r"\b(spam\s+list|block(?:\s+alerts)?|suppress|stop\s+processing|ignore|mute)\b",
    re.IGNORECASE,
)

BLOCKED_ACTION_RE = re.compile(
    r"\b(delete|archive|forward|reply|unsubscribe|label)\b"
    r"|\bmark\s+(?:as\s+)?(?:read|unread)\b"
    r"|\bmark\b.{0,80}\b(?:read|unread)\b"
    r"|\bmove\b.{0,80}\bspam\b",
    re.IGNORECASE,
)

BLOCKED_ACTION_TYPES = {
    "move_to_folder",
    "add_label",
    "mark_read",
    "mark_unread",
    "move_to_spam",
    "notify_dashboard",
    "mark_pending_alert",
    "send_imessage",
    "auto_reply",
    "external_webhook",
    "delete",
    "archive",
    "forward",
    "reply",
    "unsubscribe",
}

ALERT_ALLOWED_FIELDS = {"from_domain", "from_email", "subject", "body"}
ALERT_ALLOWED_OPERATORS = {"contains", "equals"}
ALERT_ALLOWED_ACTIONS = {"mark_pending_alert", "notify_dashboard"}
ALERT_BLOCKED_ACTIONS = {
    "delete",
    "move_to_folder",
    "add_label",
    "mark_read",
    "mark_unread",
    "move_to_spam",
    "send_imessage",
    "forward",
    "auto_reply",
    "unsubscribe",
    "external_webhook",
    "route_to_pdf_pipeline",
    "skip_ai_inference",
    "stop_processing",
    "archive",
    "reply",
}

BANK_DOMAIN_HINTS = {
    "permata": "permatabank.co.id",
    "permata bank": "permatabank.co.id",
    "bca": "bca.co.id",
    "klikbca": "klikbca.com",
    "cimb": "cimbniaga.co.id",
    "cimb niaga": "cimbniaga.co.id",
    "maybank": "maybank.co.id",
}

DEFAULT_RULE_AI_SETTINGS = {
    "enabled": False,
    "provider": "ollama",
    "base_url": "http://host.docker.internal:11434",
    "model": "gemma3:4b",
    "timeout_seconds": 30,
    "temperature": 0.0,
    "max_request_chars": MAX_RULE_AI_REQUEST_CHARS,
    "low_confidence_threshold": 0.35,
}


@dataclass(frozen=True)
class RuleConditionDraft:
    field: str
    operator: str
    value: str


@dataclass(frozen=True)
class RuleActionDraft:
    action_type: str
    target: Any = None
    value_json: Any = None
    stop_processing: bool = False


@dataclass(frozen=True)
class RuleDraft:
    name: str
    account_id: str | None
    match_type: str
    conditions: list[RuleConditionDraft]
    actions: list[RuleActionDraft]


@dataclass(frozen=True)
class RuleDraftResult:
    intent_summary: str
    confidence: float
    rule: RuleDraft | None
    explanation: list[str]
    warnings: list[str]
    safety_status: str
    requires_user_confirmation: bool
    status: str = "draft"
    saveable: bool = False
    provider: str | None = None
    model: str | None = None
    raw_model_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def draft_sender_suppression_rule(
    request_text: str,
    account_id: str | None = None,
) -> RuleDraftResult:
    if not isinstance(request_text, str):
        raise ValueError("request_text must be a string")
    text = request_text.strip()
    if not text:
        raise ValueError("request_text is required")
    if len(text) > MAX_RULE_AI_REQUEST_CHARS:
        raise ValueError(
            f"request_text must be {MAX_RULE_AI_REQUEST_CHARS} characters or fewer"
        )
    if "\r" in text or "\n" in text:
        raise ValueError("request_text must be a single line")
    if _looks_like_multi_recipient_text(text):
        raise ValueError("Only one sender email address is supported")

    candidates = [match.group(1).lower() for match in EMAIL_RE.finditer(text)]
    malformed = [email for email in candidates if not _is_valid_email(email)]
    if malformed or ("@" in text and not candidates):
        raise ValueError("Exactly one valid sender email address is required")
    emails = candidates
    text_without_emails = EMAIL_RE.sub("", text)

    if BLOCKED_ACTION_RE.search(text_without_emails):
        return _blocked_result(
            emails[0] if emails else None,
            "unsupported_live_mailbox_action",
            [
                "This request appears to ask for a live Gmail or mailbox action.",
                "Phase 4F.1a only drafts local Mail Agent suppression rules.",
            ],
        )

    unique_emails = list(dict.fromkeys(emails))
    if not unique_emails:
        raise ValueError("Exactly one sender email address is required")
    if len(emails) > 1:
        raise ValueError("Only one sender email address is supported")

    email = unique_emails[0]
    lower_text = text.lower()

    if not SUPPORTED_INTENT_RE.search(text):
        return _blocked_result(
            email,
            "unsupported_intent",
            [
                "This request is outside the Phase 4F.1a sender suppression scope.",
                "Supported requests block, suppress, ignore, mute, or stop processing a single sender.",
            ],
        )

    result = RuleDraftResult(
        intent_summary=f"Suppress alerts from {email}",
        confidence=0.95,
        rule=RuleDraft(
            name=f"Suppress sender {email}",
            account_id=account_id,
            match_type="ALL",
            conditions=[
                RuleConditionDraft(
                    field="from_email",
                    operator="equals",
                    value=email,
                )
            ],
            actions=[
                RuleActionDraft(
                    action_type="skip_ai_inference",
                    stop_processing=False,
                ),
                RuleActionDraft(
                    action_type="stop_processing",
                    stop_processing=True,
                ),
            ],
        ),
        explanation=[
            f"This rule matches messages from {email}.",
            "It suppresses further Mail Agent processing for matching messages.",
        ],
        warnings=[
            f"This will suppress alerts for {email} inside Mail Agent.",
            "It will not move existing or future emails to Gmail Spam.",
        ],
        safety_status="safe_local_suppression",
        requires_user_confirmation=True,
        status="draft",
        saveable=True,
    )
    if "spam list" in lower_text:
        result.warnings.append(
            '"Spam list" currently means local Mail Agent suppression, not Gmail Spam.'
        )
    return validate_sender_suppression_draft(result)


def validate_sender_suppression_draft(result: RuleDraftResult) -> RuleDraftResult:
    rule = result.rule
    if rule is None:
        return result
    if result.safety_status != "safe_local_suppression":
        return _blocked_validation_result("Draft safety status is not local suppression.")
    if result.requires_user_confirmation is not True:
        return _blocked_validation_result("Draft does not require user confirmation.")
    if rule.match_type != "ALL":
        return _blocked_validation_result("Draft match_type must be ALL.")
    if len(rule.conditions) != 1:
        return _blocked_validation_result("Draft must contain exactly one condition.")

    condition = rule.conditions[0]
    if condition.field != "from_email" or condition.operator != "equals":
        return _blocked_validation_result("Draft condition must be from_email equals.")
    if condition.value != condition.value.lower() or not _is_valid_email(condition.value):
        return _blocked_validation_result("Draft condition value must be one normalized email.")

    action_types = [action.action_type for action in rule.actions]
    if action_types != ["skip_ai_inference", "stop_processing"]:
        return _blocked_validation_result(
            "Draft actions must be skip_ai_inference and stop_processing."
        )
    if any(action_type in BLOCKED_ACTION_TYPES for action_type in action_types):
        return _blocked_validation_result("Draft contains a blocked action type.")
    if rule.actions[0].stop_processing is not False:
        return _blocked_validation_result("skip_ai_inference must not stop processing.")
    if rule.actions[1].stop_processing is not True:
        return _blocked_validation_result("stop_processing must stop processing.")
    for action in rule.actions:
        if action.target not in (None, ""):
            return _blocked_validation_result("Draft actions must not include targets.")
        if action.value_json not in (None, "", {}):
            return _blocked_validation_result("Draft actions must not include value_json.")
    return result


def _looks_like_multi_recipient_text(text: str) -> bool:
    for separator in (",", ";"):
        if separator not in text:
            continue
        parts = [part.strip() for part in text.split(separator) if part.strip()]
        emailish_parts = [
            part for part in parts
            if "@" in part or EMAIL_RE.search(part)
        ]
        if len(emailish_parts) > 1:
            return True
    return False


def _is_valid_email(email: str) -> bool:
    if not EMAIL_RE.fullmatch(email):
        return False
    local, domain = email.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not re.fullmatch(r"[a-z0-9-]+", label, flags=re.IGNORECASE):
            return False
    return True


def _blocked_result(
    email: str | None,
    safety_status: str,
    warnings: list[str],
) -> RuleDraftResult:
    subject = email or "the requested sender"
    return RuleDraftResult(
        intent_summary=f"Cannot draft a safe local suppression rule for {subject}",
        confidence=0.0,
        rule=None,
        explanation=[
            "No saveable rule was created.",
            "The request must be rewritten as local Mail Agent sender suppression.",
        ],
        warnings=warnings + [
            "No Gmail, IMAP, label, read/unread, archive, delete, reply, forward, or unsubscribe action was drafted.",
        ],
        safety_status=safety_status,
        requires_user_confirmation=True,
        status="unsupported",
        saveable=False,
    )


def _blocked_validation_result(reason: str) -> RuleDraftResult:
    return RuleDraftResult(
        intent_summary="Blocked unsafe AI rule draft",
        confidence=0.0,
        rule=None,
        explanation=["No saveable rule was created."],
        warnings=[reason],
        safety_status="blocked_validation_failed",
        requires_user_confirmation=True,
        status="unsupported",
        saveable=False,
    )


def normalize_rule_ai_settings(settings: dict | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_RULE_AI_SETTINGS)
    cfg.update(settings or {})
    cfg["enabled"] = bool(cfg.get("enabled", False))
    cfg["provider"] = str(cfg.get("provider") or "ollama").strip()
    cfg["base_url"] = str(
        cfg.get("base_url") or DEFAULT_RULE_AI_SETTINGS["base_url"]
    ).rstrip("/")
    cfg["model"] = str(cfg.get("model") or DEFAULT_RULE_AI_SETTINGS["model"]).strip()
    cfg["timeout_seconds"] = int(cfg.get("timeout_seconds", 30))
    cfg["temperature"] = float(cfg.get("temperature", 0.0))
    cfg["max_request_chars"] = int(
        cfg.get("max_request_chars", MAX_RULE_AI_REQUEST_CHARS)
    )
    cfg["low_confidence_threshold"] = float(cfg.get("low_confidence_threshold", 0.35))
    return cfg


def draft_alert_rule_with_local_llm(
    request_text: str,
    account_id: str | None = None,
    settings: dict | None = None,
    client: Callable[[dict[str, Any]], Any] | httpx.Client | None = None,
) -> RuleDraftResult:
    if not isinstance(request_text, str):
        raise ValueError("request_text must be a string")
    text = request_text.strip()
    if not text:
        raise ValueError("request_text is required")
    cfg = normalize_rule_ai_settings(settings)
    if len(text) > min(cfg["max_request_chars"], MAX_RULE_AI_REQUEST_CHARS):
        raise ValueError(
            f"request_text must be {MAX_RULE_AI_REQUEST_CHARS} characters or fewer"
        )
    if "\r" in text or "\n" in text:
        raise ValueError("request_text must be a single line")
    if not cfg["enabled"]:
        return _unsupported_alert_result(
            "local_llm_disabled",
            [
                "Local alert-rule drafting is disabled.",
                "No rule was saved.",
            ],
            provider=cfg["provider"],
            model=cfg["model"],
        )
    if cfg["provider"] != "ollama":
        return _unsupported_alert_result(
            "llm_draft_failed",
            ["Only local provider='ollama' is supported for this probe.", "No rule was saved."],
            provider=cfg["provider"],
            model=cfg["model"],
        )

    try:
        model_payload = _call_rule_draft_ollama(text, cfg, client)
        parsed = _parse_model_payload(model_payload)
        draft = _coerce_alert_result(parsed, account_id, cfg)
        return validate_alert_rule_draft(
            draft,
            request_text=text,
            low_confidence_threshold=cfg["low_confidence_threshold"],
        )
    except Exception as exc:
        return _unsupported_alert_result(
            "llm_draft_failed",
            [
                "The local model did not produce a safe rule draft.",
                "No rule was saved.",
            ],
            provider=cfg["provider"],
            model=cfg["model"],
            raw_model_error=_sanitize_error(exc),
        )


def validate_alert_rule_draft(
    result: RuleDraftResult,
    request_text: str = "",
    low_confidence_threshold: float = 0.35,
) -> RuleDraftResult:
    rule = result.rule
    if rule is None:
        return result
    blocked = _first_blocked_alert_reason(result, request_text, low_confidence_threshold)
    if blocked:
        return _unsupported_alert_result(
            "llm_draft_failed",
            [
                "The local model did not produce a safe rule draft.",
                blocked,
                "No rule was saved.",
            ],
            provider=result.provider,
            model=result.model,
        )
    normalized_conditions = []
    hint_domain = _domain_hint_for_text(request_text)
    for condition in rule.conditions:
        value = str(condition.value or "").strip().lower()
        if condition.field == "from_domain" and hint_domain:
            value = hint_domain
        elif condition.field in {"subject", "body"}:
            value = str(condition.value or "").strip()
        normalized_conditions.append(
            RuleConditionDraft(
                field=condition.field,
                operator=condition.operator,
                value=value,
            )
        )
    required_keywords = _required_content_keywords_for_text(request_text)
    if required_keywords and not _has_required_content_keyword(
        normalized_conditions,
        required_keywords,
    ):
        normalized_conditions.append(
            RuleConditionDraft(
                field="subject",
                operator="contains",
                value=required_keywords[0],
            )
        )
    normalized_actions = [
        RuleActionDraft(
            action_type=action.action_type,
            target=action.target,
            value_json=action.value_json if isinstance(action.value_json, dict) else None,
            stop_processing=False,
        )
        for action in rule.actions
    ]
    return RuleDraftResult(
        intent_summary=result.intent_summary,
        confidence=max(0.0, min(1.0, float(result.confidence))),
        rule=RuleDraft(
            name=rule.name[:200],
            account_id=rule.account_id,
            match_type="ALL",
            conditions=normalized_conditions,
            actions=normalized_actions,
        ),
        explanation=result.explanation,
        warnings=_dedupe_list(result.warnings + [
            "This is a draft only.",
            "This does not send an iMessage now.",
            "This does not mutate Gmail.",
        ]),
        safety_status="safe_local_alert_draft",
        requires_user_confirmation=True,
        status="draft",
        saveable=True,
        provider=result.provider,
        model=result.model,
    )


def _first_blocked_alert_reason(
    result: RuleDraftResult,
    request_text: str,
    low_confidence_threshold: float,
) -> str | None:
    rule = result.rule
    if rule is None:
        return "No rule was returned."
    if result.requires_user_confirmation is not True:
        return "Draft does not require user confirmation."
    if result.safety_status != "safe_local_alert_draft":
        return "Draft safety status is not a safe local alert draft."
    if rule.match_type != "ALL":
        return "Alert drafts must use match_type ALL."
    if result.confidence < low_confidence_threshold:
        return "The local model confidence was too low."
    if not rule.conditions:
        return "Alert drafts must include conditions."
    if not any(c.field in {"from_domain", "from_email"} for c in rule.conditions):
        return "Alert drafts must include from_domain or from_email."
    if not any(c.field in {"subject", "body"} for c in rule.conditions):
        return "Alert drafts must include a subject or body content condition."
    if not rule.actions:
        return "Alert drafts must include a safe alert action."
    if len(rule.actions) > 2:
        return "Alert drafts may only include narrow local alert actions."
    for condition in rule.conditions:
        if condition.field not in ALERT_ALLOWED_FIELDS:
            return f"Unsupported condition field: {condition.field}."
        if condition.operator not in ALERT_ALLOWED_OPERATORS:
            return f"Unsupported condition operator: {condition.operator}."
        value = str(condition.value or "").strip()
        if not value:
            return "Alert draft conditions must have values."
        if condition.field == "from_email" and not _is_valid_email(value.lower()):
            return "from_email must be one valid email address."
        if condition.field == "from_domain":
            domain = _domain_hint_for_text(request_text) or value.lower()
            if not _is_valid_domain(domain):
                return "from_domain must be one valid domain."
    for action in rule.actions:
        if action.action_type in ALERT_BLOCKED_ACTIONS:
            return f"Blocked action type: {action.action_type}."
        if action.action_type not in ALERT_ALLOWED_ACTIONS:
            return f"Unsupported action type: {action.action_type}."
        if action.action_type == "notify_dashboard":
            return "notify_dashboard is not enabled for Phase 4F.1b saveable drafts."
        if action.stop_processing is not False:
            return "Alert drafts must not stop processing."
        if action.action_type == "mark_pending_alert" and action.target not in ("imessage", "dashboard", None, ""):
            return "mark_pending_alert target must be local."
        if isinstance(action.value_json, dict):
            for value in action.value_json.values():
                if isinstance(value, str) and re.search(r"https?://|webhook", value, re.I):
                    return "External URLs and webhooks are not allowed."
        elif action.value_json not in (None, "", {}):
            return "Alert draft value_json must be an object."
    return None


def _coerce_alert_result(data: dict[str, Any], account_id: str | None, cfg: dict[str, Any]) -> RuleDraftResult:
    if not isinstance(data, dict):
        raise ValueError("invalid_model_schema: response must be object")
    rule_data = data.get("rule")
    if not isinstance(rule_data, dict):
        raise ValueError("invalid_model_schema: rule must be object")
    condition_items = rule_data.get("conditions")
    if not isinstance(condition_items, list):
        raise ValueError("invalid_model_schema: rule.conditions must be list")
    action_items = rule_data.get("actions")
    if not isinstance(action_items, list):
        raise ValueError("invalid_model_schema: rule.actions must be list")
    if not isinstance(data.get("explanation"), list):
        raise ValueError("invalid_model_schema: explanation must be list")
    if not isinstance(data.get("warnings"), list):
        raise ValueError("invalid_model_schema: warnings must be list")
    if not all(isinstance(item, dict) for item in condition_items):
        raise ValueError("invalid_model_schema: rule.conditions items must be objects")
    if not all(isinstance(item, dict) for item in action_items):
        raise ValueError("invalid_model_schema: rule.actions items must be objects")

    conditions = [
        RuleConditionDraft(
            field=str(item.get("field", "")).strip(),
            operator=str(item.get("operator", "")).strip(),
            value=str(item.get("value", "")).strip(),
        )
        for item in condition_items
    ]
    actions = [
        RuleActionDraft(
            action_type=str(item.get("action_type", "")).strip(),
            target=item.get("target"),
            value_json=item.get("value_json"),
            stop_processing=bool(item.get("stop_processing", False)),
        )
        for item in action_items
    ]
    rule = RuleDraft(
        name=str(rule_data.get("name") or "Local alert draft").strip(),
        account_id=account_id if account_id is not None else rule_data.get("account_id"),
        match_type=str(rule_data.get("match_type") or "").strip(),
        conditions=conditions,
        actions=actions,
    )
    return RuleDraftResult(
        intent_summary=str(data.get("intent_summary") or "Local alert draft").strip(),
        confidence=float(data.get("confidence") or 0.0),
        rule=rule,
        explanation=_string_list(data.get("explanation")),
        warnings=_string_list(data.get("warnings")),
        safety_status=str(data.get("safety_status") or "").strip(),
        requires_user_confirmation=bool(data.get("requires_user_confirmation", False)),
        provider="ollama",
        model=cfg["model"],
    )


def _call_rule_draft_ollama(
    request_text: str,
    cfg: dict[str, Any],
    client: Callable[[dict[str, Any]], Any] | httpx.Client | None,
) -> Any:
    payload = {
        "model": cfg["model"],
        "stream": False,
        "format": _alert_rule_schema(),
        "messages": [
            {"role": "system", "content": _alert_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request_text": request_text,
                        "bank_domain_hints": BANK_DOMAIN_HINTS,
                    },
                    ensure_ascii=True,
                ),
            },
        ],
        "options": {"temperature": cfg["temperature"]},
    }
    if callable(client):
        return client(payload)
    owns_client = client is None
    http = client or httpx.Client(timeout=cfg["timeout_seconds"])
    try:
        resp = http.post(f"{cfg['base_url']}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns_client:
            http.close()


def _parse_model_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and "message" in payload:
        content = payload.get("message", {}).get("content", "")
    else:
        content = payload
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError("model output was not JSON")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"non-JSON model output: {content[:MAX_RAW_MODEL_ERROR_CHARS]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model JSON root must be an object")
    return parsed


def _alert_rule_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intent_summary",
            "confidence",
            "rule",
            "explanation",
            "warnings",
            "safety_status",
            "requires_user_confirmation",
        ],
        "properties": {
            "intent_summary": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rule": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "account_id",
                    "match_type",
                    "conditions",
                    "actions",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "account_id": {"type": ["string", "null"]},
                    "match_type": {"type": "string", "enum": ["ALL"]},
                    "conditions": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["field", "operator", "value"],
                            "properties": {
                                "field": {
                                    "type": "string",
                                    "enum": [
                                        "from_domain",
                                        "from_email",
                                        "subject",
                                        "body",
                                    ],
                                },
                                "operator": {
                                    "type": "string",
                                    "enum": ["contains", "equals"],
                                },
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "actions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "action_type",
                                "target",
                                "value_json",
                                "stop_processing",
                            ],
                            "properties": {
                                "action_type": {
                                    "type": "string",
                                    "enum": ["mark_pending_alert"],
                                },
                                "target": {
                                    "type": "string",
                                    "enum": ["imessage"],
                                },
                                "value_json": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["template"],
                                    "properties": {
                                        "template": {"type": "string"},
                                    },
                                },
                                "stop_processing": {
                                    "type": "boolean",
                                    "enum": [False],
                                },
                            },
                        },
                    },
                },
            },
            "explanation": {
                "type": "array",
                "items": {"type": "string"},
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "safety_status": {
                "type": "string",
                "enum": ["safe_local_alert_draft"],
            },
            "requires_user_confirmation": {
                "type": "boolean",
                "enum": [True],
            },
        },
    }


def _alert_system_prompt() -> str:
    return """Return JSON only in the provided schema.
Transform one request into one draft local alert rule.
Choose a sender domain or sender email, choose narrow subject/body keywords, and produce exactly one mark_pending_alert action.
Use bank_domain_hints for bank domains. Do not invent domains.
Example for "If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification.":
{"intent_summary":"Notify me for Permata credit card clarification emails","confidence":0.9,"rule":{"name":"Permata credit card clarification alert","account_id":null,"match_type":"ALL","conditions":[{"field":"from_domain","operator":"contains","value":"permatabank.co.id"},{"field":"subject","operator":"contains","value":"clarification"},{"field":"body","operator":"contains","value":"credit card"}],"actions":[{"action_type":"mark_pending_alert","target":"imessage","value_json":{"template":"Permata credit card clarification email detected."},"stop_processing":false}]},"explanation":["This rule matches messages from Permata Bank.","It looks for clarification and credit card wording.","It queues a local Mail Agent alert only after the rule is saved."],"warnings":["This is a draft only.","This does not send an iMessage now.","This does not mutate Gmail."],"safety_status":"safe_local_alert_draft","requires_user_confirmation":true}"""


def _unsupported_alert_result(
    safety_status: str,
    warnings: list[str],
    provider: str | None = None,
    model: str | None = None,
    raw_model_error: str | None = None,
) -> RuleDraftResult:
    return RuleDraftResult(
        intent_summary="Cannot draft a safe local alert rule",
        confidence=0.0,
        rule=None,
        explanation=["No saveable rule was created."],
        warnings=warnings,
        safety_status=safety_status,
        requires_user_confirmation=True,
        status="unsupported",
        saveable=False,
        provider=provider,
        model=model,
        raw_model_error=raw_model_error,
    )


def _domain_hint_for_text(text: str) -> str | None:
    lowered = text.lower()
    for name in sorted(BANK_DOMAIN_HINTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            return BANK_DOMAIN_HINTS[name]
    return None


def _required_content_keywords_for_text(text: str) -> list[str]:
    lowered = text.lower()
    clarification = any(word in lowered for word in ("clarification", "klarifikasi"))
    credit_card = (
        "credit card" in lowered
        or "kartu kredit" in lowered
        or ("card" in lowered and "transaction" in lowered)
    )
    transaction = any(word in lowered for word in ("transaction", "transaksi"))
    if not (clarification and (credit_card or transaction)):
        return []
    return [
        "clarification",
        "klarifikasi",
        "credit card",
        "kartu kredit",
        "transaction",
        "transaksi",
    ]


def _has_required_content_keyword(
    conditions: list[RuleConditionDraft],
    keywords: list[str],
) -> bool:
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for condition in conditions:
        if condition.field not in {"subject", "body"}:
            continue
        value = condition.value.lower()
        if any(keyword in value for keyword in lowered_keywords):
            return True
    return False


def _is_valid_domain(domain: str) -> bool:
    if not domain or "@" in domain or ".." in domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    return all(
        bool(label)
        and not label.startswith("-")
        and not label.endswith("-")
        and re.fullmatch(r"[a-z0-9-]+", label, flags=re.IGNORECASE)
        for label in labels
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:10]


def _dedupe_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _sanitize_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").replace("\r", " ").strip()
    return text[:MAX_RAW_MODEL_ERROR_CHARS]
