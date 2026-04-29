"""
PDF Router — attachment lifecycle: pending → unlocked → renamed → routed.

Manages the extraction, decryption, classification, and routing of PDF
attachments from IMAP-fetched emails to the NAS mount point.

Idempotency:
  - attachment_key = message_key + sha256(pdf_bytes)
  - Same sha256 on disk → idempotent reuse
  - Different content same name → append _<8-hex>.pdf

Mount validation uses a sentinel-file probe to detect silent Docker
bind-mount failures (unmounted NAS → empty writable dir in container).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path
from typing import Optional

import httpx

from app.state import AgentState

log = logging.getLogger("agent.pdf_router")

# ── Filename validation ───────────────────────────────────────────────────

FILENAME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}_[A-Za-z0-9][A-Za-z0-9_-]{1,40}_[a-z][a-z0-9_-]{1,30}\.pdf$"
)
MAX_FILENAME_LEN = 120


def normalize_filename(raw: str) -> str:
    """Normalize a proposed filename: whitespace→underscore, strip path separators."""
    name = raw.strip()
    # Replace whitespace with underscore
    name = re.sub(r"\s+", "_", name)
    # Strip path separators
    name = name.replace("/", "_").replace("\\", "_")
    # Reject hidden, absolute, ..
    if name.startswith("."):
        name = "_" + name[1:]
    parts = name.split("_")
    parts = [p for p in parts if p not in ("..", ".", "")]
    name = "_".join(parts)
    # Cap length
    if len(name) > MAX_FILENAME_LEN:
        stem = name[: MAX_FILENAME_LEN - 4]
        name = stem + ".pdf"
    return name


def validate_filename(name: str) -> bool:
    """Return True if name matches the strict filename schema."""
    return bool(FILENAME_RE.match(name))


def deterministic_filename(
    sender_domain: str, date_str: str | None = None
) -> str:
    """Generate a fallback filename when LLM fails or returns invalid output."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Sanitize sender domain
    domain = re.sub(r"[^A-Za-z0-9_-]", "_", sender_domain.lower())
    domain = domain.strip("_")[:40]
    if not domain:
        domain = "unknown"
    return f"{date_str}_{domain}_attachment.pdf"


# ── Sentinel mount probe ─────────────────────────────────────────────────

def check_mount(nas_root: str, expected_uuid: str) -> tuple[bool, str]:
    """
    Sentinel-file probe for NAS mount validation.

    Returns (ok, reason). Checks:
      1. Sentinel file exists and is readable
      2. UUID payload matches expected value
      3. Directory is writable (write-then-delete probe)
    """
    sentinel_path = os.path.join(nas_root, ".mailagent_mount")

    if not os.path.exists(sentinel_path):
        return False, "sentinel file missing — mount may be empty"

    try:
        content = Path(sentinel_path).read_text().strip()
    except OSError as e:
        return False, f"sentinel not readable: {e}"

    if expected_uuid and content != expected_uuid:
        return False, f"sentinel UUID mismatch (got {content[:8]}...)"

    # Write probe
    probe = os.path.join(nas_root, f".probe_{os.getpid()}")
    try:
        Path(probe).write_text("ok")
        Path(probe).unlink()
    except OSError as e:
        return False, f"mount not writable: {e}"

    return True, "ok"


# ── Attachment key helpers ────────────────────────────────────────────────

def make_attachment_key(message_key: str, pdf_bytes: bytes) -> str:
    """attachment_key = message_key + sha256(pdf_bytes)"""
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    return hashlib.sha256(f"{message_key}:{sha}".encode()).hexdigest()[:32]


# ── PDF extraction from MIME ─────────────────────────────────────────────

