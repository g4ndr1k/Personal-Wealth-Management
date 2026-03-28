"""
CIMB Niaga Credit Card Billing Statement Parser
================================================
Handles: Credit Card Billing Statement (Lembar Tagihan Kartu Kredit)

Key format characteristics:
- Detection: "CIMB Niaga" (bank name) + "Tgl. Statement" (CC-specific date label)
- Statement date: "Tgl. Statement DD/MM/YY" → year = 20YY
- Transaction date format: DD/MM (no year on each row)
- Year boundary: if tx month > statement month → previous year
- Number format: Western comma-thousands, 2 decimals: "1,791,583.25"
- Credit rows end with " CR"
- Foreign currency inline: "BILLED AS USD 2.99(1 USD = 17016.66 IDR)"
- Card separator lines: "5289 NNXX XXXX NNNN OWNER NAME" → switches owner
- Multi-owner: primary + supplementary card sections
"""
from __future__ import annotations
import re
from datetime import date
from typing import Optional

import pdfplumber

from parsers.base import Transaction, AccountSummary, StatementResult


# ── Detection ──────────────────────────────────────────────────────────────

def can_parse(text: str) -> bool:
    # Bank name first; "Tgl. Statement" (abbreviated) is CC-specific —
    # the consol uses "Tanggal Laporan" (full form), not "Tgl. Statement"
    return "CIMB Niaga" in text and "Tgl. Statement" in text


# ── Helpers ────────────────────────────────────────────────────────────────

def _parse_stmt_date(text: str) -> Optional[date]:
    """Extract statement date from 'Tgl. Statement DD/MM/YY'."""
    m = re.search(r"Tgl\.\s*Statement\s+(\d{2})/(\d{2})/(\d{2})", text)
    if not m:
        return None
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + yy, mm, dd)


