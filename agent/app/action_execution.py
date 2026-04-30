from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


EXECUTABLE_ACTIONS = {"mark_read", "mark_unread"}
DANGEROUS_ACTIONS = {
    "delete",
    "spam",
    "archive",
    "expunge",
    "reply",
    "auto_reply",
    "forward",
    "unsubscribe",
    "webhook",
    "external_webhook",
    "send_imessage",
}


@dataclass
class ExecutionGateResult:
    status: str
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    idempotency_key: str | None = None
    plan_hash: str | None = None
    rollback_plan: dict[str, Any] | None = None
    safe_to_execute: bool = False
    operation: str | None = None
    target: Any = None
    approval_id: str | None = None
    account_id: str | None = None
    folder: str | None = None
    uidvalidity: str | None = None
    imap_uid: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    dry_run_plan: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "blocked_reasons": list(self.blocked_reasons),
            "warnings": list(self.warnings),
            "idempotency_key": self.idempotency_key,
            "plan_hash": self.plan_hash,
            "rollback_plan": self.rollback_plan,
            "safe_to_execute": self.safe_to_execute,
            "operation": self.operation,
            "target": self.target,
            "approval_id": self.approval_id,
            "account_id": self.account_id,
            "folder": self.folder,
            "uidvalidity": self.uidvalidity,
            "imap_uid": self.imap_uid,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "dry_run_plan": self.dry_run_plan,
        }


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(
        ("imap-mutation-plan:v1|" + canonical_json(plan)).encode("utf-8")
    ).hexdigest()


