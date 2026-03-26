"""
attachment_scanner.py — watches Mail.app's attachments folder for new bank PDFs.

How Mail.app stores attachments:
  ~/Library/Mail/V*/[account]/[mailbox]/Messages/[uuid]/Attachments/[filename]

We watch for PDF files whose sender email matches a configured bank domain.
This is a polling scanner (no FSEvents/kqueue dependency) — called on a schedule
by the bridge's mail scan cycle.

Returns a list of PendingAttachment objects for the bridge to queue for processing.
"""
import os
import re
import glob
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Bank domain → bank name mapping
BANK_DOMAINS = {
    "maybank.co.id": "Maybank",
    "cimbniaga.co.id": "CIMB Niaga",
    "permatabank.co.id": "Permata Bank",
    "bca.co.id": "BCA",
    "klikbca.com": "BCA",
}


@dataclass
class PendingAttachment:
    file_path: str
    filename: str
    bank_name: str
    sender_email: str
    received_date: Optional[str]
    message_id: Optional[str]
    size_bytes: int


class AttachmentScanner:
    def __init__(self, mail_root: str, seen_db_path: str, bank_domains: dict = None):
        """
        mail_root    : path to ~/Library/Mail (will glob for V* subdirs)
        seen_db_path : path to SQLite DB tracking already-queued attachments
        bank_domains : override default BANK_DOMAINS mapping
        """
        self.mail_root = os.path.expanduser(mail_root)
        self.seen_db_path = seen_db_path
        self.bank_domains = bank_domains or BANK_DOMAINS
        self._init_db()

    def _init_db(self):
        con = sqlite3.connect(self.seen_db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen_attachments (
                file_path TEXT PRIMARY KEY,
                queued_at TEXT NOT NULL,
                bank_name TEXT,
                processed INTEGER DEFAULT 0
            )
        """)
        con.commit()
        con.close()

    def scan(self, lookback_days: int = 30) -> list[PendingAttachment]:
        """
        Scan Mail.app attachment directories for new bank PDFs.
        Returns only files not already in the seen_attachments DB.
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)
        results = []

        # Find all V* Mail directories
        mail_dirs = sorted(glob.glob(os.path.join(self.mail_root, "V*")), reverse=True)
        if not mail_dirs:
            log.warning(f"No Mail V* directories found under {self.mail_root}")
            return results

        # Glob for all PDF attachments recursively
        for mail_dir in mail_dirs:
            pattern = os.path.join(mail_dir, "**", "Attachments", "**", "*.pdf")
            for pdf_path in glob.glob(pattern, recursive=True):
                try:
                    stat = os.stat(pdf_path)
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    if mtime < cutoff:
                        continue
                    if self._already_seen(pdf_path):
                        continue

                    bank_name = self._detect_bank_from_path(pdf_path)
                    if not bank_name:
                        continue

                    results.append(PendingAttachment(
                        file_path=pdf_path,
                        filename=Path(pdf_path).name,
                        bank_name=bank_name,
                        sender_email=self._extract_sender_from_path(pdf_path),
                        received_date=mtime.strftime("%d/%m/%Y"),
                        message_id=None,
                        size_bytes=stat.st_size,
                    ))
                except OSError as e:
                    log.debug(f"Could not stat {pdf_path}: {e}")

        log.info(f"Attachment scanner found {len(results)} new bank PDFs")
        return results

    def mark_seen(self, file_path: str, bank_name: str):
        """Record that this attachment has been queued for processing."""
        con = sqlite3.connect(self.seen_db_path)
        con.execute(
            "INSERT OR REPLACE INTO seen_attachments (file_path, queued_at, bank_name) VALUES (?,?,?)",
            (file_path, datetime.utcnow().isoformat(), bank_name)
        )
        con.commit()
        con.close()

    def mark_processed(self, file_path: str):
        con = sqlite3.connect(self.seen_db_path)
        con.execute(
            "UPDATE seen_attachments SET processed=1 WHERE file_path=?",
            (file_path,)
        )
        con.commit()
        con.close()

    def _already_seen(self, file_path: str) -> bool:
        con = sqlite3.connect(self.seen_db_path)
        row = con.execute(
            "SELECT 1 FROM seen_attachments WHERE file_path=?", (file_path,)
        ).fetchone()
        con.close()
        return row is not None

    def _detect_bank_from_path(self, pdf_path: str) -> Optional[str]:
        """
        Try to infer bank from the Mail directory structure.
        Mail stores messages under account directory names that often contain
        the email domain. Falls back to filename pattern matching.
        """
        path_lower = pdf_path.lower()

        # Check path components for known bank domains
        for domain, bank in self.bank_domains.items():
            domain_slug = domain.replace(".", "").replace("-", "")
            if domain.split(".")[0] in path_lower or domain_slug in path_lower:
                return bank

        # Filename heuristics
        filename = Path(pdf_path).name.lower()
        if "maybank" in filename or "mbb" in filename:
            return "Maybank"
        if "cimb" in filename:
            return "CIMB Niaga"
        if "permata" in filename:
            return "Permata Bank"
        if "bca" in filename or "klikbca" in filename:
            return "BCA"

        # PDF is in a Mail attachment dir but we can't determine bank — skip
        return None

    def _extract_sender_from_path(self, pdf_path: str) -> str:
        """Try to find sender email from path components (best effort)."""
        for domain in self.bank_domains:
            if domain in pdf_path:
                return f"statement@{domain}"
        return ""
