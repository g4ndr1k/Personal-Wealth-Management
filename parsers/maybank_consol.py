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
    StatementResult, AccountSummary, Transaction, BondHolding,
    parse_idr_amount, parse_date_ddmmyyyy
)

# ── Detection signature ─────────────────────────────────────────────────────
DETECTION_KEYWORDS = [
    "RINGKASAN PORTOFOLIO NASABAH",
    "DETAIL & MUTASI TRANSAKSI",
    "Consolidated Statement",
    "ALOKASI ASET",
]  # kept for reference; can_parse uses bank-name-first approach


def can_parse(text_page1: str) -> bool:
    # "RINGKASAN PORTOFOLIO NASABAH" appears on page 2 of the consolidated statement.
    # The caller passes combined (p1+p2) text, so this reliably fires for consol
    # while never matching a pure CC statement (which has no portfolio summary page).
    # "ALOKASI ASET" on page 1 is also a strong unique signal.
    if "Maybank" not in text_page1:
        return False
    return "RINGKASAN PORTOFOLIO" in text_page1 or "ALOKASI ASET" in text_page1


# ── Main parser ──────────────────────────────────────────────────────────────
def parse(pdf_path: str, ollama_client=None) -> StatementResult:
    errors = []
    customer_name = ""
    period_start = period_end = report_date = ""
    accounts: list[AccountSummary] = []
    raw_bonds: list[dict] = []      # raw dicts from obligasi table; processed after FX extracted
    transactions: list[Transaction] = []
    exchange_rates: dict = {}

    with pdfplumber.open(pdf_path) as pdf:
        all_pages = pdf.pages
        full_texts = [p.extract_text() or "" for p in all_pages]

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
                accs, page_raw_bonds = _parse_summary_table(table, errors)
                accounts.extend(accs)
                raw_bonds.extend(page_raw_bonds)

        # ── Layer 1+2: transaction pages (pages 3 onward) ─────────────────
        for pg_idx, text in enumerate(full_texts):
            if "Mutasi Debet" in text or "Mutasi Kredit" in text:
                page = all_pages[pg_idx]
                tables = page.extract_tables()
                for table in tables:
                    txns = _parse_transaction_table(table, errors, ollama_client)
                    transactions.extend(txns)

        # ── Layer 2: exchange rates (last page) ───────────────────────────
        exchange_rates = _extract_exchange_rates(full_texts[-1])

    # Build BondHolding objects now that exchange_rates are available
    bonds = _build_bond_holdings(raw_bonds, exchange_rates)

    # ── Back-fill account numbers from detail pages ───────────────────────
    # The summary tables (pages 1-2) don't contain account numbers.
    # Extract them from "Nama Produk/Mata Uang" + "Nomor Rekening" in detail pages.
    acct_num_map = _extract_account_numbers_from_detail(full_texts)
    for acct in accounts:
        if not acct.account_number:
            key = (acct.product_name, acct.currency)
            if key in acct_num_map:
                acct.account_number = acct_num_map[key]
            elif key in _KNOWN_ACCOUNT_NUMBERS:
                acct.account_number = _KNOWN_ACCOUNT_NUMBERS[key]
            elif acct.currency != "IDR":
                # Disambiguate multi-currency products (e.g. Super Valas SGD vs USD)
                # to prevent ON CONFLICT overwrites when both share the same product name.
                acct.account_number = f"{acct.product_name} {acct.currency}"

    return StatementResult(
        bank="Maybank",
        statement_type="consolidated",
        customer_name=customer_name,
        period_start=period_start,
        period_end=period_end,
        print_date=report_date,
        accounts=accounts,
        transactions=transactions,
        exchange_rates=exchange_rates,
        bonds=bonds,
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


# ── Known account numbers ──────────────────────────────────────────────────────
# Fallback for accounts that don't appear in any detail page (e.g. no
# transactions in the statement period).  Keyed by (product_name, currency).
_KNOWN_ACCOUNT_NUMBERS: dict[tuple[str, str], str] = {
    ("Maybank Tabungan Super Valas", "USD"): "2596001501",
}


# ── Account number extraction ─────────────────────────────────────────────────
def _extract_account_numbers_from_detail(full_texts: list[str]) -> dict[tuple[str, str], str]:
    """
    Scan detail pages for 'Nama Produk/Mata Uang ... / CCC\\nNomor Rekening NNNN'.
    Returns dict mapping (product_name, currency) → account_number.
    """
    mapping: dict[tuple[str, str], str] = {}
    pattern = re.compile(
        r"Nama Produk/Mata Uang\s+(.+?)/\s*([A-Z]{3})\s*\nNomor Rekening\s+(\d+)",
        re.MULTILINE,
    )
    for text in full_texts:
        for m in pattern.finditer(text):
            product = m.group(1).strip()
            currency = m.group(2).strip()
            acct_num = m.group(3).strip()
            mapping[(product, currency)] = acct_num
    return mapping


# ── Summary table helpers ─────────────────────────────────────────────────────
def _parse_summary_table(table: list, errors: list) -> tuple[list[AccountSummary], list[dict]]:
    """
    Returns (accounts, raw_bonds).
    raw_bonds is a list of dicts with raw numeric data from the Obligasi table;
    BondHolding objects are built later in parse() once exchange_rates are available.
    """
    accounts = []
    raw_bonds: list[dict] = []
    if not table or len(table) < 2:
        return accounts, raw_bonds
    header = [str(c or "").strip() for c in table[0]]
    header_joined = " ".join(header).lower()

    # Detect table type by header content
    if "kategori aset" in header_joined:
        # Page 1 "Ringkasan Alokasi Aset" — category-level aggregates.
        # These duplicate the page-2 product-level rows and are NOT individual
        # accounts.  Skip entirely to avoid orphan rows in account_balances.
        return [], []

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
                currency=currency, closing_balance=balance or 0.0,
            ))

    elif "nama produk" in header_joined and "nilai nominal" in header_joined:
        # Obligasi (bonds) — enhanced to capture market price % and unrealized P/L.
        # Typical MayBank columns:
        #   [0] Nama Produk | [1] Mata Uang | [2] Nilai Nominal
        #   [3] Harga Pasar (%) | [4] Laba/Rugi Belum Terealisasi | [-1] Nilai Pasar
        for row in table[1:]:
            if not row or not row[0]:
                continue
            name = str(row[0] or "").replace("\n", " ").strip()
            if not name or name.lower() in ("total", "jumlah"):
                continue
            currency_info = str(row[1] or "").strip() if len(row) > 1 else ""
            nominal = parse_idr_amount(str(row[2] or "")) if len(row) > 2 else None
            market_val = parse_idr_amount(str(row[-1] or ""))

            currency = "IDR"
            m = re.search(r"\b(IDR|USD|SGD|EUR|JPY)\b", currency_info)
            if m:
                currency = m.group(1)

            # Optional columns: Harga Pasar (%) at [3], Unrealized P/L at [4]
            market_price_raw: Optional[float] = None
            unrealised_idr_raw = 0.0
            if len(row) >= 5:
                market_price_raw = _parse_price_pct(str(row[3] or ""))
            if len(row) >= 6:
                unrealised_idr_raw = parse_idr_amount(str(row[4] or "")) or 0.0

            # Keep AccountSummary for summary display
            accounts.append(AccountSummary(
                product_name=name, account_number=None,
                currency=currency, closing_balance=market_val or 0.0,
                extra={"nominal": nominal, "coupon_rate": currency_info}
            ))
            # Collect raw bond data; BondHolding built after FX extraction
            if market_val:
                raw_bonds.append({
                    "product_name": name,
                    "currency": currency,
                    "face_value": nominal or 0.0,
                    "market_price_raw": market_price_raw,
                    "unrealised_idr_raw": unrealised_idr_raw,
                    "market_value_idr": market_val,
                })

    elif "jumlah unit" in header_joined or "reksa dana" in header_joined:
        # Reksa Dana (mutual funds) — tagged with account_category for holdings upsert
        for row in table[1:]:
            if not row or not row[0]:
                continue
            name = str(row[0] or "").replace("\n", " ").strip()
            if not name or name.lower() in ("total", "jumlah"):
                continue
            reksadana_type = str(row[1] or "").replace("\n", " ").strip() if len(row) > 1 else ""
            currency = str(row[2] or "").strip() if len(row) > 2 else "IDR"
            units = parse_idr_amount(str(row[3] or "")) if len(row) > 3 else None
            growth = str(row[4] or "").strip() if len(row) > 4 else ""
            unrealized = parse_idr_amount(str(row[5] or "")) if len(row) > 5 else None
            market_val = parse_idr_amount(str(row[-1] or ""))
            if not market_val:
                continue
            accounts.append(AccountSummary(
                product_name=name, account_number=None,
                currency=currency, closing_balance=market_val,
                extra={
                    "account_category": "mutual_fund",   # marker for _upsert_fund_holdings
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
                closing_balance=outstanding or 0.0,
                extra={"limit": limit}
            ))

    return accounts, raw_bonds


def _parse_price_pct(s: str) -> Optional[float]:
    """Parse a bond market-price percentage like '96,651' or '96.651'."""
    if not s:
        return None
    s = s.strip().replace(" ", "")
    if not s or s == "-":
        return None
    # Indonesian style: comma = decimal, no thousands separator in % values
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        # Western: comma=thousands e.g. "1,234.56" — unlikely for price % but handle it
        s = s.replace(",", "")
    try:
        val = float(s)
        return val if 0 < val < 1000 else None  # sanity: bond price ≈ 50-150%
    except ValueError:
        return None


def _build_bond_holdings(raw_bonds: list[dict], exchange_rates: dict) -> list[BondHolding]:
    """
    Convert raw Obligasi dicts (collected during table parsing) into proper
    BondHolding objects now that the exchange_rates dict is available.

    For IDR bonds:
      fx = 1.0, market_value = market_value_idr, unrealised_pl = unrealised_idr_raw
    For USD/foreign bonds:
      fx = exchange_rates[currency]
      market_value = market_value_idr / fx
      unrealised_pl = unrealised_idr_raw / fx
    """
    holdings = []
    for rb in raw_bonds:
        currency = rb["currency"]
        face_value = rb["face_value"]
        market_value_idr = rb["market_value_idr"]
        market_price_raw = rb.get("market_price_raw")
        unrealised_idr_raw = rb.get("unrealised_idr_raw") or 0.0

        if currency == "IDR":
            fx = 1.0
            market_value = market_value_idr
            unrealised_pl = unrealised_idr_raw
        else:
            fx = exchange_rates.get(currency, 0.0)
            if fx > 0:
                market_value = round(market_value_idr / fx, 2)
                unrealised_pl = round(unrealised_idr_raw / fx, 2)
            else:
                market_value = 0.0
                unrealised_pl = 0.0

        # Market price as % of face value
        if market_price_raw is not None:
            market_price = market_price_raw
        elif face_value > 0 and market_value > 0:
            market_price = round(100.0 * market_value / face_value, 4)
        else:
            market_price = 0.0

        # Unrealized P/L %
        cost_basis = market_value - unrealised_pl
        if cost_basis != 0:
            unrealised_pl_pct = round(100.0 * unrealised_pl / cost_basis, 2)
        else:
            unrealised_pl_pct = 0.0

        holdings.append(BondHolding(
            product_name=rb["product_name"],
            currency=currency,
            face_value=face_value,
            market_price=market_price,
            market_value=market_value,
            market_value_idr=market_value_idr,
            unrealised_pl=unrealised_pl,
            unrealised_pl_pct=unrealised_pl_pct,
            statement_fx_rate=fx,
        ))
    return holdings


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
                        currency=currency,
                        foreign_amount=None, exchange_rate=None,
                        amount_idr=bal,
                        tx_type="Credit",
                        balance=bal,
                        account_number="",
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
            currency=currency,
            foreign_amount=None,   # Savings transactions are in native currency
            exchange_rate=None,
            amount_idr=amount_idr,
            tx_type="Credit" if is_credit else "Debit",
            balance=balance,
            account_number="",
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
