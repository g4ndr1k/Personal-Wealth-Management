"""
Parser for BCA Savings Statement (Rekening Tahapan / Laporan Mutasi Rekening).

Structure observed from real PDFs:
  Pages 1-4: Each page is identical layout — header + transaction rows
  Last page: Adds SALDO AWAL / MUTASI CR / MUTASI DB / SALDO AKHIR summary

Header (every page, regex):
  NO. REKENING  : 2171138631
  PERIODE       : FEBRUARI 2026
  MATA UANG     : IDR

Transaction row format in raw text (multi-line per transaction):
  Line 1:  DD/MM  <TRANSACTION TYPE>  [<DESCRIPTION_1>]  [<CBG>]  <AMOUNT>[DB]  [<BALANCE>]
  Line 2+: continuation lines (reference numbers, counterparty name, notes) — ignored for amount

  Examples:
    "04/02 TRSF E-BANKING CR 0402/FTSCY/WS95031 200,000.00 147,788,766.21"
    "05/02 TRSF E-BANKING DB 0502/FTSCY/WS95031 30,000,000.00 DB"
    "05/02 TRANSAKSI DEBIT TGL: 05/02 16,602.00 DB"
    "25/02 KR OTOMATIS LLG-ANZ INDONESIA 0938 148,987,163.00 163,616,921.21"
    "28/02 BUNGA 656.62"
    "28/02 PAJAK BUNGA 131.32 DB 160,739,706.51"

Key observations:
  - Date: DD/MM (no year — derived from PERIODE header)
  - Debit rows end with "DB" before optional balance
  - Credit rows have no "DB" marker
  - Amount uses comma decimal, dot thousands: 30,000,000.00 (Western format, unlike Maybank)
  - CBG column is a 3-4 digit branch code (optional, appears on some rows)
  - Balance appears only on last transaction of a group, not on every row
  - Continuation lines (reference numbers, names) must not be parsed as transactions
"""
import re
import pdfplumber
from typing import Optional
from .base import (
    StatementResult, AccountSummary, Transaction,
    parse_idr_amount, parse_date_ddmmyyyy
)

DETECTION_KEYWORDS = [
    "REKENING TAHAPAN",
    "LAPORAN MUTASI REKENING",
    "MUTASI CR",
    "MUTASI DB",
    "SALDO AKHIR",
]

_MONTHS_FULL = {
    "JANUARI": "01", "FEBRUARI": "02", "MARET": "03", "APRIL": "04",
    "MEI": "05", "JUNI": "06", "JULI": "07", "AGUSTUS": "08",
    "SEPTEMBER": "09", "OKTOBER": "10", "NOVEMBER": "11", "DESEMBER": "12",
}

# Lines that are NOT transaction rows
_SKIP_RE = re.compile(
    r"^(TANGGAL|KETERANGAN|CBG|MUTASI|SALDO|CATATAN|•|Apabila|BCA berhak|"
    r"telah|Rekening|REKENING TAHAPAN|KCU|HALAMAN|PERIODE|MATA UANG|"
    r"NO\. REKENING|INDONESIA|GD |JL |JAKARTA|Bersambung|KUNINGAN|"
    r"\d+ /\d+|[A-Z][a-z])",  # continuation lines often start mixed case
    re.IGNORECASE
)

# A transaction anchor line starts with DD/MM
_TX_ANCHOR = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")

# Amount at end of line: optional CBG code, then amount[DB], optional balance
# "30,000,000.00 DB" or "200,000.00 147,788,766.21" or "148,987,163.00 163,616,921.21"
_AMOUNT_TAIL = re.compile(
    r"(?:(\d{3,4})\s+)?"           # optional CBG branch code
    r"([\d,]+\.\d{2})"             # amount (Western format: 30,000,000.00)
    r"(\s+DB)?"                    # optional debit marker
    r"(?:\s+([\d,]+\.\d{2}))?$"    # optional running balance
)


def can_parse(text_page1: str) -> bool:
    return "REKENING TAHAPAN" in text_page1 and (
        "MUTASI" in text_page1 or "SALDO AWAL" in text_page1 or "PERIODE" in text_page1
    )


def parse(pdf_path: str, ollama_client=None) -> StatementResult:
    errors = []

    with pdfplumber.open(pdf_path) as pdf:
        all_texts = [p.extract_text() or "" for p in pdf.pages]

    full_text = "\n".join(all_texts)

    # ── Layer 2: header metadata ──────────────────────────────────────────
    customer_name = _extract_customer_name(all_texts[0])
    account_number = _extract_account_number(all_texts[0])
    period_month, period_year = _extract_period(all_texts[0])
    currency = _extract_currency(all_texts[0])

    # Derive period_start / period_end from PERIODE field
    mo = _MONTHS_FULL.get(period_month.upper(), "01")
    import calendar
    last_day = calendar.monthrange(int(period_year), int(mo))[1]
    period_start = f"01/{mo}/{period_year}"
    period_end = f"{last_day:02d}/{mo}/{period_year}"
    report_date = period_end  # savings has no separate print date

    # ── Layer 2: transactions ─────────────────────────────────────────────
    transactions = _parse_transactions(
        all_texts, account_number, period_year, mo, errors, ollama_client
    )

    # ── Layer 2: summary from last page ──────────────────────────────────
    opening_balance = _extract_summary_value(all_texts[-1], "SALDO AWAL")
    closing_balance = _extract_summary_value(all_texts[-1], "SALDO AKHIR")
    total_cr = _extract_summary_value(all_texts[-1], "MUTASI CR")
    total_db = _extract_summary_value(all_texts[-1], "MUTASI DB")

    accounts = [AccountSummary(
        product_name="BCA Rekening Tahapan",
        account_number=account_number,
        currency=currency,
        closing_balance=closing_balance or 0.0,
        opening_balance=opening_balance or 0.0,
        total_debit=total_db or 0.0,
        total_credit=total_cr or 0.0,
        extra={"period": f"{period_month} {period_year}"},
    )]

    return StatementResult(
        bank="BCA",
        statement_type="savings",
        customer_name=customer_name,
        period_start=period_start,
        period_end=period_end,
        print_date=report_date,
        accounts=accounts,
        transactions=transactions,
        exchange_rates={},
        raw_errors=errors,
    )


