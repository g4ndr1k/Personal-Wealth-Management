"""
Parser for Maybank Consolidated Statement (Laporan Konsolidasi).

Structure observed from real PDFs:
  Page 1 : Header + Ringkasan Alokasi Aset (summary table)
  Page 2 : Ringkasan Portofolio (Tabungan, Obligasi, Reksa Dana, Kartu Kredit)
  Page 3 : (usually a footnote page, skipped)
  Page 4+ : Detail & Mutasi Transaksi — one sub-section per account/currency
  Last pg : Exchange rates + Info Penting

Parsing strategy:
  Layer 1 (pdfplumber tables): Summary tables on pages 1-2, transaction tables on pages 4-5
  Layer 2 (regex on raw text): Period, account numbers, exchange rates, "Saldo Awal"
  Layer 3 (Ollama): Only if a transaction row fails both layers (unexpected format)
"""
import re
import pdfplumber
from typing import Optional
from .base import (
    StatementResult, AccountSummary, Transaction,
    parse_idr_amount, parse_date_ddmmyyyy
)

# ── Detection signature ─────────────────────────────────────────────────────
DETECTION_KEYWORDS = [
    "RINGKASAN PORTOFOLIO NASABAH",
    "DETAIL & MUTASI TRANSAKSI",
    "Consolidated Statement",
    "ALOKASI ASET",
]


def can_parse(text_page1: str) -> bool:
    return any(kw in text_page1 for kw in DETECTION_KEYWORDS)


# ── Main parser ──────────────────────────────────────────────────────────────
def parse(pdf_path: str, ollama_client=None) -> StatementResult:
    errors = []
    customer_name = ""
    period_start = period_end = report_date = ""
    accounts: list[AccountSummary] = []
    transactions: list[Transaction] = []
    exchange_rates: dict = {}

    with pdfplumber.open(pdf_path) as pdf:
        all_pages = pdf.pages
        full_texts = [p.extract_text() or "" for p in all_pages]
        full_text = "\n".join(full_texts)

        # ── Layer 2: header metadata ──────────────────────────────────────
        customer_name = _extract_customer_name(full_texts[0])
        period_start, period_end = _extract_period(full_texts[0])
        report_date = period_end  # Consolidated uses end-of-period as report date

        # ── Layer 1: summary tables pages 1-2 ────────────────────────────
        for pg_idx in [0, 1]:
            if pg_idx >= len(all_pages):
                continue
            page = all_pages[pg_idx]
            tables = page.extract_tables()
            for table in tables:
                accs = _parse_summary_table(table, errors)
                accounts.extend(accs)

        # ── Layer 1+2: transaction pages (pages 3 onward) ─────────────────
        # Find which pages have "MUTASI TRANSAKSI"
        for pg_idx, text in enumerate(full_texts):
            if "Mutasi Debet" in text or "Mutasi Kredit" in text:
                page = all_pages[pg_idx]
                tables = page.extract_tables()
                for table in tables:
                    txns = _parse_transaction_table(table, errors, ollama_client)
                    transactions.extend(txns)

        # ── Layer 2: exchange rates (last page) ───────────────────────────
        exchange_rates = _extract_exchange_rates(full_texts[-1])

    return StatementResult(
        bank="Maybank",
        statement_type="consolidated",
        customer_name=customer_name,
        period_start=period_start,
        period_end=period_end,
        report_date=report_date,
        accounts=accounts,
        transactions=transactions,
        exchange_rates=exchange_rates,
        raw_errors=errors,
    )


# ── Header helpers ────────────────────────────────────────────────────────────
def _extract_customer_name(text: str) -> str:
    # Name appears on first or second line after stripping leading numbers/spaces
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines[:6]:
        # Skip lines that are purely numeric or look like CIF/codes
        if re.match(r"^[A-Z][A-Z ]+$", line) and len(line) > 5:
            return line
    return ""