def extract_pdfs_from_raw(raw_email: bytes) -> list[tuple[str, bytes]]:
    """
    Extract all application/pdf parts from a raw email.
    Returns list of (filename, pdf_bytes).
    """
    msg = message_from_bytes(raw_email, policy=default_policy)
    pdfs = []
    for part in msg.walk():
        ct = part.get_content_type()
        if ct != "application/pdf":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        filename = part.get_filename() or "unnamed.pdf"
        pdfs.append((filename, payload))
    return pdfs


# ── PDF unlock via bridge ─────────────────────────────────────────────────

def unlock_via_bridge(
    pdf_bytes: bytes,
    passwords: list[str],
    bridge_url: str,
    bridge_token: str,
) -> dict:
    """
    POST /pdf/unlock to the bridge with multipart bytes.
    Returns {unlocked_bytes, was_encrypted, password_used_index, page_count}.
    Raises on failure.
    """
    # Send passwords as a JSON field + PDF as file upload
    resp = httpx.post(
        f"{bridge_url}/pdf/unlock",
        headers={"Authorization": f"Bearer {bridge_token}"},
        files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
        data={"passwords": json.dumps(passwords)},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Bridge /pdf/unlock returned {resp.status_code}: "
            f"{resp.text[:200]}"
        )
    return {
        "unlocked_bytes": resp.content,
        "was_encrypted": resp.headers.get("X-Was-Encrypted", "false") == "true",
        "password_used_index": int(resp.headers.get("X-Password-Used-Index", "-1")),
        "page_count": int(resp.headers.get("X-Page-Count", "0")),
    }


# ── Classification for filename ──────────────────────────────────────────