# ── Header helpers ─────────────────────────────────────────────────────────────
def _extract_customer_name(text: str) -> str:
    m = re.search(r"REKENING TAHAPAN\s*\n\S+\s*\n(.+?)\s*\n", text)
    if m:
        return m.group(1).strip()
    # Line that has "NO. REKENING" on the right, name on the left
    m = re.search(r"^([A-Z][A-Z ]+)\s+NO\. REKENING", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_account_number(text: str) -> str:
    m = re.search(r"NO\.\s*REKENING\s*:\s*([\d]+)", text)
    return m.group(1) if m else ""


def _extract_period(text: str) -> tuple[str, str]:
    """Returns (month_name, year) e.g. ('FEBRUARI', '2026')"""
    m = re.search(r"PERIODE\s*:\s*([A-Z]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        return m.group(1).upper(), m.group(2)
    return "JANUARI", "2026"


def _extract_currency(text: str) -> str:
    m = re.search(r"MATA UANG\s*:\s*([A-Z]+)", text, re.IGNORECASE)
    return m.group(1) if m else "IDR"


def _extract_summary_value(text: str, label: str) -> Optional[float]:
    m = re.search(label + r"\s*:\s*([\d,]+\.\d{2})", text)
    if m:
        return _parse_bca_savings_amount(m.group(1))
    return None


# ── BCA savings number format ──────────────────────────────────────────────────
def _parse_bca_savings_amount(s: str) -> Optional[float]:
    """BCA savings uses Western format: 30,000,000.00 (comma=thousands, dot=decimal)"""
    if not s:
        return None
    s = s.strip().replace(" ", "")
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _bca_savings_date(dd_mm: str, year: str) -> str:
    """'04/02' + '2026' → '04/02/2026'"""
    m = re.match(r"(\d{2})/(\d{2})", dd_mm)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{year}"
    return dd_mm


# ── Transaction parsing ────────────────────────────────────────────────────────
def _parse_transactions(all_texts: list, account_number: str, year: str, month: str,
                        errors: list, ollama_client=None) -> list[Transaction]:
    txns = []

    for text in all_texts:
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Skip non-transaction lines
            if not line or _SKIP_RE.match(line):
                i += 1
                continue

            anchor = _TX_ANCHOR.match(line)
            if not anchor:
                i += 1
                continue

            date_raw, rest = anchor.groups()

            # Only parse dates matching our expected month
            date_month = date_raw.split("/")[1]
            if date_month != month:
                i += 1
                continue

            # Collect continuation lines (lines without a date anchor that aren't skip lines)
            continuation = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if not next_line:
                    j += 1
                    break
                # Stop if next line is another transaction or a structural line
                if _TX_ANCHOR.match(next_line) or _SKIP_RE.match(next_line):
                    break
                continuation.append(next_line)
                j += 1

            # Build full description from continuation
            # The reference/amount number lines in continuation are not useful for description
            desc_parts = [rest]
            for cont in continuation:
                # Skip pure number lines, reference codes, and lines starting with dashes
                if re.match(r"^[\d./\-]+$", cont) or cont == "-":
                    continue
                # Keep human-readable continuation (counterparty names, notes)
                if re.match(r"^[A-Z0-9]", cont) and not re.match(r"^\d{12,}$", cont):
                    desc_parts.append(cont)

            # Parse amount from the anchor line's tail
            tail_match = _AMOUNT_TAIL.search(rest)
            if not tail_match:
                i = j
                continue

            cbg, amount_str, db_marker, balance_str = tail_match.groups()
            amount = _parse_bca_savings_amount(amount_str)
            if amount is None:
                errors.append(f"BCA savings: could not parse amount in: {line!r}")
                i = j
                continue

            balance = _parse_bca_savings_amount(balance_str) if balance_str else None
            is_debit = bool(db_marker)

            # Clean description — remove the amount tail from rest
            desc_raw = rest[:tail_match.start()].strip()
            # Remove trailing CBG code from description if present
            if cbg:
                desc_raw = desc_raw.rstrip()
            # Append meaningful continuation
            extra_desc = " ".join(
                c for c in desc_parts[1:]
                if not re.match(r"^[\d./\-]+$", c) and c != "-"
            )
            full_desc = (desc_raw + (" / " + extra_desc if extra_desc else "")).strip()

            date_iso = _bca_savings_date(date_raw, year)

            txns.append(Transaction(
                date_transaction=date_iso,
                date_posted=None,
                description=full_desc,
                currency="IDR",
                foreign_amount=None,
                exchange_rate=None,
                amount_idr=amount,
                tx_type="Debit" if is_debit else "Credit",
                balance=balance,
                account_number=account_number,
            ))

            i = j

    return txns
