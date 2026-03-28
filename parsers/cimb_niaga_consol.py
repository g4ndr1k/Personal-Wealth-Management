"""
CIMB Niaga Consolidated Portfolio Statement Parser
===================================================
Handles: Laporan Konsolidasi Portfolio / Combine Statement Portfolio

Key format characteristics:
- Detection: "CIMB Niaga" (bank name) + "COMBINE STATEMENT" (consol-specific term)
- Statement date: "Tanggal Laporan : DD Month YYYY" (bilingual)
- Contains savings account transaction tables (7-column format)
- Table columns: Transaction Date | Value Date | Description | Check No | Debit | Credit | Balance
- pdfplumber column indices: 4=date, 7=description, 9=debit, 10=credit
- Number format: Western comma-thousands, 2 decimals: "29,428,022.71"
- SALDO AWAL / SALDO AKHIR markers for opening/closing balances
- Account sections: "Nomor Rekening - Mata Uang / Account Number - Currency : XXXXXXXXXX - IDR"
- Multiple savings accounts possible; only accounts with transactions show detail rows
"""
from __future__ import annotations
import re
from datetime import date
from typing import Optional

import pdfplumber

from parsers.base import Transaction, AccountSummary, StatementResult


# ── Detection ──────────────────────────────────────────────────────────────

def can_parse(text: str) -> bool:
    # Bank name first; "COMBINE STATEMENT" is unique to the consolidated statement
    return "CIMB Niaga" in text and "COMBINE STATEMENT" in text


# ── Date helpers ───────────────────────────────────────────────────────────

_MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
    "MAY": 5, "JUNE": 6, "JULY": 7, "AUGUST": 8,
    "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
    "JANUARI": 1, "FEBRUARI": 2, "MARET": 3,
    "MEI": 5, "JUNI": 6, "JULI": 7, "AGUSTUS": 8,
    "OKTOBER": 10, "DESEMBER": 12,
}


