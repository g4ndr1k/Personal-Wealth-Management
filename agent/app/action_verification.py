from __future__ import annotations

import email
import email.header
import email.utils
import hashlib
import imaplib
from dataclasses import dataclass, field
from typing import Any, Protocol

from .action_execution import ExecutionGateResult


@dataclass
class FinalVerificationBlocker:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class FinalVerificationRequest:
    approval: dict[str, Any]
    gate_result: ExecutionGateResult
    plan_hash: str
    idempotency_key: str


@dataclass
class FinalVerificationResult:
    status: str
    safe_to_execute: bool
    blockers: list[FinalVerificationBlocker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mailbox_identity: dict[str, Any] = field(default_factory=dict)
    message_identity: dict[str, Any] = field(default_factory=dict)
    current_flags: dict[str, Any] = field(default_factory=dict)
    plan_hash: str | None = None
    idempotency_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "safe_to_execute": self.safe_to_execute,
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "warnings": list(self.warnings),
            "mailbox_identity": self.mailbox_identity,
            "message_identity": self.message_identity,
            "current_flags": self.current_flags,
            "plan_hash": self.plan_hash,
            "idempotency_key": self.idempotency_key,
        }


class ReadOnlyMailboxAdapter(Protocol):
    def select_folder_readonly(self, account_id: str, folder: str) -> dict[str, Any]:
        ...

    def get_uidvalidity(self) -> str | None:
        ...

    def fetch_message_identity_by_uid(self, uid: str) -> dict[str, Any] | None:
        ...

    def fetch_flags_by_uid(self, uid: str) -> dict[str, Any] | None:
        ...

    def close(self) -> None:
        ...


class ImapReadOnlyMailboxAdapter:
    """Read-only IMAP adapter for final verification.

    This adapter only selects folders read-only and fetches headers/flags. It
    deliberately exposes no mutation methods.
    """

    def __init__(self, account: dict[str, Any], password: str | None = None):
        self.account = account
        self.password = password
        self._imap = None
        self._uidvalidity = None

    def select_folder_readonly(self, account_id: str, folder: str) -> dict[str, Any]:
        self._ensure_connected()
        status, _ = self._imap.select(folder, readonly=True)
        if status != "OK":
            return {"selected": False, "status": status}
        self._uidvalidity = _uidvalidity_from_untagged(
            getattr(self._imap, "untagged_responses", {}))
        if self._uidvalidity is None:
            status, data = self._imap.status(folder, "(UIDVALIDITY)")
            if status == "OK":
                self._uidvalidity = _uidvalidity_from_status(data)
        return {"selected": True, "status": status}

    def get_uidvalidity(self) -> str | None:
        return None if self._uidvalidity is None else str(self._uidvalidity)

    def fetch_message_identity_by_uid(self, uid: str) -> dict[str, Any] | None:
        self._ensure_connected()
        status, data = self._imap.uid(
            "FETCH",
            str(uid),
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])",
        )
        if status != "OK" or not data:
            return None
        raw_header = _first_bytes(data)
        if not raw_header:
            return None
        msg = email.message_from_bytes(raw_header)
        subject = _decode_header(msg.get("Subject"))
        from_header = msg.get("From")
        return {
            "message_id": _normalize_message_id(msg.get("Message-ID")),
            "subject": subject,
            "from": _normalize_email(from_header),
            "from_raw": from_header,
            "date": msg.get("Date"),
            "header_hash": hashlib.sha256(raw_header).hexdigest(),
        }

    def fetch_flags_by_uid(self, uid: str) -> dict[str, Any] | None:
        self._ensure_connected()
        status, data = self._imap.uid("FETCH", str(uid), "(FLAGS)")
        if status != "OK" or not data:
            return None
        flags_text = " ".join(
            part.decode("utf-8", "ignore") if isinstance(part, bytes) else str(part)
            for item in data
            for part in (item if isinstance(item, tuple) else (item,))
        )
        return {"seen": "\\Seen" in flags_text, "raw": flags_text}

    def close(self) -> None:
        if self._imap is None:
            return
        try:
            self._imap.close()
        except Exception:
            pass
        try:
            self._imap.logout()
        except Exception:
            pass
        self._imap = None

    def _ensure_connected(self):
        if self._imap is not None:
            return
        host = self.account["host"]
        port = int(self.account.get("port", 993))
        self._imap = imaplib.IMAP4_SSL(host, port)
        password = self.password or self.account.get("password")
        if password:
            self._imap.login(self.account.get("email"), password)


