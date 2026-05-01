"""
api_mail.py — Mail API data layer and FastAPI router for account management.
"""
from __future__ import annotations

import os
import sqlite3
import logging
import hmac
import imaplib
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, EmailStr, StrictStr, ValidationError

from .action_execution import (
    evaluate_execution_gate,
    execution_id_for_key,
    mock_execute_approved_action,
)
from .action_verification import (
    FinalVerificationRequest,
    ImapReadOnlyMailboxAdapter,
    verify_action_plan_readonly,
)
from .state import AgentState, apply_sqlite_pragmas
from .rules import (
    ACTIVE_ACTIONS,
    ALLOWED_OPERATORS,
    MUTATION_ACTIONS,
    build_dry_run_mutation_plan,
    evaluate_message,
    validate_action_type,
    validate_operator,
    _execute_action,
    _execute_mutation_action,
    _mutation_preview_metadata,
)
from .imap_source import probe_capabilities, _load_app_password
from .config_manager import (
    ConfigManager,
    DuplicateAccountError,
    SoftDeletedAccountError,
)
from .ai_worker import (
    MailAiClassification,
    classify_with_ollama,
)
from .rule_ai_builder import (
    MAX_RULE_AI_REQUEST_CHARS,
    draft_alert_rule_with_local_llm,
    draft_sender_suppression_rule,
)

logger = logging.getLogger("agent.api_mail")

router = APIRouter()

# ── DB connection ─────────────────────────────────────────────────────────────

_DB_DEFAULT = "/app/data/agent.db"

def _db_path() -> str:
    return os.environ.get("AGENT_DB_PATH", _DB_DEFAULT)

def _connect():
    path = _db_path()
    conn = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn

def _connect_rw():
    _ensure_state()
    conn = sqlite3.connect(_db_path(), timeout=10.0, check_same_thread=False)
    apply_sqlite_pragmas(conn)
    conn.row_factory = sqlite3.Row
    return conn

def _ensure_state() -> AgentState:
    return AgentState(_db_path())

def _db_exists() -> bool:
    return os.path.exists(_db_path())

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None

def _get_config_accounts() -> list[dict]:
    import tomllib
    settings_file = os.environ.get("SETTINGS_FILE", "/app/config/settings.toml")
    try:
        with open(settings_file, "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("mail", {}).get("imap", {}).get("accounts", [])
    except Exception:
        return []

def _get_settings() -> dict:
    import tomllib
    settings_file = os.environ.get("SETTINGS_FILE", "/app/config/settings.toml")
    try:
        with open(settings_file, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}

def _resolve_mode(settings: dict) -> str:
    agent_cfg = settings.get("agent", {})
    mode = str(agent_cfg.get("mode", "")).strip()
    if mode in ("observe", "draft_only", "live"):
        return mode
    safe_default = str(agent_cfg.get("safe_default", "draft_only")).strip()
    if safe_default in ("observe", "draft_only"):
        return safe_default
    return "draft_only"

def _find_account(account_id: str) -> dict | None:
    for acct in _get_config_accounts():
        names = {acct.get("id"), acct.get("name"), acct.get("email")}
        if account_id in names:
            return acct
    return None

def _is_placeholder_account(acct: dict) -> bool:
    email = str(acct.get("email", "")).strip()
    return email.startswith("YOUR_EMAIL")

# ── Auth ─────────────────────────────────────────────────────────────────────

def _api_key() -> str | None:
    return os.environ.get("FINANCE_API_KEY") or None

def require_api_key(x_api_key: str = Header(default="")):
    expected = _api_key()
    if not expected:
        return
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")

# ── Models ───────────────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    app_password: str = Field(..., min_length=1)

class AccountUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    enabled: Optional[bool] = None
    app_password: Optional[str] = Field(None, min_length=1)

class AccountEnabledPatch(BaseModel):
    enabled: bool

class RuleConditionIn(BaseModel):
    field: str = Field(..., min_length=1, max_length=100)
    operator: str = Field(..., min_length=1, max_length=50)
    value: Optional[str] = Field(None, max_length=2000)
    value_json: Optional[Any] = None
    case_sensitive: bool = False

class RuleActionIn(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=100)
    target: Optional[str] = Field(None, max_length=500)
    value_json: Optional[Any] = None
    stop_processing: bool = False

class RuleCreate(BaseModel):
    account_id: Optional[str] = Field(None, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    priority: int
    enabled: bool = True
    match_type: str = Field("ALL", pattern=r"^(ALL|ANY)$")
    conditions: list[RuleConditionIn] = Field(default_factory=list)
    actions: list[RuleActionIn] = Field(default_factory=list)

class RulePatch(BaseModel):
    account_id: Optional[str] = Field(None, max_length=200)
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    match_type: Optional[str] = Field(None, pattern=r"^(ALL|ANY)$")
    conditions: Optional[list[RuleConditionIn]] = None
    actions: Optional[list[RuleActionIn]] = None

class RuleReorderItem(BaseModel):
    rule_id: int
    priority: int

class RuleReorder(BaseModel):
    rules: list[RuleReorderItem]

class RulePreview(BaseModel):
    message: dict[str, Any]

class RuleAiDraftRequest(BaseModel):
    request_text: StrictStr = Field(
        ...,
        min_length=1,
        max_length=MAX_RULE_AI_REQUEST_CHARS,
    )
    account_id: Optional[str] = Field(None, max_length=200)
    mode: Optional[str] = Field("auto", pattern=r"^(auto|sender_suppression|alert_rule)$")

class MutationPreview(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=100)
    target: Optional[str] = Field(None, max_length=500)
    value_json: Optional[Any] = None
    dry_run: Optional[bool] = None

class ApprovalCleanupRequest(BaseModel):
    force: bool = False

class ApprovalArchiveRequest(BaseModel):
    decided_by: Optional[str] = Field("operator", max_length=200)

class AiSettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    timeout_seconds: Optional[int] = None
    max_body_chars: Optional[int] = None
    urgency_threshold: Optional[int] = None

class AiTestRequest(BaseModel):
    sender: str = Field(..., min_length=1, max_length=500)
    subject: str = Field(..., min_length=1, max_length=500)
    body: str = Field(..., min_length=1, max_length=50000)
    received_at: Optional[str] = None
    account_id: Optional[str] = None

class AiTriggerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    enabled: bool = True
    priority: int = 100
    conditions_json: dict[str, Any]
    actions_json: Any
    cooldown_seconds: int = Field(3600, ge=0)

class AiTriggerPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    conditions_json: Optional[dict[str, Any]] = None
    actions_json: Optional[Any] = None
    cooldown_seconds: Optional[int] = Field(None, ge=0)

class AiTriggerPreview(BaseModel):
    classification: Optional[dict[str, Any]] = None
    message_id: Optional[str] = None
    queue_id: Optional[int] = None

class ApprovalDecision(BaseModel):
    decision_note: Optional[str] = Field(None, max_length=1000)
    decided_by: Optional[str] = Field("operator", max_length=200)

class ApprovalMarkFailed(BaseModel):
    reason: Optional[str] = Field(
        "Execution started but did not finish",
        max_length=1000,
    )
    decided_by: Optional[str] = Field("operator", max_length=200)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_app_password(password: str) -> str:
    """Remove regular and non-ASCII whitespace from pasted app passwords."""
    return "".join(password.split())

def _test_imap_login(email: str, password: str) -> bool:
    """Validate Gmail IMAP login and SELECT INBOX."""
    try:
        password = _normalize_app_password(password)
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap.login(email, password)
        status, _ = imap.select("INBOX", readonly=True)
        imap.logout()
        if status != "OK":
            raise ValueError("IMAP login succeeded but SELECT INBOX failed. Ensure IMAP is enabled in Gmail settings.")
        return True
    except imaplib.IMAP4.error as e:
        raise ValueError(f"IMAP Authentication failed: {e}")
    except Exception as e:
        raise ValueError(f"IMAP Connection failed: {e}")

def _json_dumps_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)

def _row_to_rule(row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    rule_id = row["rule_id"]
    conditions = conn.execute(
        "SELECT id, field, operator, value, value_json, case_sensitive "
        "FROM mail_rule_conditions WHERE rule_id = ? ORDER BY id",
        (rule_id,),
    ).fetchall()
    actions = conn.execute(
        "SELECT id, action_type, target, value_json, stop_processing "
        "FROM mail_rule_actions WHERE rule_id = ? ORDER BY id",
        (rule_id,),
    ).fetchall()
    payload = dict(row)
    payload["enabled"] = bool(payload["enabled"])
    payload["conditions"] = [
        {
            **dict(c),
            "case_sensitive": bool(c["case_sensitive"]),
            "value_json": json.loads(c["value_json"]) if c["value_json"] else None,
        }
        for c in conditions
    ]
    payload["actions"] = [
        {
            **dict(a),
            "stop_processing": bool(a["stop_processing"]),
            "value_json": json.loads(a["value_json"]) if a["value_json"] else None,
        }
        for a in actions
    ]
    return payload

def _fetch_rule(conn: sqlite3.Connection, rule_id: int) -> dict | None:
    row = conn.execute(
        "SELECT rule_id, account_id, name, priority, enabled, match_type, "
        "created_at, updated_at FROM mail_rules WHERE rule_id = ?",
        (rule_id,),
    ).fetchone()
    return _row_to_rule(row, conn) if row else None

def _validate_rule_payload(conditions: list[RuleConditionIn],
                           actions: list[RuleActionIn]) -> None:
    for condition in conditions:
        validate_operator(condition.operator)
    for action in actions:
        validate_action_type(action.action_type)
        target = (action.target or "").strip()
        if action.action_type == "move_to_folder" and not target:
            raise ValueError("move_to_folder requires a non-empty target")
        if action.action_type == "add_label" and not target:
            raise ValueError("add_label requires a non-empty target")
        if action.action_type in MUTATION_ACTIONS - {"move_to_folder", "add_label"} and target:
            raise ValueError(f"{action.action_type} does not accept a target")

def _classification_for_trigger_preview(
        state: AgentState, data: AiTriggerPreview) -> dict[str, Any]:
    if data.classification:
        return MailAiClassification.model_validate(data.classification).model_dump()
    conn = _connect_rw()
    try:
        if data.queue_id is not None:
            row = conn.execute("""
                SELECT c.category, c.urgency_score, c.confidence,
                       c.summary, c.raw_json
                FROM mail_ai_classifications c
                WHERE c.queue_id = ?
                ORDER BY c.id DESC LIMIT 1
            """, (data.queue_id,)).fetchone()
        elif data.message_id:
            candidates = [data.message_id]
            if not data.message_id.startswith(("mkey:", "fkey:")):
                candidates.extend([
                    f"mkey:{data.message_id}",
                    f"fkey:{data.message_id}",
                ])
            row = conn.execute("""
                SELECT c.category, c.urgency_score, c.confidence,
                       c.summary, c.raw_json
                FROM mail_ai_classifications c
                JOIN mail_ai_queue q ON q.id = c.queue_id
                WHERE q.message_id IN ({})
                   OR q.bridge_id = ?
                ORDER BY c.id DESC LIMIT 1
            """.format(",".join("?" for _ in candidates)),
                (*candidates, data.message_id)).fetchone()
        else:
            raise ValueError(
                "Provide classification, queue_id, or message_id")
        if not row:
            raise ValueError("AI classification not found")
        raw = json.loads(row["raw_json"] or "{}")
        raw.setdefault("category", row["category"])
        raw.setdefault("urgency_score", row["urgency_score"])
        raw.setdefault("confidence", row["confidence"])
        raw.setdefault("summary", row["summary"] or "")
        raw.setdefault("needs_reply", False)
        raw.setdefault("reason", "")
        return MailAiClassification.model_validate(raw).model_dump()
    finally:
        conn.close()

def _approval_settings(settings: dict) -> dict[str, Any]:
    cfg = settings.get("mail", {}).get("approvals", {})
    expiry_hours = int(cfg.get("approval_expiry_hours", 72))
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "require_approval_for_ai_actions": True,
        "approval_expiry_hours": expiry_hours,
        "default_expiry_minutes": int(
            cfg.get("default_expiry_minutes", expiry_hours * 60)),
        "started_stale_after_minutes": int(
            cfg.get("started_stale_after_minutes", 30)),
        "allow_bulk_approve": False,
        "auto_expire_pending_after_hours": int(
            cfg.get("auto_expire_pending_after_hours", 24)),
        "archive_terminal_after_days": int(
            cfg.get("archive_terminal_after_days", 30)),
        "retain_audit_days": int(cfg.get("retain_audit_days", 365)),
        "cleanup_enabled": bool(cfg.get("cleanup_enabled", False)),
    }

def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _iso_add_hours(value: str | None, hours: int) -> str | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    return (dt + timedelta(hours=hours)).isoformat()

def _approval_gate_result(approval: dict) -> dict[str, Any] | None:
    result = approval.get("execution_result") or {}
    nested = result.get("result") if isinstance(result, dict) else None
    if isinstance(nested, dict):
        return nested
    if isinstance(result, dict) and result.get("status"):
        return result
    return None

def _approval_blocked_reason(approval: dict) -> str | None:
    result = approval.get("execution_result") or {}
    if not isinstance(result, dict):
        return None
    if result.get("reason"):
        return str(result["reason"])
    nested = result.get("result")
    if isinstance(nested, dict):
        return (
            nested.get("reason")
            or nested.get("gate_status")
            or nested.get("status")
        )
    if approval.get("status") == "blocked":
        return approval.get("execution_status")
    return None

def _approval_execution_error(approval: dict) -> str | None:
    result = approval.get("execution_result") or {}
    if not isinstance(result, dict):
        return None
    if result.get("error"):
        return str(result["error"])
    nested = result.get("result")
    if isinstance(nested, dict) and nested.get("error"):
        return str(nested["error"])
    return None

def _approval_execution_state(
        state: AgentState, approval: dict, stale_minutes: int) -> str:
    status = approval.get("status")
    execution_status = approval.get("execution_status")
    if status == "rejected":
        return "rejected"
    if status == "expired":
        return "expired"
    if status == "executed":
        return "executed"
    if status == "blocked":
        return "blocked"
    if status == "failed":
        return "failed"
    if execution_status == "started":
        return (
            "stuck"
            if state.approval_is_stale_started(
                approval, stale_after_minutes=stale_minutes)
            else "started"
        )
    return "not_requested"

_PHASE_BLOCKED_ACTIONS = {
    "send_imessage",
    "reply",
    "auto_reply",
    "forward",
    "delete",
    "expunge",
    "unsubscribe",
    "webhook",
    "external_webhook",
    "notify_dashboard",
}

_DANGEROUS_ACTIONS = {
    "delete",
    "expunge",
    "reply",
    "auto_reply",
    "forward",
    "webhook",
    "external_webhook",
    "unsubscribe",
    "send_imessage",
}

def _safe_preview_text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\x00", "").split())
    if len(text) > limit:
        return text[:limit - 1].rstrip() + "..."
    return text

def _approval_message_context(approval: dict) -> dict[str, Any]:
    return {
        "sender": _safe_preview_text(approval.get("sender"), 160),
        "subject": _safe_preview_text(approval.get("subject"), 180),
        "received_at": approval.get("received_at"),
        "account_id": approval.get("account_id"),
        "account_label": approval.get("account_id"),
        "folder": approval.get("folder"),
        "imap_uid": approval.get("imap_uid"),
        "uidvalidity": approval.get("uidvalidity"),
        "classification_category": approval.get("ai_category"),
        "ai_summary": _safe_preview_text(approval.get("reason"), 240),
        "urgency_score": approval.get("ai_urgency_score"),
        "confidence": approval.get("ai_confidence"),
    }

def _mutation_cfg(settings: dict) -> dict[str, Any]:
    raw = settings.get("mail", {}).get("imap_mutations", {})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "dry_run_default": bool(raw.get("dry_run_default", True)),
        "allow_mark_read": bool(raw.get("allow_mark_read", False)),
        "allow_mark_unread": bool(raw.get("allow_mark_unread", False)),
        "allow_add_label": bool(raw.get("allow_add_label", False)),
        "allow_move_to_folder": bool(raw.get("allow_move_to_folder", False)),
        "require_uidvalidity_match": bool(
            raw.get("require_uidvalidity_match", True)),
        "require_capability_cache": bool(
            raw.get("require_capability_cache", True)),
        "allow_create_folder": bool(raw.get("allow_create_folder", False)),
        "allow_copy_delete_fallback": bool(
            raw.get("allow_copy_delete_fallback", False)),
    }

