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
    for tx in result.transactions[:120]:
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
    try:
        raw = _ollama_generate(
            ollama_host=ollama_host,
            model=model,
            system=_build_system_prompt(),
            prompt=_build_prompt(payload),
            timeout_seconds=timeout_seconds,
            response_format=_response_schema(),
        )
        try:
            parsed = _parse_ollama_json(raw)
        except ValueError:
            repaired = _ollama_generate(
                ollama_host=ollama_host,
                model=model,
                system=(
                    "You convert an assistant response into valid JSON that matches the "
                    "provided schema. Return JSON only."
                ),
                prompt=_build_repair_prompt(raw),
                timeout_seconds=timeout_seconds,
                response_format=_response_schema(),
            )
            parsed = _parse_ollama_json(repaired)
        return _normalize_verifier_response(
            parsed,
            payload["deterministic_checks"],
            payload["statement"],
        )
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


def _ollama_generate(
    *,
    ollama_host: str,
    model: str,
    system: str,
    prompt: str,
    timeout_seconds: int,
    response_format: dict,
) -> str:
    body = json.dumps({
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "format": response_format,
        "think": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 320,
        },
    }).encode()
    req = urllib.request.Request(
        f"{ollama_host.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read()).get("response", "")


def _build_system_prompt() -> str:
    return (
        "You are a strict bank-statement parser verifier. "
        "Return exactly one JSON object that matches the requested schema. "
        "Do not include markdown, explanations, code fences, or extra text."
    )


def _build_prompt(payload: dict) -> str:
    return (
        "Assess whether the parsed bank-statement structure is plausible given the evidence.\n"
        "Do not re-parse the whole PDF and do not invent transactions.\n"
        "Return only the JSON object.\n\n"
        "Rules:\n"
        "- Prefer pass when evidence is consistent.\n"
        "- Use warn for uncertainty, partial inconsistency, or missing evidence.\n"
        "- Use fail only for strong evidence of parser error.\n"
        "- Do not invent missing rows unless the evidence strongly suggests they are missing.\n"
        "- Keep issues concise and evidence-based.\n"
        "- Summary must be under 160 characters.\n"
        "- Return at most 2 issues.\n"
        "- Each issue message and evidence must be under 120 characters.\n"
        "- If evidence is limited, use warn instead of fail.\n\n"
        "- If deterministic checks show only isolated or mild issues, prefer warn over fail.\n"
        "- Do not claim missing transactions, duplicate dates, or chronology problems unless directly supported by the payload evidence.\n"
        "- Do not mention dates, accounts, or facts that are not present in the payload.\n\n"
        f"Verification payload:\n{json.dumps(payload, ensure_ascii=True)}"
    )


def _build_repair_prompt(raw: str) -> str:
    return (
        "Convert the following response into a single JSON object matching the schema. "
        "Preserve the meaning, do not add new claims, and return JSON only.\n\n"
        f"Response to convert:\n{raw}"
    )


def _response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pass", "warn", "fail"],
            },
            "recommended_action": {
                "type": "string",
                "enum": ["proceed", "proceed_with_review", "block"],
            },
            "summary": {"type": "string", "maxLength": 160},
            "issues": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                        "type": {"type": "string"},
                        "message": {"type": "string", "maxLength": 120},
                        "evidence": {"type": "string", "maxLength": 120},
                    },
                    "required": ["severity", "type", "message", "evidence"],
                    "additionalProperties": False,
                },
            },
            "checks": {
                "type": "object",
                "properties": {
                    "dates_within_period": {"type": "boolean"},
                    "sign_consistency": {"type": "boolean"},
                    "running_balance_plausible": {"type": "boolean"},
                    "summary_reconciles": {"type": "boolean"},
                },
                "required": [
                    "dates_within_period",
                    "sign_consistency",
                    "running_balance_plausible",
                    "summary_reconciles",
                ],
                "additionalProperties": False,
            },
        },
        "required": [
            "status",
            "recommended_action",
            "summary",
            "issues",
            "checks",
        ],
        "additionalProperties": False,
    }


def _normalize_verifier_response(parsed: dict, deterministic: dict, statement: dict) -> dict:
    parsed.setdefault("status", "warn")
    parsed.setdefault("recommended_action", "proceed_with_review")
    parsed.setdefault("summary", "No summary provided")
    parsed.setdefault("issues", [])
    parsed.setdefault("checks", {})
    issues = [
        {
            "severity": str(issue.get("severity", "low")),
            "type": str(issue.get("type", "unknown")),
            "message": str(issue.get("message", ""))[:120],
            "evidence": str(issue.get("evidence", ""))[:120],
        }
        for issue in parsed["issues"][:2]
        if isinstance(issue, dict)
    ]
    parsed["issues"] = _filter_supported_issues(issues, deterministic, statement)
    if parsed["status"] == "fail" and not _has_strong_deterministic_signal(deterministic):
        parsed["status"] = "warn"
        if parsed.get("recommended_action") == "block":
            parsed["recommended_action"] = "proceed_with_review"
    parsed["summary"] = _build_summary(parsed, deterministic)
    return parsed


