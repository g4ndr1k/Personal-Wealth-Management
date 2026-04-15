"""
bca_rdn.py — Parser for BCA TAPRES (Rekening Dana Nasabah / RDN) statements.

Detection keywords (page 1):
  "REKENING TAPRES"  — BCA's savings product used for securities RDN accounts

Structure:
  Header: NOMOR REKENING, PERIODE (full date range), MATA UANG
  Transactions: DD/MM  DESCRIPTION  [CBG]  AMOUNT[DB]  [BALANCE]
  Summary: SALDO AWAL / MUTASI CR / MUTASI DB / SALDO AKHIR

Key differences from bca_savings:
  - Period line: "PERIODE : 01-01-2026 S/D 31-01-2026" (full dates, not month name)
  - Account number: "NOMOR REKENING : 04952478749" (vs "NO. REKENING")
  - Opening balance row "DD/MM SALDO AWAL <amount>" must be skipped
  - "TIDAK ADA TRANSAKSI" means no transactions for the month
  - Amount format: Western (3,460,000.00) — same as bca_savings
"""
from __future__ import annotations
import re
from typing import Optional

import pdfplumber

from .base import StatementResult, AccountSummary, Transaction
from .owner import detect_owner


# ── Regex patterns ─────────────────────────────────────────────────────────────

# "PERIODE : 01-01-2026 S/D 31-01-2026"
_RE_PERIOD = re.compile(
    r"PERIODE\s*:\s*(\d{2}-\d{2}-\d{4})\s+S/D\s+(\d{2}-\d{2}-\d{4})",
    re.IGNORECASE,
)

# "NOMOR REKENING : 04952478749"
_RE_ACCOUNT = re.compile(r"NOMOR\s+REKENING\s*:\s*(\d+)")

# "MATA UANG : IDR"
_RE_CURRENCY = re.compile(r"MATA\s+UANG\s*:\s*([A-Z]+)", re.IGNORECASE)

# Transaction anchor: starts with DD/MM
_TX_ANCHOR = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")

# Amount tail: optional CBG, amount, optional DB, optional running balance.
# The CBG lookbehind (?<!\w) ensures it only matches when the 3-4 digit code
# is not part of a longer alphanumeric token (e.g. "WS95051" → "5051" is NOT a CBG).
_AMOUNT_TAIL = re.compile(
    r"(?:(?<!\w)(\d{3,4})\s+)?"  # optional CBG branch code (word-boundary guard)
    r"([\d,]+\.\d{2})"           # amount  e.g. 3,460,000.00
    r"(\s+DB)?"                  # optional debit marker
    r"(?:\s+([\d,]+\.\d{2}))?$"  # optional running balance
)

# Summary values
_RE_SALDO_AWAL  = re.compile(r"SALDO AWAL\s*:\s*([\d,]+\.\d{2})")
_RE_SALDO_AKHIR = re.compile(r"SALDO AKHIR\s*:\s*([\d,]+\.\d{2})")
_RE_MUTASI_CR   = re.compile(r"MUTASI CR\s*:\s*([\d,]+\.\d{2})")
_RE_MUTASI_DB   = re.compile(r"MUTASI DB\s*:\s*([\d,]+\.\d{2})")

# Lines to skip in the main transaction-scanning loop.
# Note: \d+ /\d+ (space before slash) matches page numbers ("1 / 1") but NOT dates ("14/01").
# [A-Z][a-z] catches mixed-case header/footer/address lines without the IGNORECASE flag
# so that it only triggers on lines starting with a true uppercase letter.
_SKIP_RE = re.compile(
    r"^(TANGGAL|KETERANGAN|CBG|MUTASI|SALDO|CATATAN|•|Rekening|REKENING|"
    r"KCP|HALAMAN|PERIODE|MATA UANG|NOMOR|INDONESIA|GD |JL |JAKARTA|"
    r"TIDAK ADA|JSEB|\d+ /\d+|[A-Z][a-z])",
)

# Stop pattern for continuation-line collection (same as _SKIP_RE but without
# [A-Z][a-z], so merchant names like "Dividen BMRI" are still captured).
_CONT_STOP_RE = re.compile(
    r"^(TANGGAL|KETERANGAN|CBG|MUTASI|SALDO|CATATAN|•|Rekening|REKENING|"
    r"KCP|HALAMAN|PERIODE|MATA UANG|NOMOR|INDONESIA|GD |JL |JAKARTA|"
    r"TIDAK ADA|JSEB|\d+ /\d+)",
)


# ── Public interface ───────────────────────────────────────────────────────────

def can_parse(text: str) -> bool:
    return "REKENING TAPRES" in text


