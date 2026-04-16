"""
Permata RDN (Rekening Dana Nasabah) Parser
============================================
Detects and parses Permata Bank RDN / securities custodian account statements.

These are investor custodian (RDN) accounts used by brokerage clients — the
transactions represent fund movements between the bank and the securities
brokerage, NOT personal income or expenses.

All transactions from these PDFs are automatically flagged as "Ignored" during
import so they are excluded from income/expense/category calculations.

Detection: "Permata" + "Rekening Koran" + word-boundary "RDN"
  (Regular Permata savings PDFs contain PERMATATAB OPTIMA / Tabungan USD /
   Permata ME Saver as product names — none of which contain "RDN".)
"""
from __future__ import annotations
import re

from parsers.base import StatementResult


# ── Detection ──────────────────────────────────────────────────────────────────

def can_parse(text_page1: str) -> bool:
    return (
        "Permata" in text_page1
        and "Rekening Koran" in text_page1
        and bool(re.search(r'\bRDN\b', text_page1))
    )


# ── Parser ─────────────────────────────────────────────────────────────────────

def parse(
    pdf_path: str,
    owner_mappings: dict | None = None,
    ollama_client=None,
) -> StatementResult:
    """
    Parse a Permata RDN statement.

    Delegates all PDF parsing to permata_savings.parse(), then overrides
    statement_type to "rdn" so the importer can auto-flag these transactions
    as "Ignored" and exclude them from all financial calculations.
    """
    from parsers import permata_savings

    result = permata_savings.parse(pdf_path, owner_mappings, ollama_client)
    result.statement_type = "rdn"
    return result