class FakeReadOnlyMailboxAdapter:
    def __init__(
            self, *, folder_exists: bool = True, uidvalidity: str | int = "7",
            messages: dict[str, dict[str, Any]] | None = None):
        self.folder_exists = folder_exists
        self.uidvalidity = str(uidvalidity)
        self.messages = messages or {}
        self.selected = []

    def select_folder_readonly(self, account_id: str, folder: str) -> dict[str, Any]:
        self.selected.append((account_id, folder, True))
        return {"selected": self.folder_exists, "status": "OK" if self.folder_exists else "NO"}

    def get_uidvalidity(self) -> str | None:
        return self.uidvalidity if self.folder_exists else None

    def fetch_message_identity_by_uid(self, uid: str) -> dict[str, Any] | None:
        message = self.messages.get(str(uid))
        if not message:
            return None
        return dict(message.get("identity") or {})

    def fetch_flags_by_uid(self, uid: str) -> dict[str, Any] | None:
        message = self.messages.get(str(uid))
        if not message:
            return None
        return dict(message.get("flags") or {})

    def close(self) -> None:
        return None


def verify_action_plan_readonly(
        request: FinalVerificationRequest,
        adapter: ReadOnlyMailboxAdapter) -> FinalVerificationResult:
    approval = request.approval
    gate = request.gate_result
    blockers: list[FinalVerificationBlocker] = []
    warnings: list[str] = []
    account_id = str(approval.get("account_id") or "")
    folder = str(approval.get("folder") or "")
    uid = str(approval.get("imap_uid") or "")
    expected_uidvalidity = str(approval.get("uidvalidity") or "")
    action = str(approval.get("proposed_action_type") or "")

    mailbox_identity = {
        "account_id": account_id,
        "folder": folder,
        "uidvalidity_expected": expected_uidvalidity,
        "uidvalidity_current": None,
        "imap_uid": uid,
    }
    message_identity = {
        "message_id_expected": _normalize_message_id(approval.get("message_key")),
        "message_id_current": None,
        "subject_expected": approval.get("subject"),
        "subject_current": None,
        "from_expected": _normalize_email(approval.get("sender")),
        "from_current": None,
        "date_expected": approval.get("received_at"),
        "date_current": None,
    }
    current_flags: dict[str, Any] = {}

    def block(code: str, message: str) -> None:
        blockers.append(FinalVerificationBlocker(code, message))

    try:
        if gate.status != "ready" or not gate.safe_to_execute:
            block("gate_not_safe", "Gate evaluator did not return mock-safe readiness.")
            return _verification_result(
                blockers, warnings, mailbox_identity, message_identity,
                current_flags, request)

        selected = adapter.select_folder_readonly(account_id, folder)
        if not selected.get("selected"):
            block("folder_missing", "Folder is not selectable in read-only mode.")
            return _verification_result(
                blockers, warnings, mailbox_identity, message_identity,
                current_flags, request)

        current_uidvalidity = adapter.get_uidvalidity()
        mailbox_identity["uidvalidity_current"] = current_uidvalidity
        if str(current_uidvalidity or "") != expected_uidvalidity:
            block("uidvalidity_mismatch", "Folder UIDVALIDITY changed after approval.")

        current_message = adapter.fetch_message_identity_by_uid(uid)
        if not current_message:
            block("uid_missing", "Approved IMAP UID is no longer fetchable read-only.")
            return _verification_result(
                blockers, warnings, mailbox_identity, message_identity,
                current_flags, request)

        _populate_message_identity(message_identity, current_message)
        _compare_optional_identity(
            message_identity, blockers, warnings)

        flags = adapter.fetch_flags_by_uid(uid)
        if flags is None:
            block("flags_missing", "Message flags could not be fetched read-only.")
        else:
            current_flags.update(flags)
            if "seen" not in current_flags:
                warnings.append("current_seen_state_unknown")
            elif action == "mark_read" and current_flags["seen"] is True:
                warnings.append("mark_read_noop_message_already_read")
            elif action == "mark_unread" and current_flags["seen"] is False:
                warnings.append("mark_unread_noop_message_already_unread")

        if request.plan_hash != gate.plan_hash:
            block("plan_hash_mismatch", "Final verification plan hash differs from gate result.")
        if request.idempotency_key != gate.idempotency_key:
            block(
                "idempotency_key_mismatch",
                "Final verification idempotency key differs from gate result.",
            )
        return _verification_result(
            blockers, warnings, mailbox_identity, message_identity,
            current_flags, request)
    except Exception as exc:
        block("verification_error", str(exc)[:500])
        return _verification_result(
            blockers, warnings, mailbox_identity, message_identity,
            current_flags, request)
    finally:
        adapter.close()


