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
    "move_to_folder",
    "add_label",
    "mark_read",
    "mark_unread",
    "mark_flagged",
    "unmark_flagged",
}

MUTATION_ACTIONS = {
    "move_to_folder",
    "add_label",
    "mark_read",
    "mark_unread",
    "mark_flagged",
    "unmark_flagged",
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
                     preview: bool = False,
                     mutation_context: dict[str, Any] | None = None,
                     rule_id: int | None = None,
                     include_disabled: bool = False,
                     ) -> RuleEvaluation:
    result = RuleEvaluation()
    rules = _load_rules(
        state,
        message.get("imap_account"),
        rule_id=rule_id,
        include_disabled=include_disabled,
    )

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
        if rule.get("stop_processing") and not any(
            action.get("action_type") == "stop_processing"
            for action in actions
        ):
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

            if action_type in MUTATION_ACTIONS:
                if preview:
                    result.planned_actions[-1].update(
                        _mutation_preview_metadata(
                            message, action, mutation_context))
                    continue
                outcome = _execute_mutation_action(
                    state, message, rule, action, mutation_context)
                if outcome.get("event_written"):
                    result.events_written += int(outcome["event_written"])
                result.actions_executed.append(
                    {**planned, "status": outcome.get("status")})
                continue

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
            f"Unsupported mail rule action_type: {action_type}")
    return action_type


def validate_operator(operator: str) -> str:
    if operator not in ALLOWED_OPERATORS:
        raise ValueError(f"Unsupported rule operator: {operator}")
    return operator


def _load_rules(
        state,
        account_id: str | None,
        *,
        rule_id: int | None = None,
        include_disabled: bool = False,
        ) -> list[dict[str, Any]]:
    with state._connect() as conn:
        conn.row_factory = None
        clauses = ["(account_id IS NULL OR account_id = ?)"]
        params: list[Any] = [account_id]
        if not include_disabled:
            clauses.insert(0, "enabled = 1")
        if rule_id is not None:
            clauses.append("rule_id = ?")
            params.append(rule_id)
        rows = conn.execute(f"""
            SELECT rule_id, account_id, name, priority, enabled, match_type
            FROM mail_rules
            WHERE {' AND '.join(clauses)}
            ORDER BY priority ASC, rule_id ASC
        """, params).fetchall()

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


def explain_condition_match(
        condition: dict[str, Any],
        message: dict[str, Any],
        ) -> dict[str, Any]:
    field = condition.get("field")
    actual = _field_value(message, str(field or ""))
    return {
        "field": field,
        "operator": condition.get("operator"),
        "expected": _explain_expected(condition),
        "actual": _truncate_actual(field, actual),
        "matched": _condition_matches(condition, message),
        "case_sensitive": bool(condition.get("case_sensitive")),
    }


def _field_value(message: dict[str, Any], field_name: str) -> Any:
    aliases = {
        "from_email": "sender_email",
        "from": "sender_email",
        "sender": "sender_email",
        "body": "body_text",
        "folder": "imap_folder",
        "account": "imap_account",
        "has_attachment": "attachments",
    }
    if field_name in {"from_domain", "sender_domain"}:
        explicit_domain = message.get(field_name) or message.get("sender_domain")
        if explicit_domain:
            return explicit_domain
        sender_email = str(message.get("sender_email") or "").strip()
        return sender_email.rsplit("@", 1)[1] if "@" in sender_email else None
    key = aliases.get(field_name, field_name)
    value = message.get(key)
    if field_name == "has_attachment":
        return "true" if value else "false"
    return value


def _explain_expected(condition: dict[str, Any]) -> Any:
    expected_json = _decode_json(condition.get("value_json"))
    if condition.get("operator") == "in" and expected_json is not None:
        return expected_json
    return condition.get("value")


