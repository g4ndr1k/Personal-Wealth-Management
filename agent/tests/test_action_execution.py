from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agent.app.action_execution import (
    canonical_json,
    compute_idempotency_key,
    compute_plan_hash,
    evaluate_execution_gate,
    mock_execute_approved_action,
)
from agent.app.state import AgentState


NOW = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)


def _settings(**overrides):
    cfg = {
        "enabled": True,
        "dry_run_default": False,
        "allow_mark_read": True,
        "allow_mark_unread": True,
        "allow_add_label": False,
        "allow_move_to_folder": False,
        "require_uidvalidity_match": True,
        "require_capability_cache": True,
    }
    cfg.update(overrides)
    return {
        "mail": {
            "imap_mutations": cfg,
            "approvals": {"approval_expiry_hours": 72},
        }
    }


def _approval(**overrides):
    payload = {
        "approval_id": "approval-1",
        "status": "approved",
        "requested_at": (NOW - timedelta(hours=1)).isoformat(),
        "archived_at": None,
        "account_id": "acct",
        "folder": "INBOX",
        "uidvalidity": "7",
        "imap_uid": 42,
        "proposed_action_type": "mark_read",
        "proposed_target": None,
    }
    payload.update(overrides)
    return payload


def _plan(**overrides):
    payload = {
        "action_type": "mark_read",
        "account_id": "acct",
        "folder": "INBOX",
        "uidvalidity": "7",
        "uid": 42,
        "target": None,
        "operation": r"STORE +FLAGS.SILENT (\Seen)",
        "dry_run": True,
        "would_mutate": False,
        "reversible": True,
        "before_state": {"seen": False},
    }
    payload.update(overrides)
    return payload


def _ready_gate(**kwargs):
    params = {
        "approval": _approval(),
        "settings": _settings(),
        "account": {"enabled": True},
        "folder_state": {"uidvalidity": "7", "uid_exists": True},
        "capability_cache": {
            "uidvalidity": "7",
            "supports_store_flags": True,
            "status": "ok",
        },
        "dry_run_plan": _plan(),
        "now": NOW,
    }
    params.update(kwargs)
    return evaluate_execution_gate(**params)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"settings": _settings(enabled=False)}, "mail.imap_mutations.enabled=false"),
        ({"settings": _settings(dry_run_default=True)}, "mail.imap_mutations.dry_run_default=true"),
        ({"settings": _settings(allow_mark_read=False)}, "mail.imap_mutations.allow_mark_read=false"),
        ({
            "approval": _approval(proposed_action_type="add_label", proposed_target="Bills"),
            "dry_run_plan": _plan(action_type="add_label", target="Bills"),
        }, "add_label_deferred"),
        ({
            "approval": _approval(proposed_action_type="move_to_folder", proposed_target="Archive"),
            "dry_run_plan": _plan(action_type="move_to_folder", target="Archive"),
        }, "move_to_folder_deferred"),
        ({
            "approval": _approval(proposed_action_type="delete"),
            "dry_run_plan": _plan(action_type="delete"),
        }, "dangerous_action:delete"),
        ({"approval": _approval(status="pending")}, "approval_not_approved"),
        ({
            "approval": _approval(requested_at=(NOW - timedelta(hours=100)).isoformat()),
        }, "approval_expired"),
        ({"approval": _approval(archived_at=NOW.isoformat())}, "approval_archived"),
        ({"account": {"enabled": False}}, "account_disabled_or_missing"),
        ({"folder_state": None}, "folder_state_missing"),
        ({"folder_state": {"uidvalidity": "7", "uid_exists": False}}, "uid_missing_from_folder_state"),
        ({"approval": _approval(imap_uid=None), "dry_run_plan": _plan(uid=None)}, "imap_uid_missing"),
        ({
            "approval": _approval(uidvalidity=None),
            "dry_run_plan": _plan(uidvalidity=None),
        }, "uidvalidity_missing"),
        ({"folder_state": {"uidvalidity": "99"}}, "uidvalidity_mismatch"),
        ({"capability_cache": None}, "capability_cache_missing"),
        ({
            "capability_cache": {"uidvalidity": "7", "supports_store_flags": False, "status": "ok"},
        }, "capability_store_flags_unsupported"),
        ({"dry_run_plan": None}, "dry_run_plan_missing"),
        ({"dry_run_plan": _plan(would_mutate=True)}, "dry_run_plan_would_mutate_not_false"),
        ({"dry_run_plan": _plan(reversible=False)}, "action_not_reversible"),
        ({"dry_run_plan": _plan(uid=99)}, "dry_run_plan_identity_mismatch"),
        ({"dry_run_plan": _plan(before_state=None)}, "rollback_before_state_unknown"),
        ({"existing_execution": {"status": "mock_executed"}}, "idempotency_key_already_recorded"),
    ],
)
def test_gate_blocks_expected_conditions(kwargs, reason):
    result = _ready_gate(**kwargs)

    assert result.status == "blocked"
    assert reason in result.blocked_reasons
    assert result.safe_to_execute is False


