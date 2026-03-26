"""
Parser router: auto-detects bank and statement type from first-page text,
then dispatches to the correct parser.

Detection priority:
  1. Permata CC      — "Rekening Tagihan" + "Credit Card Billing" + "DETIL TRANSAKSI"
  2. Permata Savings — "Rekening Koran" + "Account Statement" + "Periode Laporan"
  3. BCA CC          — "REKENING KARTU KREDIT" + "TAGIHAN BARU"
  4. BCA Savings     — "REKENING TAHAPAN" + "MUTASI"
  5. Maybank CC      — "Total Tagihan" + "BALANCE OF LAST MONTH"
  6. Maybank Consol  — "RINGKASAN PORTOFOLIO NASABAH" or "DETAIL & MUTASI TRANSAKSI"
"""
import pdfplumber
from .base import StatementResult
from . import maybank_cc, maybank_consol, bca_cc, bca_savings, permata_cc, permata_savings


class UnknownStatementError(Exception):
    pass


def detect_and_parse(pdf_path: str, ollama_client=None,
                     owner_mappings: dict | None = None) -> StatementResult:
    """Open the PDF, read the first page, route to correct parser."""
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        page1_text = pdf.pages[0].extract_text() or ""
        page2_text = pdf.pages[1].extract_text() if len(pdf.pages) > 1 else ""
        combined = page1_text + "\n" + page2_text

    # Permata detection first (unique "Rekening Tagihan" / "Rekening Koran" keywords)
    if permata_cc.can_parse(page1_text):
        return permata_cc.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if permata_savings.can_parse(page1_text):
        return permata_savings.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    # BCA detection
    if bca_cc.can_parse(page1_text):
        return bca_cc.parse(pdf_path, ollama_client)

    if bca_savings.can_parse(page1_text):
        return bca_savings.parse(pdf_path, ollama_client)

    if maybank_cc.can_parse(page1_text):
        return maybank_cc.parse(pdf_path, ollama_client)

    if maybank_consol.can_parse(combined):
        return maybank_consol.parse(pdf_path, ollama_client)

    raise UnknownStatementError(
        f"Could not identify statement type from PDF: {pdf_path}\n"
        f"First-page preview: {page1_text[:300]}"
    )


def detect_bank_and_type(pdf_path: str) -> tuple[str, str]:
    """Lightweight detection — returns (bank, type) without full parsing."""
    with pdfplumber.open(pdf_path) as pdf:
        page1_text = pdf.pages[0].extract_text() or ""
        page2_text = pdf.pages[1].extract_text() if len(pdf.pages) > 1 else ""
        combined = page1_text + "\n" + page2_text

    if permata_cc.can_parse(page1_text):
        return "Permata", "cc"
    if permata_savings.can_parse(page1_text):
        return "Permata", "savings"
    if bca_cc.can_parse(page1_text):
        return "BCA", "cc"
    if bca_savings.can_parse(page1_text):
        return "BCA", "savings"
    if maybank_cc.can_parse(page1_text):
        return "Maybank", "cc"
    if maybank_consol.can_parse(combined):
        return "Maybank", "consolidated"

    return "Unknown", "unknown"
