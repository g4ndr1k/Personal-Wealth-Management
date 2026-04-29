"""
api_mail.py — Mail API data layer for the agent health server.

Provides query functions called by agent/app/health.py to serve:
  GET  /api/mail/summary   — KPIs + source split + classification + action counts
  GET  /api/mail/recent    — Recent processed messages
  GET  /api/mail/accounts  — Per-IMAP-account health
  POST /api/mail/run       — Trigger a poll cycle (handled by health.py)

All functions open agent.db read-only and return JSON-serialisable dicts/lists.
They tolerate a missing DB gracefully (agent never ran → empty responses).
"""
from __future__ import annotations

import os
import sqlite3
import tomllib
from contextlib import contextmanager
from typing import Any

# ── DB connection ─────────────────────────────────────────────────────────────

_DB_DEFAULT = "/app/data/agent.db"


def _db_path() -> str:
    return os.environ.get("AGENT_DB_PATH", _DB_DEFAULT)


@contextmanager
def _connect():
    """Open agent.db read-only; yields a connection with Row factory set."""
    path = _db_path()
    conn = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _db_exists() -> bool:
    import os as _os
    return _os.path.exists(_db_path())


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _agent_mode() -> str:
    env_mode = os.environ.get("AGENT_MODE")
    if env_mode in ("observe", "draft_only", "live"):
        return env_mode
    settings_file = os.environ.get("SETTINGS_FILE", "/app/config/settings.toml")
    try:
        with open(settings_file, "rb") as f:
            cfg = tomllib.load(f)
        agent = cfg.get("agent", {})
        mode = str(agent.get("mode", "")).strip()
        if mode in ("observe", "draft_only", "live"):
            return mode
        safe_default = str(agent.get("safe_default", "draft_only")).strip()
        if safe_default in ("observe", "draft_only"):
            return safe_default
    except Exception:
        pass
    return "draft_only"


# ── Urgency → numeric priority map ───────────────────────────────────────────

