#!/bin/bash
set -euo pipefail
BASE="$HOME/agentic-ai"
cd "$BASE"

echo "=== Applying v1.1.1 patch ==="
echo ""

# ============================================================
# PATCH 1: bridge/config.py — Safe int parsing
# ============================================================
cat > bridge/config.py << 'PYEOF'
from pathlib import Path
import tomllib


def load_settings(path: str = "config/settings.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_token_path(settings: dict) -> Path:
    return Path(settings["auth"]["token_file"]).expanduser()


def _safe_int(value, name: str) -> tuple[int | None, str | None]:
    try:
        return int(value), None
    except (ValueError, TypeError):
        return None, f"{name} is not a valid integer: {value!r}"


def validate_settings(settings: dict) -> None:
    errors = []

    for section in ["bridge", "auth", "mail", "imessage",
                     "classifier", "ollama", "agent"]:
        if section not in settings:
            errors.append(f"Missing required section: [{section}]")
    if errors:
        raise ValueError(f"Config validation failed: {'; '.join(errors)}")

    port, err = _safe_int(settings["bridge"].get("port", 0), "bridge.port")
    if err:
        errors.append(err)
    elif not (1 <= port <= 65535):
        errors.append(f"Invalid bridge port: {port}")

    token_path = Path(settings["auth"]["token_file"]).expanduser()
    if not token_path.exists():
        errors.append(f"Token file not found: {token_path}")

    recipient = settings["imessage"].get("primary_recipient", "")
    if not recipient or "@" not in recipient:
        errors.append(f"Invalid primary_recipient: {recipient}")

    poll, err = _safe_int(
        settings["agent"].get("poll_interval_seconds", 0),
        "agent.poll_interval_seconds")
    if err:
        errors.append(err)
    elif poll < 10:
        errors.append(f"poll_interval_seconds too low: {poll}")

    valid_providers = {"ollama", "anthropic"}
    for p in settings["classifier"].get("provider_order", []):
        if p not in valid_providers:
            errors.append(f"Unknown provider: {p}")

    if errors:
        raise ValueError(f"Config validation failed: {'; '.join(errors)}")
PYEOF
echo "  ✅ bridge/config.py — safe int parsing"

# ============================================================
# PATCH 2: bridge/mail_source.py — Fix ROWID check, fail-fast option
# ============================================================
cat > bridge/mail_source.py << 'PYEOF'
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

MAIL_DB_PATH = (Path.home() / "Library" / "Mail" / "V10"
                / "MailData" / "Envelope Index")


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
        self.mail_db = MAIL_DB_PATH
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
        """Verify required tables and columns exist.

        Note: ROWID is implicit in SQLite and not returned by
        PRAGMA table_info, so we verify it separately with a test query.
        """
        required_tables = {
            "messages", "subjects", "addresses", "summaries",
            "mailboxes", "message_global_data",
        }
        # ROWID excluded: implicit in SQLite, verified separately
        required_message_cols = {
            "sender", "subject", "summary", "date_received",
            "date_sent", "mailbox", "global_message_id",
            "read", "flagged", "deleted",
        }
        errors = []

        try:
            with self._connect() as conn:
                # Check tables
                tables = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing_tables = required_tables - tables
                if missing_tables:
                    errors.append(f"Missing tables: {missing_tables}")

                if "messages" in tables:
                    # Check columns
                    cols = {
                        r[1] for r in conn.execute(
                            "PRAGMA table_info(messages)"
                        ).fetchall()
                    }
                    missing_cols = required_message_cols - cols
                    if missing_cols:
                        errors.append(
                            f"Missing message columns: {missing_cols}")

                    # Verify ROWID is accessible
                    try:
                        conn.execute(
                            "SELECT ROWID FROM messages LIMIT 1")
                    except Exception as e:
                        errors.append(f"ROWID not accessible: {e}")

                    # Verify date format (Unix epoch)
                    row = conn.execute(
                        "SELECT date_received FROM messages "
                        "WHERE date_received IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if row:
                        ts = row[0]
                        if not (1_500_000_000 < ts < 2_000_000_000):
                            errors.append(
                                f"date_received={ts} may not be Unix "
                                "epoch. Expected range 1.5B-2.0B."
                            )

                # Verify key JOINs work
                if not errors:
                    try:
                        conn.execute("""
                            SELECT m.ROWID, sub.subject,
                                a.address, summ.summary
                            FROM messages m
                            LEFT JOIN subjects sub
                                ON sub.ROWID = m.subject
                            LEFT JOIN addresses a
                                ON a.ROWID = m.sender
                            LEFT JOIN summaries summ
                                ON summ.ROWID = m.summary
                            LIMIT 1
                        """)
                    except Exception as e:
                        errors.append(f"JOIN query failed: {e}")

        except Exception as e:
            errors.append(f"Schema check failed: {e}")

        self._schema_valid = len(errors) == 0
        return {"valid": self._schema_valid, "errors": errors}

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
PYEOF
echo "  ✅ bridge/mail_source.py — ROWID fix, JOIN verification, fail-fast support"

# ============================================================
# PATCH 3: bridge/messages_source.py — Better AppleScript service selection
# ============================================================
cat > bridge/messages_source.py << 'PYEOF'
import re
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager

MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = datetime(2001, 1, 1)


def normalize_handle(value: str) -> str:
    return (value or "").strip().lower()


class MessagesSource:
    def __init__(self, settings: dict):
        self.settings = settings
        self.primary_recipient = normalize_handle(
            settings["imessage"]["primary_recipient"])
        self.authorized_senders = {
            normalize_handle(x)
            for x in settings["imessage"]["authorized_senders"]}
        self.command_prefix = (
            settings["imessage"]["command_prefix"].lower())
        self.allow_same_account_commands = bool(
            settings["imessage"].get(
                "allow_same_account_commands", True))

    def can_access(self) -> bool:
        if not MESSAGES_DB.exists():
            return False
        try:
            with self._connect() as conn:
                conn.execute("SELECT COUNT(*) FROM message LIMIT 1")
            return True
        except Exception:
            return False

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(
            f"file:{MESSAGES_DB}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def send_alert(self, text: str) -> dict:
        """Send iMessage via AppleScript with stdin pipe for safety."""
        # Sanitize: remove control chars, normalize newlines
        clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        clean = clean.replace('\r\n', '\n').replace('\r', '\n')
        if len(clean) > 5000:
            clean = clean[:5000] + "... (truncated)"

        recipient = (self.primary_recipient
                     .replace('"', '').replace('\\', ''))

        # Uses 'first service whose service type = iMessage'
        # instead of hardcoded 'service 1' for robustness
        script_primary = f'''
on run argv
    set messageText to item 1 of argv
    tell application "Messages"
        set imsgService to first service whose service type = iMessage
        send messageText to buddy "{recipient}" of imsgService
    end tell
end run
'''
        script_fallback = f'''
on run argv
    set messageText to item 1 of argv
    tell application "Messages"
        send messageText to buddy "{recipient}"
    end tell
end run
'''
        last_error = ""
        for script in [script_primary, script_fallback]:
            try:
                result = subprocess.run(
                    ["osascript", "-", clean],
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    return {"success": True, "recipient": recipient}
                last_error = result.stderr.strip()
            except Exception as e:
                last_error = str(e)

        return {"success": False, "error": last_error}

    def get_pending_commands(
        self, since_rowid: int, limit: int = 20
    ) -> tuple[list[dict], str]:
        if not self.can_access():
            return [], str(since_rowid)

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT m.ROWID as rowid, m.text, m.is_from_me,
                    m.date, h.id as sender, m.service,
                    m.handle_id
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.ROWID > ? AND m.text IS NOT NULL
                ORDER BY m.ROWID ASC LIMIT ?
            """, (since_rowid, limit)).fetchall()

        results, max_rowid = [], since_rowid
        for row in rows:
            max_rowid = max(max_rowid, row["rowid"])
            sender = normalize_handle(row["sender"] or "")
            text = (row["text"] or "").strip()
            is_from_me = bool(row["is_from_me"])
            has_handle = (row["handle_id"] is not None
                          and row["handle_id"] > 0)

            if not text.lower().startswith(self.command_prefix):
                continue

            # Auth: self-sent with flag, or known sender with handle
            if is_from_me:
                if not self.allow_same_account_commands:
                    continue
            elif has_handle and sender in self.authorized_senders:
                pass
            else:
                continue

            raw_date = row["date"]
            if raw_date and raw_date > 1_000_000_000_000:
                dt = APPLE_EPOCH + timedelta(
                    seconds=raw_date / 1_000_000_000)
            elif raw_date:
                dt = APPLE_EPOCH + timedelta(seconds=raw_date)
            else:
                dt = datetime.now()

            results.append({
                "command_id": f"imsg-{row['rowid']}",
                "rowid": row["rowid"],
                "sender": sender if has_handle else "(self)",
                "text": text,
                "received_at": dt.isoformat(),
                "is_from_me": is_from_me,
            })

        return results, str(max_rowid)
PYEOF
echo "  ✅ bridge/messages_source.py — robust service selection"

# ============================================================
# PATCH 4: bridge/server.py — Remove command rate limit bug,
#           fail-fast on invalid schema, cleaner shutdown
# ============================================================
cat > bridge/server.py << 'PYEOF'
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.config import load_settings, get_token_path, validate_settings
from bridge.auth import load_token, is_authorized
from bridge.state import BridgeState
from bridge.rate_limit import RateLimiter
from bridge.mail_source import MailSource
from bridge.messages_source import MessagesSource

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.toml"
DATA_DB = PROJECT_ROOT / "data" / "bridge.db"
LOG_FILE = PROJECT_ROOT / "logs" / "bridge.log"
MAX_REQUEST_BODY = 65536

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_FILE, maxBytes=10_000_000, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bridge")


class AppContext:
    def __init__(self):
        self.settings = load_settings(str(SETTINGS_PATH))
        validate_settings(self.settings)
        self.token = load_token(get_token_path(self.settings))
        self.state = BridgeState(DATA_DB)
        self.rate = RateLimiter(DATA_DB)
        self.mail = MailSource(self.settings)
        self.messages = MessagesSource(self.settings)

        # Verify mail access and schema — fail fast if broken
        if not self.mail.can_access():
            raise RuntimeError(
                "Cannot access Mail database. "
                "Check Full Disk Access permissions.")

        schema = self.mail.verify_schema()
        if not schema["valid"]:
            raise RuntimeError(
                f"Incompatible Mail schema: {schema['errors']}. "
                "This may indicate an unsupported macOS version.")

        logger.info("Mail database accessible, schema valid")

        if not self.messages.can_access():
            logger.warning(
                "Cannot access Messages database — "
                "iMessage commands disabled")
        else:
            logger.info("Messages database accessible")


class Handler(BaseHTTPRequestHandler):
    ctx: AppContext = None

    def _json(self, code: int, payload: dict):
        raw = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BODY:
            self._json(413, {"error": "Payload too large"})
            return None
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return None

    def _validate_ack_token(self, data: dict) -> str | None:
        ack = str(data.get("ack_token", "0")).strip()
        if not ack.isdigit():
            self._json(400, {
                "error": "Invalid ack_token: must be numeric"})
            return None
        return ack

    def _auth(self) -> bool:
        ok = is_authorized(
            self.headers.get("Authorization", ""), self.ctx.token)
        if not ok:
            self.ctx.state.log_request(
                self.path, "auth_fail", False)
            self._json(401, {"error": "Unauthorized"})
        return ok

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Unauthenticated liveness probe
        if path == "/healthz":
            self._json(200, {"status": "ok"})
            return

        if not self._auth():
            return

        try:
            if path == "/health":
                self._json(200, {
                    "status": "ok",
                    "service": "bridge",
                    "mail_available": self.ctx.mail.can_access(),
                    "messages_available": (
                        self.ctx.messages.can_access()),
                    "timestamp": (
                        datetime.now(timezone.utc).isoformat()),
                })
                return

            if path == "/mail/schema":
                self._json(200, self.ctx.mail.debug_schema())
                return

            if path == "/mail/pending":
                limit = int(params.get("limit", ["25"])[0])
                ack = self.ctx.state.get_ack("mail", "0")
                items, next_ack = (
                    self.ctx.mail.get_pending_messages(
                        ack, limit=limit))
                self.ctx.state.log_request(
                    path, "mail_pending", True)
                self._json(200, {
                    "count": len(items),
                    "items": items,
                    "next_ack_token": next_ack,
                })
                return

            if path == "/commands/pending":
                if not self.ctx.messages.can_access():
                    self._json(200, {
                        "count": 0, "items": [],
                        "next_ack_token": "0"})
                    return
                # No rate limit on polling — only on command
                # execution/replies (handled via /alerts/send)
                limit = int(params.get("limit", ["20"])[0])
                ack = int(self.ctx.state.get_ack(
                    "commands", "0"))
                items, next_ack = (
                    self.ctx.messages.get_pending_commands(
                        ack, limit=limit))
                self.ctx.state.log_request(
                    path, "commands_pending", True)
                self._json(200, {
                    "count": len(items),
                    "items": items,
                    "next_ack_token": next_ack,
                })
                return

            self._json(404, {"error": "Not found"})

        except Exception as e:
            logger.exception("GET error on %s", path)
            self.ctx.state.log_request(path, "error", False)
            self._json(500, {"error": str(e)})

    def do_POST(self):
        if not self._auth():
            return
        path = urlparse(self.path).path

        try:
            data = self._read_json()
            if data is None:
                return

            if path == "/mail/ack":
                ack = self._validate_ack_token(data)
                if ack is None:
                    return
                self.ctx.state.set_ack("mail", ack)
                self.ctx.state.log_request(
                    path, "mail_ack", True)
                self._json(200, {
                    "success": True, "acked_through": ack})
                return

            if path == "/commands/ack":
                ack = self._validate_ack_token(data)
                if ack is None:
                    return
                self.ctx.state.set_ack("commands", ack)
                self.ctx.state.log_request(
                    path, "commands_ack", True)
                self._json(200, {"success": True})
                return

            if path == "/alerts/send":
                limit = self.ctx.settings[
                    "imessage"]["max_alerts_per_hour"]
                if not self.ctx.rate.allow(
                        "/alerts/send", limit, minutes=60):
                    self.ctx.state.log_request(
                        path, "rate_limited", False)
                    self._json(429, {
                        "error": "Rate limit exceeded"})
                    return
                text = (data.get("text") or "").strip()
                if not text:
                    self._json(400, {"error": "Missing text"})
                    return
                result = self.ctx.messages.send_alert(text)
                success = result.get("success", False)
                self.ctx.state.log_request(
                    path, "alerts_send", success)
                self._json(200 if success else 500, result)
                return

            self._json(404, {"error": "Not found"})

        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            logger.exception("POST error on %s", path)
            self.ctx.state.log_request(path, "error", False)
            self._json(500, {"error": str(e)})

    def log_message(self, format, *args):
        return


def main():
    ctx = AppContext()
    Handler.ctx = ctx
    host = ctx.settings["bridge"]["host"]
    port = int(ctx.settings["bridge"]["port"])
    logger.info("Bridge starting on %s:%s", host, port)

    server = ThreadingHTTPServer((host, port), Handler)

    def shutdown_handler(signum, frame):
        logger.info("Bridge signal %s, shutting down", signum)
        if ctx.settings["imessage"].get(
                "shutdown_notifications", False):
            try:
                ctx.messages.send_alert(
                    "🔴 Bridge shutting down")
            except Exception:
                pass
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Bridge stopped")


if __name__ == "__main__":
    main()
PYEOF
echo "  ✅ bridge/server.py — removed command rate limit bug, fail-fast schema"

# ============================================================
# PATCH 5: agent/app/state.py — Unique partial index on message_id
# ============================================================
cat > agent/app/state.py << 'PYEOF'
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


class AgentState:
    def __init__(self, db_path: str = "/app/data/agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                bridge_id TEXT PRIMARY KEY,
                message_id TEXT,
                processed_at TEXT,
                category TEXT,
                urgency TEXT,
                provider TEXT,
                alert_sent INTEGER,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS processed_commands (
                command_id TEXT PRIMARY KEY,
                processed_at TEXT,
                command_text TEXT,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bridge_id TEXT,
                sent_at TEXT,
                category TEXT,
                recipient TEXT,
                alert_text TEXT,
                success INTEGER
            );
            CREATE TABLE IF NOT EXISTS agent_flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Lookup index for message_id dedup queries
            CREATE INDEX IF NOT EXISTS idx_processed_message_id
                ON processed_messages(message_id);

            -- Uniqueness constraint: prevent true duplicates
            -- by Message-ID header (excludes synthetic rowid- IDs)
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_processed_real_message_id
                ON processed_messages(message_id)
                WHERE message_id IS NOT NULL
                  AND message_id != ''
                  AND message_id NOT LIKE 'rowid-%';

            CREATE INDEX IF NOT EXISTS idx_alerts_sent_at
                ON alerts(sent_at);
            """)
            conn.commit()

    def message_processed(self, bridge_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_messages "
                "WHERE bridge_id = ?",
                (bridge_id,)
            ).fetchone() is not None

    def message_id_processed(self, message_id: str) -> bool:
        """Check if we already processed an email with this
        Message-ID header. Skips synthetic IDs."""
        if (not message_id
                or message_id.startswith("rowid-")):
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_messages "
                "WHERE message_id = ? LIMIT 1",
                (message_id,)
            ).fetchone() is not None

    def save_message_result(
        self, bridge_id, message_id, category,
        urgency, provider, alert_sent, summary
    ):
        with self._connect() as conn:
            try:
                conn.execute("""
                INSERT OR REPLACE INTO processed_messages
                (bridge_id, message_id, processed_at, category,
                 urgency, provider, alert_sent, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (bridge_id, message_id, self._now(),
                      category, urgency, provider,
                      int(alert_sent), summary))
                conn.commit()
            except sqlite3.IntegrityError:
                # Unique message_id constraint hit —
                # already processed under different bridge_id
                pass

    def save_alert(self, bridge_id, category, recipient,
                   alert_text, success):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO alerts
            (bridge_id, sent_at, category, recipient,
             alert_text, success)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (bridge_id, self._now(), category,
                  recipient, alert_text, int(success)))
            conn.commit()

    def recent_alerts(self, limit: int = 5):
        with self._connect() as conn:
            return conn.execute(
                "SELECT sent_at, category, alert_text, success "
                "FROM alerts ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()

    def command_processed(self, command_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_commands "
                "WHERE command_id = ?",
                (command_id,)
            ).fetchone() is not None

    def save_command_result(self, command_id, command_text,
                            result):
        with self._connect() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO processed_commands
            (command_id, processed_at, command_text, result)
            VALUES (?, ?, ?, ?)
            """, (command_id, self._now(),
                  command_text, result))
            conn.commit()

    def get_bool_flag(self, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM agent_flags WHERE key = ?",
                (key,)
            ).fetchone()
            return ((row[0] if row else "false")
                    .lower() == "true")

    def set_bool_flag(self, key: str, value: bool):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO agent_flags (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE
                SET value = excluded.value
            """, (key, "true" if value else "false"))
            conn.commit()
PYEOF
echo "  ✅ agent/app/state.py — unique partial index, IntegrityError handling"

# ============================================================
# PATCH 6: agent/app/orchestrator.py — Record dedup hits,
#           better metric names
# ============================================================
cat > agent/app/orchestrator.py << 'PYEOF'
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger("agent.orchestrator")

MAX_PER_CYCLE = 50
MAX_CYCLE_SECONDS = 300


class Orchestrator:
    def __init__(self, bridge, classifier, state,
                 commands, settings, stats):
        self.bridge = bridge
        self.classifier = classifier
        self.state = state
        self.commands = commands
        self.settings = settings
        self.stats = stats

    def scan_mail_once(self):
        if self.commands.paused:
            logger.info("Scan skipped: paused")
            return

        cycle_start = time.time()
        total = 0

        while total < MAX_PER_CYCLE:
            if time.time() - cycle_start > MAX_CYCLE_SECONDS:
                logger.info("Cycle budget exceeded")
                break

            payload = self.bridge.mail_pending(
                limit=self.settings["mail"]["max_batch"])
            items = payload.get("items", [])
            if not items:
                break

            logger.info(
                "Processing %d emails (cycle total: %d)",
                len(items), total)
            last_ack = None

            for item in items:
                bid = item["bridge_id"]
                mid = item.get("message_id", "")

                # Dedup by bridge_id (ROWID-based)
                if self.state.message_processed(bid):
                    last_ack = str(item["source_rowid"])
                    continue

                # Dedup by Message-ID header
                if self.state.message_id_processed(mid):
                    logger.info("Dedup: %s (message_id)", bid)
                    self.stats.incr("emails_deduped")
                    # Record so bridge_id is also marked
                    self.state.save_message_result(
                        bid, mid, "dedup_skipped", "low",
                        "message_id_dedup", False,
                        "Duplicate Message-ID")
                    last_ack = str(item["source_rowid"])
                    continue

                # Classify
                try:
                    result = self.classifier.classify(item)
                except Exception:
                    logger.exception(
                        "Classification failed: %s", bid)
                    self.stats.incr("classification_failures")
                    break  # Stop batch, retry next cycle

                self.stats.incr("emails_seen")
                if result.provider == "apple_ml_prefilter":
                    self.stats.incr("emails_prefiltered")

                # Alert if needed
                alert_cats = set(
                    self.settings["agent"][
                        "alert_on_categories"])
                should_alert = (
                    result.category in alert_cats)
                alert_sent = False

                if should_alert and not self.commands.quiet:
                    alert_text = self._format_alert(
                        item, result)
                    try:
                        resp = self.bridge.send_alert(
                            alert_text)
                        alert_sent = bool(
                            resp.get("success", False))
                        self.state.save_alert(
                            bid, result.category,
                            resp.get("recipient", ""),
                            alert_text, alert_sent)
                        if alert_sent:
                            self.stats.incr("alerts_sent")
                    except Exception as e:
                        logger.error(
                            "Alert error %s: %s", bid, e)

                # Save result
                self.state.save_message_result(
                    bid, mid, result.category,
                    result.urgency, result.provider,
                    alert_sent, result.summary)

                last_ack = str(item["source_rowid"])
                total += 1

            # Ack through last successfully processed
            if last_ack:
                self.bridge.mail_ack(last_ack)
                logger.info("Acked through %s", last_ack)

        self.stats.update(
            last_scan=(
                datetime.now(timezone.utc).isoformat()))

    def scan_commands_once(self):
        payload = self.bridge.commands_pending(limit=20)
        items = payload.get("items", [])
        last_ack = None

        for item in items:
            if self.state.command_processed(
                    item["command_id"]):
                last_ack = str(item["rowid"])
                continue

            logger.info("Command: %s", item["text"])
            try:
                reply = self.commands.handle(item["text"])
                self.bridge.send_alert(
                    f"\U0001f916 {reply}")
            except Exception as e:
                logger.error("Command error: %s", e)
                reply = f"Error: {e}"

            self.state.save_command_result(
                item["command_id"], item["text"], reply)
            last_ack = str(item["rowid"])
            self.stats.incr("commands_processed")

        if last_ack:
            self.bridge.commands_ack(last_ack)

    def _format_alert(self, item, result):
        cat = result.category.replace("_", " ").title()
        sender = (item.get("sender_email")
                  or item.get("sender", "Unknown"))
        subject = item.get("subject", "(No Subject)")
        date = (item.get("date_received") or ""
                )[:16].replace("T", " ")
        return (
            f"\U0001f514 {cat} "
            f"[{result.urgency.upper()}]\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n"
            f"Summary: {result.summary}"
        )
PYEOF
echo "  ✅ agent/app/orchestrator.py — dedup records saved, better metrics"

# ============================================================
# PATCH 7: agent/app/main.py — Fixed metric names,
#           health server on 127.0.0.1
# ============================================================
cat > agent/app/main.py << 'PYEOF'
import time
import signal
import logging
from datetime import datetime, timezone

from app.config import load_settings
from app.bridge_client import BridgeClient
from app.state import AgentState
from app.classifier import Classifier
from app.commands import CommandHandler
from app.orchestrator import Orchestrator
from app.health import start_health_server, StatsView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent")

running = True


def main():
    global running

    def shutdown(signum, frame):
        global running
        logger.info("Signal %s, shutting down", signum)
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Agent starting...")
    settings = load_settings()
    state = AgentState()
    classifier = Classifier(settings)
    commands = CommandHandler(state)

    stats = StatsView({
        "started_at": (
            datetime.now(timezone.utc).isoformat()),
        "emails_seen": 0,
        "emails_prefiltered": 0,
        "emails_deduped": 0,
        "alerts_sent": 0,
        "classification_failures": 0,
        "commands_processed": 0,
        "last_scan": None,
        "last_error": None,
    })

    # Bind health server to localhost inside container
    start_health_server(stats, host="127.0.0.1", port=8080)
    logger.info("Health server on 127.0.0.1:8080")

    # Retry bridge connection (3 minutes)
    bridge = BridgeClient()
    bridge_ready = False
    for attempt in range(18):
        try:
            health = bridge.health()
            logger.info("Bridge health: %s", health)
            bridge_ready = True
            break
        except Exception as e:
            logger.warning(
                "Bridge not ready (%d/18): %s",
                attempt + 1, e)
            time.sleep(10)

    if not bridge_ready:
        logger.error("Bridge unreachable after 3 minutes")
        return

    orch = Orchestrator(
        bridge, classifier, state, commands,
        settings, stats)

    if settings["imessage"].get(
            "startup_notifications", True):
        try:
            bridge.send_alert(
                "\U0001f916 Mail agent started")
        except Exception:
            logger.warning("Startup notification failed")

    poll_mail = int(
        settings["agent"]["poll_interval_seconds"])
    poll_cmd = int(
        settings["agent"]["command_poll_interval_seconds"])
    last_mail = last_cmd = 0.0

    logger.info(
        "Main loop (mail %ds, commands %ds)",
        poll_mail, poll_cmd)

    while running:
        now = time.time()
        try:
            if (now - last_mail >= poll_mail
                    or commands.scan_requested):
                orch.scan_mail_once()
                last_mail = now
                commands.scan_requested = False

            if now - last_cmd >= poll_cmd:
                orch.scan_commands_once()
                last_cmd = now

            time.sleep(2)

        except Exception as e:
            stats.update(last_error=str(e))
            logger.exception("Main loop error")
            time.sleep(10)

    logger.info("Agent stopped")
    if settings["imessage"].get(
            "shutdown_notifications", False):
        try:
            bridge.send_alert(
                "\U0001f534 Agent shutting down")
        except Exception:
            pass
    try:
        bridge.client.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
PYEOF
echo "  ✅ agent/app/main.py — health on localhost, metric names aligned"

# ============================================================
# Summary
# ============================================================
echo ""
echo "==========================================="
echo "  v1.1.1 Patch Applied Successfully"
echo "==========================================="
echo ""
echo "  Changes:"
echo "  1. bridge/config.py    — safe int parsing"
echo "  2. bridge/mail_source  — ROWID fix, JOIN verify, fail-fast"
echo "  3. bridge/messages_src — robust AppleScript service selection"
echo "  4. bridge/server.py    — removed /commands/pending rate limit"
echo "                           fail-fast on invalid mail schema"
echo "  5. agent/app/state.py  — unique partial index on message_id"
echo "  6. agent/orchestrator  — dedup hits recorded, metric names"
echo "  7. agent/app/main.py   — health server on 127.0.0.1"
echo ""
echo "  Next steps:"
echo "    cd ~/agentic-ai"
echo "    docker compose build"
echo "    pkill -f bridge.server; sleep 2"
echo "    open ~/Applications/AgenticBridge.app"
echo "    sleep 3"
echo "    docker compose up -d"
echo ""