def compute_idempotency_key(
        *, approval_id: Any, account_id: Any, folder: Any,
        uidvalidity: Any, imap_uid: Any, operation: Any, target: Any,
        plan_hash: str) -> str:
    parts = [
        "imap-mutation:v1",
        _text(approval_id),
        _text(account_id),
        _text(folder),
        _text(uidvalidity),
        _text(imap_uid),
        _text(operation),
        canonical_json(target),
        _text(plan_hash),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def execution_id_for_key(idempotency_key: str) -> str:
    return "mock-" + hashlib.sha256(
        ("mail-action-execution:v1|" + idempotency_key).encode("utf-8")
    ).hexdigest()[:32]


def evaluate_execution_gate(
        *, approval: dict[str, Any], settings: dict[str, Any],
        account: dict[str, Any] | None,
        folder_state: dict[str, Any] | None,
        capability_cache: dict[str, Any] | None,
        dry_run_plan: dict[str, Any] | None,
        existing_execution: dict[str, Any] | None = None,
        now: datetime | None = None,
        mock_mode: bool = False) -> ExecutionGateResult:
    now = now or datetime.now(timezone.utc)
    mutation_cfg = settings.get("mail", {}).get("imap_mutations", {})
    approval_cfg = settings.get("mail", {}).get("approvals", {})
    agent_mode = str(settings.get("agent", {}).get("mode", "live") or "live")
    action = _text(approval.get("proposed_action_type"))
    target = approval.get("proposed_target")
    account_id = approval.get("account_id")
    folder = approval.get("folder")
    uidvalidity = approval.get("uidvalidity")
    imap_uid = approval.get("imap_uid")
    blocked: list[str] = []
    warnings: list[str] = []

    def block(reason: str) -> None:
        if reason not in blocked:
            blocked.append(reason)

    if agent_mode != "live":
        block(f"agent.mode={agent_mode}")
    if not bool(mutation_cfg.get("enabled", False)):
        block("mail.imap_mutations.enabled=false")
    if bool(mutation_cfg.get("dry_run_default", True)):
        block("mail.imap_mutations.dry_run_default=true")

    allow_gate = {
        "mark_read": "allow_mark_read",
        "mark_unread": "allow_mark_unread",
        "add_label": "allow_add_label",
        "move_to_folder": "allow_move_to_folder",
    }.get(action)
    if allow_gate and not bool(mutation_cfg.get(allow_gate, False)):
        block(f"mail.imap_mutations.{allow_gate}=false")

    if action not in EXECUTABLE_ACTIONS:
        block(f"unsupported_execution_action:{action or 'missing'}")
    if action in {"add_label", "move_to_folder"}:
        block(f"{action}_deferred")
    if action in DANGEROUS_ACTIONS:
        block(f"dangerous_action:{action}")

    if approval.get("status") != "approved":
        block("approval_not_approved")
    if approval.get("archived_at"):
        block("approval_archived")
    if _approval_expired(
            approval.get("requested_at"),
            approval_cfg.get("approval_expiry_hours", 72),
            now):
        block("approval_expired")

    if not account or not bool(account.get("enabled", True)):
        block("account_disabled_or_missing")
    if not folder_state or folder_state.get("uidvalidity") is None:
        block("folder_state_missing")
    elif "uid_exists" in folder_state and folder_state.get("uid_exists") is not True:
        block("uid_missing_from_folder_state")
    if imap_uid is None or _text(imap_uid) == "":
        block("imap_uid_missing")
    if uidvalidity is None or _text(uidvalidity) == "":
        block("uidvalidity_missing")
    if (
            bool(mutation_cfg.get("require_uidvalidity_match", True))
            and folder_state
            and folder_state.get("uidvalidity") is not None
            and uidvalidity is not None
            and _text(folder_state.get("uidvalidity")) != _text(uidvalidity)):
        block("uidvalidity_mismatch")

    require_cache = bool(mutation_cfg.get("require_capability_cache", True))
    if require_cache and not capability_cache:
        block("capability_cache_missing")
    if capability_cache:
        if (
                bool(mutation_cfg.get("require_uidvalidity_match", True))
                and capability_cache.get("uidvalidity") is not None
                and uidvalidity is not None
                and _text(capability_cache.get("uidvalidity")) != _text(uidvalidity)):
            block("capability_uidvalidity_mismatch")
        if capability_cache.get("status") not in {None, "ok"}:
            block("capability_cache_not_ok")
        if action in EXECUTABLE_ACTIONS and capability_cache.get("supports_store_flags") is not True:
            block("capability_store_flags_unsupported")

    if not dry_run_plan:
        block("dry_run_plan_missing")
        plan_hash = None
        rollback_plan = None
        before_state = None
        after_state = None
        operation = action
    else:
        operation = _text(dry_run_plan.get("operation") or action)
        plan_hash = compute_plan_hash(dry_run_plan)
        if dry_run_plan.get("would_mutate") is not False:
            block("dry_run_plan_would_mutate_not_false")
        if dry_run_plan.get("reversible") is not True:
            block("action_not_reversible")
        if not _plan_identity_matches(
                dry_run_plan, approval, action=action, target=target):
            block("dry_run_plan_identity_mismatch")
        before_state = dry_run_plan.get("before_state")
        if not isinstance(before_state, dict):
            block("rollback_before_state_unknown")
        rollback_plan = _rollback_plan(action, before_state, approval)
        if rollback_plan is None:
            block("rollback_not_describable")
        after_state = _mock_after_state(action, before_state)
        expected_hash = dry_run_plan.get("plan_hash")
        if expected_hash and expected_hash != plan_hash:
            block("plan_hash_mismatch")

    idempotency_key = None
    if plan_hash:
        idempotency_key = compute_idempotency_key(
            approval_id=approval.get("approval_id"),
            account_id=account_id,
            folder=folder,
            uidvalidity=uidvalidity,
            imap_uid=imap_uid,
            operation=operation,
            target=target,
            plan_hash=plan_hash,
        )
    if existing_execution and existing_execution.get("status") in {
            "ready", "mock_executed", "failed", "rolled_back",
            "rollback_failed", "blocked"}:
        block("idempotency_key_already_recorded")

    status = "blocked" if blocked else "ready"
    if status == "ready" and not mock_mode:
        warnings.append("ready_for_mock_only_no_live_mutation")
    return ExecutionGateResult(
        status=status,
        blocked_reasons=blocked,
        warnings=warnings,
        idempotency_key=idempotency_key,
        plan_hash=plan_hash,
        rollback_plan=rollback_plan,
        safe_to_execute=(status == "ready" and mock_mode),
        operation=operation,
        target=target,
        approval_id=approval.get("approval_id"),
        account_id=_none_or_text(account_id),
        folder=_none_or_text(folder),
        uidvalidity=_none_or_text(uidvalidity),
        imap_uid=_none_or_text(imap_uid),
        before_state=before_state,
        after_state=after_state,
        dry_run_plan=dry_run_plan,
    )


def mock_execute_approved_action(state, gate_result: ExecutionGateResult) -> dict:
    """Record a mock execution. This never calls IMAP or Gmail mutation APIs."""
    if gate_result.status != "ready":
        raise ValueError("Mock executor refuses blocked gate results")
    required = {
        "approval_id": gate_result.approval_id,
        "account_id": gate_result.account_id,
        "folder": gate_result.folder,
        "uidvalidity": gate_result.uidvalidity,
        "imap_uid": gate_result.imap_uid,
        "operation": gate_result.operation,
        "plan_hash": gate_result.plan_hash,
        "idempotency_key": gate_result.idempotency_key,
    }
    missing = [key for key, value in required.items() if value in {None, ""}]
    if missing:
        raise ValueError(f"Mock execution missing required fields: {', '.join(missing)}")
    return state.insert_mock_action_execution(
        execution_id=execution_id_for_key(gate_result.idempotency_key),
        approval_id=gate_result.approval_id,
        account_id=gate_result.account_id,
        folder=gate_result.folder,
        uidvalidity=gate_result.uidvalidity,
        imap_uid=gate_result.imap_uid,
        operation=gate_result.operation,
        target=_none_or_text(gate_result.target),
        plan_hash=gate_result.plan_hash,
        idempotency_key=gate_result.idempotency_key,
        before_state=gate_result.before_state,
        after_state=gate_result.after_state,
        rollback_plan=gate_result.rollback_plan,
    )


def _approval_expired(
        requested_at: Any, expiry_hours: Any, now: datetime) -> bool:
    if not requested_at:
        return True
    try:
        requested = datetime.fromisoformat(_text(requested_at))
    except ValueError:
        return True
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)
    try:
        hours = float(expiry_hours)
    except (TypeError, ValueError):
        hours = 72.0
    return requested + timedelta(hours=hours) < now


