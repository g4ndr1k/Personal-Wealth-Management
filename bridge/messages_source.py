import re
import sqlite3
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

log = logging.getLogger(__name__)

MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


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
        # Validate recipient format to prevent AppleScript injection
        if not re.fullmatch(r"[+0-9]{5,20}|[\w.+-]+@[\w.-]+", recipient):
            log.warning("Rejected malformed iMessage recipient: %r", recipient)
            return {"success": False, "error": "invalid recipient format"}

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