def _extract_period(text: str) -> tuple[str, str]:
    """Extract period from 'Periode Laporan: 1-28/02/2026' style."""
    m = re.search(r"Periode Laporan[:\s]+(\d+)-(\d+)/(\d{2})/(\d{4})", text)
    if m:
        day_start, day_end, month, year = m.groups()
        return f"{day_start.zfill(2)}/{month}/{year}", f"{day_end.zfill(2)}/{month}/{year}"
    # Fallback: look for any DD/MM/YYYY
    dates = re.findall(r"\d{1,2}/\d{2}/\d{4}", text)
    if len(dates) >= 2:
        return dates[0], dates[-1]
    if dates:
        return dates[0], dates[0]
    return "", ""


# ── Summary table helpers ─────────────────────────────────────────────────────
def _parse_summary_table(table: list, errors: list) -> list[AccountSummary]:
    accounts = []
    if not table or len(table) < 2:
        return accounts
    header = [str(c or "").strip() for c in table[0]]
    header_joined = " ".join(header).lower()

    # Detect table type by header content
    if "kategori aset" in header_joined or "saldo" in header_joined:
        # Asset allocation summary
        for row in table[1:]:
            if not row or not row[0]:
                continue
            name = str(row[0] or "").replace("\n", " ").strip()
            if not name or name.lower() in ("total", "kategori"):
                continue
            currency = str(row[1] or "").strip() if len(row) > 1 else "IDR"
            balance_str = str(row[-1] or "").strip() if len(row) > 1 else ""
            balance = parse_idr_amount(balance_str)
            accounts.append(AccountSummary(
                product_name=name,
                account_number=None,
                currency=currency,
                balance=balance,
            ))

    elif "nama produk" in header_joined and "jumlah rekening" in header_joined:
        # Tabungan portfolio
        for row in table[1:]:
            if not row or not row[0]:
                continue
            name = str(row[0] or "").replace("\n", " ").strip()
            currency = str(row[1] or "").strip() if len(row) > 1 else "IDR"
            balance_str = str(row[-1] or "").strip()
            balance = parse_idr_amount(balance_str)
            accounts.append(AccountSummary(
                product_name=name, account_number=None,
                currency=currency, balance=balance,
            ))

    elif "nama produk" in header_joined and "nilai nominal" in header_joined:
        # Obligasi
        for row in table[1:]:
            if not row or not row[0]:
                continue
            name = str(row[0] or "").replace("\n", " ").strip()
            currency_info = str(row[1] or "").strip()
            nominal = parse_idr_amount(str(row[2] or ""))
            market_val = parse_idr_amount(str(row[-1] or ""))
            currency = "IDR"
            m = re.search(r"^(IDR|USD|SGD)", currency_info)
            if m:
                currency = m.group(1)
            accounts.append(AccountSummary(
                product_name=name, account_number=None,
                currency=currency, balance=market_val,
                extra={"nominal": nominal, "coupon_rate": currency_info}
            ))

    elif "jumlah unit" in header_joined or "reksa dana" in header_joined:
        # Reksa Dana
        for row in table[1:]:
            if not row or not row[0]:
                continue
            name = str(row[0] or "").replace("\n", " ").strip()
            if not name:
                continue
            reksadana_type = str(row[1] or "").replace("\n", " ").strip() if len(row) > 1 else ""
            currency = str(row[2] or "").strip() if len(row) > 2 else "IDR"
            units = parse_idr_amount(str(row[3] or "")) if len(row) > 3 else None
            growth = str(row[4] or "").strip() if len(row) > 4 else ""
            unrealized = parse_idr_amount(str(row[5] or "")) if len(row) > 5 else None
            market_val = parse_idr_amount(str(row[-1] or ""))
            accounts.append(AccountSummary(
                product_name=name, account_number=None,
                currency=currency, balance=market_val,
                extra={
                    "type": reksadana_type,
                    "units": units,
                    "growth_pct": growth,
                    "unrealized_gain_loss": unrealized,
                }
            ))

    elif "nomor kartu" in header_joined:
        # Kartu Kredit summary
        for row in table[1:]:
            if not row or not row[0]:
                continue
            card_no = str(row[0] or "").strip()
            card_type = str(row[1] or "").replace("\n", " ").strip() if len(row) > 1 else ""
            limit = parse_idr_amount(str(row[2] or "")) if len(row) > 2 else None
            outstanding = parse_idr_amount(str(row[3] or "")) if len(row) > 3 else None
            accounts.append(AccountSummary(
                product_name=f"Kartu Kredit {card_type}",
                account_number=card_no,
                currency="IDR",
                balance=outstanding,
                extra={"limit": limit}
            ))

    return accounts