def _capability_status_for_action(
        action_type: str, cache: dict | None) -> tuple[str, str | None]:
    if not cache:
        return "missing", "Capability cache is missing."
    if cache.get("status") not in {None, "ok"}:
        return "unknown", cache.get("error") or "Capability check did not succeed."
    if action_type in {"mark_read", "mark_unread"}:
        value = cache.get("supports_store_flags")
        if value is True:
            return "known", None
        if value is False:
            return "unsupported", "STORE flag support is not advertised."
        return "unknown", "STORE flag support is unknown."
    if action_type == "add_label":
        value = cache.get("supports_gmail_labels")
        if value is True:
            return "known", None
        if value is False:
            return "unsupported", "Gmail label capability is not advertised."
        return "unknown", "Gmail label capability is unknown."
    if action_type == "move_to_folder":
        value = cache.get("supports_move")
        if value is True:
            return "known", None
        if value is False:
            return "unsupported", "UID MOVE support is not advertised."
        return "unknown", "UID MOVE support is unknown."
    return "not_applicable", None

def _mutation_readiness(
        state: AgentState, approval: dict, settings: dict) -> dict[str, Any]:
    action_type = str(approval.get("proposed_action_type") or "")
    cfg = _mutation_cfg(settings)
    mode = _resolve_mode(settings)
    account_id = approval.get("account_id")
    folder = approval.get("folder")
    uid = approval.get("imap_uid")
    uidvalidity = approval.get("uidvalidity")
    target = approval.get("proposed_target")
    account = _find_account(str(account_id or "")) if account_id else None
    folder_state = (
        state.get_imap_folder_state(str(account_id), str(folder))
        if account_id and folder else None
    )
    cache = (
        state.get_imap_capability_cache(str(account_id), str(folder))
        if account_id and folder else None
    )
    capability_status, capability_reason = _capability_status_for_action(
        action_type, cache)
    gates: list[dict[str, Any]] = []

    def add_gate(name: str, passed: bool, reason: str,
                 *, warning: bool = False) -> None:
        gates.append({
            "gate": name,
            "status": (
                "passed" if passed else "warning" if warning else "blocked"),
            "reason": reason,
        })

    add_gate("account_present", bool(account_id), str(account_id or "missing"))
    add_gate("folder_present", bool(folder), str(folder or "missing"))
    add_gate("imap_uid_present", uid is not None, str(uid or "missing"))
    add_gate(
        "uidvalidity_present",
        uidvalidity is not None and str(uidvalidity) != "",
        str(uidvalidity or "missing"),
    )
    if account is None:
        add_gate("account_enabled", False, "account unavailable")
    else:
        add_gate(
            "account_enabled",
            bool(account.get("enabled", True)),
            "true" if account.get("enabled", True) else "false",
        )
    if folder_state is None:
        add_gate("folder_state_available", False, "folder state unavailable")
    else:
        cached_validity = folder_state.get("uidvalidity")
        add_gate(
            "folder_state_available",
            cached_validity is not None,
            "uidvalidity cached" if cached_validity is not None else "uidvalidity missing",
        )
        if cfg["require_uidvalidity_match"] and uidvalidity is not None and cached_validity is not None:
            add_gate(
                "uidvalidity_match",
                str(cached_validity) == str(uidvalidity),
                f"cached={cached_validity} approval={uidvalidity}",
            )
    if cfg["require_capability_cache"]:
        add_gate(
            "capability_cache_present",
            cache is not None,
            "cached" if cache else "missing",
        )
    if cache and cache.get("uidvalidity") and uidvalidity is not None and cfg["require_uidvalidity_match"]:
        add_gate(
            "capability_uidvalidity_match",
            str(cache.get("uidvalidity")) == str(uidvalidity),
            f"cached={cache.get('uidvalidity')} approval={uidvalidity}",
        )
    if cache:
        add_gate(
            "capability_known",
            capability_status == "known",
            capability_reason or capability_status,
            warning=capability_status == "unknown",
        )
    allow_gate = {
        "mark_read": "allow_mark_read",
        "mark_unread": "allow_mark_unread",
        "add_label": "allow_add_label",
        "move_to_folder": "allow_move_to_folder",
    }.get(action_type)
    if allow_gate:
        add_gate(allow_gate, bool(cfg.get(allow_gate)), str(bool(cfg.get(allow_gate))).lower())

    plan = build_dry_run_mutation_plan(
        action_type,
        account_id=account_id,
        folder=folder,
        uid=uid,
        uidvalidity=uidvalidity,
        target=target,
        cfg=cfg,
        mode=mode,
        extra_gates=gates,
    )
    blockers = [
        gate for gate in plan["safety_gates"]
        if gate.get("status") == "blocked"
    ]
    return {
        "config": cfg,
        "mode": mode,
        "folder_state": folder_state,
        "capability_cache": cache,
        "capability_status": capability_status,
        "capability_reason": capability_reason,
        "identity_complete": bool(account_id and folder and uid is not None and uidvalidity),
        "blockers": blockers,
        "plan": plan,
    }