def parse(
    pdf_path: str,
    owner_mappings: dict | None = None,
    ollama_client=None,
) -> StatementResult:
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        all_texts = [p.extract_text() or "" for p in pdf.pages]

    full_text = "\n".join(all_texts)
    errors: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    period_start, period_end, year, month = _parse_period(full_text, errors)
    account_number = _parse_account(full_text, errors)
    currency = _parse_currency(full_text)
    customer_name = _parse_customer_name(all_texts[0])
    owner = detect_owner(customer_name, owner_mappings or None)

    # ── Transactions ──────────────────────────────────────────────────────────
    transactions = _parse_transactions(all_texts, account_number, owner, year, month, errors)

    # ── Summary ───────────────────────────────────────────────────────────────
    opening_balance = _extract_amount(_RE_SALDO_AWAL,  full_text)
    closing_balance = _extract_amount(_RE_SALDO_AKHIR, full_text)
    total_cr        = _extract_amount(_RE_MUTASI_CR,   full_text)
    total_db        = _extract_amount(_RE_MUTASI_DB,   full_text)

    accounts = [AccountSummary(
        product_name="BCA RDN",
        account_number=account_number,
        currency=currency,
        closing_balance=closing_balance or 0.0,
        opening_balance=opening_balance or 0.0,
        total_debit=total_db or 0.0,
        total_credit=total_cr or 0.0,
    )]

    return StatementResult(
        bank="BCA",
        statement_type="savings",
        owner=owner,
        customer_name=customer_name,
        print_date=period_end,
        period_start=period_start,
        period_end=period_end,
        accounts=accounts,
        transactions=transactions,
        raw_errors=errors,
    )


# ── Header helpers ─────────────────────────────────────────────────────────────

def _parse_period(text: str, errors: list) -> tuple[str, str, str, str]:
    """Returns (period_start, period_end, year, month) all as DD/MM/YYYY strings."""
    m = _RE_PERIOD.search(text)
    if not m:
        errors.append("BCA RDN: could not detect period")
        return "", "", "2026", "01"
    # Input: "01-01-2026" → output: "01/01/2026"
    start = m.group(1).replace("-", "/")
    end   = m.group(2).replace("-", "/")
    year  = m.group(1)[6:]    # YYYY from DD-MM-YYYY
    month = m.group(1)[3:5]   # MM
    return start, end, year, month


def _parse_account(text: str, errors: list) -> str:
    m = _RE_ACCOUNT.search(text)
    if not m:
        errors.append("BCA RDN: could not detect account number")
        return ""
    return m.group(1)


def _parse_currency(text: str) -> str:
    m = _RE_CURRENCY.search(text)
    return m.group(1).upper() if m else "IDR"


def _parse_customer_name(text: str) -> str:
    # "EMANUEL GUNARIS ADRIANTO NOMOR REKENING : 04952478749"
    m = re.search(r"^([A-Z][A-Z ]+[A-Z])\s+NOMOR REKENING", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


# ── Amount helper ──────────────────────────────────────────────────────────────

def _parse_amount(s: str) -> Optional[float]:
    """Western format: 3,460,000.00 → 3460000.0"""
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _extract_amount(pattern: re.Pattern, text: str) -> Optional[float]:
    m = pattern.search(text)
    return _parse_amount(m.group(1)) if m else None


# ── Transaction parser ─────────────────────────────────────────────────────────

def _parse_transactions(
    all_texts: list[str],
    account_number: str,
    owner: str,
    year: str,
    month: str,
    errors: list,
) -> list[Transaction]:
    txns: list[Transaction] = []

    for text in all_texts:
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line or _SKIP_RE.match(line):
                i += 1
                continue

            anchor = _TX_ANCHOR.match(line)
            if not anchor:
                i += 1
                continue

            date_raw, rest = anchor.groups()

            # Only parse dates in the statement month
            if date_raw.split("/")[1] != month:
                i += 1
                continue

            # Skip opening/closing balance rows
            if rest.startswith("SALDO AWAL") or rest.startswith("SALDO AKHIR"):
                i += 1
                continue

            # Collect continuation lines (use _CONT_STOP_RE, not _SKIP_RE, so
            # mixed-case lines like "Dividen BMRI" are included in the description)
            continuation: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if not next_line:
                    j += 1
                    continue
                if _TX_ANCHOR.match(next_line) or _CONT_STOP_RE.match(next_line):
                    break
                continuation.append(next_line)
                j += 1

            # Find amount — first try anchor line tail, then scan continuations
            tail_match = _AMOUNT_TAIL.search(rest)
            amount_source = rest
            cont_for_desc = continuation
            if not tail_match:
                for idx, cont in enumerate(continuation):
                    tm = _AMOUNT_TAIL.search(cont)
                    if tm:
                        tail_match = tm
                        amount_source = cont
                        cont_for_desc = continuation[:idx]
                        break

            if not tail_match:
                i = j
                continue

            _cbg, amount_str, db_marker, balance_str = tail_match.groups()
            amount = _parse_amount(amount_str)
            if amount is None:
                errors.append(f"BCA RDN: could not parse amount in: {line!r}")
                i = j
                continue

            balance = _parse_amount(balance_str) if balance_str else None
            is_debit = bool(db_marker)

            # Build description: primary (anchor line before amount) + useful continuations
            primary = (
                rest[:tail_match.start()].strip()
                if amount_source is rest
                else rest
            ).strip()

            extra_parts: list[str] = []
            for cont in cont_for_desc:
                # Skip pure numbers / separators
                if re.match(r"^[\d.,\-/]+$", cont):
                    continue
                # Skip bare long digit strings (account numbers)
                if re.match(r"^\d{10,}$", cont):
                    continue
                extra_parts.append(cont)

            full_desc = (primary + (" / " + " / ".join(extra_parts) if extra_parts else "")).strip()

            txns.append(Transaction(
                date_transaction=f"{date_raw}/{year}",
                date_posted=None,
                description=full_desc,
                currency="IDR",
                foreign_amount=None,
                exchange_rate=None,
                amount_idr=amount,
                tx_type="Debit" if is_debit else "Credit",
                balance=balance,
                account_number=account_number,
                owner=owner,
            ))

            i = j

    return txns
