#!/bin/bash
set -euo pipefail

BASE="$HOME/agentic-ai"
cd "$BASE"

echo "Creating directory structure..."
mkdir -p bridge config secrets data logs scripts agent/app/providers

# ============================================================
# bridge/__init__.py
# ============================================================
cat > bridge/__init__.py << 'PYEOF'
# bridge package
PYEOF

# ============================================================
# bridge/config.py
# ============================================================
cat > bridge/config.py << 'PYEOF'
from pathlib import Path
import tomllib


def load_settings(path: str = "config/settings.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_token_path(settings: dict) -> Path:
    return Path(settings["auth"]["token_file"]).expanduser()
PYEOF

# ============================================================
# bridge/auth.py
# ============================================================
cat > bridge/auth.py << 'PYEOF'
import hmac
from pathlib import Path


def load_token(token_file: Path) -> str:
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError("Bridge token file is empty")
    return token


def is_authorized(header_value: str, token: str) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    supplied = header_value[7:].strip()
    return hmac.compare_digest(supplied.encode(), token.encode())
PYEOF

# ============================================================
# bridge/state.py
# ============================================================
cat > bridge/state.py << 'PYEOF'
import sqlite3
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager


class BridgeState:
    def __init__(self, db_path: Path):
        self.db_path = db_path
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

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                stream TEXT PRIMARY KEY,
                ack_token TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                action TEXT NOT NULL,
                success INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            conn.commit()

    def get_ack(self, stream: str, default: str = "0") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ack_token FROM checkpoints WHERE stream = ?",
                (stream,)
            ).fetchone()
            return row[0] if row else default

    def set_ack(self, stream: str, ack_token: str):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO checkpoints (stream, ack_token)
            VALUES (?, ?)
            ON CONFLICT(stream) DO UPDATE SET ack_token = excluded.ack_token
            """, (stream, ack_token))
            conn.commit()

    def log_request(self, endpoint: str, action: str, success: bool):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO request_log(endpoint, action, success, created_at)
            VALUES (?, ?, ?, ?)
            """, (endpoint, action, int(success), datetime.now().isoformat()))
            conn.commit()
PYEOF

# ============================================================
# bridge/rate_limit.py
# ============================================================
cat > bridge/rate_limit.py << 'PYEOF'
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager


class RateLimiter:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def count_recent(self, endpoint: str, minutes: int = 60) -> int:
        with self._connect() as conn:
            cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
            row = conn.execute("""
                SELECT COUNT(*)
                FROM request_log
                WHERE endpoint = ? AND created_at >= ? AND success = 1
            """, (endpoint, cutoff)).fetchone()
            return row[0] if row else 0

    def allow(self, endpoint: str, limit: int, minutes: int = 60) -> bool:
        return self.count_recent(endpoint, minutes) < limit
PYEOF

