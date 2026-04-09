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

import email as _email_module
import html as _html_module
import logging
import re
import sqlite3
import subprocess
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
        # Detect optional schema features at startup
        self._has_document_id = False
        self._gen_summary_join: str | None = None  # SQL fragment for JOIN
        self._detect_optional_schema()
        self._log_mail_directory_structure()

    def _log_mail_directory_structure(self) -> None:
        """Log top-level folder names under ~/Library/Mail/V* for debugging."""
        log = logging.getLogger("bridge.mail_source")
        try:
            mail_root = Path.home() / "Library" / "Mail"
            for v_dir in sorted(mail_root.glob("V*"), reverse=True):
                folders = sorted(p.name for p in v_dir.iterdir()
                                 if p.is_dir())[:10]
                log.info("Mail %s folders: %s", v_dir.name, folders)
                break  # Only need first V* dir
        except Exception as e:
            log.warning("Could not list Mail directory: %s", e)

    def _detect_optional_schema(self) -> None:
        """Detect optional schema features (document_id, generated_summaries)."""
        import logging
        log = logging.getLogger("bridge.mail_source")
        try:
            with self._connect() as conn:
                # Check messages.document_id
                msg_cols = {
                    r[1] for r in conn.execute(
                        "PRAGMA table_info(messages)").fetchall()
                }
                self._has_document_id = "document_id" in msg_cols

                # Check generated_summaries and find its join column
                tables = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table'").fetchall()
                }
                if "generated_summaries" in tables:
                    gs_cols = {
                        r[1] for r in conn.execute(
                            "PRAGMA table_info(generated_summaries)"
                        ).fetchall()
                    }
                    # Common join patterns in order of preference
                    if "message_id" in gs_cols:
                        self._gen_summary_join = \
                            "LEFT JOIN generated_summaries gs " \
                            "ON gs.message_id = m.ROWID"
                    elif "mail_id" in gs_cols:
                        self._gen_summary_join = \
                            "LEFT JOIN generated_summaries gs " \
                            "ON gs.mail_id = m.ROWID"
                    elif "summary" in gs_cols:
                        # Fall back to ROWID-based join (same primary key)
                        self._gen_summary_join = \
                            "LEFT JOIN generated_summaries gs " \
                            "ON gs.ROWID = m.ROWID"
                    log.info(
                        "generated_summaries detected, cols=%s, join=%s",
                        sorted(gs_cols), self._gen_summary_join)
        except Exception as e:
            log.warning("Optional schema detection failed: %s", e)

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

    def _find_emlx_by_spotlight(self, message_id: str) -> Path | None:
        """Use mdfind (Spotlight) to locate the .emlx file by Message-ID.

        This does not require Automation permission and works from daemon context.
        Returns None if Spotlight doesn't index ~/Library/Mail or the message
        isn't found.
        """
        if not message_id or message_id.startswith("rowid-"):
            return None
        log = logging.getLogger("bridge.mail_source")
        mail_root = str(Path.home() / "Library" / "Mail")
        safe_id = message_id.replace("'", "").replace("\\", "")
        # Try known Spotlight attributes for RFC 2822 Message-ID
        for attr in ("kMDItemIdentifier", "kMDItemMessageID",
                     "com_apple_mail_messageID"):
            try:
                r = subprocess.run(
                    ["mdfind", f"{attr} == '{safe_id}'",
                     "-onlyin", mail_root],
                    capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    for p in r.stdout.strip().split("\n"):
                        if p.endswith(".emlx"):
                            log.info("Spotlight found emlx via %s: %s",
                                     attr, p)
                            return Path(p)
                elif r.returncode != 0:
                    log.info("mdfind %s returncode=%d stderr=%s",
                             attr, r.returncode, r.stderr[:80])
            except subprocess.TimeoutExpired:
                log.warning("mdfind timed out for attr=%s msgid=%s",
                            attr, message_id[:40])
            except Exception as e:
                log.warning("mdfind error (%s): %s", attr, e)
        log.debug("Spotlight found no emlx for %s", message_id[:40])
        return None

    def _mailbox_to_fs_path(self, mailbox_url: str) -> Path | None:
        """Convert a Mail.app mailbox URL to its Messages directory on disk.

        Format: imap://{account_uuid}/{encoded_mailbox_path}
        Maps to: ~/Library/Mail/V*/{account_uuid}.mbox/{mailbox}.mbox/Messages/
        """
        log = logging.getLogger("bridge.mail_source")
        if not mailbox_url:
            return None
        try:
            decoded = unquote(mailbox_url)
            # Strip scheme: imap://UUID/path  or  mailboxes://UUID/path
            no_scheme = re.sub(r"^[a-z]+://", "", decoded)
            # UUID is the first path component
            parts = no_scheme.split("/", 1)
            if len(parts) < 2:
                return None
            account_id = parts[0]
            mailbox_path = parts[1].strip("/")
            # Build filesystem path
            mail_root = Path.home() / "Library" / "Mail"
            # Account folder: V*/{account_id}.mbox
            for v_dir in sorted(mail_root.glob("V*"), reverse=True):
                # V10 Tahoe: account dirs are plain UUIDs (no .mbox suffix)
                for suffix in ("", ".mbox"):
                    acct_dir = v_dir / f"{account_id}{suffix}"
                    if not acct_dir.exists():
                        continue
                    # Log account dir contents to understand structure
                    try:
                        contents = sorted(p.name for p in acct_dir.iterdir()
                                          if p.is_dir())[:6]
                        log.info("acct_dir %s contents: %s",
                                 acct_dir.name, contents)
                    except Exception:
                        pass
                    # Mailbox path: each component gets a .mbox suffix
                    # e.g. "[Gmail]/All Mail" → "[Gmail].mbox/All Mail.mbox"
                    sub_parts = mailbox_path.split("/")
                    sub = Path("/".join(p + ".mbox" for p in sub_parts))
                    messages_dir = acct_dir / sub / "Messages"
                    log.info("Messages dir: %s exists=%s",
                             messages_dir, messages_dir.exists())
                    if messages_dir.exists():
                        return messages_dir
        except Exception as e:
            log.warning("_mailbox_to_fs_path error: %s", e)
        return None

    def _find_emlx(self, rowid: int, document_id: str | None = None,
                   remote_id: int | None = None,
                   mailbox_url: str | None = None,
                   message_id: int | None = None) -> Path | None:
        """Search for the .emlx file for this message.

        In Mail.app V10 Tahoe, emlx files are named by messages.message_id
        (a local integer ID), NOT the IMAP remote_id (server UID).

        Tries in order:
        1. account dir + message_id.emlx glob (correct for V10 Tahoe)
        2. account dir + remote_id.emlx glob (fallback)
        3. document_id-based broad glob
        4. ROWID-based broad glob (older Mail.app layout)
        """
        log = logging.getLogger("bridge.mail_source")
        mail_root = Path.home() / "Library" / "Mail"
        try:
            account_id = None
            if mailbox_url:
                decoded = unquote(mailbox_url)
                no_scheme = re.sub(r"^[a-z]+://", "", decoded)
                account_id = no_scheme.split("/", 1)[0]

            if account_id:
                for v_dir in sorted(mail_root.glob("V*"), reverse=True):
                    for suffix in ("", ".mbox"):
                        acct_dir = v_dir / f"{account_id}{suffix}"
                        if not acct_dir.exists():
                            continue
                        # Strategy 1: message_id (local ID, correct for V10)
                        if message_id:
                            matches = list(
                                acct_dir.glob(f"**/{message_id}.emlx"))
                            if matches:
                                log.info(
                                    "Found emlx via message_id: %s",
                                    matches[0])
                                return matches[0]
                        # Strategy 2: remote_id (IMAP UID, may differ)
                        if remote_id:
                            matches = list(
                                acct_dir.glob(f"**/{remote_id}.emlx"))
                            if matches:
                                log.info(
                                    "Found emlx via remote_id: %s",
                                    matches[0])
                                return matches[0]
                log.debug(
                    "Acct glob miss rowid=%s msg_id=%s remote_id=%s acct=%s",
                    rowid, message_id, remote_id, account_id)
            # Strategy 3: document_id broad glob
            if document_id:
                matches = list(mail_root.glob(f"V*/**/{document_id}.emlx"))
                if matches:
                    return matches[0]
            # Strategy 4: message_id broad glob
            if message_id:
                matches = list(mail_root.glob(f"V*/**/{message_id}.emlx"))
                if matches:
                    log.info("Found emlx via broad message_id glob: %s",
                             matches[0])
                    return matches[0]
            # Strategy 5: ROWID broad glob (older Mail.app layout)
            matches = list(mail_root.glob(f"V*/**/{rowid}.emlx"))
            if matches:
                log.info("Found emlx via ROWID glob: %s", matches[0])
                return matches[0]
            log.debug("No emlx found rowid=%s msg_id=%s remote_id=%s",
                      rowid, message_id, remote_id)
            return None
        except Exception as e:
            log.warning("_find_emlx error rowid=%s: %s", rowid, e)
            return None

    def _read_emlx_body(self, path: Path) -> str:
        """Parse a .emlx file and return plain-text body (up to 6000 chars).

        .emlx format:
          Line 1: byte count of the RFC 2822 message (ASCII integer)
          Next N bytes: RFC 2822 MIME message
          Remainder: plist XML (ignored)
        """
        log = logging.getLogger("bridge.mail_source")
        try:
            raw = path.read_bytes()
        except OSError as e:
            log.warning("emlx read error %s: %s", path.name, e)
            return ""

        # Find the first newline to get the byte count
        nl = raw.find(b"\n")
        if nl < 0:
            return ""
        try:
            msg_len = int(raw[:nl].strip())
        except ValueError:
            return ""

        rfc822_bytes = raw[nl + 1: nl + 1 + msg_len]
        if not rfc822_bytes.strip():
            return ""

        rfc822 = rfc822_bytes.decode("utf-8", errors="replace")
        msg = _email_module.message_from_string(rfc822)
        parts = []
        walk = msg.walk() if msg.is_multipart() else [msg]
        for part in walk:
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_param("charset") or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                parts.append(decoded)
            elif ctype == "text/html" and not parts:
                # Remove style/script blocks before stripping tags
                decoded = re.sub(
                    r"<(style|script)[^>]*>.*?</(style|script)>",
                    " ", decoded, flags=re.DOTALL | re.IGNORECASE)
                decoded = re.sub(r"<[^>]+>", " ", decoded)
                decoded = _html_module.unescape(decoded)
                parts.append(" ".join(decoded.split()))
        result = "\n".join(parts)[:6000]
        log.debug("emlx body extracted %s: %d chars", path.name, len(result))
        return result

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

            doc_id_col = (
                "m.document_id" if self._has_document_id else "NULL")
            gen_sum_col = (
                "gs.summary" if self._gen_summary_join else "NULL")
            gen_sum_join = self._gen_summary_join or ""

            query = f"""
            SELECT m.ROWID, sub.subject,
                a.address AS sender_email,
                a.comment AS sender_name,
                summ.summary AS body_text,
                {gen_sum_col} AS generated_summary,
                {doc_id_col} AS document_id,
                m.message_id,
                m.remote_id,
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
            {gen_sum_join}
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

            # Prefer Apple Intelligence generated summary when richer
            gen_summary = (row["generated_summary"] or "").strip()
            if len(gen_summary) > len(body_text):
                body_text = gen_summary
                body_truncated = False

            document_id = row["document_id"] or None
            message_id = row["message_id"] if row["message_id"] else None
            remote_id = row["remote_id"] if row["remote_id"] else None
            mailbox_url_row = row["mailbox_url"] or ""

            # If the summaries/generated tables gave us little or no content,
            # try to find and read the actual .emlx file on disk.
            body_source = "summaries_db"
            message_id_header = row["message_id_header"] or ""
            if len(body_text.encode("utf-8", errors="ignore")) < 200:
                emlx_path = self._find_emlx(
                    rowid, document_id, remote_id, mailbox_url_row,
                    message_id=message_id)
                if emlx_path:
                    emlx_body = self._read_emlx_body(emlx_path)
                    if len(emlx_body) > len(body_text):
                        body_text = emlx_body
                        body_truncated = len(body_text) >= 6000
                        body_source = "emlx_file"

            if len(body_text.encode("utf-8", errors="ignore")) < 200:
                spot_path = self._find_emlx_by_spotlight(message_id_header)
                if spot_path:
                    spot_body = self._read_emlx_body(spot_path)
                    if len(spot_body) > len(body_text):
                        body_text = spot_body
                        body_truncated = len(body_text) >= 6000
                        body_source = "spotlight_emlx"

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
                "body_source": body_source,
                "has_body": bool(body_text),
                "apple_category": row["apple_category"],
                "apple_high_impact": row["apple_high_impact"],
                "apple_urgent": row["apple_urgent"],
                "is_read": bool(row["read"]),
                "is_flagged": bool(row["flagged"]),
                "attachments": [],
            })

        return results, str(max_rowid)
