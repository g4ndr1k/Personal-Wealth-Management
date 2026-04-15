"""
Parser router: auto-detects bank and statement type from first-page text,
then dispatches to the correct parser.

Detection priority:
  1. Permata CC      — "Rekening Tagihan" + "Credit Card Billing"  (page 1 bilingual title)
  2. Permata Savings — "Permata" + "Rekening Koran"  (page 1)
  3. BCA CC          — "BCA"/"Bank Central Asia" + "KARTU KREDIT"  (page 1, case-insensitive)
  4. BCA RDN         — "REKENING TAPRES"  (page 1; BCA's securities RDN product)
  5. BCA Savings     — "BCA"/"Bank Central Asia" + "TAHAPAN"  (page 1, case-insensitive)
  6. Maybank CC      — "maybank" + "kartu kredit"  (page 1, case-insensitive)
  7. CIMB Niaga CC   — "CIMB Niaga" + "Tgl. Statement"  (page 1+2 combined; on 2-page
                       statements "CIMB Niaga" appears in the Poin Xtra footer on page 2)
  8. CIMB Niaga Consol — "CIMB Niaga" + "COMBINE STATEMENT"  (page 1)
  9. Maybank Consol  — "Maybank" + "PORTFOLIO"  (page 1+2 combined)
 10. IPOT Portfolio  — "PT INDO PREMIER SEKURITAS" + "Client Portofolio"  (page 1)
 11. IPOT Statement  — "PT INDO PREMIER SEKURITAS" + "Client Statement"  (page 1)
 12. BNI Sekuritas (legacy) — "CONSOLIDATE ACCOUNT STATEMENT" + "CASH SUMMARY"  (page 1)
 13. BNI Sekuritas   — "BNI Sekuritas" + "CLIENT STATEMENT"  (page 1, all-caps)
"""
import pdfplumber
from .base import StatementResult
from . import (
    maybank_cc, maybank_consol,
    bca_cc, bca_savings, bca_rdn,
    permata_cc, permata_savings,
    cimb_niaga_cc, cimb_niaga_consol,
    ipot_portfolio, ipot_statement,
    bni_sekuritas_legacy,
    bni_sekuritas,
    stockbit_sekuritas,
)


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

    if bca_rdn.can_parse(page1_text):
        return bca_rdn.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if bca_savings.can_parse(page1_text):
        return bca_savings.parse(pdf_path, ollama_client)

    # Maybank consolidated MUST be checked before Maybank CC:
    # the consolidated PDF lists "Maybank Kartu Kredit" as a product on page 1,
    # which would falsely trigger the CC detector. The consolidated statement has
    # "ALOKASI ASET" on page 1 and "RINGKASAN PORTOFOLIO" on page 2 — both unique.
    if maybank_consol.can_parse(combined):
        return maybank_consol.parse(pdf_path, ollama_client)

    if maybank_cc.can_parse(page1_text):
        return maybank_cc.parse(pdf_path, ollama_client)

    # CIMB Niaga must be checked before Maybank consol: the CIMB consol page 2
    # contains "ALOKASI ASET" which is also a Maybank consol detection keyword.
    # Use combined (p1+p2) for CIMB CC: on 2-page statements "CIMB Niaga" only
    # appears in the Poin Xtra footer on page 2, not on page 1.
    if cimb_niaga_cc.can_parse(combined):
        return cimb_niaga_cc.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if cimb_niaga_consol.can_parse(page1_text):
        return cimb_niaga_consol.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    # IPOT: portfolio before statement (both share "PT INDO PREMIER SEKURITAS";
    # "Client Portofolio" vs "Client Statement" are mutually exclusive)
    if ipot_portfolio.can_parse(page1_text):
        return ipot_portfolio.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if ipot_statement.can_parse(page1_text):
        return ipot_statement.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if bni_sekuritas_legacy.can_parse(page1_text):
        return bni_sekuritas_legacy.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if bni_sekuritas.can_parse(page1_text):
        return bni_sekuritas.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

    if stockbit_sekuritas.can_parse(page1_text):
        return stockbit_sekuritas.parse(pdf_path, owner_mappings=owner_mappings, ollama_client=ollama_client)

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
    if bca_rdn.can_parse(page1_text):
        return "BCA", "rdn"
    if bca_savings.can_parse(page1_text):
        return "BCA", "savings"
    if maybank_consol.can_parse(combined):
        return "Maybank", "consolidated"
    if maybank_cc.can_parse(page1_text):
        return "Maybank", "cc"
    if cimb_niaga_cc.can_parse(combined):
        return "CIMB Niaga", "cc"
    if cimb_niaga_consol.can_parse(page1_text):
        return "CIMB Niaga", "consol"

    if ipot_portfolio.can_parse(page1_text):
        return "IPOT", "portfolio"

    if ipot_statement.can_parse(page1_text):
        return "IPOT", "statement"

    if bni_sekuritas_legacy.can_parse(page1_text):
        return "BNI Sekuritas", "portfolio"

    if bni_sekuritas.can_parse(page1_text):
        return "BNI Sekuritas", "portfolio"

    if stockbit_sekuritas.can_parse(page1_text):
        return "Stockbit Sekuritas", "portfolio"

    return "Unknown", "unknown"