_URGENCY_WEIGHT: dict[str, int] = {
    "urgent": 10,
    "high":   8,
    "medium": 5,
    "low":    2,
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_summary() -> dict[str, Any]:
    """
    Returns a single payload with:
      - kpis: total_processed, urgent_count, drafts_created, avg_priority
      - source_split: {gmail: N, outlook: N}
      - classification_counts: {category: count, ...}
      - action_counts: {drafts_created, labels_applied, imessage_alerts,
                        important_count, reply_needed_count}
    """
    if not _db_exists():
        return _empty_summary()

    try:
        with _connect() as conn:
            # ── KPIs ──────────────────────────────────────────────────────────
            total_processed: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages"
            ).fetchone()[0]

            urgent_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE urgency IN ('urgent', 'high')"
            ).fetchone()[0]

            # Drafts: approximated from category until a dedicated table exists
            drafts_created: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE category IN ('draft_created', 'draft')"
            ).fetchone()[0]

            # avg_priority: map text urgency → numeric weight, then mean
            urg_rows = conn.execute(
                "SELECT urgency, COUNT(*) AS cnt "
                "FROM processed_messages GROUP BY urgency"
            ).fetchall()
            total_weight = 0
            total_cnt = 0
            for row in urg_rows:
                w = _URGENCY_WEIGHT.get((row["urgency"] or "low").lower(), 2)
                total_weight += w * row["cnt"]
                total_cnt += row["cnt"]
            avg_priority = (
                round(total_weight / total_cnt, 1) if total_cnt else 0.0
            )

            # ── Source split ──────────────────────────────────────────────────
            # provider / message_id carry source hints; fall back to 0s if absent
            gmail_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE LOWER(COALESCE(source,'')) = 'imap' "
                "   OR LOWER(COALESCE(provider,'')) LIKE '%gmail%' "
                "   OR LOWER(COALESCE(message_id,'')) LIKE '%gmail%'"
            ).fetchone()[0]
            outlook_count: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages "
                "WHERE LOWER(COALESCE(provider,'')) LIKE '%outlook%' "
                "   OR LOWER(COALESCE(message_id,'')) LIKE '%outlook%'"
            ).fetchone()[0]

            # ── Classification counts ─────────────────────────────────────────
            cat_rows = conn.execute(
                "SELECT COALESCE(category,'unknown') AS cat, COUNT(*) AS cnt "
                "FROM processed_messages GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            classification_counts: dict[str, int] = {
                r["cat"]: r["cnt"] for r in cat_rows
            }

            # ── Action counts ─────────────────────────────────────────────────
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
            # labels_applied ≈ messages that triggered an alert
            labels_applied: int = conn.execute(
                "SELECT COUNT(*) FROM processed_messages WHERE alert_sent = 1"
            ).fetchone()[0]

            # pdf_attachments table may not exist yet
            pdf_count = 0
            if _table_exists(conn, "pdf_attachments"):
                pdf_count = conn.execute(
                    "SELECT COUNT(*) FROM pdf_attachments"
                ).fetchone()[0]

        mode = _agent_mode()
        payload = {
            "kpis": {
                "total_processed":  total_processed,
                "urgent_count":     urgent_count,
                "drafts_created":   drafts_created,
                "avg_priority":     avg_priority,
            },
            "source_split": {
                "gmail":   gmail_count,
                "outlook": outlook_count,
            },
            "classification_counts": classification_counts,
            "action_counts": {
                "drafts_created":    drafts_created,
                "labels_applied":    labels_applied,
                "imessage_alerts":   imessage_alerts,
                "important_count":   important_count,
                "reply_needed_count": reply_needed_count,
            },
            "pdf_attachments": pdf_count,
            "mode": mode,
        }
        payload.update({
            "total_processed": total_processed,
            "urgent_count": urgent_count,
            "drafts_created": drafts_created,
            "avg_priority": avg_priority,
            "classification": classification_counts,
            "actions": payload["action_counts"],
        })
        return payload
    except Exception as exc:
        return {**_empty_summary(), "error": str(exc)}


def get_recent(limit: int = 20) -> list[dict[str, Any]]:
    """
    Returns the last `limit` processed messages ordered by processed_at DESC.
    Clamps limit to [1, 200].
    """
    limit = min(max(1, limit), 200)

    if not _db_exists():
        return []

    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT bridge_id, message_id, processed_at, category, "
                "       urgency, provider, alert_sent, summary, "
                "       COALESCE(status, 'processed') AS status, "
                "       COALESCE(source, 'bridge') AS source "
                "FROM processed_messages "
                "ORDER BY processed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_accounts() -> list[dict[str, Any]]:
    """
    Per-IMAP-account health from `imap_accounts` table (if it exists).
    Returns an empty list until that table is populated by a future IMAP layer.
    """
    if not _db_exists():
        return []

    try:
        with _connect() as conn:
            if not _table_exists(conn, "imap_accounts"):
                return []
            rows = conn.execute(
                "SELECT * FROM imap_accounts ORDER BY account_name"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_summary() -> dict[str, Any]:
    payload = {
        "kpis": {
            "total_processed": 0,
            "urgent_count":    0,
            "drafts_created":  0,
            "avg_priority":    0.0,
        },
        "source_split": {"gmail": 0, "outlook": 0},
        "classification_counts": {},
        "action_counts": {
            "drafts_created":    0,
            "labels_applied":    0,
            "imessage_alerts":   0,
            "important_count":   0,
            "reply_needed_count": 0,
        },
        "pdf_attachments": 0,
        "mode": _agent_mode(),
    }
    payload.update({
        "total_processed": 0,
        "urgent_count": 0,
        "drafts_created": 0,
        "avg_priority": 0.0,
        "classification": {},
        "actions": payload["action_counts"],
    })
    return payload
