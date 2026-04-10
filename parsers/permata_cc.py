"""
Permata Bank Credit Card Statement Parser
==========================================
Handles: Rekening Tagihan / Credit Card Billing

Key format characteristics:
- Date: DDMM (4 digits, no separator), e.g. "1702" = 17 Feb
- Year derived from Tanggal Cetak (DD/MM/YY)
- Numbers: Western comma-thousands, no decimal, e.g. "1,290,831"
- Credit rows end with " CR"
- Foreign currency: separate line "US DOLLAR 12.99 (1 USD = Rp 16,919.17)"
- Card separator lines: "NNNN-NNXX-XXXX-NNNN NAME 0" -> switches owner
- Multi-owner: Gandrik's card may contain Helen's sub-card section
"""

from __future__ import annotations
import re
from datetime import date
from typing import Optional

import pdfplumber

from parsers.base import (
    Transaction,
    AccountSummary,
    StatementResult,
    parse_idr_amount,
)


# ── Detection ──────────────────────────────────────────────────────────────

def can_parse(text_page1: str) -> bool:
    # Use the bilingual document title as primary anchor — it appears on page 1
    # of every Permata CC PDF regardless of card product name or layout version.
    # "Rekening Tagihan" / "Credit Card Billing" is unique to Permata CC;
    # Permata Savings uses "Rekening Koran" / "Account Statement" instead.
    # Avoids case-sensitivity issues: card names appear as "PERMATA BLACK" /
    # "PERMATAVISA INFINITE CREDIT CARD" (all-caps) — not "Permata".
    return "Rekening Tagihan" in text_page1 and "Credit Card Billing" in text_page1


# ── Helpers ────────────────────────────────────────────────────────────────

def _parse_print_date(text: str) -> Optional[str]:
    """Extract Tanggal Cetak DD/MM/YY -> DD/MM/YYYY string."""
    m = re.search(r"Tanggal Cetak\s+(\d{2})/(\d{2})/(\d{2})", text)
    if not m:
        return None
    day, month, year2 = m.group(1), m.group(2), int(m.group(3))
    return f"{day}/{month}/{2000 + year2}"


def _parse_print_date_obj(text: str) -> Optional[date]:
    """Extract Tanggal Cetak as date object (for year resolution)."""
    m = re.search(r"Tanggal Cetak\s+(\d{2})/(\d{2})/(\d{2})", text)
    if not m:
        return None
    day, month, year2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + year2, month, day)


def _parse_amount_str(s: str) -> int:
    """Parse '1,290,831' -> 1290831 (integer IDR, no decimals)."""
    return int(s.replace(",", "").strip())


def _parse_ddmm_date(ddmm: str, cetak: date) -> Optional[str]:
    """
    Parse 4-digit DDMM string into DD/MM/YYYY string.
    Year logic: if transaction month > cetak month → previous year.
    """
    if len(ddmm) != 4:
        return None
    try:
        dd, mm = int(ddmm[:2]), int(ddmm[2:])
        year = cetak.year
        if mm > cetak.month:
            year -= 1
        return f"{dd:02d}/{mm:02d}/{year}"
    except ValueError:
        return None


def _extract_card_info(text: str) -> tuple[str, str]:
    """Return (card_number, card_name) from page 1 text."""
    card_m = re.search(r"(\d{4}-\d{2}XX-XXXX-\d{4})", text)
    name_m = re.search(r"(PERMATA BLACK|PERMATAVISA INFINITE(?:\s+CREDIT CARD)?)", text, re.IGNORECASE)
    card_num = card_m.group(1) if card_m else ""
    card_name = name_m.group(1).strip() if name_m else "PERMATA CC"
    return card_num, card_name