# ============================================================
# bridge/mail_source.py
# ============================================================
cat > bridge/mail_source.py << 'PYEOF'
"""
Mail source adapter for macOS Tahoe (26.x) Mail.app V10 schema.

Tahoe V10 uses a fully normalized schema:
- messages.sender -> addresses.ROWID (direct FK)
- messages.subject -> subjects.ROWID
- messages.summary -> summaries.ROWID (body text, when available)
- messages.mailbox -> mailboxes.ROWID
- messages.global_message_id -> message_global_data.ROWID
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager
from urllib.parse import unquote


MAIL_DB_PATH = Path.home() / "Library" / "Mail" / "V10" / "MailData" / "Envelope Index"
APPLE_EPOCH = datetime(2001, 1, 1)


def apple_epoch_to_datetime(value) -> datetime | None:
    if value is None:
        return None
    try:
        return APPLE_EPOCH + timedelta(seconds=float(value))
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
        self.max_body_text_bytes = int(settings["mail"].get("max_body_text_bytes", 200000))
        self.initial_lookback_days = int(settings["mail"].get("initial_lookback_days", 7))
        self.max_batch = int(settings["mail"].get("max_batch", 25))

    def can_access(self) -> bool:
        if not self.mail_db.exists():
            return False
        try:
            with self._connect() as conn:
                conn.execute("SELECT COUNT(*) FROM messages LIMIT 1")
            return True
        except Exception:
            return False

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(
            f"file:{self.mail_db}?mode=ro",
            uri=True,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def debug_schema(self) -> dict:
        with self._connect() as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            cols = [
                r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()
            ]
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            with_sender = conn.execute("""
                SELECT COUNT(*) FROM messages m
                JOIN addresses a ON a.ROWID = m.sender
            """).fetchone()[0]
            with_body = conn.execute("""
                SELECT COUNT(*) FROM messages m
                JOIN summaries s ON s.ROWID = m.summary
            """).fetchone()[0]

            return {
                "db_path": str(self.mail_db),
                "schema_version": "V10-Tahoe",
                "tables": tables,
                "messages_columns": cols,
                "total_messages": total,
                "messages_with_sender_email": with_sender,
                "messages_with_body_text": with_body,
                "join_strategy": "messages.sender -> addresses.ROWID (direct)",
            }

    def get_pending_messages(
        self, ack_token: str, limit: int = 25
    ) -> tuple[list[dict], str]:

        ack_rowid = int(ack_token or "0")
        query_limit = min(max(1, limit), self.max_batch)

        with self._connect() as conn:
            where_parts = []
            params: list = []

            if ack_rowid > 0:
                where_parts.append("m.ROWID > ?")
                params.append(ack_rowid)
            else:
                cutoff = datetime.now() - timedelta(days=self.initial_lookback_days)
                cutoff_ref = (cutoff - APPLE_EPOCH).total_seconds()
                where_parts.append("m.date_received >= ?")
                params.append(cutoff_ref)

            where_sql = " AND ".join(where_parts) if where_parts else "1=1"

            query = f"""
            SELECT
                m.ROWID,
                sub.subject,
                a.address AS sender_email,
                a.comment AS sender_name,
                summ.summary AS body_text,
                m.date_received,
                m.date_sent,
                mb.url AS mailbox_url,
                mgd.message_id_header,
                mgd.model_category AS apple_category,
                mgd.model_high_impact AS apple_high_impact,
                mgd.urgent AS apple_urgent,
                m.read,
                m.flagged,
                m.deleted
            FROM messages m
            LEFT JOIN subjects sub ON sub.ROWID = m.subject
            LEFT JOIN addresses a ON a.ROWID = m.sender
            LEFT JOIN summaries summ ON summ.ROWID = m.summary
            LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
            LEFT JOIN message_global_data mgd ON mgd.ROWID = m.global_message_id
            WHERE {where_sql}
              AND m.deleted = 0
            ORDER BY m.ROWID ASC
            LIMIT ?
            """
            params.append(query_limit)

            rows = conn.execute(query, params).fetchall()

        results = []
        max_rowid = ack_rowid

        for row in rows:
            rowid = row["ROWID"]
            max_rowid = max(max_rowid, rowid)

            raw_body = row["body_text"] or ""
            body_text, body_truncated = truncate_bytes(raw_body, self.max_body_text_bytes)

            sender_email = row["sender_email"] or ""
            sender_name = row["sender_name"] or ""
            if sender_name and sender_email:
                sender_combined = f"{sender_name} <{sender_email}>"
            else:
                sender_combined = sender_email or sender_name or "Unknown"

            mailbox_url = row["mailbox_url"] or ""
            mailbox_folder = parse_mailbox_folder(mailbox_url)

            results.append({
                "bridge_id": f"mail-{rowid}",
                "source_rowid": rowid,
                "message_id": row["message_id_header"] or f"rowid-{rowid}",
                "mailbox": mailbox_folder,
                "mailbox_url": mailbox_url,
                "sender": sender_combined,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "subject": row["subject"] or "(No Subject)",
                "date_received": (
                    apple_epoch_to_datetime(row["date_received"]).isoformat()
                    if row["date_received"] else None
                ),
                "date_sent": (
                    apple_epoch_to_datetime(row["date_sent"]).isoformat()
                    if row["date_sent"] else None
                ),
                "snippet": (body_text[:500] if body_text else ""),
                "body_text": body_text,
                "body_html": "",
                "body_text_truncated": body_truncated,
                "body_html_truncated": False,
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

# ============================================================
# bridge/messages_source.py
# ============================================================
cat > bridge/messages_source.py << 'PYEOF'
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
            settings["imessage"]["primary_recipient"]
        )
        self.authorized_senders = {
            normalize_handle(x)
            for x in settings["imessage"]["authorized_senders"]
        }
        self.command_prefix = settings["imessage"]["command_prefix"].lower()
        self.allow_same_account_commands = bool(
            settings["imessage"].get("allow_same_account_commands", True)
        )

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
            f"file:{MESSAGES_DB}?mode=ro",
            uri=True,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def send_alert(self, text: str) -> dict:
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        if len(safe_text) > 5000:
            safe_text = safe_text[:5000] + "... (truncated)"

        recipient = self.primary_recipient.replace('"', "").replace("\\", "")

        scripts = [
            f'tell application "Messages" to send "{safe_text}" to buddy "{recipient}" of (service 1 whose service type is iMessage)',
            f'tell application "Messages" to send "{safe_text}" to buddy "{recipient}"',
        ]

        last_error = ""
        for script in scripts:
            try:
                result = subprocess.run(
                    ["osascript", "-e", script],
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
                SELECT
                    m.ROWID as rowid,
                    m.text as text,
                    m.is_from_me as is_from_me,
                    m.date as date,
                    h.id as sender,
                    m.service as service
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.ROWID > ?
                  AND m.text IS NOT NULL
                ORDER BY m.ROWID ASC
                LIMIT ?
            """, (since_rowid, limit)).fetchall()

        results = []
        max_rowid = since_rowid

        for row in rows:
            max_rowid = max(max_rowid, row["rowid"])

            sender = normalize_handle(row["sender"] or "")
            text = (row["text"] or "").strip()
            is_from_me = bool(row["is_from_me"])

            if not text.lower().startswith(self.command_prefix):
                continue
            if sender not in self.authorized_senders and not is_from_me:
                continue
            if is_from_me and not self.allow_same_account_commands:
                continue

            raw_date = row["date"]
            if raw_date and raw_date > 1_000_000_000_000:
                dt = APPLE_EPOCH + timedelta(seconds=raw_date / 1_000_000_000)
            elif raw_date:
                dt = APPLE_EPOCH + timedelta(seconds=raw_date)
            else:
                dt = datetime.now()

            results.append({
                "command_id": f"imsg-{row['rowid']}",
                "rowid": row["rowid"],
                "sender": sender,
                "text": text,
                "received_at": dt.isoformat(),
                "is_from_me": is_from_me,
            })

        return results, str(max_rowid)
