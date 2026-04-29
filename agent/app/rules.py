from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("agent.rules")

ACTIVE_ACTIONS = {
    "mark_pending_alert",
    "skip_ai_inference",
    "add_to_needs_reply",
    "route_to_pdf_pipeline",
    "notify_dashboard",
    "stop_processing",
}

ALLOWED_OPERATORS = {
    "equals",
    "contains",
    "starts_with",
    "ends_with",
    "regex",
    "exists",
    "domain_equals",
    "in",
}


@dataclass
class RuleEvaluation:
    matched_conditions: list[dict[str, Any]] = field(default_factory=list)
    planned_actions: list[dict[str, Any]] = field(default_factory=list)
    actions_executed: list[dict[str, Any]] = field(default_factory=list)
    continue_to_classifier: bool = True
    enqueue_ai: bool = True
    route_to_pdf_pipeline: bool = False
    stopped: bool = False
    events_written: int = 0
    needs_reply_written: int = 0

    @property
    def would_skip_ai(self) -> bool:
        return not self.enqueue_ai


def message_audit_id(message: dict[str, Any]) -> str:
    return (
        message.get("message_key")
        or message.get("fallback_message_key")
        or message.get("message_id")
        or message.get("bridge_id")
        or _body_hash(message)
    )


def evaluate_message(state, message: dict[str, Any],
                     preview: bool = False) -> RuleEvaluation:
    result = RuleEvaluation()
    rules = _load_rules(state, message.get("imap_account"))

    for rule in rules:
        conditions = rule["conditions"]
        matches = [
            _condition_matches(condition, message)
            for condition in conditions
        ]
        rule_matched = (
            all(matches) if rule["match_type"] == "ALL"
            else any(matches)
        )
        if not conditions:
            rule_matched = True

        result.matched_conditions.append({
            "rule_id": rule["rule_id"],
            "name": rule["name"],
            "matched": rule_matched,
            "conditions": [
                {**condition, "matched": matched}
                for condition, matched in zip(conditions, matches)
            ],
        })

        if not rule_matched:
            continue

        actions = list(rule["actions"])
        if rule.get("stop_processing"):
            actions.append({
                "id": None,
                "rule_id": rule["rule_id"],
                "action_type": "stop_processing",
                "target": None,
                "value_json": None,
                "stop_processing": 1,
            })

        for action in actions:
            action_type = action["action_type"]
            if action_type not in ACTIVE_ACTIONS:
                planned = {
                    "rule_id": rule["rule_id"],
                    "action_type": action_type,
                    "status": "deferred",
                }
                result.planned_actions.append(planned)
                continue

            planned = {
                "rule_id": rule["rule_id"],
                "action_type": action_type,
                "target": action.get("target"),
                "value": _decode_json(action.get("value_json")),
            }
            result.planned_actions.append(planned)

            if action_type == "skip_ai_inference":
                result.enqueue_ai = False
            elif action_type == "route_to_pdf_pipeline":
                result.route_to_pdf_pipeline = True
            elif action_type == "stop_processing":
                result.stopped = True

            if not preview:
                outcome = _execute_action(state, message, rule, action)
                if outcome.get("event_written"):
                    result.events_written += 1
                if outcome.get("needs_reply_written"):
                    result.needs_reply_written += 1
                result.actions_executed.append(
                    {**planned, "status": "completed"})

        if rule.get("stop_processing") or result.stopped:
            result.stopped = True
            break

    return result


def validate_action_type(action_type: str) -> str:
    if action_type not in ACTIVE_ACTIONS:
        raise ValueError(
            f"Unsupported Phase 4A action_type: {action_type}")
    return action_type


def validate_operator(operator: str) -> str:
    if operator not in ALLOWED_OPERATORS:
        raise ValueError(f"Unsupported rule operator: {operator}")
    return operator


def _load_rules(state, account_id: str | None) -> list[dict[str, Any]]:
    with state._connect() as conn:
        conn.row_factory = None
        rows = conn.execute("""
            SELECT rule_id, account_id, name, priority, enabled, match_type
            FROM mail_rules
            WHERE enabled = 1
              AND (account_id IS NULL OR account_id = ?)
            ORDER BY priority ASC, rule_id ASC
        """, (account_id,)).fetchall()

        rules = []
        for row in rows:
            rule_id = row[0]
            condition_rows = conn.execute("""
                SELECT id, field, operator, value, value_json, case_sensitive
                FROM mail_rule_conditions
                WHERE rule_id = ?
                ORDER BY id ASC
            """, (rule_id,)).fetchall()
            action_rows = conn.execute("""
                SELECT id, action_type, target, value_json, stop_processing
                FROM mail_rule_actions
                WHERE rule_id = ?
                ORDER BY id ASC
            """, (rule_id,)).fetchall()
            rules.append({
                "rule_id": rule_id,
                "account_id": row[1],
                "name": row[2],
                "priority": row[3],
                "enabled": bool(row[4]),
                "match_type": row[5],
                "stop_processing": any(bool(a[4]) for a in action_rows),
                "conditions": [
                    {
                        "id": c[0],
                        "field": c[1],
                        "operator": c[2],
                        "value": c[3],
                        "value_json": c[4],
                        "case_sensitive": bool(c[5]),
                    }
                    for c in condition_rows
                ],
                "actions": [
                    {
                        "id": a[0],
                        "rule_id": rule_id,
                        "action_type": a[1],
                        "target": a[2],
                        "value_json": a[3],
                        "stop_processing": bool(a[4]),
                    }
                    for a in action_rows
                ],
            })
        return rules


