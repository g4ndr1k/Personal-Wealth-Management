"""Base dataclasses shared by all bank statement parsers."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import re


@dataclass
class Transaction:
    """A single transaction row — works for CC, savings, and consolidated."""
    date_transaction: str               # DD/MM/YYYY, or "" for synthetic rows
    date_posted: Optional[str]          # DD/MM/YYYY (CC only), None for savings
    description: str
    currency: str                       # ISO code: IDR, USD, SGD, …
    foreign_amount: Optional[float]     # Amount in original currency (None if IDR only)
    exchange_rate: Optional[float]      # IDR rate used for conversion
    amount_idr: float                   # Always in IDR (converted if foreign)
    tx_type: str                        # "Credit" or "Debit"
    balance: Optional[float]            # Running balance (savings/koran only)
    account_number: str                 # Card or account number ("" if unknown)
    owner: str = ""                     # Derived owner label (Gandrik, Helen, …)


@dataclass
class AccountSummary:
    """Summary block for one account (savings / CC / investment)."""
    product_name: str
    account_number: Optional[str]
    currency: str
    closing_balance: float
    opening_balance: float = 0.0
    total_debit: float = 0.0
    total_credit: float = 0.0
    print_date: Optional[str] = None    # DD/MM/YYYY
    period_start: Optional[str] = None  # DD/MM/YYYY
    period_end: Optional[str] = None    # DD/MM/YYYY
    credit_limit: Optional[float] = None
    extra: dict = field(default_factory=dict)  # Flexible bag for consolidated statements


@dataclass
class StatementResult:
    """Full parsed result from one PDF."""
    bank: str
    statement_type: str                 # "cc", "savings", "consolidated", "CC", "Savings"
    owner: str = ""                     # Derived owner label; set by parser or exporter
    sheet_name: str = ""                # Precomputed by parser; exporter computes if empty
    print_date: Optional[str] = None    # DD/MM/YYYY — statement print / generation date
    transactions: list[Transaction] = field(default_factory=list)
    summary: Optional[AccountSummary] = None   # Primary account summary (new parsers)
    accounts: list[AccountSummary] = field(default_factory=list)
    customer_name: str = ""             # Raw customer name for owner detection fallback
    period_start: str = ""              # DD/MM/YYYY
    period_end: str = ""                # DD/MM/YYYY
    exchange_rates: dict = field(default_factory=dict)
    raw_errors: list[str] = field(default_factory=list)


# ── Number helpers ────────────────────────────────────────────────────────────

def parse_idr_amount(s: str) -> Optional[float]:
    """
    Parse Indonesian number format: 1.234.567,89 → 1234567.89
    Also handles Western format (comma-thousands): 1,234,567.89 → 1234567.89
    """
    if not s:
        return None
    s = str(s).strip().replace(" ", "")
    s = s.replace(" CR", "").replace("CR", "")
    negative = s.startswith("-")
    s = s.lstrip("-")
    # Determine format by position of last comma vs last dot
    last_dot = s.rfind(".")
    last_comma = s.rfind(",")
    if last_comma > last_dot:
        # Indonesian: dot=thousands, comma=decimal  e.g. 1.234.567,89
        s = s.replace(".", "").replace(",", ".")
    else:
        # Western or dot-only: comma=thousands, dot=decimal  e.g. 1,234,567.89 or 1.234.567
        s = s.replace(",", "")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def parse_date_ddmmyyyy(s: str) -> Optional[str]:
    """Normalise date to DD/MM/YYYY. Accepts DD/MM/YYYY, DD-MM-YY, or DD-MM-YYYY."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        return s
    # DD-MM-YY (CC format: 20-02-26)
    m = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{d}/{mo}/20{y}"
    # DD-MM-YYYY
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{d}/{mo}/{y}"
    return s