def test_gate_ready_only_when_all_checks_pass():
    result = _ready_gate(mock_mode=True)

    assert result.status == "ready"
    assert result.blocked_reasons == []
    assert result.safe_to_execute is True
    assert result.rollback_plan["operation"] == "mark_unread"


def test_gate_ready_is_mock_only_without_mock_mode():
    result = _ready_gate()

    assert result.status == "ready"
    assert result.safe_to_execute is False
    assert "ready_for_mock_only_no_live_mutation" in result.warnings


def test_canonical_json_is_deterministic():
    assert canonical_json({"b": 2, "a": [3, 1]}) == canonical_json({"a": [3, 1], "b": 2})


def test_idempotency_key_is_deterministic_and_changes_with_identity_action_target_plan():
    plan_hash = compute_plan_hash(_plan())
    base = {
        "approval_id": "approval-1",
        "account_id": "acct",
        "folder": "INBOX",
        "uidvalidity": "7",
        "imap_uid": "42",
        "operation": "mark_read",
        "target": None,
        "plan_hash": plan_hash,
    }
    key = compute_idempotency_key(**base)

    assert compute_idempotency_key(**base) == key
    assert compute_idempotency_key(**{**base, "uidvalidity": "8"}) != key
    assert compute_idempotency_key(**{**base, "target": "Done"}) != key
    assert compute_idempotency_key(**{**base, "operation": "mark_unread"}) != key
    assert compute_idempotency_key(**{**base, "plan_hash": compute_plan_hash(_plan(before_state={"seen": True}))}) != key


def test_duplicate_execution_is_not_inserted_twice(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    gate = _ready_gate(mock_mode=True)

    first = mock_execute_approved_action(state, gate)
    second = mock_execute_approved_action(state, gate)

    assert first["inserted"] is True
    assert second["inserted"] is False
    with state._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM mail_action_executions").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM mail_action_execution_events").fetchone()[0]
    assert count == 1
    assert event_count == 1


def test_mock_executor_refuses_blocked_gate(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))
    gate = _ready_gate(settings=_settings(enabled=False), mock_mode=True)

    with pytest.raises(ValueError, match="refuses blocked"):
        mock_execute_approved_action(state, gate)


def test_mock_executor_writes_execution_event_and_rollback_plan(tmp_path, monkeypatch):
    state = AgentState(str(tmp_path / "agent.db"))
    calls = []
    monkeypatch.setattr(sqlite3, "connect", sqlite3.connect)
    gate = _ready_gate(mock_mode=True)

    result = mock_execute_approved_action(state, gate)
    events = state.list_action_execution_events(result["execution_id"])

    assert calls == []
    assert result["status"] == "mock_executed"
    assert result["before_state"] == {"seen": False}
    assert result["after_state"] == {"seen": True}
    assert result["rollback_plan"]["operation"] == "mark_unread"
    assert events[0]["event_type"] == "mock_executed"
    assert events[0]["event_json"]["mailbox_mutation_occurred"] is False


def test_execution_schema_exists(tmp_path):
    state = AgentState(str(tmp_path / "agent.db"))

    with state._connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }

    assert "mail_action_executions" in tables
    assert "mail_action_execution_events" in tables
    assert "idx_mail_action_exec_approval" in indexes
    assert "idx_mail_action_exec_idempotency" in indexes
