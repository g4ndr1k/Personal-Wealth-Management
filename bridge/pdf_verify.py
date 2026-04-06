"""Verification helpers for parsed PDF statements."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime

import pdfplumber

from parsers.base import StatementResult

log = logging.getLogger(__name__)


def verify_statement(
    pdf_path: str,
    result: StatementResult,
    *,
    ollama_host: str,
    model: str,
    timeout_seconds: int = 120,
) -> dict:
    """Run deterministic checks plus an Ollama verification pass."""
    deterministic = run_deterministic_checks(result)
    evidence = extract_verification_evidence(pdf_path)

    payload = {
        "statement": _statement_payload(result),
        "deterministic_checks": deterministic,
        "evidence": evidence,
    }
    llm = _verify_with_ollama(
        payload,
        ollama_host=ollama_host,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    return {
        "deterministic_checks": deterministic,
        "llm": llm,
    }


def run_deterministic_checks(result: StatementResult) -> dict:
    txns = result.transactions
    accounts = result.accounts or ([result.summary] if result.summary else [])

    checks: dict[str, object] = {
        "transaction_count": len(txns),
        "account_count": len([a for a in accounts if a]),
        "date_out_of_period_count": 0,
        "missing_account_number_count": 0,
        "invalid_tx_type_count": 0,
        "missing_currency_count": 0,
        "missing_exchange_rate_count": 0,
        "nonpositive_foreign_amount_count": 0,
        "running_balance_issues": [],
        "summary_reconciliation_issues": [],
    }

    period_start = _parse_ddmmyyyy(result.period_start)
    period_end = _parse_ddmmyyyy(result.period_end)

    balances_by_account: dict[str, list] = {}
    for tx in txns:
        if not tx.account_number:
            checks["missing_account_number_count"] += 1
        if tx.tx_type not in ("Debit", "Credit"):
            checks["invalid_tx_type_count"] += 1
        if not tx.currency:
            checks["missing_currency_count"] += 1
        if tx.foreign_amount is not None:
            if not tx.exchange_rate or tx.exchange_rate <= 0:
                checks["missing_exchange_rate_count"] += 1
            if tx.foreign_amount <= 0:
                checks["nonpositive_foreign_amount_count"] += 1

        tx_date = _parse_ddmmyyyy(tx.date_transaction)
        if tx_date and period_start and period_end:
            if tx_date < period_start or tx_date > period_end:
                checks["date_out_of_period_count"] += 1

        if tx.balance is not None and tx.account_number:
            balances_by_account.setdefault(tx.account_number, []).append(tx)

    for account_number, account_txns in balances_by_account.items():
        sorted_txns = sorted(
            account_txns,
            key=lambda tx: (
                _parse_ddmmyyyy(tx.date_transaction) or datetime.min.date(),
                tx.date_posted or "",
                tx.description,
            ),
        )
        prev_balance = None
        for tx in sorted_txns:
            if prev_balance is None:
                prev_balance = tx.balance
                continue
            delta = tx.amount_idr if tx.tx_type == "Credit" else -tx.amount_idr
            expected_balance = prev_balance + delta
            if abs((tx.balance or 0.0) - expected_balance) > 1.0:
                checks["running_balance_issues"].append({
                    "account_number": account_number,
                    "date_transaction": tx.date_transaction,
                    "description": tx.description[:80],
                    "expected_balance": round(expected_balance, 2),
                    "observed_balance": round(tx.balance or 0.0, 2),
                })
                break
            prev_balance = tx.balance

    txns_by_account: dict[str, list] = {}
    for tx in txns:
        if tx.account_number:
            txns_by_account.setdefault(tx.account_number, []).append(tx)

    for account in accounts:
        if not account or not account.account_number:
            continue
        account_txns = txns_by_account.get(account.account_number, [])
        if not account_txns:
            continue
        debit_total = sum(tx.amount_idr for tx in account_txns if tx.tx_type == "Debit")
        credit_total = sum(tx.amount_idr for tx in account_txns if tx.tx_type == "Credit")
        if account.total_debit and abs(debit_total - account.total_debit) > 1.0:
            checks["summary_reconciliation_issues"].append({
                "account_number": account.account_number,
                "field": "total_debit",
                "expected": round(account.total_debit, 2),
                "observed": round(debit_total, 2),
            })
        if account.total_credit and abs(credit_total - account.total_credit) > 1.0:
            checks["summary_reconciliation_issues"].append({
                "account_number": account.account_number,
                "field": "total_credit",
                "expected": round(account.total_credit, 2),
                "observed": round(credit_total, 2),
            })

    checks["has_issues"] = any([
        checks["date_out_of_period_count"],
        checks["missing_account_number_count"],
        checks["invalid_tx_type_count"],
        checks["missing_currency_count"],
        checks["missing_exchange_rate_count"],
        checks["nonpositive_foreign_amount_count"],
        checks["running_balance_issues"],
        checks["summary_reconciliation_issues"],
    ])
    return checks


def extract_verification_evidence(pdf_path: str, max_pages: int = 2, max_chars: int = 6000) -> dict:
    snippets: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:max_pages]:
            text = (page.extract_text() or "").strip()
            if text:
                snippets.append(text[: max_chars // max(1, max_pages)])
    combined = "\n\n".join(snippets)
    return {
        "page_count_considered": min(max_pages, len(snippets)),
        "text_excerpt": combined[:max_chars],
    }


def _statement_payload(result: StatementResult) -> dict:
    accounts = []
    for account in result.accounts[:5]:
        account_dict = asdict(account)
        account_dict["extra"] = dict(account.extra or {})
        accounts.append(account_dict)

    transactions = []
    for tx in result.transactions[:200]:
        transactions.append(asdict(tx))

    bonds = [asdict(bond) for bond in result.bonds[:50]]

    return {
        "bank": result.bank,
        "statement_type": result.statement_type,
        "owner": result.owner,
        "customer_name": result.customer_name,
        "print_date": result.print_date,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "raw_errors": result.raw_errors[:20],
        "accounts": accounts,
        "transactions": transactions,
        "bonds": bonds,
    }


def _verify_with_ollama(
    payload: dict,
    *,
    ollama_host: str,
    model: str,
    timeout_seconds: int,
) -> dict:
    prompt = _build_prompt(payload)
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 300,
        },
    }).encode()

    req = urllib.request.Request(
        f"{ollama_host.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = json.loads(resp.read()).get("response", "")
        parsed = _extract_json(raw)
        parsed.setdefault("status", "warn")
        parsed.setdefault("recommended_action", "proceed_with_review")
        parsed.setdefault("summary", "No summary provided")
        parsed.setdefault("issues", [])
        return parsed
    except urllib.error.URLError as e:
        return {
            "status": "warn",
            "recommended_action": "proceed_with_review",
            "summary": f"Verifier unavailable: {e}",
            "issues": [],
        }
    except Exception as e:
        log.warning("PDF verifier failed: %s", e)
        return {
            "status": "warn",
            "recommended_action": "proceed_with_review",
            "summary": f"Verifier failed: {e}",
            "issues": [],
        }


def _build_prompt(payload: dict) -> str:
    return (
        "You are a strict verification layer for bank-statement parser output.\n"
        "Your task is NOT to re-parse the whole PDF and NOT to invent transactions.\n"
        "Only assess whether the parsed structure looks plausible given the evidence.\n\n"
        "Return ONLY valid JSON with this schema:\n"
        '{"status":"pass|warn|fail","recommended_action":"proceed|proceed_with_review|block",'
        '"summary":"short sentence","issues":[{"severity":"low|medium|high","type":"string",'
        '"message":"string","evidence":"string"}],"checks":{"dates_within_period":true,'
        '"sign_consistency":true,"running_balance_plausible":true,"summary_reconciles":true}}\n\n'
        "Rules:\n"
        "- Prefer pass when evidence is consistent.\n"
        "- Use warn for uncertainty, partial inconsistency, or missing evidence.\n"
        "- Use fail only for strong evidence of parser error.\n"
        "- Do not invent missing rows unless the evidence strongly suggests they are missing.\n"
        "- Keep issues concise and evidence-based.\n\n"
        f"Verification payload:\n{json.dumps(payload, ensure_ascii=True)}"
    )


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"No JSON found in verifier response: {raw[:200]}")
    parsed = json.loads(raw[start:end])
    if not isinstance(parsed, dict):
        raise ValueError("Verifier response was not a JSON object")
    return parsed


def _parse_ddmmyyyy(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None
