"""
Parser for BCA Credit Card Statement (Rekening Kartu Kredit).

Structure observed from real PDFs:
  Page 1: Header + transaction list + billing summary
  Page 2: Informational content (discarded)

Header fields (raw text, regex):
  TANGGAL REKENING   : 03 MARET 2026   ← report/print date
  TANGGAL JATUH TEMPO: 19 MARET 2026
  TAGIHAN BARU       : RP 1.791.583
  PEMBAYARAN MINIMUM : RP 1.791.583
  NOMOR CUSTOMER     : 14020257

Card number line (just before transactions):
  4556-33XX-XXXX-0602 EMANUEL G.A

Transaction row format (all IDR, no foreign currency in this statement type):
  11-FEB 11-FEB CICILAN BCA KE 11 DARI 12, HELLO - FX SU 1.791.583
  19-FEB 19-FEB PEMBAYARAN - DEBET OTOMATIS 1.791.583 CR

Date format: DD-MON (e.g. 11-FEB, 19-FEB) — no year, derived from report date year/month context.

Key differences from Maybank CC:
  - Dates are DD-MON not DD-MM-YY
  - Numbers use dot thousands separator, no comma decimals (1.791.583 = IDR 1,791,583)
  - No foreign currency transactions in this statement sample
  - Subtotal/Total lines must be skipped
  - "SALDO SEBELUMNYA" is the opening balance row
"""
import re
import pdfplumber
from typing import Optional
from .base import (
    StatementResult, AccountSummary, Transaction,
    parse_idr_amount, parse_date_ddmmyyyy
)

DETECTION_KEYWORDS = [
    "REKENING KARTU KREDIT",
    "TAGIHAN BARU",
    "KUALITAS KREDIT",
    "SALDO SEBELUMNYA",
]  # kept for reference; can_parse uses bank-name-first approach

# Indonesian month abbreviations → zero-padded month number
_MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MEI": "05", "JUN": "06", "JUL": "07", "AGU": "08",
    "SEP": "09", "OKT": "10", "NOV": "11", "DES": "12",
}

# Full Indonesian month names (for header date parsing)
_MONTHS_FULL = {
    "JANUARI": "01", "FEBRUARI": "02", "MARET": "03", "APRIL": "04",
    "MEI": "05", "JUNI": "06", "JULI": "07", "AGUSTUS": "08",
    "SEPTEMBER": "09", "OKTOBER": "10", "NOVEMBER": "11", "DESEMBER": "12",
}

# Skip lines that are subtotals/headers
_SKIP_PATTERNS = re.compile(
    r"^(SUBTOTAL|TOTAL|SALDO SEBELUMNYA|TANGGAL|KETERANGAN|VISA CARD|"
    r"MASTERCARD|JCB|% SUKU|KREDIT LIMIT|BATAS TARIK|TAGIHAN SEBELUMNYA|"
    r"BIAYA ADM|PEMBAYARAN/CR|PEMBELANJAAN|TUNGGAKAN|BEA METERAI|"
    r"SISA |REKENING KARTU|INFORMASI|PROMO|SEGENAP|\d+ / \d+)",
    re.IGNORECASE
)


def can_parse(text_page1: str) -> bool:
    # Bank name first; "KARTU KREDIT" distinguishes CC from BCA savings (Tahapan)
    return ("BCA" in text_page1 or "Bank Central Asia" in text_page1) and "KARTU KREDIT" in text_page1


