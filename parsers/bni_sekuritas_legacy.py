"""
bni_sekuritas_legacy.py — Parser for old-format BNI Sekuritas
"CONSOLIDATE ACCOUNT STATEMENT" PDFs.

This parser is intentionally separate from `bni_sekuritas.py` so newer
"CLIENT STATEMENT" PDFs (e.g. Feb 2026 onward) remain untouched.

What is extracted:
  holdings     — stock positions from "Equity Instrument" and mutual-fund
                  positions from "Mutual Fund" → StatementResult.holdings
  accounts     — Cash summary closing balance → StatementResult.accounts[0]

Transactions are intentionally not parsed for this temporary legacy format.
"""
from __future__ import annotations

import calendar
import re
from typing import Optional

import pdfplumber

from .base import AccountSummary, InvestmentHolding, StatementResult, _parse_ipot_amount
from .owner import detect_owner

_RE_CLIENT = re.compile(
    r"Mr/Mrs\.\s+([A-Z][A-Z ]+[A-Z])\s+\((\S+)\)",
    re.MULTILINE,
)
_RE_PERIOD = re.compile(r"Period\s*:\s*([A-Z]+)\s+(\d{4})", re.IGNORECASE)
_RE_TOTAL_ASSET = re.compile(r"Total Asset\s*:\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_RE_CASH_REGULER = re.compile(
    r"Reguler\s+\(Acc\.ID\s*:\s*([^)]+)\)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)",
    re.IGNORECASE,
)
_RE_CASH_TOTAL = re.compile(
    r"Total\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)",
    re.IGNORECASE,
)
_RE_STOCK_ROW = re.compile(
    r"^(\d+)\s+([A-Z0-9]+)\s+([\d,]+)\s+([\d,]+\.\d+)\s+([\d,]+)\s+"
    r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+\(([\d,]+)\)$"
)
_RE_FUND_ROW = re.compile(
    r"^(\d+)\s+(.+?)\s+([\d,]+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+"
    r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$"
)

_MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}


def can_parse(text: str) -> bool:
    return (
        "CONSOLIDATE ACCOUNT STATEMENT" in text
        and "CASH SUMMARY" in text
        and "PORTFOLIO STATEMENT" in text
        and "BNI Sekuritas" in text
    )


def parse(
    pdf_path: str,
    owner_mappings: dict | None = None,
    ollama_client=None,
) -> StatementResult:
    del ollama_client  # deterministic only for this legacy format
    if owner_mappings is None:
        owner_mappings = {}

    with pdfplumber.open(pdf_path) as pdf:
        pages_text = [p.extract_text() or "" for p in pdf.pages]
    full_text = "\n".join(pages_text)

    errors: list[str] = []
    customer_name, client_code = _parse_client(full_text, errors)
    owner = detect_owner(customer_name, owner_mappings)
    period_start, period_end = _parse_period(full_text, errors)
    total_asset = _parse_total_asset(full_text)
    account_number, cash_balance = _parse_cash_summary(full_text, errors)
    holdings = _parse_equity_section(full_text, errors)
    holdings.extend(_parse_mutual_fund_section(full_text, errors))

    holdings_total = sum(h.market_value_idr for h in holdings)
    if total_asset is not None and cash_balance is not None:
        combined = holdings_total + cash_balance
        if abs(combined - total_asset) > 5:
            errors.append(
                f"BNI Sekuritas legacy: holdings + cash mismatch total asset "
                f"({combined:.2f} vs {total_asset:.2f})"
            )

    rdn_summary = AccountSummary(
        product_name="BNI Sekuritas RDN",
        account_number=client_code or account_number or "BNIS",
        currency="IDR",
        closing_balance=cash_balance or 0.0,
        print_date=None,
        period_start=period_start,
        period_end=period_end,
    )

    return StatementResult(
        bank="BNI Sekuritas",
        statement_type="portfolio",
        owner=owner,
        customer_name=customer_name,
        print_date=None,
        period_start=period_start or "",
        period_end=period_end or "",
        transactions=[],
        summary=rdn_summary,
        accounts=[rdn_summary],
        holdings=holdings,
        raw_errors=errors,
    )


def _parse_client(text: str, errors: list[str]) -> tuple[str, str]:
    m = _RE_CLIENT.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    errors.append("BNI Sekuritas legacy: could not detect client name/code")
    return "", ""


