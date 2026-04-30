from __future__ import annotations

from typing import Any


TRIGGER_FIELDS = {
    "category",
    "urgency_score",
    "confidence",
    "needs_reply",
    "summary",
    "reason",
}

TEXT_OPERATORS = {"equals", "in", "contains"}
NUMBER_OPERATORS = {">=", "<=", "="}
BOOL_OPERATORS = {"equals"}

ALLOWED_TRIGGER_ACTIONS = {
    "notify_dashboard",
    "send_imessage",
    "move_to_folder",
    "mark_read",
    "mark_unread",
    "mark_flagged",
    "unmark_flagged",
    "add_to_needs_reply",
}

DANGEROUS_TRIGGER_ACTIONS = {
    "delete",
    "expunge",
    "reply",
    "auto_reply",
    "forward",
    "unsubscribe",
    "webhook",
    "external_webhook",
}

PREVIEW_ONLY_REASON = "Phase 4C.3A preview-only"


def validate_trigger_conditions(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("conditions_json must be an object")
    match_type = str(payload.get("match_type", "ALL")).upper()
    if match_type not in {"ALL", "ANY"}:
        raise ValueError("conditions_json.match_type must be ALL or ANY")
    conditions = payload.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValueError("conditions_json.conditions must be a list")
    normalized = []
    for condition in conditions:
        if not isinstance(condition, dict):
            raise ValueError("Each trigger condition must be an object")
        field = str(condition.get("field", "")).strip()
        operator = str(condition.get("operator", "")).strip()
        if field not in TRIGGER_FIELDS:
            raise ValueError(f"Unsupported AI trigger field: {field}")
        _validate_operator(field, operator)
        normalized.append({
            "field": field,
            "operator": operator,
            "value": condition.get("value"),
        })
    return {"match_type": match_type, "conditions": normalized}


def validate_trigger_actions(payload: Any) -> list[dict[str, Any]]:
    actions = payload.get("actions") if isinstance(payload, dict) else payload
    if not isinstance(actions, list):
        raise ValueError("actions_json must be a list or object with actions")
    normalized = []
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("Each trigger action must be an object")
        action_type = str(action.get("action_type", "")).strip()
        if action_type in DANGEROUS_TRIGGER_ACTIONS:
            raise ValueError(f"Dangerous AI trigger action rejected: {action_type}")
        if action_type not in ALLOWED_TRIGGER_ACTIONS:
            raise ValueError(f"Unsupported AI trigger action: {action_type}")
        target = action.get("target")
        if action_type == "move_to_folder" and not str(target or "").strip():
            raise ValueError("move_to_folder requires a non-empty target")
        if action_type != "move_to_folder":
            target = None
        normalized.append({
            "action_type": action_type,
            "target": target,
            "value": action.get("value"),
            "dry_run": True,
            "would_execute": False,
            "reason": PREVIEW_ONLY_REASON,
        })
    return normalized


def evaluate_trigger(
        trigger: dict[str, Any],
        classification: dict[str, Any]) -> dict[str, Any]:
    conditions_payload = validate_trigger_conditions(
        trigger.get("conditions_json", {}))
    actions = validate_trigger_actions(trigger.get("actions_json", []))
    condition_results = [
        {
            **condition,
            "matched": _condition_matches(condition, classification),
        }
        for condition in conditions_payload["conditions"]
    ]
    if not condition_results:
        matched = True
    elif conditions_payload["match_type"] == "ALL":
        matched = all(c["matched"] for c in condition_results)
    else:
        matched = any(c["matched"] for c in condition_results)
    return {
        "trigger_id": trigger["trigger_id"],
        "name": trigger["name"],
        "priority": trigger["priority"],
        "matched": matched,
        "matched_conditions": condition_results,
        "planned_actions": actions if matched else [],
        "dry_run": True,
        "reason": PREVIEW_ONLY_REASON,
    }


def evaluate_triggers(
        triggers: list[dict[str, Any]],
        classification: dict[str, Any]) -> list[dict[str, Any]]:
    ordered = sorted(triggers, key=lambda t: (int(t.get("priority", 0)), t["trigger_id"]))
    return [evaluate_trigger(trigger, classification) for trigger in ordered]


def _validate_operator(field: str, operator: str) -> None:
    if field in {"category"} and operator not in {"equals", "in"}:
        raise ValueError(f"Unsupported operator for {field}: {operator}")
    if field in {"summary", "reason"} and operator not in TEXT_OPERATORS:
        raise ValueError(f"Unsupported operator for {field}: {operator}")
    if field in {"urgency_score", "confidence"} and operator not in NUMBER_OPERATORS:
        raise ValueError(f"Unsupported operator for {field}: {operator}")
    if field == "needs_reply" and operator not in BOOL_OPERATORS:
        raise ValueError(f"Unsupported operator for {field}: {operator}")


def _condition_matches(
        condition: dict[str, Any],
        classification: dict[str, Any]) -> bool:
    field = condition["field"]
    operator = condition["operator"]
    actual = classification.get(field)
    expected = condition.get("value")

    if field in {"urgency_score", "confidence"}:
        actual_num = float(actual)
        expected_num = float(expected)
        if operator == ">=":
            return actual_num >= expected_num
        if operator == "<=":
            return actual_num <= expected_num
        return actual_num == expected_num

    if field == "needs_reply":
        return bool(actual) is _to_bool(expected)

    actual_text = str(actual or "")
    if operator == "contains":
        return str(expected or "").lower() in actual_text.lower()
    if operator == "in":
        values = expected if isinstance(expected, list) else []
        return actual_text in [str(v) for v in values]
    return actual_text == str(expected or "")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