PYEOF

# ============================================================
# bridge/server.py
# ============================================================
cat > bridge/server.py << 'PYEOF'
import json
import logging
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.config import load_settings, get_token_path
from bridge.auth import load_token, is_authorized
from bridge.state import BridgeState
from bridge.rate_limit import RateLimiter
from bridge.mail_source import MailSource
from bridge.messages_source import MessagesSource


SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.toml"
DATA_DB = PROJECT_ROOT / "data" / "bridge.db"
LOG_FILE = PROJECT_ROOT / "logs" / "bridge.log"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger("bridge")


class AppContext:
    def __init__(self):
        self.settings = load_settings(str(SETTINGS_PATH))
        self.token = load_token(get_token_path(self.settings))
        self.state = BridgeState(DATA_DB)
        self.rate = RateLimiter(DATA_DB)
        self.mail = MailSource(self.settings)
        self.messages = MessagesSource(self.settings)

        if not self.mail.can_access():
            logger.error("Cannot access Mail database - check Full Disk Access")
        else:
            logger.info("Mail database accessible")

        if not self.messages.can_access():
            logger.warning("Cannot access Messages database - iMessage commands disabled")
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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _auth(self) -> bool:
        ok = is_authorized(self.headers.get("Authorization", ""), self.ctx.token)
        if not ok:
            self.ctx.state.log_request(self.path, "auth_fail", False)
            self._json(401, {"error": "Unauthorized"})
        return ok

    def do_GET(self):
        if not self._auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        try:
            if path == "/health":
                self._json(200, {
                    "status": "ok",
                    "service": "bridge",
                    "mail_available": self.ctx.mail.can_access(),
                    "messages_available": self.ctx.messages.can_access(),
                    "timestamp": datetime.now().isoformat(),
                })
                return

            if path == "/mail/schema":
                self._json(200, self.ctx.mail.debug_schema())
                return

            if path == "/mail/pending":
                limit = int(params.get("limit", ["25"])[0])
                ack = self.ctx.state.get_ack("mail", "0")
                items, next_ack = self.ctx.mail.get_pending_messages(ack, limit=limit)
                self.ctx.state.log_request(path, "mail_pending", True)
                self._json(200, {
                    "count": len(items),
                    "items": items,
                    "next_ack_token": next_ack,
                })
                return

            if path == "/commands/pending":
                if not self.ctx.messages.can_access():
                    self._json(200, {"count": 0, "items": [], "next_ack_token": "0"})
                    return
                limit = int(params.get("limit", ["20"])[0])
                ack = int(self.ctx.state.get_ack("commands", "0"))
                items, next_ack = self.ctx.messages.get_pending_commands(ack, limit=limit)
                self.ctx.state.log_request(path, "commands_pending", True)
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

            if path == "/mail/ack":
                ack_token = str(data.get("ack_token", "0"))
                self.ctx.state.set_ack("mail", ack_token)
                self.ctx.state.log_request(path, "mail_ack", True)
                self._json(200, {"success": True, "acked_through": ack_token})
                return

            if path == "/commands/ack":
                ack_token = str(data.get("ack_token", "0"))
                self.ctx.state.set_ack("commands", ack_token)
                self.ctx.state.log_request(path, "commands_ack", True)
                self._json(200, {"success": True})
                return

            if path == "/alerts/send":
                limit = self.ctx.settings["imessage"]["max_alerts_per_hour"]
                if not self.ctx.rate.allow("/alerts/send", limit, minutes=60):
                    self.ctx.state.log_request(path, "rate_limited", False)
                    self._json(429, {"error": "Rate limit exceeded"})
                    return

                text = (data.get("text") or "").strip()
                if not text:
                    self._json(400, {"error": "Missing text"})
                    return

                result = self.ctx.messages.send_alert(text)
                success = result.get("success", False)
                self.ctx.state.log_request(path, "alerts_send", success)
                self._json(200 if success else 500, result)
                return

            self._json(404, {"error": "Not found"})

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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Bridge shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
PYEOF

echo ""
echo "=== All bridge files created ==="
echo ""
echo "Verifying..."
for f in bridge/__init__.py bridge/config.py bridge/auth.py bridge/state.py \
         bridge/rate_limit.py bridge/mail_source.py bridge/messages_source.py \
         bridge/server.py; do
    if [ -f "$f" ]; then
        echo "  ✅ $f"
    else
        echo "  ❌ $f MISSING"
    fi
done

echo ""
echo "Testing import..."
cd "$BASE"
PYTHONPATH="$BASE" python3 -c "from bridge.server import main; print('✅ bridge.server imports OK')"