def _approval_trigger_context(
        approval: dict, events: list[dict] | None = None) -> dict[str, Any] | None:
    if approval.get("source_type") != "ai_trigger":
        return None
    details = None
    for event in events or []:
        if event.get("event_type") != "ai_trigger_matched":
            continue
        candidate = event.get("details") or {}
        if candidate.get("trigger_id") == approval.get("source_id"):
            details = candidate
            break
    planned_actions = details.get("planned_actions") if details else None
    matched_conditions = details.get("matched_conditions") if details else None
    return {
        "trigger_id": approval.get("source_id"),
        "trigger_name": (
            details.get("trigger_name") if details else approval.get("source_id")),
        "matched_category": (
            details.get("category") if details else approval.get("ai_category")),
        "urgency_score": (
            details.get("urgency_score") if details else approval.get("ai_urgency_score")),
        "confidence": (
            details.get("confidence") if details else approval.get("ai_confidence")),
        "planned_action": {
            "action_type": approval.get("proposed_action_type"),
            "target": approval.get("proposed_target"),
            "value": approval.get("proposed_value"),
        },
        "planned_actions": planned_actions,
        "matched_conditions": matched_conditions,
        "reason": _safe_preview_text(
            details.get("reason") if details else approval.get("reason"), 240),
        "dry_run": details.get("dry_run") if details else True,
    }

def _approval_rule_context(approval: dict) -> dict[str, Any] | None:
    if approval.get("source_type") not in {"rule", "rule_preview"}:
        return None
    return {
        "rule_id": approval.get("source_id"),
        "rule_name": approval.get("source_id"),
        "matched_conditions": None,
        "planned_action": {
            "action_type": approval.get("proposed_action_type"),
            "target": approval.get("proposed_target"),
            "value": approval.get("proposed_value"),
        },
        "stop_processing": None,
        "skip_ai": None,
    }

def _approval_current_gate_preview(
        state: AgentState, approval: dict, settings: dict,
        execution_state: str, expires_at: str | None) -> dict[str, Any]:
    action_type = str(approval.get("proposed_action_type") or "")
    approval_cfg = _approval_settings(settings)
    notes: list[str] = []
    base = {
        "would_execute_now": False,
        "would_be_blocked_now": True,
        "gate": "blocked",
        "reason": "Approval cannot execute in its current state.",
        "capability": "not_applicable",
        "notes": notes,
    }
    if not approval_cfg["enabled"]:
        return {
            **base,
            "gate": "approval_disabled",
            "reason": "[mail.approvals].enabled=false",
            "notes": ["Approval queue is disabled by config."],
        }
    if execution_state == "stuck":
        return {
            **base,
            "gate": "manual_review_required",
            "reason": "execution_status='started' is stale",
            "notes": ["Do not retry automatically. Mark failed only after review."],
        }
    if approval.get("execution_status") == "started":
        return {
            **base,
            "gate": "execution_started",
            "reason": "A gated attempt is already marked started.",
            "notes": ["Wait for a terminal audit event before taking action."],
        }
    status = approval.get("status")
    if status == "rejected":
        return {**base, "gate": "rejected", "reason": "Approval was rejected."}
    if status == "expired" or (
            status == "pending" and _parse_dt(expires_at)
            and _parse_dt(expires_at) < datetime.now(timezone.utc)):
        return {**base, "gate": "expired", "reason": "Approval is expired."}
    if status in {"executed", "blocked", "failed"}:
        return {
            **base,
            "gate": "terminal",
            "reason": f"Approval already reached terminal status '{status}'.",
        }
    if status not in {"pending", "approved"}:
        return {**base, "gate": "invalid_status", "reason": f"Unsupported approval status: {status}"}
    if action_type in _PHASE_BLOCKED_ACTIONS or action_type not in ACTIVE_ACTIONS:
        return {
            **base,
            "gate": "unsupported",
            "reason": f"{action_type} remains blocked in Phase 4D.",
            "notes": ["Unsupported action remains blocked even after approval."],
        }
    if action_type == "add_to_needs_reply":
        return {
            **base,
            "would_execute_now": True,
            "would_be_blocked_now": False,
            "gate": "ready",
            "reason": "Approval would authorize one gated needs-reply queue update.",
            "capability": "not_applicable",
            "notes": ["This does not mutate the mailbox."],
        }
    if action_type not in MUTATION_ACTIONS:
        return {
            **base,
            "gate": "unsupported",
            "reason": f"Unsupported approval action: {action_type}",
            "notes": ["No execution path is available for this action."],
        }

    readiness = _mutation_readiness(state, approval, settings)
    cfg = readiness["config"]
    plan = readiness["plan"]
    blockers = readiness["blockers"]
    first_blocker = blockers[0] if blockers else None
    gate_map = {
        "agent_mode_live": "mode_blocked",
        "imap_mutations_enabled": "mutation_disabled",
        "dry_run_default": "dry_run",
        "allow_mark_read": "action_not_allowed",
        "allow_mark_unread": "action_not_allowed",
        "allow_add_label": "action_not_allowed",
        "allow_move_to_folder": "action_not_allowed",
        "imap_uid_present": "identity_incomplete",
        "uidvalidity_present": "identity_incomplete",
        "account_present": "identity_incomplete",
        "folder_present": "identity_incomplete",
        "account_enabled": "account_disabled",
        "folder_state_available": "folder_state_unavailable",
        "uidvalidity_match": "uidvalidity_mismatch",
        "capability_uidvalidity_match": "uidvalidity_mismatch",
        "capability_cache_present": "capability_cache_missing",
        "capability_known": "capability_unknown",
    }
    gate = gate_map.get(first_blocker["gate"], "blocked") if first_blocker else "ready"
    reason_map = {
        "imap_mutations_enabled": "mail.imap_mutations.enabled=false",
        "dry_run_default": "mail.imap_mutations.dry_run_default=true",
        "allow_mark_read": "mail.imap_mutations.allow_mark_read=false",
        "allow_mark_unread": "mail.imap_mutations.allow_mark_unread=false",
        "allow_add_label": "mail.imap_mutations.allow_add_label=false",
        "allow_move_to_folder": "mail.imap_mutations.allow_move_to_folder=false",
    }
    reason = (
        reason_map.get(first_blocker["gate"], first_blocker["reason"])
        if first_blocker
        else "all readiness gates are present, but live mutation remains phase-gated"
    )
    capability = readiness["capability_status"]
    notes = [
        "Readiness only.",
        "No mailbox change will occur under current settings.",
        "Live mutation disabled unless explicitly enabled in a later phase.",
    ]
    if blockers:
        notes.append("Blocked by default config or readiness guard.")
    if readiness["identity_complete"]:
        notes.append("UIDVALIDITY guard present.")
    if capability in {"missing", "unknown"}:
        notes.append("Capability unknown; live execution would be blocked.")
    return {
        "would_execute_now": False,
        "would_be_blocked_now": True,
        "gate": gate,
        "reason": reason,
        "capability": capability,
        "notes": notes,
        "mode": readiness["mode"],
        "mutation_enabled": cfg["enabled"],
        "dry_run_default": cfg["dry_run_default"],
        "allow_mark_read": cfg["allow_mark_read"],
        "allow_mark_unread": cfg["allow_mark_unread"],
        "allow_add_label": cfg["allow_add_label"],
        "allow_move_to_folder": cfg["allow_move_to_folder"],
        "identity_complete": readiness["identity_complete"],
        "uidvalidity_guard": bool(approval.get("uidvalidity")),
        "capability_cache": readiness["capability_cache"],
        "folder_state": readiness["folder_state"],
        "dry_run_plan": plan,
        "mutation_plan": plan,
        "rollback_hint": plan.get("rollback_hint"),
        "reversible": plan.get("reversible"),
        "safety_gates": plan["safety_gates"],
    }

def _approval_risk(
        approval: dict, gate_preview: dict[str, Any]) -> tuple[str, list[str], str]:
    action_type = str(approval.get("proposed_action_type") or "")
    reasons: list[str] = []
    if action_type in _DANGEROUS_ACTIONS:
        level = "dangerous_blocked"
        reasons.append(f"{action_type} can create irreversible or external effects.")
    elif action_type in _PHASE_BLOCKED_ACTIONS or action_type not in ACTIVE_ACTIONS:
        level = "unsupported_blocked"
        reasons.append(f"{action_type} is unsupported in the approval executor.")
    elif action_type == "move_to_folder":
        level = "caution"
        reasons.append("Moving mail changes folder placement and may be disruptive.")
    elif action_type in {"mark_read", "mark_unread", "mark_flagged", "unmark_flagged", "add_label"}:
        level = "safe_reversible"
        reasons.append("This action is usually reversible, but still requires the configured gate.")
    elif action_type == "add_to_needs_reply":
        level = "safe_readonly"
        reasons.append("This updates the local needs-reply queue and does not mutate the mailbox.")
    else:
        level = "caution"
        reasons.append("Review the proposed action before approval.")
    if gate_preview.get("would_be_blocked_now"):
        reasons.append(f"Current gate preview blocks execution: {gate_preview.get('reason')}")
    if gate_preview.get("capability") == "unknown":
        reasons.append("Mailbox capability is unknown; live execution would be blocked.")
    reversibility = {
        "safe_readonly": "No mailbox mutation.",
        "safe_reversible": "Generally reversible in the mailbox UI.",
        "caution": "May require manual mailbox correction.",
        "dangerous_blocked": "Potentially irreversible or external; blocked.",
        "unsupported_blocked": "Unsupported by this phase; blocked.",
    }[level]
    return level, reasons, reversibility