def _parse_period(text: str, errors: list[str]) -> tuple[Optional[str], Optional[str]]:
    m = _RE_PERIOD.search(text)
    if not m:
        errors.append("BNI Sekuritas legacy: could not detect period")
        return None, None

    month_num = _MONTHS.get(m.group(1).upper())
    year = int(m.group(2))
    if not month_num:
        errors.append("BNI Sekuritas legacy: unknown month in period")
        return None, None
    last_day = calendar.monthrange(year, month_num)[1]
    return f"01/{month_num:02d}/{year}", f"{last_day:02d}/{month_num:02d}/{year}"


def _parse_total_asset(text: str) -> Optional[float]:
    m = _RE_TOTAL_ASSET.search(text)
    return _parse_ipot_amount(m.group(1)) if m else None


def _parse_cash_summary(text: str, errors: list[str]) -> tuple[str, Optional[float]]:
    start = text.find("CASH SUMMARY")
    end = text.find("PORTFOLIO STATEMENT", start + 1)
    if start == -1 or end == -1:
        errors.append("BNI Sekuritas legacy: cash summary section not found")
        return "", None
    section = text[start:end]

    m = _RE_CASH_REGULER.search(section)
    if m:
        return m.group(1).strip(), _parse_ipot_amount(m.group(4))

    m = _RE_CASH_TOTAL.search(section)
    if m:
        return "", _parse_ipot_amount(m.group(3))

    errors.append("BNI Sekuritas legacy: could not detect cash closing balance")
    return "", None


def _parse_equity_section(text: str, errors: list[str]) -> list[InvestmentHolding]:
    start = text.find("Equity Instrument")
    end = text.find("Mutual Fund", start + 1)
    if start == -1:
        return []
    section = text[start:end if end != -1 else len(text)]
    lines = [line.rstrip() for line in section.splitlines()]

    holdings: list[InvestmentHolding] = []
    for idx, line in enumerate(lines):
        m = _RE_STOCK_ROW.match(line.strip())
        if not m:
            continue
        name = ""
        if idx + 1 < len(lines):
            continuation = lines[idx + 1].strip()
            if continuation and not continuation.lower().startswith("total"):
                name = continuation.rsplit(None, 1)[0].strip()

        holdings.append(InvestmentHolding(
            asset_name=name or m.group(2).strip(),
            isin_or_code=m.group(2).strip(),
            asset_class="stock",
            quantity=_parse_ipot_amount(m.group(3)) or 0.0,
            unit_price=_parse_ipot_amount(m.group(5)) or 0.0,
            market_value_idr=_parse_ipot_amount(m.group(7)) or 0.0,
            cost_basis_idr=_parse_ipot_amount(m.group(6)) or 0.0,
            unrealised_pnl_idr=-(_parse_ipot_amount(m.group(9)) or 0.0),
        ))

    if not holdings:
        errors.append("BNI Sekuritas legacy: equity section found but no rows matched")
    return holdings


def _parse_mutual_fund_section(text: str, errors: list[str]) -> list[InvestmentHolding]:
    start = text.find("Mutual Fund")
    if start == -1:
        return []
    section = text[start:]
    lines = [line.rstrip() for line in section.splitlines()]

    holdings: list[InvestmentHolding] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _RE_FUND_ROW.match(line)
        if not m:
            i += 1
            continue

        name = m.group(2).strip()
        if i + 1 < len(lines):
            continuation = lines[i + 1].strip()
            if continuation and not continuation.lower().startswith("total"):
                blocked_match = re.match(r"^(.*?)(\d+(?:,\d+)*)$", continuation)
                if blocked_match:
                    name = f"{name} {blocked_match.group(1).strip()}".strip()
                    i += 1

        holdings.append(InvestmentHolding(
            asset_name=name,
            isin_or_code="",
            asset_class="mutual_fund",
            quantity=_parse_ipot_amount(m.group(3)) or 0.0,
            unit_price=_parse_ipot_amount(m.group(5)) or 0.0,
            market_value_idr=_parse_ipot_amount(m.group(7)) or 0.0,
            cost_basis_idr=_parse_ipot_amount(m.group(6)) or 0.0,
            unrealised_pnl_idr=_parse_ipot_amount(m.group(9)) or 0.0,
        ))
        i += 1

    if not holdings:
        errors.append("BNI Sekuritas legacy: mutual fund section found but no rows matched")
    return holdings
