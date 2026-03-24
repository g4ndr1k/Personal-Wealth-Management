"""
Mail source adapter for macOS Tahoe (26.x) Mail.app V10 schema.

Tahoe V10 normalized schema:
- messages.sender -> addresses.ROWID (direct FK)
- messages.subject -> subjects.ROWID
- messages.summary -> summaries.ROWID (body text, when available)
- messages.mailbox -> mailboxes.ROWID
- messages.global_message_id -> message_global_data.ROWID

Dates: Unix timestamps (seconds since 1970-01-01).
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from urllib.parse import unquote

def discover_mail_db() -> Path:
    root = Path.home() / "Library" / "Mail"
    candidates = sorted(root.glob("V*/MailData/Envelope Index"), reverse=True)
    if not candidates:
        raise FileNotFoundError("No Mail Envelope Index found under ~/Library/Mail")
    return candidates[0]


def unix_ts_to_datetime(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except Exception:
        return None


def truncate_bytes(text: str | None, limit: int) -> tuple[str, bool]:
    if not text:
        return "", False
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def parse_mailbox_folder(url: str) -> str:
    if not url:
        return ""
    try:
        decoded = unquote(url)
        parts = decoded.rstrip("/").split("/")
        if parts:
            return parts[-1]
    except Exception:
        pass
    return url


class MailSource:
    def __init__(self, settings: dict):
        self.settings = settings
        self.mail_db = discover_mail_db()
        self.max_body_text_bytes = int(
            settings["mail"].get("max_body_text_bytes", 200000))
        self.initial_lookback_days = int(
            settings["mail"].get("initial_lookback_days", 7))
        self.max_batch = int(settings["mail"].get("max_batch", 25))
        self._schema_valid = None

    def can_access(self) -> bool:
        if not self.mail_db.exists():
            return False
        try:
            with self._connect() as conn:
                conn.execute("SELECT COUNT(*) FROM messages LIMIT 1")
            return True
        except Exception:
            return False

    @property
    def schema_valid(self) -> bool:
        """Returns cached schema validity. None if never checked."""
        return self._schema_valid

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(
            f"file:{self.mail_db}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def verify_schema(self) -> dict:
        required_tables = {
            "messages", "subjects", "addresses",
            "summaries", "mailboxes", "message_global_data"
        }
        required_cols = {
            "sender", "subject", "summary", "date_received",
            "date_sent", "mailbox", "global_message_id",
            "read", "flagged", "deleted"
        }
        mgd_required_cols = {
            "message_id_header", "model_category",
            "model_high_impact", "urgent"
        }
        errors = []
    
        try:
            with self._connect() as conn:
                tables = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing_tables = required_tables - tables
                if missing_tables:
                    errors.append(f"Missing tables: {sorted(missing_tables)}")
    
                if "messages" in tables:
                    cols = {
                        r[1] for r in conn.execute(
                            "PRAGMA table_info(messages)"
                        ).fetchall()
                    }
                    missing_cols = required_cols - cols
                    if missing_cols:
                        errors.append(f"Missing columns in messages: {sorted(missing_cols)}")
    
                    try:
                        conn.execute("SELECT ROWID FROM messages LIMIT 1").fetchone()
                    except Exception as e:
                        errors.append(f"ROWID query failed: {e}")
    
                    row = conn.execute(
                        "SELECT date_received FROM messages "
                        "WHERE date_received IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if row and row[0] is not None:
                        try:
                            datetime.fromtimestamp(float(row[0]), tz=timezone.utc)
                        except Exception as e:
                            errors.append(f"date_received not parseable as Unix epoch: {row[0]} ({e})")
    
                if "message_global_data" in tables:
                    mgd_cols = {
                        r[1] for r in conn.execute(
                            "PRAGMA table_info(message_global_data)"
                        ).fetchall()
                    }
                    missing_mgd_cols = mgd_required_cols - mgd_cols
                    if missing_mgd_cols:
                        errors.append(
                            f"Missing columns in message_global_data: {sorted(missing_mgd_cols)}"
                        )
    
        except Exception as e:
            errors.append(f"Schema check failed: {e}")
    
        return {"valid": len(errors) == 0, "errors": errors}

    def debug_schema(self) -> dict:
        with self._connect() as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            cols = [
                r[1] for r in conn.execute(
                    "PRAGMA table_info(messages)"
                ).fetchall()
            ]
            total = conn.execute(
                "SELECT COUNT(*) FROM messages").fetchone()[0]
            with_sender = conn.execute(
                "SELECT COUNT(*) FROM messages m "
                "JOIN addresses a ON a.ROWID = m.sender"
            ).fetchone()[0]
            with_body = conn.execute(
                "SELECT COUNT(*) FROM messages m "
                "JOIN summaries s ON s.ROWID = m.summary"
            ).fetchone()[0]

        return {
            "db_path": str(self.mail_db),
            "schema_version": "V10-Tahoe",
            "date_format": "unix_epoch",
            "tables": tables,
            "messages_columns": cols,
            "total_messages": total,
            "messages_with_sender_email": with_sender,
            "messages_with_body_text": with_body,
            "join_strategy": "messages.sender -> addresses.ROWID (direct)",
            "schema_valid": self._schema_valid,
        }

    def get_pending_messages(
        self, ack_token: str, limit: int = 25
    ) -> tuple[list[dict], str]:
        ack_rowid = int(ack_token or "0")
        query_limit = min(max(1, limit), self.max_batch)

        with self._connect() as conn:
            where_parts, params = [], []
            if ack_rowid > 0:
                where_parts.append("m.ROWID > ?")
                params.append(ack_rowid)
            else:
                cutoff = (datetime.now(tz=timezone.utc)
                          - timedelta(days=self.initial_lookback_days))
                where_parts.append("m.date_received >= ?")
                params.append(cutoff.timestamp())

            where_sql = (" AND ".join(where_parts)
                         if where_parts else "1=1")

            query = f"""
            SELECT m.ROWID, sub.subject,
                a.address AS sender_email,
                a.comment AS sender_name,
                summ.summary AS body_text,
                m.date_received, m.date_sent,
                mb.url AS mailbox_url,
                mgd.message_id_header,
                mgd.model_category AS apple_category,
                mgd.model_high_impact AS apple_high_impact,
                mgd.urgent AS apple_urgent,
                m.read, m.flagged, m.deleted
            FROM messages m
            LEFT JOIN subjects sub ON sub.ROWID = m.subject
            LEFT JOIN addresses a ON a.ROWID = m.sender
            LEFT JOIN summaries summ ON summ.ROWID = m.summary
            LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
            LEFT JOIN message_global_data mgd
                ON mgd.ROWID = m.global_message_id
            WHERE {where_sql} AND m.deleted = 0
            ORDER BY m.ROWID ASC LIMIT ?
            """
            params.append(query_limit)
            rows = conn.execute(query, params).fetchall()

        results, max_rowid = [], ack_rowid
        for row in rows:
            rowid = row["ROWID"]
            max_rowid = max(max_rowid, rowid)

            raw_body = row["body_text"] or ""
            body_text, body_truncated = truncate_bytes(
                raw_body, self.max_body_text_bytes)

            sender_email = row["sender_email"] or ""
            sender_name = row["sender_name"] or ""
            if sender_name and sender_email:
                sender = f"{sender_name} <{sender_email}>"
            else:
                sender = sender_email or sender_name or "Unknown"

            mailbox_url = row["mailbox_url"] or ""

            results.append({
                "bridge_id": f"mail-{rowid}",
                "source_rowid": rowid,
                "message_id": (row["message_id_header"]
                               or f"rowid-{rowid}"),
                "mailbox": parse_mailbox_folder(mailbox_url),
                "mailbox_url": mailbox_url,
                "sender": sender,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "subject": row["subject"] or "(No Subject)",
                "date_received": (
                    unix_ts_to_datetime(
                        row["date_received"]).isoformat()
                    if row["date_received"] else None),
                "date_sent": (
                    unix_ts_to_datetime(
                        row["date_sent"]).isoformat()
                    if row["date_sent"] else None),
                "snippet": (body_text[:500] if body_text else ""),
                "body_text": body_text,
                "body_html": "",
                "body_text_truncated": body_truncated,
                "has_body": bool(raw_body),
                "apple_category": row["apple_category"],
                "apple_high_impact": row["apple_high_impact"],
                "apple_urgent": row["apple_urgent"],
                "is_read": bool(row["read"]),
                "is_flagged": bool(row["flagged"]),
                "attachments": [],
            })

        return results, str(max_rowid)