def _approval_preview_fields(
        state: AgentState, approval: dict, settings: dict,
        execution_state: str, expires_at: str | None,
        events: list[dict] | None = None) -> dict[str, Any]:
    action_type = str(approval.get("proposed_action_type") or "")
    target = approval.get("proposed_target")
    message_context = _approval_message_context(approval)
    trigger_context = _approval_trigger_context(approval, events)
    rule_context = _approval_rule_context(approval)
    gate_preview = _approval_current_gate_preview(
        state, approval, settings, execution_state, expires_at)
    risk_level, risk_reasons, reversibility = _approval_risk(
        approval, gate_preview)
    subject = message_context.get("subject") or approval.get("message_key") or "message"
    action_label = action_type.replace("_", " ")
    target_text = f" to {target}" if target else ""
    guidance = "Review message context before approving. Approval allows one gated attempt."
    if gate_preview.get("gate") == "dry_run":
        guidance = "No mailbox change would occur under current settings."
    elif gate_preview.get("would_be_blocked_now"):
        guidance = "Current config would block mailbox mutation."
    elif gate_preview.get("capability") == "unknown":
        guidance = "Review account, folder, UID, and capability risk before approving."
    return {
        "preview_title": f"{action_label}{target_text}: {subject}",
        "preview_summary": (
            f"Preview before approval: {action_label}{target_text} "
            f"for {message_context.get('folder') or 'unknown folder'} "
            f"UID {message_context.get('imap_uid') or 'unknown'}."
        ),
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
        "operator_guidance": guidance,
        "reversibility": reversibility,
        "would_execute_now": gate_preview["would_execute_now"],
        "would_be_blocked_now": gate_preview["would_be_blocked_now"],
        "current_gate_preview": gate_preview,
        "message_context": message_context,
        "trigger_context": trigger_context,
        "rule_context": rule_context,
    }

def _approval_response(
        state: AgentState, approval: dict, settings: dict, *,
        include_events: bool = False) -> dict[str, Any]:
    approval_cfg = _approval_settings(settings)
    stale_minutes = approval_cfg["started_stale_after_minutes"]
    events = state.approval_events(approval) if include_events else []
    event_ids = [event["id"] for event in events]
    execution_state = _approval_execution_state(
        state, approval, stale_minutes)
    expires_at = _iso_add_hours(
        approval.get("requested_at"),
        approval_cfg["approval_expiry_hours"],
    )
    result = {
        **approval,
        "action_type": approval.get("proposed_action_type"),
        "target": approval.get("proposed_target"),
        "message_id": approval.get("message_key"),
        "trigger_id": (
            approval.get("source_id")
            if approval.get("source_type") == "ai_trigger" else None),
        "rule_id": None,
        "expires_at": expires_at,
        "approved_at": (
            approval.get("decided_at")
            if approval.get("decided_at")
            and approval.get("status") != "rejected" else None),
        "rejected_at": (
            approval.get("decided_at")
            if approval.get("status") == "rejected" else None),
        "execution_finished_at": approval.get("executed_at"),
        "execution_state": execution_state,
        "is_stuck": execution_state == "stuck",
        "stale_after_minutes": stale_minutes,
        "blocked_reason": _approval_blocked_reason(approval),
        "execution_error": _approval_execution_error(approval),
        "gate_result": _approval_gate_result(approval),
        "audit_event_ids": event_ids,
        "is_archived": bool(approval.get("archived_at")),
    }
    result.update(_approval_preview_fields(
        state, approval, settings, execution_state, expires_at, events))
    if include_events:
        result["events"] = events
        result["audit_event_ids"] = event_ids
    return result

def _approval_message(approval: dict) -> dict[str, Any]:
    return {
        "bridge_id": approval.get("message_key") or approval.get("approval_id"),
        "message_id": approval.get("message_key"),
        "message_key": approval.get("message_key"),
        "imap_account": approval.get("account_id"),
        "imap_folder": approval.get("folder"),
        "imap_uidvalidity": approval.get("uidvalidity"),
        "imap_uid": approval.get("imap_uid"),
        "sender_email": approval.get("sender"),
        "subject": approval.get("subject"),
        "date_received": approval.get("received_at"),
    }

def _approval_action(approval: dict) -> dict[str, Any]:
    return {
        "id": None,
        "rule_id": None,
        "action_type": approval["proposed_action_type"],
        "target": approval.get("proposed_target"),
        "value_json": approval.get("proposed_value_json"),
        "stop_processing": False,
    }

def _approval_rule(approval: dict) -> dict[str, Any]:
    return {
        "rule_id": None,
        "name": f"approval:{approval['approval_id']}",
    }

_APPROVAL_STATUSES = {
    "pending", "approved", "rejected", "expired",
    "executed", "failed", "blocked",
}

_APPROVAL_EXECUTION_STATES = {
    "not_requested", "started", "executed", "blocked", "failed",
    "expired", "rejected", "stuck",
}

_APPROVAL_RISK_LEVELS = {
    "safe_readonly", "safe_reversible", "caution",
    "dangerous_blocked", "unsupported_blocked",
}

def _approval_example(approval: dict) -> dict[str, Any]:
    return {
        "approval_id": approval.get("approval_id"),
        "status": approval.get("status"),
        "action_type": approval.get("proposed_action_type"),
        "subject": _safe_preview_text(approval.get("subject"), 120),
        "account_id": approval.get("account_id"),
        "folder": approval.get("folder"),
        "requested_at": approval.get("requested_at"),
        "archived_at": approval.get("archived_at"),
    }

def _approval_cleanup_preview(
        state: AgentState, settings: dict) -> dict[str, Any]:
    cfg = _approval_settings(settings)
    candidates = state.approval_cleanup_candidates(
        expire_after_hours=cfg["auto_expire_pending_after_hours"],
        archive_after_days=cfg["archive_terminal_after_days"],
        retain_audit_days=cfg["retain_audit_days"],
    )
    return {
        "cleanup_enabled": cfg["cleanup_enabled"],
        "would_expire_pending": len(candidates["expire_pending"]),
        "would_archive_terminal": len(candidates["archive_terminal"]),
        "would_hard_delete": 0,
        "stuck_or_started_excluded": candidates["stuck_or_started_count"],
        "auto_expire_pending_after_hours": cfg["auto_expire_pending_after_hours"],
        "retain_audit_days": cfg["retain_audit_days"],
        "archive_terminal_after_days": cfg["archive_terminal_after_days"],
        "examples": {
            "expire_pending": [
                _approval_example(a)
                for a in candidates["examples"]["expire_pending"]
            ],
            "archive_terminal": [
                _approval_example(a)
                for a in candidates["examples"]["archive_terminal"]
            ],
            "hard_delete": [],
        },
        "notes": [
            "Cleanup is disabled by default." if not cfg["cleanup_enabled"] else "Cleanup is enabled.",
            "Cleanup preview is read-only.",
            "Started/stuck approvals are never auto-cleaned.",
            "Hard delete is disabled in Phase 4D.4.",
        ],
    }

def _validate_approval_filters(
        status: str | None, execution_state: str | None,
        risk_level: str | None) -> None:
    if status and status not in _APPROVAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid approval status: {status}",
        )
    if execution_state and execution_state not in _APPROVAL_EXECUTION_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid approval execution_state: {execution_state}",
        )
    if risk_level and risk_level not in _APPROVAL_RISK_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid approval risk_level: {risk_level}",
        )

def _date_in_range(
        value: str | None, created_from: str | None,
        created_to: str | None) -> bool:
    dt = _parse_dt(value)
    if not dt:
        return True
    from_dt = _parse_dt(created_from)
    to_dt = _parse_dt(created_to)
    if from_dt and dt < from_dt:
        return False
    if to_dt and dt > to_dt:
        return False
    return True

def _mutation_executor(settings: dict):
    def execute(action_type: str, message: dict, target, *, dry_run: bool):
        from .imap_source import move_message_by_uid, store_flags_by_uid

        account = _find_account(str(message.get("imap_account") or ""))
        if not account:
            raise RuntimeError(
                f"Unknown IMAP account: {message.get('imap_account')}")
        account = {
            **account,
            "imap_mutations": settings.get("mail", {}).get("imap_mutations", {}),
        }
        folder = message.get("imap_folder")
        uidvalidity = message.get("imap_uidvalidity")
        uid = message.get("imap_uid")
        if action_type == "move_to_folder":
            return move_message_by_uid(
                account, folder, uidvalidity, uid, target,
                dry_run=dry_run)
        if action_type == "add_label":
            return {
                "status": "unsupported",
                "error": "add_label live execution is not implemented",
            }
        flag_map = {
            "mark_read": (["\\Seen"], []),
            "mark_unread": ([], ["\\Seen"]),
            "mark_flagged": (["\\Flagged"], []),
            "unmark_flagged": ([], ["\\Flagged"]),
        }
        add_flags, remove_flags = flag_map[action_type]
        return store_flags_by_uid(
            account, folder, uidvalidity, uid,
            add_flags=add_flags,
            remove_flags=remove_flags,
            dry_run=dry_run)
    return execute

def _execution_plan_for_approval(approval: dict, settings: dict) -> dict:
    action_type = str(approval.get("proposed_action_type") or "")
    value = approval.get("proposed_value")
    value = value if isinstance(value, dict) else {}
    plan = build_dry_run_mutation_plan(
        action_type,
        account_id=approval.get("account_id"),
        folder=approval.get("folder"),
        uid=approval.get("imap_uid"),
        uidvalidity=approval.get("uidvalidity"),
        target=approval.get("proposed_target"),
        cfg=_mutation_cfg(settings),
        mode=_resolve_mode(settings),
    )
    if isinstance(value.get("before_state"), dict):
        plan["before_state"] = value["before_state"]
    if isinstance(value.get("message_identity"), dict):
        plan["message_identity"] = value["message_identity"]
    if value.get("plan_hash"):
        plan["plan_hash"] = value["plan_hash"]
    return plan