def parse(pdf_path: str, ollama_client=None) -> StatementResult:
    errors = []
    customer_name = ""
    card_number = ""
    report_date = ""
    due_date = ""
    total_bill = 0.0
    min_payment = 0.0
    credit_limit = 0.0
    transactions = []

    with pdfplumber.open(pdf_path) as pdf:
        page1_text = pdf.pages[0].extract_text() or ""

    # ── Layer 2: header metadata ──────────────────────────────────────────
    customer_name = _extract_customer_name(page1_text)
    card_number = _extract_card_number(page1_text)
    report_date = _extract_report_date(page1_text)
    due_date = _extract_due_date(page1_text)
    total_bill = _extract_labelled_amount(page1_text, "TAGIHAN BARU") or 0.0
    min_payment = _extract_labelled_amount(page1_text, "PEMBAYARAN MINIMUM") or 0.0
    credit_limit = _extract_limit(page1_text)

    # ── Layer 2: transactions ─────────────────────────────────────────────
    report_year, report_month = _year_from_report_date(report_date)
    transactions = _parse_transactions(page1_text, card_number, report_year, report_month, errors, ollama_client)

    # Infer period from transaction dates
    period_start, period_end = _infer_period(transactions, report_date)

    accounts = [AccountSummary(
        product_name="BCA Kartu Kredit",
        account_number=card_number,
        currency="IDR",
        closing_balance=total_bill,
        credit_limit=credit_limit,
        extra={
            "min_payment": min_payment,
            "due_date": due_date,
        }
    )]

    return StatementResult(
        bank="BCA",
        statement_type="cc",
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
    # "EMANUEL G.A I0/00960/N" — name is before the account code
    m = re.search(r"REKENING KARTU KREDIT\s*\n(.+?)\s+[A-Z]\d+/\d+/[A-Z]", text)
    if m:
        return m.group(1).strip()
    # Fallback: all-caps name line
    for line in text.splitlines()[:8]:
        line = line.strip()
        if re.match(r"^[A-Z][A-Z .]+$", line) and 4 < len(line) < 50:
            return line
    return ""


def _extract_card_number(text: str) -> str:
    m = re.search(r"(\d{4}-\d{2}XX-XXXX-\d{4})", text)
    return m.group(1) if m else ""


def _extract_report_date(text: str) -> str:
    """Parse 'TANGGAL REKENING : 03 MARET 2026' → DD/MM/YYYY"""
    m = re.search(r"TANGGAL REKENING\s*:\s*(\d{1,2})\s+([A-Z]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        day, mon, year = m.groups()
        mo = _MONTHS_FULL.get(mon.upper(), "01")
        return f"{day.zfill(2)}/{mo}/{year}"
    return ""


def _extract_due_date(text: str) -> str:
    m = re.search(r"TANGGAL JATUH TEMPO\s*:\s*(\d{1,2})\s+([A-Z]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        day, mon, year = m.groups()
        mo = _MONTHS_FULL.get(mon.upper(), "01")
        return f"{day.zfill(2)}/{mo}/{year}"
    return ""


def _extract_labelled_amount(text: str, label: str) -> Optional[float]:
    m = re.search(label + r"\s*:\s*RP\s*([\d.,]+)", text, re.IGNORECASE)
    if m:
        return _parse_bca_amount(m.group(1))
    return None


def _extract_limit(text: str) -> float:
    # "KREDIT LIMIT GABUNGAN ... 124.000.000"
    m = re.search(r"KREDIT LIMIT\s*\n?GABUNGAN\s+([\d.]+)", text)
    if m:
        return _parse_bca_amount(m.group(1)) or 0.0
    # Also in summary line: first number on the bottom summary row
    m = re.search(r"(\d{1,3}(?:\.\d{3})+)\s+\d{1,3}(?:\.\d{3})+\s+0\s+0\s+0", text)
    if m:
        return _parse_bca_amount(m.group(1)) or 0.0
    return 0.0


def _year_from_report_date(report_date: str) -> tuple[int, int]:
    """Returns (year: int, month: int) from DD/MM/YYYY report date."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})$", report_date)
    if m:
        return int(m.group(3)), int(m.group(2))
    return 2026, 1


# ── BCA number format ──────────────────────────────────────────────────────────
def _parse_bca_amount(s: str) -> Optional[float]:
    """BCA uses dot as thousands separator, no decimal comma: 1.791.583 → 1791583.0"""
    if not s:
        return None
    s = s.strip().replace(" ", "")
    is_negative = s.startswith("-")
    s = s.lstrip("-")
    # If it ends with ,XX treat as Indonesian decimal (shouldn't appear in BCA CC, but be safe)
    if re.search(r",\d{2}$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(".", "")
    try:
        val = float(s)
        return -val if is_negative else val
    except ValueError:
        return None


def _bca_date_to_iso(dd_mon: str, report_year: int, report_month: int) -> str:
    """
    Convert 'DD-MON' to DD/MM/YYYY, handling year boundaries.

    Rule from Tanggal Rekening:
      - Transaction month <= report month → same year as report
      - Transaction month >  report month → previous year
        (e.g. report is Jan 2026, transaction is Dec → Dec 2025)
    """
    m = re.match(r"(\d{1,2})-([A-Z]{3})", dd_mon.strip().upper())
    if not m:
        return dd_mon
    day, mon = m.groups()
    tx_month = int(_MONTHS.get(mon, "01"))
    year = report_year if tx_month <= report_month else report_year - 1
    return f"{day.zfill(2)}/{tx_month:02d}/{year}"


# ── Transaction parsing ────────────────────────────────────────────────────────
# Transaction line: DD-MON DD-MON  <description>  <amount> [CR]
_TX_ROW = re.compile(
    r"^(\d{2}-[A-Z]{3})\s+(\d{2}-[A-Z]{3})\s+(.+?)\s+([\d.]+)\s*(CR)?$",
    re.IGNORECASE
)


def _parse_transactions(text: str, card_number: str, report_year: int, report_month: int,
                        errors: list, ollama_client=None) -> list[Transaction]:
    txns = []
    lines = text.splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _SKIP_PATTERNS.match(line):
            continue

        # Opening balance row: "SALDO SEBELUMNYA 1.791.583"
        if line.startswith("SALDO SEBELUMNYA"):
            m = re.search(r"([\d.]+)$", line)
            if m:
                amt = _parse_bca_amount(m.group(1))
                if amt:
                    txns.append(Transaction(
                        date_transaction="", date_posted=None,
                        description="Saldo Sebelumnya",
                        currency="IDR",
                        foreign_amount=None, exchange_rate=None,
                        amount_idr=amt,
                        tx_type="Debit",
                        balance=None,
                        account_number=card_number,
                    ))
            continue

        m = _TX_ROW.match(line)
        if not m:
            continue

        date_tx_raw, date_post_raw, desc, amount_str, cr_flag = m.groups()
        date_tx = _bca_date_to_iso(date_tx_raw, report_year, report_month)
        date_post = _bca_date_to_iso(date_post_raw, report_year, report_month)
        amount = _parse_bca_amount(amount_str)
        if amount is None:
            errors.append(f"BCA CC: could not parse amount in: {line!r}")
            continue
        is_credit = bool(cr_flag)

        txns.append(Transaction(
            date_transaction=date_tx,
            date_posted=date_post,
            description=desc.strip(),
            currency="IDR",
            foreign_amount=None,
            exchange_rate=None,
            amount_idr=amount,
            tx_type="Credit" if is_credit else "Debit",
            balance=None,
            account_number=card_number,
        ))

    return txns


def _infer_period(transactions: list, report_date: str) -> tuple[str, str]:
    dates = [t.date_transaction for t in transactions if t.date_transaction]
    if not dates:
        return "", report_date

    def sortkey(d):
        parts = d.split("/")
        if len(parts) == 3:
            return parts[2] + parts[1] + parts[0]
        return d

    dates.sort(key=sortkey)
    return dates[0], dates[-1]
