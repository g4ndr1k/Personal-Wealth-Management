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
from typing import Any, Optional
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, EmailStr, ValidationError

from .state import AgentState, apply_sqlite_pragmas
from .rules import (
    ACTIVE_ACTIONS,
    ALLOWED_OPERATORS,
    MUTATION_ACTIONS,
    evaluate_message,
    validate_action_type,
    validate_operator,
)
from .imap_source import probe_capabilities
from .config_manager import (
    ConfigManager,
    DuplicateAccountError,
    SoftDeletedAccountError,
)
from .ai_worker import (
    MailAiClassification,
    classify_with_ollama,
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

class MutationPreview(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=100)
    target: Optional[str] = Field(None, max_length=500)
    value_json: Optional[Any] = None
    dry_run: Optional[bool] = None

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
        if action.action_type in MUTATION_ACTIONS - {"move_to_folder"} and target:
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
    return probe_capabilities(payload).to_dict()

@router.post(
    "/messages/{message_id}/mutation-preview",
    dependencies=[Depends(require_api_key)],
)
async def mutation_preview(message_id: str, data: MutationPreview):
    validate_action_type(data.action_type)
    if data.action_type not in {
        "move_to_folder", "mark_read", "mark_unread",
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