def _condition_matches(condition: dict[str, Any],
                       message: dict[str, Any]) -> bool:
    operator = validate_operator(condition["operator"])
    field_value = _field_value(message, condition["field"])
    expected = condition.get("value")
    expected_json = _decode_json(condition.get("value_json"))
    case_sensitive = bool(condition.get("case_sensitive"))

    if operator == "exists":
        return field_value is not None and str(field_value) != ""

    actual = "" if field_value is None else str(field_value)
    expected_text = "" if expected is None else str(expected)
    if not case_sensitive:
        actual_cmp = actual.lower()
        expected_cmp = expected_text.lower()
    else:
        actual_cmp = actual
        expected_cmp = expected_text

    if operator == "equals":
        return actual_cmp == expected_cmp
    if operator == "contains":
        return expected_cmp in actual_cmp
    if operator == "starts_with":
        return actual_cmp.startswith(expected_cmp)
    if operator == "ends_with":
        return actual_cmp.endswith(expected_cmp)
    if operator == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.search(expected_text, actual, flags=flags) is not None
    if operator == "domain_equals":
        domain = actual_cmp.rsplit("@", 1)[-1] if "@" in actual_cmp else actual_cmp
        return domain == expected_cmp.lstrip("@")
    if operator == "in":
        values = expected_json if isinstance(expected_json, list) else []
        normalized = [str(v if case_sensitive else str(v).lower()) for v in values]
        return actual_cmp in normalized
    return False


def _field_value(message: dict[str, Any], field_name: str) -> Any:
    aliases = {
        "from": "sender_email",
        "sender": "sender_email",
        "body": "body_text",
        "folder": "imap_folder",
        "account": "imap_account",
        "has_attachment": "attachments",
    }
    key = aliases.get(field_name, field_name)
    value = message.get(key)
    if field_name == "has_attachment":
        return "true" if value else "false"
    return value


def _execute_action(state, message: dict[str, Any],
                    rule: dict[str, Any], action: dict[str, Any]) -> dict[str, bool]:
    action_type = action["action_type"]
    audit_message_id = message_audit_id(message)
    account_id = message.get("imap_account")
    bridge_id = message.get("bridge_id")
    now = _now()
    outcome = {"event_written": False, "needs_reply_written": False}

    with state._connect() as conn:
        if action_type == "add_to_needs_reply":
            existed = conn.execute(
                "SELECT 1 FROM mail_needs_reply "
                "WHERE message_id = ? AND account_id IS ?",
                (audit_message_id, account_id),
            ).fetchone() is not None
            conn.execute("""
                INSERT INTO mail_needs_reply
                    (message_id, account_id, bridge_id, sender_email,
                     subject, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                ON CONFLICT(message_id, account_id) DO UPDATE
                    SET updated_at = excluded.updated_at,
                        status = 'open'
            """, (
                audit_message_id,
                account_id,
                bridge_id,
                message.get("sender_email"),
                message.get("subject"),
                now,
                now,
            ))
            outcome["needs_reply_written"] = not existed
            logger.info(
                "phase4a_needs_reply_%s message_id=%s account_id=%s "
                "bridge_id=%s sender_email=%s subject=%s",
                "skipped_existing" if existed else "inserted",
                audit_message_id,
                account_id,
                bridge_id,
                message.get("sender_email"),
                _truncate(message.get("subject"), 120),
            )

        conn.execute("""
            INSERT INTO mail_processing_events
                (message_id, account_id, bridge_id, rule_id, action_type,
                 event_type, outcome, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?)
        """, (
            audit_message_id,
            account_id,
            bridge_id,
            rule["rule_id"],
            action_type,
            f"rule_action:{action_type}",
            json.dumps({
                "rule_name": rule["name"],
                "target": action.get("target"),
                "value": _decode_json(action.get("value_json")),
            }),
            now,
        ))
        outcome["event_written"] = True
        logger.info(
            "phase4a_event_inserted message_id=%s account_id=%s "
            "bridge_id=%s rule_id=%s action_type=%s event_type=%s",
            audit_message_id,
            account_id,
            bridge_id,
            rule["rule_id"],
            action_type,
            f"rule_action:{action_type}",
        )
        conn.commit()
    return outcome


def _decode_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _body_hash(message: dict[str, Any]) -> str:
    body = message.get("body_text") or message.get("snippet") or ""
    return hashlib.sha256(str(body).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    return text[:limit]