# ── Transaction table helpers ─────────────────────────────────────────────────
def _parse_transaction_table(table: list, errors: list, ollama_client=None) -> list[Transaction]:
    txns = []
    if not table or len(table) < 2:
        return txns

    header = [str(c or "").replace("\n", " ").strip().lower() for c in table[0]]

    # Detect currency from column headers: "Mutasi Debet (IDR)" etc.
    currency = "IDR"
    for h in header:
        m = re.search(r"\(([A-Z]{3})\)", h)
        if m:
            currency = m.group(1)
            break

    for row in table[1:]:
        if not row:
            continue
        date_str = str(row[0] or "").strip()
        desc = str(row[1] or "").replace("\n", " ").strip()
        debit_str = str(row[2] or "").strip() if len(row) > 2 else ""
        credit_str = str(row[3] or "").strip() if len(row) > 3 else ""
        balance_str = str(row[4] or "").strip() if len(row) > 4 else ""

        # Skip header-repeat rows, totals, empty rows
        if not desc or desc.lower() in ("keterangan", "total", "saldo awal"):
            if desc.lower() == "saldo awal":
                # Record opening balance as a synthetic credit row
                bal = parse_idr_amount(balance_str)
                if bal is not None:
                    txns.append(Transaction(
                        date_transaction=parse_date_ddmmyyyy(date_str) or "",
                        date_posted=None,
                        description="Saldo Awal",
                        debit_original=None, credit_original=bal,
                        amount_idr=bal, currency=currency,
                        foreign_amount=None, exchange_rate=None,
                        balance_idr=bal, is_credit=True,
                        account_number=None,
                    ))
            continue
        if re.match(r"^(tanggal|total)", date_str.lower()):
            continue

        date_norm = parse_date_ddmmyyyy(date_str)
        debit = parse_idr_amount(debit_str)
        credit = parse_idr_amount(credit_str)
        balance = parse_idr_amount(balance_str)

        # Determine direction
        is_credit = False
        amount_idr = 0.0
        if credit is not None and credit != 0:
            is_credit = True
            amount_idr = abs(credit)
        elif debit is not None:
            amount_idr = abs(debit)

        if amount_idr == 0 and not desc:
            continue

        txns.append(Transaction(
            date_transaction=date_norm or date_str,
            date_posted=None,
            description=desc,
            debit_original=abs(debit) if debit else None,
            credit_original=abs(credit) if credit else None,
            amount_idr=amount_idr,
            currency=currency,
            foreign_amount=None,   # Savings transactions are in native currency
            exchange_rate=None,
            balance_idr=balance,
            is_credit=is_credit,
            account_number=None,
        ))

    return txns


# ── Exchange rate helpers ──────────────────────────────────────────────────────
def _extract_exchange_rates(text: str) -> dict:
    """Parse 'AUD : 11.942,49' style rate table from last page."""
    KNOWN_CCY = {"AUD","CNY","EUR","GBP","HKD","JPY","MYR","SGD","THB","USD","TWD","CHF","CAD","NZD"}
    rates = {}
    for m in re.finditer(r"\b([A-Z]{3})\s*:\s*([\d.,]+)", text):
        currency = m.group(1)
        if currency not in KNOWN_CCY:
            continue
        amount = parse_idr_amount(m.group(2))
        if amount:
            rates[currency] = amount
    return rates
