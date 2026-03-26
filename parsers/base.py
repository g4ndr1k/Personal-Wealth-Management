"""Abstract base class for all bank statement parsers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import re


@dataclass
class Transaction:
    """A single transaction row — works for both savings and CC."""
    date_transaction: str          # DD/MM/YYYY or DD-MM-YY
    date_posted: Optional[str]     # DD-MM-YY (CC only), None for savings
    description: str
    debit_original: Optional[float]   # In original currency (None if credit)
    credit_original: Optional[float]  # In original currency (None if debit)
    amount_idr: float              # Always in IDR (converted if foreign)
    currency: str                  # IDR, USD, SGD, TWD, etc.
    foreign_amount: Optional[float]  # Amount in original currency if foreign
    exchange_rate: Optional[float]   # Rate used for conversion
    balance_idr: Optional[float]   # Running balance (savings only)
    is_credit: bool                # True = money in, False = money out
    account_number: Optional[str]  # Source account/card number
    notes: str = ""


@dataclass
class AccountSummary:
    """Summary block for one account (savings/investment/CC)."""
    product_name: str
    account_number: Optional[str]
    currency: str
    balance: Optional[float]
    extra: dict = field(default_factory=dict)  # Extra fields (units, market value, etc.)


@dataclass
class StatementResult:
    """Full parsed result from one PDF."""
    bank: str
    statement_type: str            # "cc" | "consolidated"
    customer_name: str
    period_start: str              # DD/MM/YYYY
    period_end: str                # DD/MM/YYYY
    report_date: str               # Date of printing/generation
    accounts: list[AccountSummary]
    transactions: list[Transaction]
    exchange_rates: dict           # {"USD": 16898.00, "SGD": 13351.14, ...}
    raw_errors: list[str]          # Parser warnings/fallbacks used


def parse_idr_amount(s: str) -> Optional[float]:
    """Parse Indonesian number format: 1.234.567,89 → 1234567.89"""
    if not s:
        return None
    s = s.strip().replace(" ", "")
    # Handle CR suffix (credit indicator on CC)
    s = s.replace(" CR", "").replace("CR", "")
    # Remove negative sign for now, caller decides debit/credit
    negative = s.startswith("-")
    s = s.lstrip("-")
    # Indonesian format: dots as thousands sep, comma as decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def parse_date_ddmmyyyy(s: str) -> Optional[str]:
    """Normalise date to DD/MM/YYYY. Accepts DD/MM/YYYY or DD-MM-YY or DD-MM-YYYY."""
    if not s:
        return None
    s = s.strip()
    # Already DD/MM/YYYY
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        return s
    # DD-MM-YY (CC format: 20-02-26)
    m = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", s)
    if m:
        d, mo, y = m.groups()
        year = f"20{y}"
        return f"{d}/{mo}/{year}"
    # DD-MM-YYYY
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{d}/{mo}/{y}"
    return s