def _truncate_actual(field_name: Any, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    text = str(value)
    field = str(field_name or "")
    if field in {"subject"}:
        limit = 180
    elif field in {"body", "body_text"}:
        limit = 240
    elif field in {"sender_email", "from_email", "from", "sender"}:
        limit = 160
    else:
        limit = 160
    return _truncate(text, limit)


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


def _execute_mutation_action(
        state, message: dict[str, Any], rule: dict[str, Any],
        action: dict[str, Any], mutation_context: dict[str, Any] | None
        ) -> dict[str, Any]:
    mutation_context = mutation_context or {}
    mode = _safe_mode(mutation_context.get("mode"))
    cfg = _mutation_cfg(mutation_context.get("config"))
    action_type = action["action_type"]
    target = _mutation_target(action)
    audit_base = _mutation_audit_payload(
        message, rule, action, mode, cfg, target)
    dry_run = bool(mutation_context.get("dry_run", cfg["dry_run_default"]))
    audit_base["dry_run"] = dry_run
    events_written = 0
    _write_processing_event(
        state, message, rule, action,
        event_type="mutation:planned",
        outcome="planned",
        details=audit_base)
    events_written += 1

    if mode != "live":
        status = "mode_blocked"
        details = {**audit_base, "status": status,
                   "gate_status": status}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:mode_blocked",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    if not cfg["enabled"]:
        status = "mutation_disabled"
        details = {**audit_base, "status": status,
                   "gate_status": status}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:mutation_disabled",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    if dry_run:
        status = "dry_run"
        details = {**audit_base, "status": status,
                   "gate_status": status, "dry_run": True}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:dry_run",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    allow_gate = {
        "mark_read": "allow_mark_read",
        "mark_unread": "allow_mark_unread",
        "add_label": "allow_add_label",
        "move_to_folder": "allow_move_to_folder",
    }.get(action_type)
    if allow_gate and not cfg.get(allow_gate, False):
        status = "action_not_allowed"
        details = {**audit_base, "status": status,
                   "gate_status": status,
                   "reason": f"mail.imap_mutations.{allow_gate}=false"}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:action_not_allowed",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    if not _message_has_imap_identity(message):
        status = "identity_incomplete"
        details = {**audit_base, "status": status,
                   "gate_status": status,
                   "reason": "message lacks account/folder/UID/UIDVALIDITY"}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:identity_incomplete",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    if action_type == "add_label":
        status = "unsupported"
        details = {**audit_base, "status": status,
                   "gate_status": status,
                   "reason": "add_label live execution is not implemented"}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:unsupported",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    executor = mutation_context.get("executor")
    if executor is None:
        status = "unsupported"
        details = {**audit_base, "status": status,
                   "gate_status": status,
                   "error": "No mutation executor configured"}
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:unsupported",
            outcome=status,
            details=details)
        return {"event_written": events_written + 1, "status": status}

    try:
        raw_result = executor(action_type, message, target, dry_run=False)
        result = (
            raw_result.to_dict()
            if hasattr(raw_result, "to_dict")
            else dict(raw_result or {}))
        status = result.get("status") or "failed"
        event_status = status if status in {
            "completed", "failed", "unsupported", "uidvalidity_mismatch",
            "mutation_disabled", "dry_run", "mode_blocked",
            "action_not_allowed", "identity_incomplete"
        } else "failed"
        event_type = (
            "mutation:completed"
            if event_status == "completed"
            else "mutation:unsupported"
            if event_status == "unsupported"
            else "mutation:failed"
        )
        _write_processing_event(
            state, message, rule, action,
            event_type=event_type,
            outcome=event_status,
            details={**audit_base, "mutation_result": result,
                     "gate_status": event_status,
                     "status": event_status})
        return {"event_written": events_written + 1, "status": event_status}
    except Exception as exc:
        status = "failed"
        _write_processing_event(
            state, message, rule, action,
            event_type="mutation:failed",
            outcome=status,
            details={**audit_base, "status": status,
                     "gate_status": status,
                     "error": str(exc)[:500]})
        return {
            "event_written": events_written + 1,
            "status": status,
            "error": str(exc)[:500],
        }


