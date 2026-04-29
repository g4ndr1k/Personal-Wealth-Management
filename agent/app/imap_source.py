"""
imap_source.py — Agent-side IMAP intake module.

Fetches emails directly via IMAP (imaplib, stdlib, sync).
One IMAPAccount per configured mailbox under [mail.imap].

Design notes:
  - Fresh TCP connection per poll cycle (no long-lived sockets).
  - TLS only (IMAP4_SSL). Connection timeout 15 s.
  - UID SEARCH since checkpoint UID per (account, folder).
  - UIDVALIDITY is tracked; mismatch triggers bounded lookback re-scan.
  - Idempotency keys: message_key = account + folder + normalize(message_id)
    fallback_message_key = account + folder + uidvalidity + uid
  - Size guards: max_message_mb / max_attachment_mb (skip oversized items).
  - Attachment metadata is surfaced; decoding is left to pdf_router.
  - Auth: 'keychain' (macOS host — security CLI) or 'file' (Docker-friendly).

State storage: agent/app/state.AgentState (imap_folder_state table).
"""

import email as _email_mod
import email.header
import email.utils
import hashlib
import html as _html
import imaplib
import json
import logging
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.imap_source")

# ── Auth helper ──────────────────────────────────────────────────────────────

_SUBPROCESS_ENV = {**os.environ, "MallocStackLogging": "0"}
DEFAULT_IMAP_SECRETS_FILE = os.environ.get(
    "IMAP_SECRETS_FILE", "/app/secrets/imap.toml")


def _normalize_app_password(password: str | None) -> str:
    return "".join(str(password or "").split())


def _read_toml(path: Path) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomllib  # type: ignore
        except ImportError:
            import tomli as tomllib  # type: ignore
    with open(path, "rb") as f:
        return tomllib.load(f)