def _date_str(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _parse_ddmm(ddmm: str, stmt_date: date) -> Optional[str]:
    """Parse DD/MM to DD/MM/YYYY using statement date for year context."""
    try:
        parts = ddmm.strip().split("/")
        if len(parts) != 2:
            return None
        dd, mm = int(parts[0]), int(parts[1])
        year = stmt_date.year
        if mm > stmt_date.month:
            year -= 1
        return f"{dd:02d}/{mm:02d}/{year}"
    except (ValueError, IndexError):
        return None


def _parse_amount(s: str) -> float:
    """Parse '1,791,583.25' → 1791583.25"""
    return float(s.replace(",", ""))


def _detect_owner(name: str, owner_mappings: dict, default: str) -> str:
    name_upper = name.upper()
    for keyword, owner in owner_mappings.items():
        if keyword.upper() in name_upper:
            return owner
    return default


# ── Patterns ───────────────────────────────────────────────────────────────

# Card separator: "5289 NNXX XXXX NNNN OWNER NAME" (in PERINCIAN TAGIHAN section)
_CARD_SEP = re.compile(
    r"^(5289\s+\d{2}XX\s+XXXX\s+\d{4})\s+(.+)$"
)

# Transaction: DD/MM DD/MM DESCRIPTION AMOUNT [CR]
_TX_PAT = re.compile(
    r"^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([\d,]+\.\d{2})(\s+CR)?$"
)

# LAST BALANCE (opening) and ENDING BALANCE (closing)
_LAST_BAL = re.compile(r"^LAST BALANCE\s+([\d,]+\.\d{2})(\s+CR)?$")
_END_BAL = re.compile(r"^ENDING BALANCE\s+([\d,]+\.\d{2})(\s+CR)?$")
_SUBTOTAL = re.compile(r"^SUBTOTAL\s+([\d,]+\.\d{2})(\s+CR)?$")

# Foreign currency inline: "BILLED AS USD 2.99(1 USD = 17016.66 IDR)"
_FX_PAT = re.compile(
    r"BILLED AS ([A-Z]+)\s+([\d.]+)\(1 [A-Z]+ = ([\d.]+) IDR\)"
)

# Credit limit: "MC PLATINUM REGULER 150,000,000.00 37,500,000.00 ..."
_CREDIT_LIMIT_PAT = re.compile(
    r"(?:MC PLATINUM|VISA|JCB)\s+\w+(?:\s+\w+)?\s+([\d,]+\.\d{2})\s+[\d,]+\.\d{2}\s+[\d,]+\.\d{2}"
)

# Skip lines in PERINCIAN TAGIHAN that are headers, not transactions
_SKIP_HEADERS = {
    "Jenis Kartu", "Batas Kredit", "Batas Penarikan",
    "Tgl. Transaksi", "Tgl. Pembukuan", "Keterangan", "Jumlah",
    "Sisa Kredit", "Sisa Penarikan", "HALAMAN",
}


# ── Transaction parsing ────────────────────────────────────────────────────

def _parse_transactions(
    lines: list[str],
    stmt_date: date,
    primary_card: str,
    primary_owner: str,
    owner_mappings: dict,
) -> tuple[list[Transaction], float, float]:
    """
    Parse PERINCIAN TAGIHAN section.
    Returns (transactions, opening_balance, closing_balance).
    opening_balance = LAST BALANCE value (CR suffix → stored as-is positive,
    semantically means credit balance / bank owes customer).
    """
    txns: list[Transaction] = []
    current_card = primary_card
    current_owner = primary_owner
    opening_balance = 0.0
    closing_balance = 0.0
    in_detail = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line:
            continue

        # Enter detail section
        if "PERINCIAN TAGIHAN" in line:
            in_detail = True
            continue

        if not in_detail:
            continue

        # Stop at summary section
        if "RINGKASAN TAGIHAN" in line:
            break

        # Skip known header lines
        if any(h in line for h in _SKIP_HEADERS):
            continue

        # LAST BALANCE (opening)
        lb_m = _LAST_BAL.match(line)
        if lb_m:
            amt = _parse_amount(lb_m.group(1))
            # CR = credit balance (bank owes customer); store negative for consistency
            opening_balance = -amt if lb_m.group(2) else amt
            continue

        # ENDING BALANCE (closing)
        eb_m = _END_BAL.match(line)
        if eb_m:
            amt = _parse_amount(eb_m.group(1))
            closing_balance = -amt if eb_m.group(2) else amt
            continue

        # Subtotal line → skip
        if _SUBTOTAL.match(line):
            continue

        # Card separator → switch current owner
        sep_m = _CARD_SEP.match(line)
        if sep_m:
            current_card = sep_m.group(1).replace(" ", "")
            owner_name = re.sub(r"^DR\s+", "", sep_m.group(2).strip())
            current_owner = _detect_owner(owner_name, owner_mappings, primary_owner)
            continue

        # Regular transaction line
        tx_m = _TX_PAT.match(line)
        if not tx_m:
            continue

        date_tx = _parse_ddmm(tx_m.group(1), stmt_date)
        date_posted = _parse_ddmm(tx_m.group(2), stmt_date)
        description = tx_m.group(3).strip()
        amount = _parse_amount(tx_m.group(4))
        is_credit = tx_m.group(5) is not None

        # Collect continuation lines (description wrap-around from pdfplumber)
        while i < len(lines):
            cont = lines[i].strip()
            if not cont:
                break
            # Stop on structural markers
            if ("PERINCIAN TAGIHAN" in cont or "RINGKASAN TAGIHAN" in cont or
                    any(h in cont for h in _SKIP_HEADERS)):
                break
            # Stop on new transaction anchor (DD/MM DD/MM), balance, subtotal, card sep
            if (_TX_PAT.match(cont) or _LAST_BAL.match(cont) or _END_BAL.match(cont) or
                    _SUBTOTAL.match(cont) or _CARD_SEP.match(cont)):
                break
            # Stop on pure-number / amount-only lines
            if re.match(r"^[\d,]+\.\d{2}$", cont):
                break
            description = description + " / " + cont
            i += 1

        # Detect and extract foreign currency from description
        currency = "IDR"
        foreign_amount = None
        exchange_rate = None
        fx_m = _FX_PAT.search(description)
        if fx_m:
            currency = fx_m.group(1)
            foreign_amount = float(fx_m.group(2))
            exchange_rate = float(fx_m.group(3))
            description = description[:fx_m.start()].strip()

        # Credit (payment/refund) = negative spend; Debit (charge) = positive
        amount_idr = -amount if is_credit else amount

        txns.append(Transaction(
            date_transaction=date_tx or "",
            date_posted=date_posted,
            description=description,
            currency=currency,
            foreign_amount=foreign_amount,
            exchange_rate=exchange_rate,
            amount_idr=amount_idr,
            tx_type="Credit" if is_credit else "Debit",
            balance=None,
            account_number=current_card,
            owner=current_owner,
        ))

    return txns, opening_balance, closing_balance


# ── Main parser ─────────────────────────────────────────────────────────────

def parse(
    pdf_path: str,
    owner_mappings: dict | None = None,
    ollama_client=None,
) -> StatementResult:
    """
    Parse a CIMB Niaga credit card billing statement PDF.
    Returns StatementResult with transactions tagged by owner.
    """
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    stmt_date = _parse_stmt_date(all_text)
    if stmt_date is None:
        raise ValueError("Could not find 'Tgl. Statement' in CIMB Niaga CC PDF")
    stmt_date_str = _date_str(stmt_date)

    # Detect customer name and primary owner from document header
    # Header line: "FIRSTNAME LASTNAME Jenis Kartu / ..."
    customer_name = ""
    primary_owner = ""
    name_m = re.search(r"^([A-Z][A-Z ]+[A-Z])\s+Jenis Kartu", all_text, re.MULTILINE)
    if name_m:
        customer_name = name_m.group(1).strip()
    elif "EMANUEL" in all_text[:300]:
        customer_name = "EMANUEL GUNARIS ADRIANTO"

    primary_owner = _detect_owner(customer_name, owner_mappings, "")
    if not primary_owner and "EMANUEL" in all_text[:500]:
        primary_owner = _detect_owner("EMANUEL", owner_mappings, "Gandrik")

    # Primary card number (first 5289 card in document header — before PERINCIAN TAGIHAN)
    primary_card = ""
    card_m = re.search(r"(5289\s+\d{2}XX\s+XXXX\s+\d{4})", all_text)
    if card_m:
        primary_card = card_m.group(1).replace(" ", "")

    # Card product type
    prod_m = re.search(r"(MC PLATINUM REGULER|MC PLATINUM SYARIAH|VISA PLATINUM|JCB PRECIOUS)", all_text)
    card_product = prod_m.group(1) if prod_m else "CIMB Niaga CC"

    # Credit limit from PERINCIAN TAGIHAN table
    credit_limit = 0.0
    lim_m = _CREDIT_LIMIT_PAT.search(all_text)
    if lim_m:
        credit_limit = _parse_amount(lim_m.group(1))

    # Parse transactions
    lines = all_text.splitlines()
    txns, opening_bal, closing_bal = _parse_transactions(
        lines, stmt_date, primary_card, primary_owner, owner_mappings
    )

    sheet_name = stmt_date.strftime("%b %Y") + " CC"

    summary = AccountSummary(
        product_name=card_product,
        account_number=primary_card,
        currency="IDR",
        closing_balance=closing_bal,
        opening_balance=opening_bal,
        print_date=stmt_date_str,
        credit_limit=credit_limit,
    )

    return StatementResult(
        bank="CIMB Niaga",
        statement_type="cc",
        owner=primary_owner,
        sheet_name=sheet_name,
        print_date=stmt_date_str,
        transactions=txns,
        summary=summary,
        accounts=[summary],
        customer_name=customer_name,
    )