def _verification_result(
        blockers: list[FinalVerificationBlocker], warnings: list[str],
        mailbox_identity: dict[str, Any], message_identity: dict[str, Any],
        current_flags: dict[str, Any],
        request: FinalVerificationRequest) -> FinalVerificationResult:
    blocked = bool(blockers)
    return FinalVerificationResult(
        status="blocked" if blocked else "verified",
        safe_to_execute=not blocked,
        blockers=blockers,
        warnings=warnings,
        mailbox_identity=mailbox_identity,
        message_identity=message_identity,
        current_flags=current_flags,
        plan_hash=request.plan_hash,
        idempotency_key=request.idempotency_key,
    )


def _populate_message_identity(
        message_identity: dict[str, Any], current_message: dict[str, Any]) -> None:
    message_identity["message_id_current"] = _normalize_message_id(
        current_message.get("message_id"))
    message_identity["subject_current"] = current_message.get("subject")
    message_identity["from_current"] = _normalize_email(
        current_message.get("from") or current_message.get("sender"))
    message_identity["date_current"] = current_message.get("date")


def _compare_optional_identity(
        identity: dict[str, Any],
        blockers: list[FinalVerificationBlocker],
        warnings: list[str]) -> None:
    comparisons = [
        ("message_id", "Message-ID changed after approval."),
        ("subject", "Subject changed after approval."),
        ("from", "Sender changed after approval."),
        ("date", "Message date changed after approval."),
    ]
    for field, message in comparisons:
        expected = identity.get(f"{field}_expected")
        current = identity.get(f"{field}_current")
        if expected in {None, ""}:
            warnings.append(f"{field}_expected_missing")
            continue
        if current in {None, ""}:
            warnings.append(f"{field}_current_missing")
            continue
        if _normalize_cmp(expected) != _normalize_cmp(current):
            blockers.append(FinalVerificationBlocker(f"{field}_mismatch", message))


def _decode_header(value: str | None) -> str | None:
    if not value:
        return None
    parts = email.header.decode_header(value)
    decoded = []
    for payload, charset in parts:
        if isinstance(payload, bytes):
            decoded.append(payload.decode(charset or "utf-8", "replace"))
        else:
            decoded.append(payload)
    return "".join(decoded)


def _normalize_email(value: Any) -> str | None:
    if not value:
        return None
    _, addr = email.utils.parseaddr(str(value))
    return (addr or str(value)).strip().lower() or None


def _normalize_message_id(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text.startswith("mkey:") or text.startswith("fkey:"):
        return None
    return text.strip("<>").lower() or None


def _normalize_cmp(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _first_bytes(data: Any) -> bytes | None:
    for item in data:
        if isinstance(item, tuple):
            for part in item:
                if isinstance(part, bytes) and b":" in part:
                    return part
        elif isinstance(item, bytes) and b":" in item:
            return item
    return None


def _uidvalidity_from_untagged(untagged: dict[str, Any]) -> str | None:
    values = untagged.get("UIDVALIDITY") or untagged.get(b"UIDVALIDITY")
    if not values:
        return None
    first = values[0]
    return first.decode() if isinstance(first, bytes) else str(first)


def _uidvalidity_from_status(data: Any) -> str | None:
    text = " ".join(
        item.decode("utf-8", "ignore") if isinstance(item, bytes) else str(item)
        for item in data or []
    )
    marker = "UIDVALIDITY"
    if marker not in text:
        return None
    return text.split(marker, 1)[1].strip(" )").split()[0]