def _readonly_mailbox_adapter(settings: dict, approval: dict):
    account_id = str(approval.get("account_id") or "")
    account = _find_account(account_id)
    if not account:
        raise RuntimeError(f"IMAP account not found: {account_id}")
    password = _load_app_password(account)
    return ImapReadOnlyMailboxAdapter(account, password=password)

def _first_gate_reason(gate_result) -> str:
    if not gate_result.blocked_reasons:
        return "blocked"
    reason = gate_result.blocked_reasons[0]
    if reason.startswith("agent.mode="):
        return "mode_blocked"
    if reason == "mail.imap_mutations.dry_run_default=true":
        return "dry_run"
    if reason == "mail.imap_mutations.enabled=false":
        return "mutation_disabled"
    if reason.startswith("mail.imap_mutations.allow_"):
        return "action_not_allowed"
    if reason.startswith("unsupported_execution_action"):
        return "unsupported"
    if reason.endswith("_deferred"):
        return "unsupported"
    if reason.startswith("dangerous_action"):
        return "unsupported"
    return reason

def _blocked_execution_record(
        state: AgentState, gate_result, final_verification: dict | None,
        *, event_type: str) -> dict | None:
    if not gate_result.idempotency_key or not gate_result.plan_hash:
        return None
    execution_id = execution_id_for_key(gate_result.idempotency_key)
    blockers = (
        final_verification.get("blockers", [])
        if final_verification else [
            {"code": "gate_blocked", "message": "; ".join(gate_result.blocked_reasons)}
        ]
    )
    error = blockers[0].get("message") if blockers else "Execution blocked"
    return state.insert_blocked_action_execution(
        execution_id=execution_id,
        approval_id=str(gate_result.approval_id),
        account_id=str(gate_result.account_id),
        folder=str(gate_result.folder),
        uidvalidity=str(gate_result.uidvalidity),
        imap_uid=str(gate_result.imap_uid),
        operation=str(gate_result.operation),
        target=None if gate_result.target is None else str(gate_result.target),
        plan_hash=gate_result.plan_hash,
        idempotency_key=gate_result.idempotency_key,
        error_message=error,
        event_type=event_type,
        event_payload={
            "gate_result": gate_result.to_dict(),
            "final_verification": final_verification,
            "execution_mode": "mock",
        },
    )

def _execute_approved_action(state: AgentState, approval: dict) -> dict:
    action_type = approval["proposed_action_type"]
    blocked_actions = {
        "send_imessage",
        "reply",
        "auto_reply",
        "forward",
        "delete",
        "expunge",
        "unsubscribe",
        "webhook",
        "external_webhook",
        "notify_dashboard",
    }
    if action_type in blocked_actions:
        return {
            "status": "blocked",
            "execution_status": "unsupported",
            "reason": f"{action_type} execution is disabled in Phase 4D.1",
        }
    message = _approval_message(approval)
    action = _approval_action(approval)
    rule = _approval_rule(approval)
    if action_type == "add_to_needs_reply":
        outcome = _execute_action(state, message, rule, action)
        return {
            "status": "executed",
            "execution_status": "completed",
            "result": outcome,
        }
    if action_type not in MUTATION_ACTIONS:
        return {
            "status": "blocked",
            "execution_status": "unsupported",
            "reason": f"Unsupported approval action: {action_type}",
        }

    settings = _get_settings()
    plan = _execution_plan_for_approval(approval, settings)
    account = _find_account(str(approval.get("account_id") or ""))
    folder_state = (
        state.get_imap_folder_state(
            str(approval.get("account_id")), str(approval.get("folder")))
        if approval.get("account_id") and approval.get("folder") else None
    )
    capability_cache = (
        state.get_imap_capability_cache(
            str(approval.get("account_id")), str(approval.get("folder")))
        if approval.get("account_id") and approval.get("folder") else None
    )
    gate_result = evaluate_execution_gate(
        approval=approval,
        settings=settings,
        account=account,
        folder_state=folder_state,
        capability_cache=capability_cache,
        dry_run_plan=plan,
        existing_execution=None,
        mock_mode=True,
    )
    existing = (
        state.get_action_execution_by_idempotency(gate_result.idempotency_key)
        if gate_result.idempotency_key else None
    )
    if existing:
        gate_result = evaluate_execution_gate(
            approval=approval,
            settings=settings,
            account=account,
            folder_state=folder_state,
            capability_cache=capability_cache,
            dry_run_plan=plan,
            existing_execution=existing,
            mock_mode=True,
        )
    if gate_result.status != "ready":
        execution_status = _first_gate_reason(gate_result)
        _blocked_execution_record(
            state, gate_result, None, event_type="mock_execution_blocked")
        return {
            "status": "blocked",
            "execution_status": execution_status,
            "execution_mode": "mock",
            "gate_result": gate_result.to_dict(),
            "final_verification": None,
            "result": {
                "status": execution_status,
                "gate_result": gate_result.to_dict(),
                "mailbox_mutation_occurred": False,
                "mock_only": True,
            },
        }

    verification_request = FinalVerificationRequest(
        approval=approval,
        gate_result=gate_result,
        plan_hash=str(gate_result.plan_hash),
        idempotency_key=str(gate_result.idempotency_key),
    )
    verifier = verify_action_plan_readonly(
        verification_request,
        _readonly_mailbox_adapter(settings, approval),
    )
    verifier_payload = verifier.to_dict()
    if verifier.status != "verified":
        blocked = _blocked_execution_record(
            state, gate_result, verifier_payload,
            event_type="final_verification_blocked")
        return {
            "status": "blocked",
            "execution_status": "final_verification_blocked",
            "execution_mode": "mock",
            "gate_result": gate_result.to_dict(),
            "final_verification": verifier_payload,
            "execution": blocked,
            "result": {
                "status": "final_verification_blocked",
                "gate_result": gate_result.to_dict(),
                "final_verification": verifier_payload,
                "mailbox_mutation_occurred": False,
                "mock_only": True,
            },
        }

    execution = mock_execute_approved_action(state, gate_result)
    state.write_action_execution_event(
        execution["execution_id"],
        "final_verification_passed",
        verifier_payload,
    )
    state.write_action_execution_event(
        execution["execution_id"],
        "mock_execution_completed",
        {
            "execution_id": execution["execution_id"],
            "approval_id": approval.get("approval_id"),
            "execution_mode": "mock",
            "mailbox_mutation_occurred": False,
        },
    )
    return {
        "status": "executed",
        "execution_status": "mock_executed",
        "execution_mode": "mock",
        "gate_result": gate_result.to_dict(),
        "final_verification": verifier_payload,
        "execution": execution,
        "result": {
            "status": "mock_executed",
            "gate_result": gate_result.to_dict(),
            "final_verification": verifier_payload,
            "execution": execution,
            "mailbox_mutation_occurred": False,
            "mock_only": True,
        },
    }


def _normalize_rule_action(action: RuleActionIn) -> RuleActionIn:
    if action.action_type in MUTATION_ACTIONS - {"move_to_folder"}:
        action.target = None
    return action

def _replace_rule_children(conn: sqlite3.Connection, rule_id: int,
                           conditions: list[RuleConditionIn],
                           actions: list[RuleActionIn]) -> None:
    conn.execute("DELETE FROM mail_rule_conditions WHERE rule_id = ?", (rule_id,))
    conn.execute("DELETE FROM mail_rule_actions WHERE rule_id = ?", (rule_id,))
    for condition in conditions:
        conn.execute("""
            INSERT INTO mail_rule_conditions
                (rule_id, field, operator, value, value_json, case_sensitive)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            rule_id,
            condition.field,
            condition.operator,
            condition.value,
            _json_dumps_or_none(condition.value_json),
            int(condition.case_sensitive),
        ))
    for action in actions:
        action = _normalize_rule_action(action)
        conn.execute("""
            INSERT INTO mail_rule_actions
                (rule_id, action_type, target, value_json, stop_processing)
            VALUES (?, ?, ?, ?, ?)
        """, (
            rule_id,
            action.action_type,
            action.target,
            _json_dumps_or_none(action.value_json),
            int(action.stop_processing),
        ))

# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(require_api_key)])
async def get_summary():
    if not _db_exists():
        return _empty_summary()

    try:
        conn = _connect()
        try:
            total_processed: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages"
            ).fetchone()[0]

            urgent_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE urgency IN ('urgent', 'high')"
            ).fetchone()[0]

            drafts_created: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE category IN ('draft_created', 'draft')"
            ).fetchone()[0]

            urg_rows = conn.execute(
                "SELECT urgency, COUNT(*) AS cnt "
                "FROM processed_messages GROUP BY urgency"
            ).fetchall()
            
            total_weight = 0
            total_cnt = 0
            urgency_map = {"urgent": 10, "high": 8, "medium": 5, "low": 2}
            for row in urg_rows:
                w = urgency_map.get((row["urgency"] or "low").lower(), 2)
                total_weight += w * row["cnt"]
                total_cnt += row["cnt"]
            avg_priority = (
                round(total_weight / total_cnt, 1) if total_cnt else 0.0
            )

            gmail_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE source = 'imap' AND provider = 'gmail'"
            ).fetchone()[0]
            outlook_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE provider = 'outlook'"
            ).fetchone()[0]

            cat_rows = conn.execute(
                "SELECT COALESCE(category,'unknown') AS cat, COUNT(*) AS cnt "
                "FROM processed_messages GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            classification_counts: dict[str, int] = {
                r["cat"]: r["cnt"] for r in cat_rows
            }

            imessage_alerts: int = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE success = 1"
            ).fetchone()[0]
            important_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE category = 'important'"
            ).fetchone()[0]
            reply_needed_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE category IN ('reply_needed', 'action_required')"
            ).fetchone()[0]
            labels_applied: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages WHERE alert_sent = 1"
            ).fetchone()[0]

            pdf_count = 0
            if _table_exists(conn, "pdf_attachments"):
                pdf_count = conn.execute(
                    "SELECT COUNT(*) FROM pdf_attachments"
                ).fetchone()[0]
        finally:
            conn.close()

        payload = {
            "total_processed":  total_processed,
            "urgent_count":     urgent_count,
            "drafts_created":   drafts_created,
            "avg_priority":     avg_priority,
            "source_split": {
                "gmail":   gmail_count,
                "outlook": outlook_count,
            },
            "classification": classification_counts,
            "actions": {
                "drafts_created":    drafts_created,
                "labels_applied":    labels_applied,
                "imessage_alerts":   imessage_alerts,
                "important_count":   important_count,
                "reply_needed_count": reply_needed_count,
            },
            "pdf_attachments": pdf_count,
            "mode": "draft_only", # TODO: read from settings
        }
        return payload
    except Exception as exc:
        logger.error(f"Error getting summary: {exc}")
        return {**_empty_summary(), "error": str(exc)}

@router.get("/rules", dependencies=[Depends(require_api_key)])
async def list_rules(account_id: Optional[str] = Query(None)):
    conn = _connect_rw()
    try:
        if account_id is None:
            rows = conn.execute(
                "SELECT rule_id, account_id, name, priority, enabled, "
                "match_type, created_at, updated_at "
                "FROM mail_rules ORDER BY priority, rule_id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rule_id, account_id, name, priority, enabled, "
                "match_type, created_at, updated_at "
                "FROM mail_rules "
                "WHERE account_id IS NULL OR account_id = ? "
                "ORDER BY priority, rule_id",
                (account_id,),
            ).fetchall()
        return [_row_to_rule(row, conn) for row in rows]
    finally:
        conn.close()

@router.post("/rules", dependencies=[Depends(require_api_key)])
async def create_rule(data: RuleCreate):
    try:
        _validate_rule_payload(data.conditions, data.actions)
        conn = _connect_rw()
        try:
            now = AgentState(_db_path())._now()
            cur = conn.execute("""
                INSERT INTO mail_rules
                    (account_id, name, priority, enabled, match_type,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data.account_id,
                data.name,
                data.priority,
                int(data.enabled),
                data.match_type,
                now,
                now,
            ))
            rule_id = int(cur.lastrowid)
            _replace_rule_children(conn, rule_id, data.conditions, data.actions)
            conn.commit()
            return _fetch_rule(conn, rule_id)
        finally:
            conn.close()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post("/rules/ai/draft", dependencies=[Depends(require_api_key)])
