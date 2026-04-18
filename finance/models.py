"""Stage 2 transaction dataclass and XLSX date helpers."""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import calendar


@dataclass
class FinanceTransaction:
    """One transaction row written to the SQLite transactions table."""

    # ── Core fields ───────────────────────────────────────────────────────────
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
                self.institution, self.owner, self.account,
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
              raw_description: str, institution: str,
              owner: str, account: str = "") -> str:
    """
    16-hex-char dedup fingerprint.
    Deterministic: same inputs always produce the same hash.
    """
    key = f"{date}|{amount:.2f}|{raw_description}|{institution}|{owner}|{account}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# ── Date helpers ──────────────────────────────────────────────────────────────

def _validate_date(yr: int, mo: int, d: int) -> bool:
    """Return True if (yr, mo, d) is a valid calendar date."""
    try:
        calendar.monthrange(yr, mo)  # validates month
        return 1 <= d <= calendar.monthrange(yr, mo)[1]
    except (ValueError, calendar.IllegalMonthError):
        return False


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
        d, mo, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _validate_date(yr, mo, d):
            return f"{yr}-{mo:02d}-{d:02d}"
        return None
    # DD-MM-YYYY
    m = re.match(r"^(\d{1,2})-(\d{2})-(\d{4})$", s)
    if m:
        d, mo, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _validate_date(yr, mo, d):
            return f"{yr}-{mo:02d}-{d:02d}"
        return None
    # DD-MM-YY (xls_writer shorthand) — use century heuristic
    m = re.match(r"^(\d{1,2})-(\d{2})-(\d{2})$", s)
    if m:
        d, mo, yr_raw = int(m.group(1)), int(m.group(2)), int(m.group(3))
        century = "19" if yr_raw >= 80 else "20"
        yr = int(f"{century}{yr_raw:02d}")
        if _validate_date(yr, mo, d):
            return f"{yr}-{mo:02d}-{d:02d}"
        return None
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None