def _extract_summary(text: str) -> dict:
    """Extract ringkasan transaksi values."""
    patterns = {
        "total_tagihan":      r"Total Tagihan saat ini \(Rp\)\s+([\d,]+)",
        "tagihan_sebelumnya": r"Tagihan Sebelumnya \(Rp\)\s+([\d,]+)",
        "pembelanjaan":       r"Pembelanjaan &\s*Pengembalian Tunai \(Rp\)\s+([\d,]+)",
        "pembayaran":         r"Pembayaran/Konversi Cicilan\s*\(Rp\)\s+([\d,]+)",
        "bunga":              r"Bunga & Biaya Administrasi\s*\(Rp\)\s+([\d,]+)",
        "pagu_kredit":        r"Pagu Kredit \(Rp\)\s+([\d,]+)",
        "min_payment":        r"Pembayaran Minimum \(Rp\)\s+([\d,]+)",
    }
    result = {}
    for k, pat in patterns.items():
        m = re.search(pat, text)
        result[k] = _parse_amount_str(m.group(1)) if m else 0
    return result


# ── Transaction line parsing ───────────────────────────────────────────────

# Pattern: DDMM DDMM DESCRIPTION [amount] [CR]
_TX_PATTERN = re.compile(
    r"^(\d{4})\s+(\d{4})\s+(.+?)\s+([\d,]+)(\s+CR)?$"
)

# Card separator: "NNNN-NNXX-XXXX-NNNN NAME 0"
_SEPARATOR_PATTERN = re.compile(
    r"^(\d{4}-\d{2}XX-XXXX-\d{4})\s+(.+?)\s+0$"
)

# FX note: "US DOLLAR 12.99 (1 USD = Rp 16,919.17)"
_FX_PATTERN = re.compile(
    r"([A-Z]+)\s+([\d.]+)\s+\(1\s+\w+\s+=\s+Rp\s+([\d,.]+)\)"
)


def _parse_transactions_from_lines(
    lines: list[str],
    cetak: date,
    primary_card: str,
    primary_owner: str,
    owner_mappings: dict[str, str],
) -> list[Transaction]:
    """
    Parse DETIL TRANSAKSI lines.
    Returns transactions with owner set per card separator.
    Each page repeats the DETIL TRANSAKSI header, so we re-enter the table
    on each occurrence.
    """
    txns: list[Transaction] = []
    current_owner = primary_owner
    current_card = primary_card

    in_detil = False
    _HEADER_LINES = {
        "Tanggal Transaksi Tanggal Pembukuan Keterangan Transaksi Jumlah (Rp)",
        "Tanggal Transaksi", "Tanggal Pembukuan", "Keterangan Transaksi", "Jumlah (Rp)",
    }

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        # Start (or re-start) of transaction section on each page
        if "DETIL TRANSAKSI" in line:
            in_detil = True
            continue

        if not in_detil:
            continue

        # Stop at reward/summary sections (but not permanently — next page may re-open)
        if line.startswith("JUMLAH POIN") or line.startswith("Sub Total"):
            in_detil = False
            continue

        # Skip header rows and opening balance
        if line in _HEADER_LINES or "TAGIHAN BULAN LALU" in line:
            continue

        # Card separator line → switch owner
        sep_m = _SEPARATOR_PATTERN.match(line)
        if sep_m:
            current_card = sep_m.group(1)
            name_on_card = sep_m.group(2).strip()
            current_owner = _detect_owner(name_on_card, owner_mappings, primary_owner)
            continue

        # FX annotation line (follows the transaction it belongs to)
        fx_m = _FX_PATTERN.search(line)
        if fx_m and txns:
            # Attach FX to the last transaction
            currency = fx_m.group(1)
            fc_amount = float(fx_m.group(2).replace(",", ""))
            rate = float(fx_m.group(3).replace(",", ""))
            last = txns[-1]
            last.currency = currency
            last.foreign_amount = fc_amount
            last.exchange_rate = rate
            continue

        # Regular transaction line
        tx_m = _TX_PATTERN.match(line)
        if not tx_m:
            continue

        tgl_tx_str = tx_m.group(1)
        tgl_bk_str = tx_m.group(2)
        description = tx_m.group(3).strip()
        amount_str = tx_m.group(4)
        is_credit = tx_m.group(5) is not None

        date_tx = _parse_ddmm_date(tgl_tx_str, cetak)
        date_posted = _parse_ddmm_date(tgl_bk_str, cetak)
        amount = _parse_amount_str(amount_str)
        if is_credit:
            amount = -amount  # credit = negative spend

        # Collect continuation lines (description wrap-around from pdfplumber)
        while i < len(lines):
            cont = lines[i].strip()
            if not cont:
                break
            # Stop on structural markers
            if ("DETIL TRANSAKSI" in cont or cont.startswith("JUMLAH POIN") or
                    cont.startswith("Sub Total") or cont in _HEADER_LINES or
                    "TAGIHAN BULAN LALU" in cont):
                break
            # Stop on a new transaction anchor (DDMM DDMM)
            if _TX_PATTERN.match(cont) or _SEPARATOR_PATTERN.match(cont):
                break
            # Stop on FX annotation (will be processed in the next iteration)
            if _FX_PATTERN.search(cont):
                break
            # Stop on pure-number lines
            if re.match(r"^[\d,]+$", cont):
                break
            description = description + " / " + cont
            i += 1

        txns.append(Transaction(
            date_transaction=date_tx or "",
            date_posted=date_posted,
            description=description,
            currency="IDR",
            foreign_amount=None,
            exchange_rate=None,
            amount_idr=float(amount),
            tx_type="Credit" if is_credit else "Debit",
            balance=None,
            account_number=current_card,
            owner=current_owner,
        ))

    return txns


