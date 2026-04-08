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
    closing_balance_idr: float = 0.0    # Bank's own IDR equivalent (from Saldo Rupiah column)
    print_date: Optional[str] = None    # DD/MM/YYYY
    period_start: Optional[str] = None  # DD/MM/YYYY
    period_end: Optional[str] = None    # DD/MM/YYYY
    credit_limit: Optional[float] = None
    extra: dict = field(default_factory=dict)  # Flexible bag for consolidated statements


@dataclass
class BondHolding:
    """
    A single bond position from the 'Rekening Investasi Obligasi' summary
    table in a Permata (or similar) consolidated statement.

    Column mapping from PDF:
      product_name      — e.g. "FR0097", "INDOIS54 NEW", "INDON47"
      currency          — "IDR" or "USD"
      face_value        — Outstanding Amount (nominal / par value)
      market_price      — Price as % of face value (e.g. 104.734, 96.651)
      market_value      — face_value × market_price/100 in original currency
      market_value_idr  — Saldo Rupiah: bank's own IDR equivalent
      unrealised_pl     — Unrealized P/L in original currency (negative = loss)
      unrealised_pl_pct — Unrealized P/L as %  (e.g. -6.00)
      statement_fx_rate — Implied FX: market_value_idr / market_value (1.0 for IDR)
    """
    product_name:      str
    currency:          str
    face_value:        float
    market_price:      float
    market_value:      float
    market_value_idr:  float
    unrealised_pl:     float
    unrealised_pl_pct: float
    statement_fx_rate: float


@dataclass
class InvestmentHolding:
    """
    A single equity or mutual-fund position from an IPOT portfolio PDF.

    Column mapping from PDF:
      asset_name        — full company/fund name e.g. "BANK MANDIRI (PERSERO) Tbk"
      isin_or_code      — ticker e.g. "BMRI", "BNGA", "XRDN"
      asset_class       — "stock" or "mutual_fund"
      quantity          — shares (stock) or units (mutual fund)
      unit_price        — Close price (stock) or Last NAV (fund) in IDR
      market_value_idr  — Market Value column from PDF (IDR)
      cost_basis_idr    — Stock Value / Avg Value column from PDF (IDR)
      unrealised_pnl_idr — Unrealize column (IDR, negative = loss)
    """
    asset_name: str
    isin_or_code: str
    asset_class: str          # "stock" or "mutual_fund"
    quantity: float
    unit_price: float
    market_value_idr: float
    cost_basis_idr: float
    unrealised_pnl_idr: float


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
    bonds: list[BondHolding] = field(default_factory=list)
    holdings: list[InvestmentHolding] = field(default_factory=list)
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
    elif "." in s and "," not in s:
        parts = s.split(".")
        # Indonesian whole-IDR amounts commonly use dot thousands only:
        #   147.857   → 147857
        #   1.572.426 → 1572426
        # Treat dot-only values as thousands separators when every group after
        # the first is exactly 3 digits.
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)
        else:
            # Western decimal or already-normalized number
            s = s.replace(",", "")
    else:
        # Western or dot-only: comma=thousands, dot=decimal  e.g. 1,234,567.89 or 1.234.567
        s = s.replace(",", "")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def _parse_ipot_amount(s: str) -> Optional[float]:
    """
    Parse IPOT Western number format where commas are ALWAYS thousands separators.

    Examples:
      '2,084,355,000' → 2084355000.0
      '6,349.54'      → 6349.54
      '-122,363,304'  → -122363304.0

    Note: parse_idr_amount() cannot be used here. For comma-only strings like
    '2,084,355,000' it detects Indonesian format (last_comma > last_dot=-1) and
    replaces commas with dots → float('2.084.355.000') raises ValueError.
    """
    if not s:
        return None
    s = str(s).strip().replace(" ", "")
    negative = s.startswith("-")
    s = s.lstrip("-").replace(",", "")
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
    return None