def _parse_stmt_date(text: str) -> Optional[date]:
    """Extract 'Tanggal Laporan : DD Month YYYY' (bilingual heading)."""
    m = re.search(
        r"Tanggal Laporan\s*[:\s]+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    try:
        dd = int(m.group(1))
        mm = _MONTHS.get(m.group(2).upper())
        yyyy = int(m.group(3))
        if mm is None:
            return None
        return date(yyyy, mm, dd)
    except (ValueError, KeyError):
        return None


def _date_str(d: Optional[date]) -> Optional[str]:
    if d is None:
        return None
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _parse_ddmm(ddmm: str, stmt_date: date) -> Optional[str]:
    """Parse DD/MM → DD/MM/YYYY, using statement date for year context."""
    try:
        parts = str(ddmm).strip().split("/")
        if len(parts) != 2:
            return None
        dd, mm = int(parts[0]), int(parts[1])
        year = stmt_date.year
        if mm > stmt_date.month:
            year -= 1
        return f"{dd:02d}/{mm:02d}/{year}"
    except (ValueError, IndexError):
        return None


# ── Amount helpers ─────────────────────────────────────────────────────────

def _parse_amount(s: Optional[str]) -> float:
    """Parse '29,428,022.71' → 29428022.71; returns 0.0 if blank."""
    if not s:
        return 0.0
    s = str(s).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


# ── Owner detection ────────────────────────────────────────────────────────

def _detect_owner(text: str, owner_mappings: dict) -> str:
    header = text[:600].upper()
    for keyword, owner in owner_mappings.items():
        if keyword.upper() in header:
            return owner
    if "DIAN PRATIWI" in header:
        return "Helen"
    if "EMANUEL" in header:
        return "Gandrik"
    return "Unknown"


# ── Table helpers ──────────────────────────────────────────────────────────

# Column indices within the savings transaction table extracted by pdfplumber
_COL_TX_DATE = 4
_COL_DESC = 7
_COL_DEBIT = 9
_COL_CREDIT = 10

# Minimum number of columns in a valid transaction table row
_MIN_COLS = 11

_DDMM_RE = re.compile(r"^\d{2}/\d{2}$")


def _is_tx_table(table: list) -> bool:
    """
    Check if a pdfplumber table is the savings transaction table.
    Heuristic: header contains both 'Debet' and 'Kredit' keywords.
    """
    if not table or len(table) < 2:
        return False
    header_text = " ".join(
        str(c) for row in table[:5] for c in (row or []) if c
    )
    return "Debet" in header_text and "Kredit" in header_text


# Account section marker pattern
_ACCT_SECTION_RE = re.compile(
    r"Nomor Rekening - Mata Uang /Account Number - Currency\s*:\s*(\d+)\s*-\s*(\w+)"
)

# Account summary line: "701347791200 XTRA Savers MANFAAT IDR 36,489,221.45 36,489,221.45"
_ACCT_SUM_RE = re.compile(
    r"^(\d{9,12})\s+(.+?)\s+(IDR|USD|SGD|EUR)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$",
    re.MULTILINE,
)

_SALDO_AWAL_RE = re.compile(r"SALDO AWAL\s+([\d,]+\.\d{2})")
_SALDO_AKHIR_RE = re.compile(r"SALDO AKHIR\s+([\d,]+\.\d{2})")


# ── Core parsing logic ─────────────────────────────────────────────────────

def _build_account_map(all_text: str, stmt_date_str: Optional[str]) -> dict[str, AccountSummary]:
    """
    Build a map of account_number → AccountSummary from the asset summary table.
    Also extracts SALDO AWAL / SALDO AKHIR from account detail sections.
    """
    summaries: dict[str, AccountSummary] = {}

    # Parse account summary rows
    for m in _ACCT_SUM_RE.finditer(all_text):
        acct_no = m.group(1)
        if acct_no not in summaries:
            summaries[acct_no] = AccountSummary(
                product_name=m.group(2).strip(),
                account_number=acct_no,
                currency=m.group(3),
                closing_balance=_parse_amount(m.group(5)),
                opening_balance=0.0,
                print_date=stmt_date_str,
                period_end=stmt_date_str,
            )

    # Fill SALDO AWAL / SALDO AKHIR from account detail sections
    sections = list(_ACCT_SECTION_RE.finditer(all_text))
    for i, sec_m in enumerate(sections):
        acct_no = sec_m.group(1)
        start = sec_m.start()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(all_text)
        section_text = all_text[start:end]

        sa_m = _SALDO_AWAL_RE.search(section_text)
        if sa_m and acct_no in summaries:
            summaries[acct_no].opening_balance = _parse_amount(sa_m.group(1))

        sakhir_m = _SALDO_AKHIR_RE.search(section_text)
        if sakhir_m and acct_no in summaries:
            summaries[acct_no].closing_balance = _parse_amount(sakhir_m.group(1))

    return summaries


def _parse_tx_tables(
    pages: list,
    pages_text: list[str],
    stmt_date: date,
    owner: str,
    summaries: dict[str, AccountSummary],
) -> list[Transaction]:
    """
    Extract transactions from savings transaction tables across all pages.
    Tracks current account context page-by-page using text markers.
    """
    txns: list[Transaction] = []
    current_acct: Optional[str] = None
    acct_saldo_awal: dict[str, float] = {
        acct: s.opening_balance for acct, s in summaries.items()
    }
    running_saldo: dict[str, float] = dict(acct_saldo_awal)

    for page, page_text in zip(pages, pages_text):
        # Update current account context from page text
        sec_m = _ACCT_SECTION_RE.search(page_text)
        if sec_m:
            current_acct = sec_m.group(1)
            # Reset running saldo to SALDO AWAL for this account when section starts
            if current_acct not in running_saldo:
                running_saldo[current_acct] = acct_saldo_awal.get(current_acct, 0.0)

        if not current_acct:
            continue

        currency = summaries.get(current_acct, AccountSummary("", None, "IDR", 0)).currency or "IDR"

        for table in page.extract_tables():
            if not _is_tx_table(table):
                continue

            for row in table:
                if not row or len(row) < _MIN_COLS:
                    continue

                tx_date_raw = row[_COL_TX_DATE]
                if not tx_date_raw:
                    continue

                tx_date_str_raw = str(tx_date_raw).strip()
                if not _DDMM_RE.match(tx_date_str_raw):
                    continue

                desc_raw = str(row[_COL_DESC] or "").strip()
                if not desc_raw or "SALDO AWAL" in desc_raw or "SALDO AKHIR" in desc_raw:
                    continue

                debit_amt = _parse_amount(str(row[_COL_DEBIT]).strip() if row[_COL_DEBIT] else "")
                credit_amt = _parse_amount(str(row[_COL_CREDIT]).strip() if row[_COL_CREDIT] else "")

                if debit_amt == 0.0 and credit_amt == 0.0:
                    continue

                is_credit = credit_amt > 0.0
                amount_idr = credit_amt if is_credit else -debit_amt
                running_saldo[current_acct] = running_saldo.get(current_acct, 0.0) + amount_idr

                # Join all lines of a multi-line description
                desc_lines = [ln.strip() for ln in desc_raw.split("\n") if ln.strip()]
                description = " / ".join(desc_lines) if desc_lines else desc_raw

                date_str = _parse_ddmm(tx_date_str_raw, stmt_date)

                txns.append(Transaction(
                    date_transaction=date_str or "",
                    date_posted=date_str,
                    description=description,
                    currency=currency,
                    foreign_amount=None,
                    exchange_rate=None,
                    amount_idr=amount_idr,
                    tx_type="Credit" if is_credit else "Debit",
                    balance=running_saldo[current_acct],
                    account_number=current_acct,
                    owner=owner,
                ))

    return txns


# ── Main parser ─────────────────────────────────────────────────────────────

def parse(
    pdf_path: str,
    owner_mappings: dict | None = None,
    ollama_client=None,
) -> StatementResult:
    """
    Parse a CIMB Niaga Consolidated Portfolio Statement PDF.
    Extracts savings account transactions and account summaries.
    """
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        pages = list(pdf.pages)
        pages_text = [page.extract_text() or "" for page in pages]

    all_text = "\n".join(pages_text)

    stmt_date = _parse_stmt_date(all_text)
    if stmt_date is None:
        raise ValueError("Could not find 'Tanggal Laporan' in CIMB Niaga consol PDF")
    stmt_date_str = _date_str(stmt_date)

    owner = _detect_owner(all_text, owner_mappings)

    summaries = _build_account_map(all_text, stmt_date_str)
    txns = _parse_tx_tables(pages, pages_text, stmt_date, owner, summaries)

    summaries_list = list(summaries.values())

    primary = summaries_list[0] if summaries_list else AccountSummary(
        product_name="CIMB Niaga Savings",
        account_number="",
        currency="IDR",
        closing_balance=0.0,
        print_date=stmt_date_str,
    )

    # Sheet name uses statement date month/year
    sheet_name = stmt_date.strftime("%b %Y") + " Consol"

    # Period: first day of statement month → statement date
    period_start_str = f"01/{stmt_date.month:02d}/{stmt_date.year}"

    return StatementResult(
        bank="CIMB Niaga",
        statement_type="consol",
        owner=owner,
        sheet_name=sheet_name,
        print_date=stmt_date_str,
        transactions=txns,
        summary=primary,
        accounts=summaries_list,
        period_start=period_start_str,
        period_end=stmt_date_str,
    )
