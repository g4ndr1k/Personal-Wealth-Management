"""
Permata Bank Savings Statement Parser
======================================
Handles: Rekening Koran / Account Statement

Transaction line format (observed from actual PDFs):
  DD/MM  DD/MM  DESCRIPTION  AMOUNT  BALANCE
  (only 2 numeric values per line — amount and running balance)
  Debit/credit determined by saldo direction vs previous saldo.

Covers:
  - RDN Ind IDR, PERMATATAB OPTIMA, Tabungan USD
  - Permata ME Saver, Permata ME Saver iB (Syariah)
"""

from __future__ import annotations
import re
from datetime import date
from typing import Optional

import pdfplumber

from parsers.base import Transaction, AccountSummary, StatementResult


# ── Detection ──────────────────────────────────────────────────────────────

def can_parse(text_page1: str) -> bool:
    return (
        "Rekening Koran" in text_page1
        and "Account Statement" in text_page1
        and "Periode Laporan" in text_page1
        and "Permata" in text_page1
    )


# ── Number parsing ─────────────────────────────────────────────────────────

def _parse_num(s: str) -> float:
    """
    Parse numbers in either:
      Indonesian: 1.116.726.951,20  (dot=thousands, comma=decimal)
      USD-style:  56,925.79         (comma=thousands, period=decimal)
    """
    s = s.strip()
    if not s or s == "-":
        return 0.0
    last_dot = s.rfind(".")
    last_comma = s.rfind(",")
    if last_dot > last_comma:
        # USD style: 56,925.79
        return float(s.replace(",", ""))
    elif last_comma > last_dot:
        # Indonesian style: 1.116.726.951,20
        return float(s.replace(".", "").replace(",", "."))
    else:
        try:
            return float(s)
        except ValueError:
            return 0.0


# ── Date parsing ───────────────────────────────────────────────────────────

MONTHS_ID = {
    "JANUARI": 1, "FEBRUARI": 2, "MARET": 3, "APRIL": 4,
    "MEI": 5, "JUNI": 6, "JULI": 7, "AGUSTUS": 8,
    "SEPTEMBER": 9, "OKTOBER": 10, "NOVEMBER": 11, "DESEMBER": 12,
}