def _write_processing_event(
        state, message: dict[str, Any], rule: dict[str, Any],
        action: dict[str, Any], *, event_type: str,
        outcome: str, details: dict[str, Any]) -> None:
    with state._connect() as conn:
        conn.execute("""
            INSERT INTO mail_processing_events
                (message_id, account_id, bridge_id, rule_id, action_type,
                 event_type, outcome, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            message_audit_id(message),
            message.get("imap_account"),
            message.get("bridge_id"),
            rule["rule_id"],
            action["action_type"],
            event_type,
            outcome,
            json.dumps(details, sort_keys=True),
            _now(),
        ))
        conn.commit()


def _safe_mode(mode: Any) -> str:
    text = str(mode or "").strip()
    return text if text in {"observe", "draft_only", "live"} else "draft_only"


def _mutation_cfg(raw: dict[str, Any] | None) -> dict[str, bool]:
    raw = raw or {}
    return {
        "enabled": bool(raw.get("enabled", False)),
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
        "dry_run_default": bool(raw.get("dry_run_default", True)),
    }


def _mutation_target(action: dict[str, Any]) -> Any:
    value = _decode_json(action.get("value_json"))
    if isinstance(value, dict) and value.get("target_folder"):
        return value["target_folder"]
    return action.get("target")


def _mutation_audit_payload(
        message: dict[str, Any], rule: dict[str, Any],
        action: dict[str, Any], mode: str, cfg: dict[str, bool],
        target: Any) -> dict[str, Any]:
    plan = build_dry_run_mutation_plan(
        action["action_type"],
        account_id=message.get("imap_account"),
        folder=message.get("imap_folder"),
        uid=message.get("imap_uid"),
        uidvalidity=message.get("imap_uidvalidity"),
        target=target,
        cfg=cfg,
        mode=mode,
    )
    return {
        "rule_name": rule["name"],
        "account_id": message.get("imap_account"),
        "folder": message.get("imap_folder"),
        "uidvalidity": message.get("imap_uidvalidity"),
        "uid": message.get("imap_uid"),
        "operation": action["action_type"],
        "target": target,
        "mode": mode,
        "mutation_enabled": cfg["enabled"],
        "dry_run_default": cfg["dry_run_default"],
        "dry_run_plan": plan,
        "safety_gates": plan["safety_gates"],
        "rollback_hint": plan.get("rollback_hint"),
        "mailbox_mutation_occurred": False,
    }


def _mutation_preview_metadata(
        message: dict[str, Any], action: dict[str, Any],
        mutation_context: dict[str, Any] | None) -> dict[str, Any]:
    mutation_context = mutation_context or {}
    mode = _safe_mode(mutation_context.get("mode"))
    cfg = _mutation_cfg(mutation_context.get("config"))
    dry_run = bool(mutation_context.get("dry_run", cfg["dry_run_default"]))
    target = _mutation_target(action)
    action_type = action["action_type"]
    plan = build_dry_run_mutation_plan(
        action_type,
        account_id=message.get("imap_account"),
        folder=message.get("imap_folder"),
        uid=message.get("imap_uid"),
        uidvalidity=message.get("imap_uidvalidity"),
        target=target,
        cfg=cfg,
        mode=mode,
    )

    if mode != "live":
        status = "mode_blocked"
        reason = f"agent.mode={mode}"
        would_execute = False
    elif not cfg["enabled"]:
        status = "mutation_disabled"
        reason = "mail.imap_mutations.enabled=false"
        would_execute = False
    elif dry_run:
        status = "dry_run"
        reason = "mail.imap_mutations.dry_run_default=true"
        would_execute = False
    elif not _message_has_imap_identity(message):
        status = "unsupported"
        reason = "message lacks IMAP account/folder/UID metadata"
        would_execute = False
    elif action_type == "move_to_folder" and not target:
        status = "failed"
        reason = "move_to_folder requires target"
        would_execute = False
    else:
        status = "ready"
        reason = "all static gates allow execution"
        would_execute = True

    return {
        "would_execute": would_execute,
        "gate_status": status,
        "reason": reason,
        "dry_run": dry_run,
        "mutation": True,
        "dry_run_plan": plan,
        "safety_gates": plan["safety_gates"],
        "rollback_hint": plan.get("rollback_hint"),
        "reversible": plan.get("reversible"),
    }


def build_dry_run_mutation_plan(
        action_type: str, *, account_id: Any, folder: Any, uid: Any,
        uidvalidity: Any, target: Any = None,
        cfg: dict[str, bool] | None = None, mode: str = "draft_only",
        extra_gates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cfg = _mutation_cfg(cfg)
    operation, rollback_hint, reversible = _mutation_operation(
        action_type, target)
    gates = [
        {
            "gate": "agent_mode_live",
            "status": "passed" if mode == "live" else "blocked",
            "reason": mode,
        },
        {
            "gate": "imap_mutations_enabled",
            "status": "passed" if cfg["enabled"] else "blocked",
            "reason": str(cfg["enabled"]).lower(),
        },
        {
            "gate": "dry_run_default",
            "status": "blocked" if cfg["dry_run_default"] else "passed",
            "reason": str(cfg["dry_run_default"]).lower(),
        },
    ]
    allow_gate = {
        "mark_read": "allow_mark_read",
        "mark_unread": "allow_mark_unread",
        "add_label": "allow_add_label",
        "move_to_folder": "allow_move_to_folder",
    }.get(action_type)
    if allow_gate:
        gates.append({
            "gate": allow_gate,
            "status": "passed" if cfg.get(allow_gate) else "blocked",
            "reason": str(bool(cfg.get(allow_gate))).lower(),
        })
    if extra_gates:
        gates.extend(extra_gates)
    return {
        "action_type": action_type,
        "account_id": account_id,
        "folder": folder,
        "uid": uid,
        "uidvalidity": uidvalidity,
        "target": target,
        "operation": operation,
        "dry_run": True,
        "would_mutate": False,
        "reversible": reversible,
        "rollback_hint": rollback_hint,
        "safety_gates": gates,
    }


def _mutation_operation(action_type: str, target: Any = None) -> tuple[str, str | None, bool]:
    if action_type == "mark_read":
        return (
            r"STORE +FLAGS.SILENT (\Seen)",
            r"mark_unread using STORE -FLAGS.SILENT (\Seen)",
            True,
        )
    if action_type == "mark_unread":
        return (
            r"STORE -FLAGS.SILENT (\Seen)",
            r"mark_read using STORE +FLAGS.SILENT (\Seen)",
            True,
        )
    if action_type == "add_label":
        label = str(target or "<label>")
        return (
            f"STORE +X-GM-LABELS.SILENT ({label})",
            f"remove Gmail label {label} if supported",
            True,
        )
    if action_type == "move_to_folder":
        folder = str(target or "<target folder>")
        return (
            f"UID MOVE/COPY to {folder}",
            "move back to original folder if UID identity can be re-established",
            True,
        )
    return action_type, None, False


def _message_has_imap_identity(message: dict[str, Any]) -> bool:
    return all(
        message.get(key) is not None
        for key in (
            "imap_account",
            "imap_folder",
            "imap_uidvalidity",
            "imap_uid",
        )
    )


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
