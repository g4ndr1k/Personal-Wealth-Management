"""Stage 2 transaction dataclass and XLSX date helpers."""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class FinanceTransaction:
    """One transaction row destined for the Google Sheets Transactions tab."""

    # ── Core fields (written to Sheet in this order) ──────────────────────────
    date: str                           # ISO 8601  YYYY-MM-DD
    amount: float                       # IDR — negative = expense, positive = income
    original_currency: Optional[str]    # ISO 4217; None for domestic (Currency = IDR)
    original_amount: Optional[float]    # Foreign-currency amount; None for domestic
    exchange_rate: Optional[float]      # abs(amount) / abs(original_amount); None for domestic
    raw_description: str                # Verbatim from statement (Keterangan column)
    merchant: Optional[str]             # Resolved canonical merchant name
    category: Optional[str]             # Assigned category; None = needs review
    institution: str                    # Bank name (e.g. "BCA", "Maybank")
    account: str                        # Card / account number
    owner: str                          # "Gandrik" or "Helen"
    notes: str = ""                     # User annotations (blank on import)
    hash: str = ""                      # SHA-256 dedup fingerprint — set in __post_init__
    import_date: str = ""               # YYYY-MM-DD of import run — set in __post_init__
    import_file: str = ""               # Source filename (e.g. ALL_TRANSACTIONS.xlsx)

    def __post_init__(self):
        if not self.hash:
            self.hash = make_hash(
                self.date, self.amount, self.raw_description,
                self.institution, self.owner,
            )
        if not self.import_date:
            self.import_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def to_sheet_row(self) -> list:
        """Return values in Transactions tab column order (A→O)."""
        return [
            self.date,
            self.amount,
            self.original_currency or "",
            "" if self.original_amount is None else self.original_amount,
            "" if self.exchange_rate  is None else round(self.exchange_rate, 4),
            self.raw_description,
            self.merchant  or "",
            self.category  or "",
            self.institution,
            self.account,
            self.owner,
            self.notes,
            self.hash,
            self.import_date,
            self.import_file,
        ]


# ── Hash ──────────────────────────────────────────────────────────────────────

def make_hash(date: str, amount: float,
              raw_description: str, institution: str, owner: str) -> str:
    """
    16-hex-char dedup fingerprint.
    Deterministic: same inputs always produce the same hash.
    """
    key = f"{date}|{amount:.2f}|{raw_description}|{institution}|{owner}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# ── Date helpers ──────────────────────────────────────────────────────────────

def parse_xlsx_date(val) -> Optional[str]:
    """
    Convert an XLSX cell value to ISO 8601 (YYYY-MM-DD).

    Accepts:
      - datetime / date objects (from openpyxl with data_only=True)
      - "DD/MM/YYYY" strings (as written by xls_writer.py)
      - "DD-MM-YYYY" / "DD-MM-YY" strings
    """
    if val is None or val == "":
        return None
    # openpyxl may return a datetime directly
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    # DD/MM/YYYY
    m = re.match(r"^(\d{1,2})/(\d{2})/(\d{4})$", s)
    if m:
        d, mo, yr = m.groups()
        return f"{yr}-{mo}-{d.zfill(2)}"
    # DD-MM-YYYY
    m = re.match(r"^(\d{1,2})-(\d{2})-(\d{4})$", s)
    if m:
        d, mo, yr = m.groups()
        return f"{yr}-{mo}-{d.zfill(2)}"
    # DD-MM-YY (xls_writer shorthand)
    m = re.match(r"^(\d{1,2})-(\d{2})-(\d{2})$", s)
    if m:
        d, mo, yr = m.groups()
        return f"20{yr}-{mo}-{d.zfill(2)}"
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None