def _plan_identity_matches(
        plan: dict[str, Any], approval: dict[str, Any], *,
        action: str, target: Any) -> bool:
    comparisons = {
        "action_type": action,
        "account_id": approval.get("account_id"),
        "folder": approval.get("folder"),
        "uidvalidity": approval.get("uidvalidity"),
        "uid": approval.get("imap_uid"),
        "target": target,
    }
    for key, expected in comparisons.items():
        if _text(plan.get(key)) != _text(expected):
            return False
    return True


def _rollback_plan(
        action: str, before_state: dict[str, Any] | None,
        approval: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(before_state, dict) or "seen" not in before_state:
        return None
    before_seen = bool(before_state["seen"])
    rollback_action = "mark_read" if before_seen else "mark_unread"
    return {
        "operation": rollback_action,
        "account_id": approval.get("account_id"),
        "folder": approval.get("folder"),
        "uidvalidity": approval.get("uidvalidity"),
        "imap_uid": approval.get("imap_uid"),
        "restore_seen": before_seen,
        "description": "Restore the IMAP \\Seen flag to the known before-state.",
    }


def _mock_after_state(
        action: str, before_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(before_state, dict):
        return None
    after = dict(before_state)
    if action == "mark_read":
        after["seen"] = True
    elif action == "mark_unread":
        after["seen"] = False
    return after


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _none_or_text(value: Any) -> str | None:
    return None if value is None else str(value)