async def draft_rule_from_natural_language(data: RuleAiDraftRequest):
    try:
        mode = data.mode or "auto"
        if mode == "sender_suppression":
            draft = draft_sender_suppression_rule(
                data.request_text,
                account_id=data.account_id,
            )
        elif mode == "alert_rule":
            draft = draft_alert_rule_with_local_llm(
                data.request_text,
                account_id=data.account_id,
                settings=_get_settings().get("mail", {}).get("rule_ai", {}),
            )
        else:
            try:
                draft = draft_sender_suppression_rule(
                    data.request_text,
                    account_id=data.account_id,
                )
                if draft.safety_status == "unsupported_intent":
                    draft = draft_alert_rule_with_local_llm(
                        data.request_text,
                        account_id=data.account_id,
                        settings=_get_settings().get("mail", {}).get("rule_ai", {}),
                    )
            except ValueError as sender_exc:
                if "sender email" not in str(sender_exc):
                    raise
                draft = draft_alert_rule_with_local_llm(
                    data.request_text,
                    account_id=data.account_id,
                    settings=_get_settings().get("mail", {}).get("rule_ai", {}),
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return draft.to_dict()

@router.get("/rules/{rule_id}", dependencies=[Depends(require_api_key)])
async def get_rule(rule_id: int):
    conn = _connect_rw()
    try:
        rule = _fetch_rule(conn, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")
        return rule
    finally:
        conn.close()

@router.patch("/rules/{rule_id}", dependencies=[Depends(require_api_key)])
async def patch_rule(rule_id: int, data: RulePatch):
    try:
        conn = _connect_rw()
        try:
            existing = _fetch_rule(conn, rule_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Rule not found")

            updates = data.model_dump(exclude_unset=True)
            conditions = updates.pop("conditions", None)
            actions = updates.pop("actions", None)
            if conditions is not None or actions is not None:
                _validate_rule_payload(
                    conditions or [
                        RuleConditionIn(**{
                            **c,
                            "value_json": c.get("value_json"),
                        })
                        for c in existing["conditions"]
                    ],
                    actions or [
                        RuleActionIn(**{
                            **a,
                            "value_json": a.get("value_json"),
                        })
                        for a in existing["actions"]
                    ],
                )
            if "enabled" in updates:
                updates["enabled"] = int(updates["enabled"])
            updates["updated_at"] = AgentState(_db_path())._now()

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE mail_rules SET {set_clause} WHERE rule_id = ?",
                    [*updates.values(), rule_id],
                )

            if conditions is not None or actions is not None:
                final_conditions = (
                    conditions if conditions is not None
                    else [RuleConditionIn(**c) for c in existing["conditions"]]
                )
                final_actions = (
                    actions if actions is not None
                    else [RuleActionIn(**a) for a in existing["actions"]]
                )
                _replace_rule_children(
                    conn, rule_id, final_conditions, final_actions)
            conn.commit()
            return _fetch_rule(conn, rule_id)
        finally:
            conn.close()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.delete("/rules/{rule_id}", dependencies=[Depends(require_api_key)])
async def delete_rule(rule_id: int):
    conn = _connect_rw()
    try:
        cur = conn.execute("DELETE FROM mail_rules WHERE rule_id = ?", (rule_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Rule not found")
        return {"ok": True}
    finally:
        conn.close()

@router.put("/rules/reorder", dependencies=[Depends(require_api_key)])
async def reorder_rules(data: RuleReorder):
    conn = _connect_rw()
    try:
        now = AgentState(_db_path())._now()
        for offset, item in enumerate(data.rules, start=1):
            conn.execute(
                "UPDATE mail_rules SET priority = ?, updated_at = ? "
                "WHERE rule_id = ?",
                (-1000000 - offset, now, item.rule_id),
            )
        for item in data.rules:
            conn.execute(
                "UPDATE mail_rules SET priority = ?, updated_at = ? "
                "WHERE rule_id = ?",
                (item.priority, now, item.rule_id),
            )
        conn.commit()
        return {"ok": True}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        conn.close()

@router.post("/rules/preview", dependencies=[Depends(require_api_key)])
async def preview_rules(data: RulePreview):
    state = _ensure_state()
    settings = _get_settings()
    cfg = settings.get("mail", {}).get("imap_mutations", {})
    result = evaluate_message(
        state,
        data.message,
        preview=True,
        mutation_context={
            "mode": _resolve_mode(settings),
            "config": cfg,
            "dry_run": bool(cfg.get("dry_run_default", True)),
        },
    )
    return {
        "matched_conditions": result.matched_conditions,
        "planned_actions": result.planned_actions,
        "would_skip_ai": result.would_skip_ai,
        "continue_to_classifier": result.continue_to_classifier,
        "route_to_pdf_pipeline": result.route_to_pdf_pipeline,
        "active_actions": sorted(ACTIVE_ACTIONS),
        "allowed_operators": sorted(ALLOWED_OPERATORS),
    }

@router.get(
    "/accounts/{account_id}/imap-capabilities",
    dependencies=[Depends(require_api_key)],
)
async def get_imap_capabilities(
        account_id: str,
        folder: str = Query("INBOX", min_length=1, max_length=500),
        target_folder: Optional[str] = Query(None, max_length=500)):
    account = _find_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    payload = dict(account)
    payload["folder"] = folder
    if target_folder:
        payload["target_folder"] = target_folder
    caps = probe_capabilities(payload).to_dict()
    _ensure_state().upsert_imap_capability_cache(
        account_id=account_id,
        folder=folder,
        uidvalidity=caps.get("uidvalidity"),
        capabilities=caps.get("capabilities") or [],
        supports_store_flags=caps.get("supports_store_flags"),
        supports_move=caps.get("supports_move"),
        supports_create_folder=(
            caps.get("supports_create_folder")
            if caps.get("supports_create_folder") is not None
            else caps.get("create_supported")
        ),
        supports_gmail_labels=caps.get("supports_gmail_labels"),
        source="live",
        status=caps.get("status") or "ok",
        error=caps.get("error"),
    )
    return caps

@router.get(
    "/imap-mutations/readiness",
    dependencies=[Depends(require_api_key)],
)
async def imap_mutation_readiness():
    state = _ensure_state()
    settings = _get_settings()
    cfg = _mutation_cfg(settings)
    summaries = []
    for account in _get_config_accounts():
        account_id = account.get("id") or account.get("name") or account.get("email")
        for folder in account.get("folders") or ["INBOX"]:
            cache = state.get_imap_capability_cache(str(account_id), str(folder))
            folder_state = state.get_imap_folder_state(str(account_id), str(folder))
            blockers = []
            if not account.get("enabled", True):
                blockers.append("account disabled")
            if cfg["require_capability_cache"] and not cache:
                blockers.append("capability cache missing")
            if cfg["require_uidvalidity_match"] and not folder_state.get("uidvalidity"):
                blockers.append("UIDVALIDITY missing")
            summaries.append({
                "account_id": account_id,
                "folder": folder,
                "account_enabled": bool(account.get("enabled", True)),
                "folder_state": folder_state,
                "capability_cache": cache,
                "blockers": blockers,
            })
    return {
        "readiness_only": True,
        "live_mutation_disabled": not cfg["enabled"],
        "dry_run_default": cfg["dry_run_default"],
        "config": cfg,
        "accounts": summaries,
    }

@router.post(
    "/messages/{message_id}/mutation-preview",
    dependencies=[Depends(require_api_key)],
)
async def mutation_preview(message_id: str, data: MutationPreview):
    validate_action_type(data.action_type)
    if data.action_type not in {
        "move_to_folder", "add_label", "mark_read", "mark_unread",
        "mark_flagged", "unmark_flagged",
    }:
        raise HTTPException(
            status_code=400,
            detail="Action is not an IMAP mutation action.",
        )
    state = _ensure_state()
    source = state.find_ai_reprocess_source(message_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail="Message metadata is not available for mutation preview.",
        )
    settings = _get_settings()
    cfg = settings.get("mail", {}).get("imap_mutations", {})
    mode = _resolve_mode(settings)
    dry_run = data.dry_run
    if dry_run is None:
        dry_run = bool(cfg.get("dry_run_default", True))
    gate = "planned"
    if mode != "live":
        gate = "mode_blocked"
    elif not bool(cfg.get("enabled", False)):
        gate = "mutation_disabled"
    elif dry_run:
        gate = "dry_run"
    return {
        "message_id": message_id,
        "planned_actions": [{
            "action_type": data.action_type,
            "target": data.target,
            "value": data.value_json,
            "status": gate,
        }],
        "gate": {
            "mode": mode,
            "mutation_enabled": bool(cfg.get("enabled", False)),
            "dry_run": dry_run,
            "status": gate,
        },
        "message": {
            "account_id": source.get("account_id"),
            "folder": source.get("folder"),
            "imap_uid": source.get("imap_uid"),
            "uidvalidity": source.get("uidvalidity"),
        },
        "dry_run_plan": build_dry_run_mutation_plan(
            data.action_type,
            account_id=source.get("account_id"),
            folder=source.get("folder"),
            uid=source.get("imap_uid"),
            uidvalidity=source.get("uidvalidity"),
            target=data.target,
            cfg=_mutation_cfg(settings),
            mode=mode,
        ),
    }

@router.get(
    "/messages/{message_id}/processing-events",
    dependencies=[Depends(require_api_key)],
)
async def get_processing_events(message_id: str):
    conn = _connect_rw()
    try:
        rows = conn.execute(
            "SELECT id, message_id, account_id, bridge_id, rule_id, "
            "action_type, event_type, outcome, details_json, created_at "
            "FROM mail_processing_events "
            "WHERE message_id = ? ORDER BY id ASC",
            (message_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "details_json": (
                    json.loads(row["details_json"])
                    if row["details_json"] else None
                ),
            }
            for row in rows
        ]
    finally:
        conn.close()

@router.get("/processing-events", dependencies=[Depends(require_api_key)])
async def list_processing_events(limit: int = Query(50, ge=1, le=200)):
    conn = _connect_rw()
    try:
        rows = conn.execute(
            "SELECT id, message_id, account_id, bridge_id, rule_id, "
            "action_type, event_type, outcome, details_json, created_at "
            "FROM mail_processing_events "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                **dict(row),
                "details_json": (
                    json.loads(row["details_json"])
                    if row["details_json"] else None
                ),
            }
            for row in rows
        ]
    finally:
        conn.close()

@router.get("/ai/settings", dependencies=[Depends(require_api_key)])
async def get_ai_settings():
    try:
        return ConfigManager(state=_ensure_state()).get_ai_settings()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.put("/ai/settings", dependencies=[Depends(require_api_key)])
async def put_ai_settings(data: AiSettingsPatch):
    try:
        payload = data.model_dump(exclude_unset=True)
        return ConfigManager(state=_ensure_state()).update_ai_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/ai/test", dependencies=[Depends(require_api_key)])
async def test_ai(data: AiTestRequest):
    try:
        settings = ConfigManager(state=_ensure_state()).get_ai_settings()
        item = {
            "sender": data.sender,
            "subject": data.subject,
            "body_text": data.body,
            "received_at": data.received_at,
            "account_id": data.account_id,
        }
        result = classify_with_ollama(item, settings)
        return result.model_dump()
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": "AI output failed validation",
                     "errors": exc.errors()},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/ai/triggers", dependencies=[Depends(require_api_key)])
async def list_ai_triggers():
    return _ensure_state().list_ai_triggers()

@router.post("/ai/triggers", dependencies=[Depends(require_api_key)])
async def create_ai_trigger(data: AiTriggerIn):
    try:
        return _ensure_state().create_ai_trigger(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.patch(
    "/ai/triggers/{trigger_id}",
    dependencies=[Depends(require_api_key)],
)
async def patch_ai_trigger(trigger_id: str, data: AiTriggerPatch):
    try:
        payload = data.model_dump(exclude_unset=True)
        updated = _ensure_state().update_ai_trigger(trigger_id, payload)
        if not updated:
            raise HTTPException(status_code=404, detail="AI trigger not found")
        return updated
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.delete(
    "/ai/triggers/{trigger_id}",
    dependencies=[Depends(require_api_key)],
)
async def delete_ai_trigger(trigger_id: str):
    if not _ensure_state().delete_ai_trigger(trigger_id):
        raise HTTPException(status_code=404, detail="AI trigger not found")
    return {"ok": True}

@router.post("/ai/triggers/preview", dependencies=[Depends(require_api_key)])
async def preview_ai_triggers(data: AiTriggerPreview):
    state = _ensure_state()
    try:
        classification = _classification_for_trigger_preview(state, data)
        results = state.preview_ai_triggers(classification)
        matched = [r for r in results if r.get("matched")]
        return {
            "matched": bool(matched),
            "results": results,
            "matched_conditions": (
                matched[0]["matched_conditions"] if matched else []),
            "planned_actions": (
                matched[0]["planned_actions"] if matched else []),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get(
    "/messages/{message_id}/ai-triggers",
    dependencies=[Depends(require_api_key)],
)
async def get_message_ai_triggers(message_id: str):
    return _ensure_state().ai_trigger_events_for_message(message_id)

@router.get("/approvals", dependencies=[Depends(require_api_key)])
async def list_approvals(
        status: Optional[str] = Query("pending", max_length=50),
        execution_state: Optional[str] = Query(None, max_length=50),
        include_archived: bool = Query(False),
        risk_level: Optional[str] = Query(None, max_length=50),
        source_type: Optional[str] = Query(None, max_length=50),
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0)):
    _validate_approval_filters(status, execution_state, risk_level)
    state = _ensure_state()
    settings = _get_settings()
    approval_cfg = _approval_settings(settings)
    approvals = state.list_action_approvals(
        status=status,
        source_type=source_type,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
        expiry_hours=approval_cfg["approval_expiry_hours"],
    )
    rows = [
        _approval_response(state, approval, settings)
        for approval in approvals
    ]
    if execution_state:
        rows = [
            row for row in rows
            if row.get("execution_state") == execution_state
        ]
    if risk_level:
        rows = [
            row for row in rows
            if row.get("risk_level") == risk_level
        ]
    return rows

@router.get(
    "/approvals/cleanup/preview",
    dependencies=[Depends(require_api_key)],
)
async def preview_approval_cleanup():
    return _approval_cleanup_preview(_ensure_state(), _get_settings())

@router.post(
    "/approvals/cleanup",
    dependencies=[Depends(require_api_key)],
)
async def cleanup_approvals(data: ApprovalCleanupRequest):
    state = _ensure_state()
    settings = _get_settings()
    cfg = _approval_settings(settings)
    preview = _approval_cleanup_preview(state, settings)
    if not cfg["cleanup_enabled"] and not data.force:
        return {
            **preview,
            "cleanup_ran": False,
            "disabled": True,
            "expired_ids": [],
            "archived_ids": [],
            "hard_deleted_ids": [],
            "notes": preview["notes"] + [
                "POST again with force=true for an explicit manual cleanup run."
            ],
        }
    result = state.cleanup_action_approvals(
        expire_after_hours=cfg["auto_expire_pending_after_hours"],
        archive_after_days=cfg["archive_terminal_after_days"],
        retain_audit_days=cfg["retain_audit_days"],
        hard_delete=False,
    )
    return {
        "cleanup_ran": True,
        "forced": data.force and not cfg["cleanup_enabled"],
        "expired_count": len(result["expired_ids"]),
        "archived_count": len(result["archived_ids"]),
        "hard_deleted_count": 0,
        **result,
        "notes": [
            "Cleanup executed explicit status/archive transitions only.",
            "Started/stuck approvals were excluded.",
            "Hard delete is disabled in Phase 4D.4.",
        ],
    }

@router.get(
    "/approvals/export",
    dependencies=[Depends(require_api_key)],
)
async def export_approvals(
        format: str = Query("json", pattern="^json$"),
        status: Optional[str] = Query(None, max_length=50),
        execution_state: Optional[str] = Query(None, max_length=50),
        created_from: Optional[str] = Query(None, max_length=80),
        created_to: Optional[str] = Query(None, max_length=80),
        include_archived: bool = Query(False),
        include_events: bool = Query(True),
        limit: int = Query(500, ge=1, le=1000),
        offset: int = Query(0, ge=0)):
    _validate_approval_filters(status, execution_state, None)
    state = _ensure_state()
    settings = _get_settings()
    approval_cfg = _approval_settings(settings)
    approvals = state.list_action_approvals(
        status=status,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
        expiry_hours=approval_cfg["approval_expiry_hours"],
    )
    exported = []
    for approval in approvals:
        if not _date_in_range(
                approval.get("created_at") or approval.get("requested_at"),
                created_from, created_to):
            continue
        row = _approval_response(
            state, approval, settings, include_events=include_events)
        if execution_state and row.get("execution_state") != execution_state:
            continue
        item = {"approval": row}
        if include_events:
            item["events"] = row.get("events", [])
        exported.append(item)
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format": format,
        "filters": {
            "status": status,
            "execution_state": execution_state,
            "created_from": created_from,
            "created_to": created_to,
            "include_archived": include_archived,
            "include_events": include_events,
            "limit": limit,
            "offset": offset,
        },
        "count": len(exported),
        "approvals": exported,
    }

@router.get(
    "/approvals/{approval_id}",
    dependencies=[Depends(require_api_key)],
)
async def get_approval(approval_id: str):
    state = _ensure_state()
    approval = state.get_action_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    return _approval_response(
        state, approval, _get_settings(), include_events=True)

@router.post(
    "/approvals/{approval_id}/archive",
    dependencies=[Depends(require_api_key)],
)
async def archive_approval(
        approval_id: str, data: ApprovalArchiveRequest | None = None):
    state = _ensure_state()
    try:
        approval = state.archive_action_approval(
            approval_id,
            decided_by=(data.decided_by if data else None) or "operator",
        )
        return _approval_response(
            state, approval, _get_settings(), include_events=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post(
    "/approvals/{approval_id}/unarchive",
    dependencies=[Depends(require_api_key)],
)
async def unarchive_approval(
        approval_id: str, data: ApprovalArchiveRequest | None = None):
    state = _ensure_state()
    try:
        approval = state.unarchive_action_approval(
            approval_id,
            decided_by=(data.decided_by if data else None) or "operator",
        )
        return _approval_response(
            state, approval, _get_settings(), include_events=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.get(
    "/approvals/{approval_id}/events",
    dependencies=[Depends(require_api_key)],
)
async def get_approval_events(approval_id: str):
    state = _ensure_state()
    approval = state.get_action_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    return state.approval_events(approval)

@router.post(
    "/approvals/{approval_id}/approve",
    dependencies=[Depends(require_api_key)],
)
async def approve_approval(approval_id: str, data: ApprovalDecision):
    state = _ensure_state()
    approval_cfg = _approval_settings(_get_settings())
    state.expire_pending_approvals(approval_cfg["approval_expiry_hours"])
    try:
        approval = state.approve_action_approval(
            approval_id,
            decided_by=data.decided_by or "operator",
            decision_note=data.decision_note,
        )
        return _approval_response(state, approval, _get_settings())
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post(
    "/approvals/{approval_id}/reject",
    dependencies=[Depends(require_api_key)],
)
async def reject_approval(approval_id: str, data: ApprovalDecision):
    state = _ensure_state()
    approval_cfg = _approval_settings(_get_settings())
    state.expire_pending_approvals(approval_cfg["approval_expiry_hours"])
    try:
        approval = state.reject_action_approval(
            approval_id,
            decided_by=data.decided_by or "operator",
            decision_note=data.decision_note,
        )
        return _approval_response(state, approval, _get_settings())
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post(
    "/approvals/{approval_id}/expire",
    dependencies=[Depends(require_api_key)],
)
async def expire_approval(approval_id: str):
    state = _ensure_state()
    try:
        approval = state.expire_action_approval(approval_id)
        return _approval_response(state, approval, _get_settings())
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post(
    "/approvals/{approval_id}/execute",
    dependencies=[Depends(require_api_key)],
)
async def execute_approval(approval_id: str):
    state = _ensure_state()
    try:
        approval = state.mark_approval_execution_started(approval_id)
        result = _execute_approved_action(state, approval)
        approval = state.finish_action_approval_execution(
            approval_id,
            status=result["status"],
            execution_status=result["execution_status"],
            result=result,
        )
        return _approval_response(
            state, approval, _get_settings(), include_events=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        approval = state.get_action_approval(approval_id)
        if approval and approval.get("status") == "approved":
            failed = state.finish_action_approval_execution(
                approval_id,
                status="failed",
                execution_status="failed",
                result={"error": str(exc)[:500]},
            )
            return _approval_response(
                state, failed, _get_settings(), include_events=True)
        raise HTTPException(status_code=500, detail=str(exc))

@router.post(
    "/approvals/{approval_id}/mark-failed",
    dependencies=[Depends(require_api_key)],
)
async def mark_approval_failed(approval_id: str, data: ApprovalMarkFailed):
    state = _ensure_state()
    approval_cfg = _approval_settings(_get_settings())
    try:
        approval = state.mark_stale_started_approval_failed(
            approval_id,
            stale_after_minutes=approval_cfg["started_stale_after_minutes"],
            decided_by=data.decided_by or "operator",
            reason=data.reason or "Execution started but did not finish",
        )
        return _approval_response(
            state, approval, _get_settings(), include_events=True)
    except KeyError:
        raise HTTPException(status_code=404, detail="Approval not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post(
    "/messages/{message_id}/reprocess",
    dependencies=[Depends(require_api_key)],
)
async def reprocess_message(message_id: str):
    state = _ensure_state()
    source = state.find_ai_reprocess_source(message_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail="Message body is not available for AI reprocess.",
        )
    if not source.get("body_text"):
        raise HTTPException(
            status_code=422,
            detail="Message body is empty; fetch a fresh message before reprocessing.",
        )
    settings = ConfigManager(state=state).get_ai_settings()
    queue_id = state.enqueue_manual_ai_reprocess(
        source, max_body_chars=int(settings["max_body_chars"]))
    return {"queue_id": queue_id, "status": "pending"}

@router.get("/recent", dependencies=[Depends(require_api_key)])
async def get_recent(limit: int = Query(20, ge=1, le=200)):
    if not _db_exists():
        return []
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT pm.bridge_id, pm.message_id, pm.processed_at, "
                "       pm.category, pm.urgency, pm.provider, "
                "       pm.alert_sent, pm.summary, "
                "       COALESCE(pm.status, 'processed') AS status, "
                "       COALESCE(pm.source, 'bridge') AS source, "
                "       q.id AS ai_queue_id, q.status AS ai_status, "
                "       q.last_error AS ai_last_error, "
                "       c.category AS ai_category, "
                "       c.urgency_score AS ai_urgency_score, "
                "       c.confidence AS ai_confidence, "
                "       c.summary AS ai_summary "
                "FROM processed_messages pm "
                "LEFT JOIN mail_ai_queue q ON q.message_id = pm.message_id "
                "  AND q.id = ("
                "    SELECT MAX(q2.id) FROM mail_ai_queue q2 "
                "    WHERE q2.message_id = pm.message_id"
                "  ) "
                "LEFT JOIN mail_ai_classifications c ON c.queue_id = q.id "
                "ORDER BY pm.processed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []

@router.get("/accounts", dependencies=[Depends(require_api_key)])
async def get_accounts_health():
    # Source of truth is settings.toml
    config_accounts = _get_config_accounts()
    
    # Status is from agent.db
    status_map = {}
    if _db_exists():
        try:
            conn = _connect()
            try:
                if _table_exists(conn, "imap_accounts"):
                    rows = conn.execute("SELECT * FROM imap_accounts").fetchall()
                    for r in rows:
                        # Runtime table uses account_name as key (which is name in config)
                        status_map[r["account_name"]] = dict(r)
            finally:
                conn.close()
        except Exception:
            pass

    # Merge
    merged = []
    for acct in config_accounts:
        if acct.get("deleted_at") or _is_placeholder_account(acct):
            continue
            
        name = acct.get("name") or acct.get("id") or acct.get("email")
        health = status_map.get(name, {})
        
        merged.append({
            "id": acct.get("id") or name,
            "name": name,
            "email": acct.get("email"),
            "provider": acct.get("provider", "gmail"),
            "enabled": acct.get("enabled", True),
            "status": health.get("status", "inactive"),
            "last_success_at": health.get("last_success_at"),
            "last_error": health.get("last_error"),
        })
    return merged

@router.post("/accounts/test", dependencies=[Depends(require_api_key)])
async def test_account(data: AccountCreate):
    try:
        _test_imap_login(data.email, data.app_password)
        return {"ok": True, "message": "IMAP connection successful."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error during test: {e}")

@router.post("/accounts", dependencies=[Depends(require_api_key)])
async def add_account(data: AccountCreate):
    try:
        # 1. Test connection first
        _test_imap_login(data.email, data.app_password)
        
        # 2. Persist
        # We need AgentState to log events, but for now we'll pass None if not easy
        from .state import AgentState
        state = AgentState()
        cm = ConfigManager(state=state)
        
        cm.add_account({
            "display_name": data.display_name,
            "email": data.email,
            "provider": "gmail"
        }, _normalize_app_password(data.app_password))
        
        return {"ok": True, "message": "Account added successfully."}
    except DuplicateAccountError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SoftDeletedAccountError as e:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(e),
                "error_code": "soft_deleted_exists",
                "account_id": e.account_id
            }
        )
    except Exception as e:
        logger.exception("Failed to add account")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/accounts/{account_id}", dependencies=[Depends(require_api_key)])
async def update_account(account_id: str, data: AccountUpdate):
    try:
        from .state import AgentState
        state = AgentState()
        cm = ConfigManager(state=state)
        
        updates = data.model_dump(exclude_unset=True)
        pwd = updates.pop("app_password", None)
        if pwd is not None:
            pwd = _normalize_app_password(pwd)
        
        if pwd:
            # Plan: "Re-tests credentials if email or password changes."
            # Find email first
            config_accounts = _get_config_accounts()
            email = None
            for acct in config_accounts:
                if acct.get("id") == account_id:
                    email = acct.get("email")
                    break
            
            if email:
                try:
                    _test_imap_login(email, pwd)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=f"New credentials failed test: {e}")

        cm.update_account(account_id, updates, app_password=pwd)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/accounts/{account_id}/enabled", dependencies=[Depends(require_api_key)])
async def patch_account_enabled(account_id: str, data: AccountEnabledPatch):
    try:
        from .state import AgentState
        state = AgentState()
        cm = ConfigManager(state=state)
        cm.update_account(account_id, {"enabled": data.enabled})
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/accounts/{account_id}/reactivate", dependencies=[Depends(require_api_key)])
async def reactivate_account(account_id: str):
    try:
        from .state import AgentState
        state = AgentState()
        cm = ConfigManager(state=state)
        cm.reactivate_account(account_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/accounts/{account_id}", dependencies=[Depends(require_api_key)])
async def delete_account(account_id: str, purge_secret: bool = Query(False)):
    try:
        from .state import AgentState
        state = AgentState()
        cm = ConfigManager(state=state)
        cm.delete_account(account_id, purge_secret=purge_secret)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/config/reload", dependencies=[Depends(require_api_key)])
async def reload_config():
    # In a simple implementation, we just set a flag in the DB or a file
    # for the orchestrator to pick up.
    from .state import AgentState
    state = AgentState()
    state.set_bool_flag("config_reload_pending", True)
    return {"ok": True, "message": "Reload requested."}

@router.post("/run", dependencies=[Depends(require_api_key)])
async def trigger_run(force: bool = Query(False)):
    """Trigger a scan cycle by proxying to the agent's internal trigger endpoint."""
    try:
        import httpx
        agent_url = os.environ.get("AGENT_INTERNAL_URL", "http://localhost:8080")
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{agent_url}/trigger", params={"force": "1" if force else "0"})
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to trigger agent: {e}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_summary() -> dict[str, Any]:
    return {
        "total_processed": 0,
        "urgent_count": 0,
        "drafts_created": 0,
        "avg_priority": 0.0,
        "source_split": {"gmail": 0, "outlook": 0},
        "classification": {},
        "actions": {
            "drafts_created":    0,
            "labels_applied":    0,
            "imessage_alerts":   0,
            "important_count":   0,
            "reply_needed_count": 0,
        },
        "pdf_attachments": 0,
        "mode": "draft_only",
    }