def _detect_owner(name_on_card: str, owner_mappings: dict[str, str], default: str) -> str:
    """Match name on card to owner using owner_mappings."""
    name_upper = name_on_card.upper()
    for keyword, owner in owner_mappings.items():
        if keyword.upper() in name_upper:
            return owner
    return default


# ── Main parser ─────────────────────────────────────────────────────────────

def parse(
    pdf_path: str,
    owner_mappings: dict[str, str] | None = None,
    ollama_client=None,
) -> StatementResult:
    """
    Parse a Permata CC statement PDF.
    Returns StatementResult with transactions tagged by owner.
    owner_mappings: e.g. {"Emanuel": "Gandrik", "Dian Pratiwi": "Helen"}
    """
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        all_text = "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

    # Parse print date (used for year resolution)
    cetak = _parse_print_date_obj(all_text)
    if cetak is None:
        raise ValueError("Could not find Tanggal Cetak in PDF")
    print_date_str = _parse_print_date(all_text)

    # Extract primary card/owner from page 1
    first_page_text = all_text[:3000]
    primary_card, card_product = _extract_card_info(first_page_text)

    # Detect primary owner from cardholder name
    holder_m = re.search(r"(?:NOMOR KARTU.*?\n)(.*?)\n", first_page_text)
    if not holder_m:
        holder_m = re.search(r"^([A-Z][A-Z ]+ADRIANTO|[A-Z][A-Z ]+PRATIWI)", all_text, re.MULTILINE)
    primary_owner_name = holder_m.group(1).strip() if holder_m else ""
    primary_owner = _detect_owner(primary_owner_name, owner_mappings, "Unknown")
    if primary_owner == "Unknown":
        if "EMANUEL" in all_text[:500]:
            primary_owner = _detect_owner("EMANUEL", owner_mappings, "Gandrik")

    # Extract summary
    summary_data = _extract_summary(all_text)

    # Parse transactions
    lines = all_text.splitlines()
    transactions = _parse_transactions_from_lines(
        lines, cetak, primary_card, primary_owner, owner_mappings
    )

    # Sheet name: "Feb 2026 CC" based on print date
    sheet_name = cetak.strftime("%b %Y") + " CC"

    summary = AccountSummary(
        product_name=card_product,
        account_number=primary_card,
        currency="IDR",
        closing_balance=float(summary_data["total_tagihan"]),
        opening_balance=float(summary_data["tagihan_sebelumnya"]),
        total_debit=float(summary_data["pembelanjaan"]),
        total_credit=float(summary_data["pembayaran"]),
        print_date=print_date_str,
        credit_limit=float(summary_data["pagu_kredit"]),
        extra={
            "min_payment": float(summary_data.get("min_payment", 0)),
            "bunga": float(summary_data.get("bunga", 0)),
        },
    )

    return StatementResult(
        bank="Permata",
        statement_type="cc",
        owner=primary_owner,
        sheet_name=sheet_name,
        print_date=print_date_str,
        transactions=transactions,
        summary=summary,
        accounts=[summary],
    )