def _keychain_get(service: str, account: str) -> str | None:
    """Read a secret from macOS Keychain via the `security` CLI."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5,
            env=_SUBPROCESS_ENV,
        )
        if result.returncode == 0 and result.stdout.strip():
            val = result.stdout.strip()
            # Auto-detect hex encoding (security CLI hex-encodes non-ASCII)
            if len(val) >= 4 and len(val) % 2 == 0:
                try:
                    decoded = bytes.fromhex(val).decode("utf-8")
                    return decoded
                except (ValueError, UnicodeDecodeError):
                    pass
            return val
    except FileNotFoundError:
        log.debug("'security' CLI not found — running in non-macOS environment")
    except Exception as e:
        log.warning("Keychain lookup error for %s/%s: %s", service, account, e)
    return None


def _load_app_password(acct_cfg: dict) -> str | None:
    """Resolve IMAP app-password for an account config entry.

    Supports auth_source = 'keychain' or 'file'.
    """
    source = acct_cfg.get("auth_source", "file")
    email_addr = acct_cfg.get("email", "")

    if source == "keychain":
        service = acct_cfg.get(
            "keychain_service", "agentic-ai-mail-imap")
        pwd = _keychain_get(service, email_addr)
        if pwd:
            return _normalize_app_password(pwd)
        log.warning(
            "Keychain miss for %s (service=%s), falling back to file",
            email_addr, service)

    # File fallback: Docker runtime source is /app/secrets/imap.toml by default.
    secrets_file = acct_cfg.get("secrets_file") or DEFAULT_IMAP_SECRETS_FILE
    path = Path(secrets_file).expanduser()
    if path.exists():
        try:
            data = _read_toml(path)
            # Support format: {accounts: [{email:..., app_password:...}]}
            for entry in data.get("accounts", []):
                if entry.get("email", "").strip() == email_addr:
                    return _normalize_app_password(entry.get("app_password"))
            # Support flat: {app_password: "..."}
            if "app_password" in data:
                return _normalize_app_password(data["app_password"])
        except Exception as e:
            log.error("Failed to read secrets file %s: %s", secrets_file, e)
    return None


def credential_debug_status(acct_cfg: dict) -> dict[str, Any]:
    """Return credential source and presence metadata without secret values."""
    source = acct_cfg.get("auth_source", "file")
    email_addr = acct_cfg.get("email", "")
    service = acct_cfg.get("keychain_service", "agentic-ai-mail-imap")
    secrets_file = acct_cfg.get("secrets_file") or DEFAULT_IMAP_SECRETS_FILE
    file_path = Path(secrets_file).expanduser()

    keychain_present = None
    if source == "keychain":
        keychain_present = bool(_keychain_get(service, email_addr))

    file_present = False
    file_exists = file_path.exists()
    if file_exists:
        try:
            data = _read_toml(file_path)
            for entry in data.get("accounts", []):
                if entry.get("email", "").strip() == email_addr:
                    file_present = bool(
                        _normalize_app_password(entry.get("app_password")))
                    break
            if not file_present and "app_password" in data:
                file_present = bool(
                    _normalize_app_password(data.get("app_password")))
        except Exception:
            file_present = False

    resolved = (
        bool(keychain_present)
        if source == "keychain" and keychain_present
        else file_present
    )
    if source == "keychain" and keychain_present:
        configured_source = "keychain"
    elif file_exists:
        configured_source = "file"
    else:
        configured_source = "missing"
    return {
        "account_id": acct_cfg.get("id") or acct_cfg.get("name") or email_addr,
        "name": acct_cfg.get("name"),
        "email": email_addr,
        "auth_source": source,
        "configured_source": configured_source,
        "keychain_present": keychain_present,
        "file_present": file_present,
        "file_exists": file_exists,
        "secrets_file": str(file_path),
        "credential_present": bool(resolved),
    }


def credential_debug_statuses(settings: dict) -> list[dict[str, Any]]:
    imap_cfg = settings.get("mail", {}).get("imap", {})
    return [
        credential_debug_status(acct)
        for acct in imap_cfg.get("accounts", [])
        if not str(acct.get("email", "")).startswith("YOUR_EMAIL")
        and not acct.get("deleted_at")
        and acct.get("enabled", True)
    ]


# ── Idempotency key helpers ──────────────────────────────────────────────────

def _normalize_message_id(mid: str) -> str:
    """Strip angle brackets, lowercase, trim whitespace."""
    return mid.strip().strip("<>").lower().strip()


def make_message_key(account: str, folder: str,
                     message_id: str) -> str:
    raw = f"{account}\x00{folder}\x00{_normalize_message_id(message_id)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def make_fallback_key(account: str, folder: str,
                      uidvalidity: int, uid: int) -> str:
    raw = f"{account}\x00{folder}\x00{uidvalidity}\x00{uid}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Header / body helpers ────────────────────────────────────────────────────

def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(email.header.make_header(
            email.header.decode_header(value)))
    except Exception:
        return value or ""


def _extract_body(msg) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    walk = msg.walk() if msg.is_multipart() else [msg]
    for part in walk:
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_param("charset") or "utf-8"
        decoded = payload.decode(charset, errors="replace")
        if ctype == "text/plain":
            text_parts.append(decoded)
        elif ctype == "text/html":
            html_parts.append(decoded)

    text = "\n".join(text_parts)
    html = "\n".join(html_parts)

    if not text.strip() and html.strip():
        clean = re.sub(
            r"<(style|script)[^>]*>.*?</(style|script)>",
            " ", html, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = _html.unescape(clean)
        text = " ".join(clean.split())

    return text[:6000], html[:6000]


def _collect_attachments(msg,
                          max_attachment_mb: float) -> list[dict]:
    """Return metadata for PDF attachments; no content decoded here."""
    attachments: list[dict] = []
    max_bytes = int(max_attachment_mb * 1024 * 1024)
    walk = msg.walk() if msg.is_multipart() else [msg]

    for part in walk:
        cd = part.get("Content-Disposition", "") or ""
        ct = (part.get_content_type() or "").lower()
        filename = None

        # Disposition-based filename
        if "attachment" in cd or "inline" in cd:
            raw_fn = part.get_filename()
            if raw_fn:
                filename = _decode_header(raw_fn)

        # Content-Type name fallback
        if not filename:
            name = part.get_param("name")
            if name:
                filename = _decode_header(name)

        if not filename:
            continue

        fn_lower = filename.lower()
        if not fn_lower.endswith(".pdf"):
            continue

        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0

        entry: dict = {
            "filename": filename,
            "content_type": ct,
            "size_bytes": size,
        }

        if size > max_bytes:
            entry["status"] = "skipped_oversized"
            entry["content"] = None
            log.info(
                "Attachment %s skipped: %.1f MB > limit %.1f MB",
                filename, size / 1024 / 1024, max_attachment_mb)
        else:
            entry["status"] = "pending"
            entry["content"] = payload

        attachments.append(entry)

    return attachments


# ── Core IMAP poller ─────────────────────────────────────────────────────────

class IMAPFetchError(Exception):
    pass


class IMAPPoller:
    """
    Polls one IMAP account across configured folders.

    Usage:
        poller = IMAPPoller(acct_cfg, state, imap_cfg)
        messages = poller.poll()
    """

    TIMEOUT = 15  # seconds

    def __init__(self, acct_cfg: dict, state, imap_cfg: dict):
        self.acct_cfg = acct_cfg
        self.state = state
        self.name = acct_cfg["name"]
        self.email = acct_cfg["email"]
        self.host = acct_cfg.get("host", "imap.gmail.com")
        self.port = int(acct_cfg.get("port", 993))
        self.folders: list[str] = acct_cfg.get("folders", ["INBOX"])
        self.lookback_days = int(
            acct_cfg.get("lookback_days", 15))
        self.max_batch = int(imap_cfg.get("max_batch", 25))
        self.max_message_mb = float(
            imap_cfg.get("max_message_mb", 25))
        self.max_attachment_mb = float(
            imap_cfg.get("max_attachment_mb", 20))
        self._password: str | None = None
        status = credential_debug_status(acct_cfg)
        log.info(
            "IMAP credential source for %s: configured_source=%s "
            "auth_source=%s file_exists=%s credential_present=%s",
            self.email,
            status["configured_source"],
            status["auth_source"],
            status["file_exists"],
            status["credential_present"],
        )

    def _get_password(self) -> str:
        if self._password is None:
            pwd = _load_app_password(self.acct_cfg)
            if not pwd:
                raise IMAPFetchError(
                    f"No app-password resolved for {self.email}")
            self._password = pwd
        return self._password

    def _connect(self) -> imaplib.IMAP4_SSL:
        sock = socket.create_connection(
            (self.host, self.port), timeout=self.TIMEOUT)
        imap = imaplib.IMAP4_SSL(self.host, self.port,
                                  ssl_context=None)
        # Re-open with the already-connected socket is not directly
        # supported; create_connection is a pre-check.  Use
        # IMAP4_SSL directly — imaplib uses its own socket internally.
        # The timeout is set on the underlying socket after connect.
        sock.close()
        imap = imaplib.IMAP4_SSL(self.host, self.port)
        imap.sock.settimeout(self.TIMEOUT)
        return imap

    def poll(self) -> list[dict]:
        """Fetch new messages across all configured folders."""
        messages: list[dict] = []
        try:
            password = self._get_password()
        except IMAPFetchError as e:
            log.error("Cannot poll %s: %s", self.name, e)
            return messages

        for folder in self.folders:
            try:
                fetched = self._poll_folder(folder, password)
                messages.extend(fetched)
            except Exception as e:
                log.error(
                    "Poll failed for %s/%s: %s",
                    self.name, folder, e)
                self.state.update_imap_account_error(
                    self.name, str(e))

        return messages

    def _poll_folder(self, folder: str,
                     password: str) -> list[dict]:
        folder_state = self.state.get_imap_folder_state(
            self.name, folder)
        stored_uid = int(folder_state.get("last_uid", 0))
        stored_validity = folder_state.get("uidvalidity")

        imap = self._connect()
        try:
            imap.login(self.email, password)
            status, data = imap.select(folder, readonly=True)
            if status != "OK":
                raise IMAPFetchError(
                    f"SELECT {folder} failed: {status}")

            # ── UIDVALIDITY check ────────────────────────────────
            server_validity = self._get_uidvalidity(imap, folder)
            reset = False
            if (stored_validity is not None
                    and server_validity != int(stored_validity)):
                log.warning(
                    "UIDVALIDITY changed for %s/%s "
                    "(stored=%s server=%s) — bounded re-scan",
                    self.name, folder,
                    stored_validity, server_validity)
                self.state.write_event(
                    "uidvalidity_reset",
                    {"account": self.name, "folder": folder,
                     "old": stored_validity,
                     "new": server_validity})
                stored_uid = 0
                reset = True

            # ── Search UIDs ──────────────────────────────────────
            if stored_uid > 0 and not reset:
                status, data = imap.uid(
                    "SEARCH", None,
                    f"UID {stored_uid + 1}:*")
            else:
                cutoff = (
                    datetime.now(tz=timezone.utc)
                    - timedelta(days=self.lookback_days))
                since_str = cutoff.strftime("%d-%b-%Y")
                status, data = imap.uid(
                    "SEARCH", None, f"SINCE {since_str}")

            if status != "OK":
                raise IMAPFetchError(
                    f"SEARCH failed in {folder}: {status}")

            raw_uids = (data[0].decode().split()
                        if data[0] else [])
            uid_list = [
                u for u in raw_uids
                if int(u) > stored_uid
            ]

            if not uid_list:
                log.debug("No new messages in %s/%s",
                          self.name, folder)
                return []

            log.info(
                "Fetching %d new message(s) from %s/%s",
                len(uid_list), self.name, folder)

            # ── Batch fetch ──────────────────────────────────────
            messages: list[dict] = []
            max_uid = stored_uid
            batch_size = self.max_batch
            max_msg_bytes = int(
                self.max_message_mb * 1024 * 1024)

            for i in range(0, len(uid_list), batch_size):
                batch = uid_list[i:i + batch_size]
                uid_set = ",".join(batch)
                # Fetch headers first to check size
                hdr_status, hdr_data = imap.uid(
                    "FETCH", uid_set,
                    "(RFC822.SIZE RFC822.HEADER)")
                if hdr_status != "OK":
                    log.warning(
                        "FETCH headers failed for batch %s", uid_set)
                    continue

                # Build size map from response
                size_map: dict[str, int] = {}
                for resp in hdr_data or []:
                    if not isinstance(resp, tuple):
                        continue
                    meta = resp[0].decode(errors="replace")
                    uid_m = re.search(
                        r"UID\s+(\d+)", meta)
                    size_m = re.search(
                        r"RFC822\.SIZE\s+(\d+)", meta)
                    if uid_m and size_m:
                        size_map[uid_m.group(1)] = int(
                            size_m.group(1))

                for uid_str in batch:
                    uid = int(uid_str)
                    size = size_map.get(uid_str, 0)
                    max_uid = max(max_uid, uid)

                    if size > max_msg_bytes:
                        log.info(
                            "Skipping UID %s in %s/%s: "
                            "%.1f MB > %.1f MB limit",
                            uid_str, self.name, folder,
                            size / 1024 / 1024,
                            self.max_message_mb)
                        msg_dict = self._make_skipped(
                            uid_str, folder,
                            server_validity,
                            "oversized_message",
                            size)
                        messages.append(msg_dict)
                        continue

                    try:
                        msg_dict = self._fetch_one(
                            imap, uid_str, folder,
                            server_validity)
                        if msg_dict:
                            messages.append(msg_dict)
                    except Exception as e:
                        log.warning(
                            "Failed to fetch UID %s: %s",
                            uid_str, e)

            self.state.update_imap_account_success(self.name)
            return messages

        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def _get_uidvalidity(self, imap: imaplib.IMAP4_SSL,
                         folder: str) -> int:
        status, data = imap.status(
            folder, "(UIDVALIDITY)")
        if status == "OK" and data:
            m = re.search(
                r"UIDVALIDITY\s+(\d+)",
                data[0].decode(errors="replace"))
            if m:
                return int(m.group(1))
        return 0

    def _fetch_one(self, imap: imaplib.IMAP4_SSL,
                   uid_str: str, folder: str,
                   uidvalidity: int) -> dict | None:
        status, data = imap.uid(
            "FETCH", uid_str, "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            return None

        raw = data[0][1] if isinstance(data[0], tuple) else None
        if not raw:
            return None

        msg = _email_mod.message_from_bytes(raw)
        uid = int(uid_str)

        # ── Parse headers ────────────────────────────────────────
        subject = _decode_header(msg.get("Subject", ""))
        from_raw = _decode_header(msg.get("From", ""))
        message_id = (msg.get("Message-ID") or "").strip()
        date_str = msg.get("Date", "")

        sender_email, sender_name = "", ""
        m = re.match(r"^(.*?)\s*<(.+?)>\s*$",
                     from_raw.strip())
        if m:
            sender_name = m.group(1).strip().strip('"')
            sender_email = m.group(2).strip()
        else:
            sender_email = from_raw.strip()

        date_received = None
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            date_received = (
                dt.astimezone(timezone.utc).isoformat())
        except Exception:
            pass

        body_text, body_html = _extract_body(msg)
        sender = (f"{sender_name} <{sender_email}>"
                  if sender_name else sender_email)

        # ── Idempotency keys ─────────────────────────────────────
        if message_id:
            mkey = make_message_key(
                self.name, folder, message_id)
        else:
            mkey = None

        fkey = make_fallback_key(
            self.name, folder, uidvalidity, uid)

        # ── Attachments ──────────────────────────────────────────
        attachments = _collect_attachments(
            msg, self.max_attachment_mb)

        return {
            # Standard bridge-compatible fields
            "bridge_id": (
                f"imap-{self.name}-{folder}-{uid_str}"),
            "source_rowid": uid,
            "message_id": (
                message_id or f"uid-{uid_str}"),
            "mailbox": folder,
            "mailbox_url": (
                f"imap://{self.email}/{folder}"),
            "sender": sender,
            "sender_email": sender_email,
            "sender_name": sender_name,
            "subject": subject or "(No Subject)",
            "date_received": date_received,
            "date_sent": date_received,
            "snippet": body_text[:500],
            "body_text": body_text,
            "body_html": body_html,
            "body_text_truncated": len(body_text) >= 6000,
            "body_source": "imap_rfc822",
            "has_body": bool(body_text),
            "apple_category": None,
            "apple_high_impact": None,
            "apple_urgent": None,
            "is_read": False,
            "is_flagged": False,
            # IMAP-specific enrichment
            "imap_account": self.name,
            "imap_folder": folder,
            "imap_uid": uid,
            "imap_uidvalidity": uidvalidity,
            "message_key": mkey,
            "fallback_message_key": fkey,
            "source": "imap",
            "status": "pending",
            "attachments": attachments,
        }

    def _make_skipped(self, uid_str: str, folder: str,
                      uidvalidity: int, reason: str,
                      size: int) -> dict:
        uid = int(uid_str)
        fkey = make_fallback_key(
            self.name, folder, uidvalidity, uid)
        return {
            "bridge_id": (
                f"imap-{self.name}-{folder}-{uid_str}"),
            "source_rowid": uid,
            "message_id": f"uid-{uid_str}",
            "imap_account": self.name,
            "imap_folder": folder,
            "imap_uid": uid,
            "imap_uidvalidity": uidvalidity,
            "message_key": None,
            "fallback_message_key": fkey,
            "source": "imap",
            "status": f"skipped_with_reason:{reason}",
            "skipped_reason": reason,
            "skipped_size_bytes": size,
            "attachments": [],
        }


# ── Top-level helper ─────────────────────────────────────────────────────────

class IMAPIntake:
    """
    Coordinates polling across all accounts defined in [mail.imap].

    Usage:
        intake = IMAPIntake(settings, state)
        messages = intake.poll_all()
    """

    def __init__(self, settings: dict, state):
        imap_cfg = (settings.get("mail", {})
                    .get("imap", {}))
        acct_list = imap_cfg.get("accounts", [])
        self.pollers = [
            IMAPPoller(acct, state, imap_cfg)
            for acct in acct_list
        ]
        if not self.pollers:
            log.warning(
                "No accounts in [mail.imap] — "
                "IMAP intake disabled")

    def poll_all(self) -> list[dict]:
        messages: list[dict] = []
        for poller in self.pollers:
            try:
                fetched = poller.poll()
                messages.extend(fetched)
            except Exception as e:
                log.error(
                    "IMAPIntake: poller %s failed: %s",
                    poller.name, e)
        return messages

    def can_access(self) -> bool:
        return bool(self.pollers)