def _parse_period(text: str) -> tuple[Optional[date], Optional[date]]:
    m = re.search(
        r"(\d{2})\s+([A-Z]+)\s+(\d{4})\s*[-\u2013]\s*(\d{2})\s+([A-Z]+)\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None
    try:
        d1 = date(int(m.group(3)), MONTHS_ID[m.group(2).upper()], int(m.group(1)))
        d2 = date(int(m.group(6)), MONTHS_ID[m.group(5).upper()], int(m.group(4)))
        return d1, d2
    except (KeyError, ValueError):
        return None, None


def _parse_statement_date(text: str) -> Optional[date]:
    m = re.search(
        r"Tanggal Laporan\s+(\d{1,2})\s+([A-Z]+)\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS_ID[m.group(2).upper()], int(m.group(1)))
    except (KeyError, ValueError):
        return None


def _date_to_str(d: Optional[date]) -> Optional[str]:
    """Convert date object to DD/MM/YYYY string."""
    if d is None:
        return None
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _parse_tx_date(ddmm: str, period_start: date, period_end: date) -> Optional[str]:
    """Parse DD/MM transaction date to DD/MM/YYYY string."""
    try:
        parts = ddmm.strip().split("/")
        if len(parts) != 2:
            return None
        dd, mm = int(parts[0]), int(parts[1])
        year = period_end.year
        if mm > period_end.month:
            year = period_start.year
        return f"{dd:02d}/{mm:02d}/{year}"
    except (ValueError, IndexError):
        return None


# ── Transaction line regex ─────────────────────────────────────────────────

# Format: DD/MM  DD/MM  DESCRIPTION  AMOUNT  BALANCE
_TX_LINE = re.compile(
    r"^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})$"
)
# USD format amounts end with period decimal: 56,925.79
_TX_LINE_USD = re.compile(
    r"^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$"
)
_SALDO_AWAL = re.compile(r"^SALDO AWAL\s+([\d.,]+)$")
_TOTAL_LINE = re.compile(r"^Total\s+([\d.,]+)\s+([\d.,]+)$")
_ACCT_NO = re.compile(r"^No\.\s*Rekening\s+(\d+)\s*$")
_PRODUCT = re.compile(r"^Nama Produk\s+(.+)")
_CURRENCY = re.compile(r"^Mata Uang\s+(\w+)$")

_SKIP_SUBSTRS = [
    "Disclaimer", "Laporan transaksi ini sah", "Jika dalam waktu",
    "Downloaded eStatement", "PermataBank.com", "PT Bank Permata",
    "serta merupakan", "Halaman/Page", "Rasakan kemudah",
    "Permata Bank telah", "Realisasi bagi hasil", "Efektif 2",
    "Gunakan layanan", "Product Name", "Trx. Date", "Val. Date",
    "Trx. Description", "Debit Credit", "Statement Period",
    "Statement Date", "CIF No.", "Rekening Koran", "Account Statement",
    "Kepada Yth", "Periode Laporan", "Tanggal Laporan", "No.CIF",
    "Ringkasan Rekening", "Rekening Simpanan", "Rekening Investasi",
    "Ekuivalen Saldo", "Nama Produk", "(dd/mm)", "Tgl Trx.",
    "Tgl Valuta", "Uraian Trx.", "Pemberitahuan Privasi",
    "Unit Usaha Syariah", "privacynotice", "https://", "Account No.",
    "Branch", "Currency",
]


def _should_skip(line: str) -> bool:
    return any(p in line for p in _SKIP_SUBSTRS)


# ── Account section parsing ─────────────────────────────────────────────────

def _parse_sections(
    pages_text: list[str],
    period_start: date,
    period_end: date,
    owner: str,
) -> tuple[list[Transaction], list[AccountSummary]]:

    all_txns: list[Transaction] = []
    summaries: list[AccountSummary] = []

    current_acct = ""
    current_product = ""
    current_currency = "IDR"
    in_table = False
    section_done = False
    last_saldo = 0.0
    saldo_awal = 0.0
    total_debit = 0.0
    total_credit = 0.0
    pending_tx: Optional[Transaction] = None
    section_txns: list[Transaction] = []

    def _commit_section():
        nonlocal saldo_awal, total_debit, total_credit, section_txns, last_saldo, section_done
        if current_acct and re.match(r"^\d+$", current_acct):
            summaries.append(AccountSummary(
                product_name=current_product,
                account_number=current_acct,
                currency=current_currency,
                closing_balance=last_saldo,
                opening_balance=saldo_awal,
                total_debit=total_debit,
                total_credit=total_credit,
                period_start=_date_to_str(period_start),
                period_end=_date_to_str(period_end),
            ))
            all_txns.extend(section_txns)
        section_txns = []
        saldo_awal = 0.0
        total_debit = 0.0
        total_credit = 0.0
        last_saldo = 0.0
        section_done = False

    for page_text in pages_text:
        lines = page_text.splitlines()

        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue

            # New account section
            acct_m = _ACCT_NO.match(line_s)
            if acct_m:
                new_acct = acct_m.group(1)
                if new_acct == current_acct:
                    continue
                if pending_tx is not None:
                    section_txns.append(pending_tx)
                    pending_tx = None
                _commit_section()
                current_acct = new_acct
                current_product = ""
                current_currency = "IDR"
                in_table = False
                section_done = False
                continue

            # Product name
            prod_m = _PRODUCT.match(line_s)
            if prod_m and "Product Name" not in line_s and current_acct and not in_table and not section_done:
                raw = prod_m.group(1)
                raw = re.sub(r"\s+terkait.*", "", raw, flags=re.IGNORECASE).strip()
                raw = re.sub(r"\s+Pemberitahuan.*", "", raw, flags=re.IGNORECASE).strip()
                raw = re.sub(r"\s{2,}.*", "", raw).strip()
                if raw and len(raw) < 60:
                    current_product = raw
                continue

            # Currency
            curr_m = _CURRENCY.match(line_s)
            if curr_m and current_acct:
                current_currency = curr_m.group(1)
                continue

            # Saldo awal
            sa_m = _SALDO_AWAL.match(line_s)
            if sa_m and current_acct:
                if pending_tx is not None:
                    section_txns.append(pending_tx)
                    pending_tx = None
                saldo_awal = _parse_num(sa_m.group(1))
                last_saldo = saldo_awal
                in_table = True
                continue

            # Table header
            if "Tgl Trx." in line_s or "(dd/mm)" in line_s:
                in_table = True
                continue

            # Total line
            tot_m = _TOTAL_LINE.match(line_s)
            if tot_m and current_acct and in_table:
                if pending_tx is not None:
                    section_txns.append(pending_tx)
                    pending_tx = None
                total_debit = _parse_num(tot_m.group(1))
                total_credit = _parse_num(tot_m.group(2))
                in_table = False
                section_done = True
                continue

            if not in_table or not current_acct:
                continue

            if _should_skip(line_s):
                continue

            # Try transaction line (Indonesian format or USD format)
            tx_m = _TX_LINE.match(line_s) or _TX_LINE_USD.match(line_s)
            if tx_m:
                if pending_tx is not None:
                    section_txns.append(pending_tx)

                date_tx = _parse_tx_date(tx_m.group(1), period_start, period_end)
                date_val = _parse_tx_date(tx_m.group(2), period_start, period_end)
                description = tx_m.group(3).strip()
                amount = _parse_num(tx_m.group(4))
                new_saldo = _parse_num(tx_m.group(5))

                if new_saldo < last_saldo - 0.01:
                    tx_type = "Debit"
                    amount_idr = -amount
                else:
                    tx_type = "Credit"
                    amount_idr = amount

                last_saldo = new_saldo

                pending_tx = Transaction(
                    date_transaction=date_tx or "",
                    date_posted=date_val,
                    description=description,
                    currency=current_currency,
                    foreign_amount=None,
                    exchange_rate=None,
                    amount_idr=amount_idr,
                    tx_type=tx_type,
                    balance=new_saldo,
                    account_number=current_acct,
                    owner=owner,
                )
                continue

            # Continuation line for multi-line descriptions
            if pending_tx is not None and in_table:
                if not re.match(r"^\d{2}/\d{2}", line_s) and not _should_skip(line_s):
                    if not re.match(r"^[\d\s.,:;]+$", line_s):
                        pending_tx.description += " " + line_s

    if pending_tx is not None:
        section_txns.append(pending_tx)
    _commit_section()

    return all_txns, summaries


# ── Owner detection ────────────────────────────────────────────────────────

def _detect_owner(text: str, owner_mappings: dict[str, str]) -> str:
    header = text[:600].upper()
    for keyword, owner in owner_mappings.items():
        if keyword.upper() in header:
            return owner
    if "DIAN PRATIWI" in header:
        return "Helen"
    if "EMANUEL" in header:
        return "Gandrik"
    return "Unknown"


# ── Main parser ─────────────────────────────────────────────────────────────

def parse(
    pdf_path: str,
    owner_mappings: dict[str, str] | None = None,
    ollama_client=None,
) -> StatementResult:
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]

    all_text = "\n".join(pages_text)

    period_start, period_end = _parse_period(all_text)
    if period_end is None:
        raise ValueError("Could not find Periode Laporan in PDF")

    stmt_date = _parse_statement_date(all_text) or period_end
    stmt_date_str = _date_to_str(stmt_date)
    owner = _detect_owner(all_text, owner_mappings)

    all_txns, summaries = _parse_sections(pages_text, period_start, period_end, owner)

    # Deduplicate summaries: keep only the last (most complete) entry per account number
    seen: dict[str, AccountSummary] = {}
    for s in summaries:
        if s.account_number not in seen or s.opening_balance > 0 or s.closing_balance > 0:
            seen[s.account_number] = s
    summaries = list(seen.values())

    # Filter: only keep proper numeric account numbers (skip bond/fund codes like ORI026T6)
    summaries = [s for s in summaries if re.match(r"^\d{7,}$", s.account_number)]

    for s in summaries:
        s.print_date = stmt_date_str

    sheet_name = period_end.strftime("%b %Y") + " Savings"

    primary_summary = summaries[0] if summaries else AccountSummary(
        product_name="Permata Savings",
        account_number="",
        currency="IDR",
        closing_balance=0.0,
        opening_balance=0.0,
        print_date=stmt_date_str,
        period_start=_date_to_str(period_start),
        period_end=_date_to_str(period_end),
    )

    return StatementResult(
        bank="Permata",
        statement_type="savings",
        owner=owner,
        sheet_name=sheet_name,
        print_date=stmt_date_str,
        transactions=all_txns,
        summary=primary_summary,
        accounts=summaries,
    )