def _has_strong_deterministic_signal(deterministic: dict) -> bool:
    strong_counts = [
        deterministic.get("date_out_of_period_count", 0),
        deterministic.get("missing_account_number_count", 0),
        deterministic.get("invalid_tx_type_count", 0),
        deterministic.get("missing_currency_count", 0),
        deterministic.get("missing_exchange_rate_count", 0),
        deterministic.get("nonpositive_foreign_amount_count", 0),
    ]
    if any(value and value > 0 for value in strong_counts):
        return True
    if len(deterministic.get("running_balance_issues", [])) >= 2:
        return True
    if len(deterministic.get("summary_reconciliation_issues", [])) >= 2:
        return True
    return False


def _filter_supported_issues(issues: list[dict], deterministic: dict, statement: dict) -> list[dict]:
    allowed_keywords: set[str] = set()
    allowed_type_tokens: set[str] = set()
    if deterministic.get("date_out_of_period_count", 0):
        allowed_keywords.update({"date", "period", "out_of_period"})
        allowed_type_tokens.update({"date", "period"})
    if deterministic.get("missing_account_number_count", 0):
        allowed_keywords.update({"account", "account_number", "missing_account"})
        allowed_type_tokens.update({"account", "missing_account"})
    if deterministic.get("invalid_tx_type_count", 0):
        allowed_keywords.update({"tx_type", "transaction type", "debit", "credit", "sign"})
        allowed_type_tokens.update({"tx_type", "sign", "debit", "credit"})
    if deterministic.get("missing_currency_count", 0) or deterministic.get("missing_exchange_rate_count", 0):
        allowed_keywords.update({"currency", "exchange", "fx", "foreign"})
        allowed_type_tokens.update({"currency", "exchange", "fx", "foreign"})
    if deterministic.get("nonpositive_foreign_amount_count", 0):
        allowed_keywords.update({"foreign", "amount", "fx"})
        allowed_type_tokens.update({"foreign", "fx", "amount"})
    if deterministic.get("running_balance_issues"):
        allowed_keywords.update({"balance", "running", "ledger"})
        allowed_type_tokens.update({"balance", "running"})
    if deterministic.get("summary_reconciliation_issues"):
        allowed_keywords.update({"reconcile", "reconciliation", "summary", "total", "debit", "credit", "balance"})
        allowed_type_tokens.update({"reconcile", "reconciliation", "summary", "total", "balance"})

    if not allowed_keywords:
        return []

    filtered: list[dict] = []
    for issue in issues:
        if _issue_mentions_unsupported_fact(issue, statement):
            continue
        haystack = " ".join([
            issue.get("type", ""),
            issue.get("message", ""),
            issue.get("evidence", ""),
        ]).lower()
        issue_type = issue.get("type", "").lower()
        if allowed_type_tokens and not any(token in issue_type for token in allowed_type_tokens):
            if not any(keyword in haystack for keyword in allowed_keywords):
                continue
        if any(keyword in haystack for keyword in allowed_keywords):
            filtered.append(issue)
    return filtered[:2]


def _build_summary(parsed: dict, deterministic: dict) -> str:
    parts: list[str] = []
    if deterministic.get("date_out_of_period_count", 0):
        parts.append(f"{deterministic['date_out_of_period_count']} out-of-period dates")
    if deterministic.get("missing_account_number_count", 0):
        parts.append(f"{deterministic['missing_account_number_count']} rows missing account numbers")
    if deterministic.get("invalid_tx_type_count", 0):
        parts.append(f"{deterministic['invalid_tx_type_count']} invalid tx types")
    if deterministic.get("missing_exchange_rate_count", 0):
        parts.append(f"{deterministic['missing_exchange_rate_count']} FX rows missing rates")
    if deterministic.get("nonpositive_foreign_amount_count", 0):
        parts.append(f"{deterministic['nonpositive_foreign_amount_count']} nonpositive foreign amounts")

    running_balance_count = len(deterministic.get("running_balance_issues", []))
    if running_balance_count:
        parts.append(f"{running_balance_count} running-balance mismatches")

    summary_recon_count = len(deterministic.get("summary_reconciliation_issues", []))
    if summary_recon_count:
        parts.append(f"{summary_recon_count} summary reconciliation mismatches")

    status = parsed.get("status", "warn")
    if status == "pass":
        return "No material inconsistencies detected."

    if not parts:
        if parsed.get("issues"):
            return "Model raised concerns, but deterministic checks were limited."
        return "No strong deterministic issues detected."

    prefix = "Review suggested:" if status == "warn" else "Review needed:"
    return f"{prefix} " + "; ".join(parts[:3])


def _issue_mentions_unsupported_fact(issue: dict, statement: dict) -> bool:
    text = " ".join([
        issue.get("type", ""),
        issue.get("message", ""),
        issue.get("evidence", ""),
    ])
    allowed_years = {
        value[-4:]
        for value in (
            statement.get("period_start", ""),
            statement.get("period_end", ""),
            statement.get("print_date", ""),
        )
        if isinstance(value, str) and len(value) >= 4 and value[-4:].isdigit()
    }
    for token in text.replace("/", "-").split():
        if len(token) >= 4:
            for part in token.split("-"):
                if len(part) == 4 and part.isdigit() and part not in allowed_years:
                    return True
    return False


def _parse_ollama_json(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty verifier response")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Verifier response was not a JSON object")
    return parsed


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