def classify_pdf_filename(
    sender: str,
    subject: str,
    original_filename: str,
    ollama_host: str,
    ollama_model: str,
    confidence_threshold: float = 0.5,
) -> tuple[str, str]:
    """
    Call Ollama to classify a PDF and propose a filename.

    Returns (proposed_filename, category).
    Falls back to deterministic filename on any failure.
    """
    prompt = (
        "You are a PDF document classifier. Given the email context below, "
        "classify the attachment and propose a filename.\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"category":"invoices|statements|receipts|other",'
        '"filename":"YYYY-MM-DD_vendor_type.pdf",'
        '"confidence":0.0}\n\n'
        f"Sender: {sender}\n"
        f"Subject: {subject}\n"
        f"Original filename: {original_filename}\n"
    )

    try:
        resp = httpx.post(
            f"{ollama_host}/api/generate",
            json={
                "model": ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        data = json.loads(text)
        category = data.get("category", "other")
        filename = data.get("filename", "")
        confidence = float(data.get("confidence", 0))

        if confidence < confidence_threshold or not validate_filename(
            normalize_filename(filename)
        ):
            raise ValueError(
                f"Low confidence ({confidence}) or invalid filename"
            )

        return normalize_filename(filename), category

    except Exception as e:
        log.warning(
            "PDF classification failed (falling back): %s", e
        )
        domain = sender.rsplit("@", 1)[-1] if "@" in sender else "unknown"
        return deterministic_filename(domain), "__pending_review__"


# ── Main router class ────────────────────────────────────────────────────

class PdfRouter:
    def __init__(
        self,
        state: AgentState,
        config: dict,
    ):
        self.state = state
        self.nas_root = config.get("nas_root", "/mnt/mailagent")
        self.host_nas_root = config.get("host_nas_root", "/Volumes/Synology/mailagent")
        self.categories = config.get("categories", ["invoices", "statements", "receipts", "other"])
        self.filename_regex = config.get("filename_regex", FILENAME_RE.pattern)
        self.sentinel_uuid = config.get("mount_sentinel_uuid", "")
        self.sentinel_path = config.get("sentinel_path", ".mailagent_mount")
        self.bridge_url = os.environ.get("BRIDGE_URL", "http://host.docker.internal:9100")
        self.bridge_token = self._read_bridge_token()
        self.ollama_host = config.get("ollama_host", "http://host.docker.internal:11434")
        self.ollama_model = config.get("ollama_model", "gemma3:4b")

        # Passwords from secrets/banks.toml (read once)
        self._passwords: list[str] | None = None

    def _read_bridge_token(self) -> str:
        token_file = os.environ.get("BRIDGE_TOKEN_FILE", "")
        if token_file and os.path.exists(token_file):
            return Path(token_file).read_text().strip()
        return ""

    def _get_passwords(self) -> list[str]:
        if self._passwords is not None:
            return self._passwords
        pw_file = os.environ.get("BANK_PASSWORDS_FILE", "")
        if pw_file and os.path.exists(pw_file):
            import tomllib
            with open(pw_file, "rb") as f:
                data = tomllib.load(f)
            # Collect all passwords from the banks config
            pws = []
            for bank, info in data.items():
                if isinstance(info, dict) and "password" in info:
                    pws.append(info["password"])
            self._passwords = pws
        else:
            self._passwords = []
        return self._passwords

    def process_attachment(
        self,
        message_key: str,
        fallback_message_key: str | None,
        account: str,
        folder: str,
        uid: int,
        original_filename: str,
        pdf_bytes: bytes,
        sender: str,
        subject: str,
    ) -> str:
        """
        Process a single PDF attachment through the full pipeline.
        Returns the final status string.
        """
        attachment_key = make_attachment_key(message_key, pdf_bytes)
        sha = hashlib.sha256(pdf_bytes).hexdigest()

        # Check if already processed
        existing = self.state.get_pdf_attachment(attachment_key)
        if existing and existing["status"] in ("routed", "unlocked"):
            log.info("Attachment %s already %s, skipping", attachment_key[:8], existing["status"])
            return existing["status"]

        # Record in DB as pending
        self.state.upsert_pdf_attachment(
            attachment_key=attachment_key,
            message_key=message_key,
            fallback_message_key=fallback_message_key,
            account=account,
            folder=folder,
            uid=uid,
            original_filename=original_filename,
            status="pending",
            sha256=sha,
        )

        try:
            # Step 1: Unlock
            unlocked = self._unlock(pdf_bytes)
            if unlocked:
                pdf_bytes = unlocked
                self.state.upsert_pdf_attachment(
                    attachment_key=attachment_key,
                    message_key=message_key,
                    fallback_message_key=fallback_message_key,
                    account=account, folder=folder, uid=uid,
                    original_filename=original_filename,
                    status="unlocked",
                    sha256=sha,
                )

            # Step 2: Classify + rename
            proposed_filename, category = classify_pdf_filename(
                sender=sender,
                subject=subject,
                original_filename=original_filename,
                ollama_host=self.ollama_host,
                ollama_model=self.ollama_model,
            )

            if not validate_filename(proposed_filename):
                domain = sender.rsplit("@", 1)[-1] if "@" in sender else "unknown"
                proposed_filename = deterministic_filename(domain)
                status = "pending_review"
            elif category == "__pending_review__":
                category = "other"
                status = "pending_review"
            else:
                status = "renamed"

            self.state.upsert_pdf_attachment(
                attachment_key=attachment_key,
                message_key=message_key,
                fallback_message_key=fallback_message_key,
                account=account, folder=folder, uid=uid,
                original_filename=original_filename,
                status=status,
                sha256=sha,
                proposed_filename=proposed_filename,
            )

            if status == "pending_review":
                return "pending_review"

            # Step 3: Route to NAS
            ok, reason = check_mount(self.nas_root, self.sentinel_uuid)
            if not ok:
                log.warning("NAS mount probe failed: %s", reason)
                self.state.upsert_pdf_attachment(
                    attachment_key=attachment_key,
                    message_key=message_key,
                    fallback_message_key=fallback_message_key,
                    account=account, folder=folder, uid=uid,
                    original_filename=original_filename,
                    status="pending",
                    sha256=sha,
                    proposed_filename=proposed_filename,
                    error_reason=f"mount_check_failed: {reason}",
                )
                return "pending"

            routed_path = self._route_to_nas(
                pdf_bytes, category, proposed_filename, sha
            )

            self.state.upsert_pdf_attachment(
                attachment_key=attachment_key,
                message_key=message_key,
                fallback_message_key=fallback_message_key,
                account=account, folder=folder, uid=uid,
                original_filename=original_filename,
                status="routed",
                sha256=sha,
                proposed_filename=proposed_filename,
                routed_path=routed_path,
            )
            return "routed"

        except Exception as e:
            log.error("PDF pipeline error for %s: %s", attachment_key[:8], e)
            msg = str(e)
            if "Bridge /pdf/unlock returned 422" in msg:
                status = "pending_review"
            elif "Bridge /pdf/unlock returned 5" in msg:
                status = "failed_retryable"
            else:
                status = "failed"
            self.state.upsert_pdf_attachment(
                attachment_key=attachment_key,
                message_key=message_key,
                fallback_message_key=fallback_message_key,
                account=account, folder=folder, uid=uid,
                original_filename=original_filename,
                status=status,
                sha256=sha,
                error_reason=msg[:500],
            )
            return status

    def _unlock(self, pdf_bytes: bytes) -> bytes | None:
        """Try to unlock PDF. Returns unlocked bytes or None if not encrypted."""
        passwords = self._get_passwords()
        if not passwords:
            return None
        try:
            result = unlock_via_bridge(
                pdf_bytes, passwords,
                self.bridge_url, self.bridge_token,
            )
            if result["was_encrypted"]:
                return result["unlocked_bytes"]
            return None  # not encrypted
        except Exception:
            raise

    def _route_to_nas(
        self,
        pdf_bytes: bytes,
        category: str,
        filename: str,
        sha: str,
    ) -> str:
        """Write PDF to NAS. Returns the routed path."""
        if category not in self.categories:
            category = "other"
        # Ensure category dir exists
        cat_dir = os.path.join(self.nas_root, "pdf", category)
        os.makedirs(cat_dir, exist_ok=True)

        dest = os.path.join(cat_dir, filename)

        # Idempotent: same content already on disk
        if os.path.exists(dest):
            existing_sha = hashlib.sha256(
                Path(dest).read_bytes()
            ).hexdigest()
            if existing_sha == sha:
                log.info("Idempotent: %s already routed", filename)
                return dest
            # Collision: different content, same name
            hex_suffix = sha[:8]
            base = filename[:-4]  # strip .pdf
            filename = f"{base}_{hex_suffix}.pdf"
            if not validate_filename(filename):
                raise ValueError(f"Collision filename invalid: {filename}")
            dest = os.path.join(cat_dir, filename)

        Path(dest).write_bytes(pdf_bytes)
        log.info("Routed %s → %s", filename, dest)
        return dest

    def setup_host_sentinel(self) -> None:
        """
        Create the sentinel file on the host NAS path.
        Call this once during setup (not from inside Docker).
        """
        sentinel_uuid = str(uuid.uuid4())
        sentinel_path = os.path.join(
            self.host_nas_root, self.sentinel_path
        )
        os.makedirs(self.host_nas_root, exist_ok=True)
        Path(sentinel_path).write_text(sentinel_uuid)
        log.info(
            "Sentinel created at %s with UUID %s",
            sentinel_path, sentinel_uuid,
        )
        print(f"Set sentinel UUID: {sentinel_uuid}")
        print(f"Add to config: mount_sentinel_uuid = \"{sentinel_uuid}\"")

    def retry_pending(self) -> int:
        """Re-process attachments stuck in pending/failed_retryable. Returns count retried."""
        # This would query pdf_attachments WHERE status IN ('pending', 'failed_retryable')
        # and re-process. For now, a placeholder that the orchestrator's retry worker calls.
        log.info("Retry pending: not yet implemented — attachments will retry on next cycle")
        return 0
