"""
Stage 2.5 — FastAPI backend for the personal finance dashboard.

Endpoints
─────────
  GET  /api/health
  GET  /api/owners
  GET  /api/categories
  GET  /api/transactions           ?year= &month= &owner= &category= &uncategorised_only= &q= &limit= &offset=
  GET  /api/transactions/foreign   ?year= &month= &owner=
  GET  /api/summary/years
  GET  /api/summary/year/{year}
  GET  /api/summary/{year}/{month}
  GET  /api/review-queue           ?limit=
  POST  /api/alias                  {hash, alias, merchant, category, match_type, apply_to_similar}
  PATCH /api/transaction/{hash}/category  {category, notes?}
  POST  /api/sync                   (no-op — SQLite is authoritative)
  POST  /api/import                 {dry_run?, overwrite?}

All endpoints read and write SQLite directly (data/finance.db).

Start with:  python3 -m finance.server
"""
from __future__ import annotations

import logging
import os
import sqlite3
import json
import hashlib
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone
from typing import Generator, NamedTuple, Optional
import urllib.error
import urllib.request

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from finance.config import (
    load_config,
    get_finance_config,
    get_fastapi_config,
    get_ollama_finance_config,
)
from finance.db import open_db

log = logging.getLogger(__name__)


# ── Module-level singletons (initialised once at import / startup) ────────────

_cfg           = load_config()
_finance_cfg   = get_finance_config(_cfg)
_fastapi_cfg   = get_fastapi_config(_cfg)
_ollama_cfg    = get_ollama_finance_config(_cfg)

_db_path: str = _finance_cfg.sqlite_db

# Read-only mode: set FINANCE_READ_ONLY=true on the NAS replica to block writes.
_READ_ONLY: bool = os.environ.get("FINANCE_READ_ONLY", "false").lower() in ("true", "1", "yes")


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    if not _READ_ONLY:
        try:
            from finance.backup import ensure_auto_backups, start_backup_scheduler
            ensure_auto_backups(_db_path)
            start_backup_scheduler(_db_path)
        except Exception as exc:
            log.warning("Backup scheduler startup failed: %s", exc)
    try:
        yield
    finally:
        try:
            from finance.backup import stop_backup_scheduler
            stop_backup_scheduler()
        except Exception as exc:
            log.warning("Backup scheduler shutdown failed: %s", exc)


app = FastAPI(
    title="Personal Finance API",
    version="3.0.0",
    description="Personal finance dashboard — SQLite authoritative store.",
    lifespan=app_lifespan,
)

_cors_origins = _fastapi_cfg.cors_origins
if "*" in _cors_origins:
    raise RuntimeError(
        "CORS wildcard origin ('*') is not allowed when allow_credentials=True. "
        "Set explicit origins in [fastapi].cors_origins."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Key"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    # Security headers on all responses
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/app") or request.url.path == "/":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
    return response


# ── Rate limiter (in-memory sliding window) ────────────────────────────────

import time as _time
from collections import defaultdict as _defaultdict

_rate_limit_store: dict[str, list[float]] = _defaultdict(list)
_RATE_LIMIT_REQUESTS = 60
_RATE_LIMIT_WINDOW_S = 60


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        now = _time.time()
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        _rate_limit_store[key] = [
            t for t in _rate_limit_store[key]
            if now - t < _RATE_LIMIT_WINDOW_S
        ]
        if len(_rate_limit_store[key]) >= _RATE_LIMIT_REQUESTS:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
        _rate_limit_store[key].append(now)
    return await call_next(request)


# ── Auth ─────────────────────────────────────────────────────────────────────

def _resolve_api_key() -> str:
    """Resolve API key once at startup: env var → Keychain → empty string."""
    key = os.environ.get("FINANCE_API_KEY", "")
    if not key:
        try:
            from bridge.secret_manager import resolve_env_key
            key = resolve_env_key("FINANCE_API_KEY")
        except ImportError:
            pass
    return key


_EXPECTED_API_KEY = _resolve_api_key()


def require_api_key(x_api_key: str = Header(default="")):
    """Validate X-Api-Key header against FINANCE_API_KEY (constant-time compare)."""
    if not _EXPECTED_API_KEY or not hmac.compare_digest(x_api_key, _EXPECTED_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_writable():
    """Dependency: raises 403 when running in read-only mode (NAS replica)."""
    if _READ_ONLY:
        raise HTTPException(status_code=403, detail="read_only_mode")


# ── DB connection helper ──────────────────────────────────────────────────────

@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    """
    Open a SQLite connection, commit on clean exit, rollback on error.

    Usage::

        with _db() as conn:
            conn.execute(...)
    """
    conn = open_db(_db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(row) -> dict:
    """Convert a sqlite3.Row to a dict. Returns {} for non-Row inputs."""
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {}


def _fmt_idr_compact(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"Rp {value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"Rp {value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"Rp {value / 1_000:.0f}K"
    return f"Rp {round(value):,}"


def _previous_year_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return (year - 1, 12)
    return (year, month - 1)


MIN_COMPARISON_YEAR = 2026
MIN_COMPARISON_MONTH = 1


def _is_first_comparison_month(year: int, month: int) -> bool:
    return year == MIN_COMPARISON_YEAR and month == MIN_COMPARISON_MONTH


def _comparison_period_started(year: int, month: int) -> bool:
    return (year, month) > (MIN_COMPARISON_YEAR, MIN_COMPARISON_MONTH)


def _snapshot_date_allows_comparison(snapshot_date: str) -> bool:
    key = (snapshot_date or "")[:7]
    if not key or "-" not in key:
        return False
    year, month = key.split("-", 1)
    return _comparison_period_started(int(year), int(month))


def _get_monthly_summary_data(conn: sqlite3.Connection, year: int, month: int) -> dict:
    if not (1 <= month <= 12):
        raise HTTPException(400, f"month must be 1–12, got {month}")

    period = f"{year}-{month:02d}"

    cat_rows = conn.execute(
        """
        SELECT
            COALESCE(t.category, '') AS category,
            c.icon,
            c.sort_order,
            SUM(t.amount)            AS total_amount,
            COUNT(*)                 AS tx_count
        FROM transactions_resolved t
        LEFT JOIN categories c ON t.category = c.category
        WHERE strftime('%Y-%m', t.date) = ?
        GROUP BY t.category
        ORDER BY c.sort_order NULLS LAST, t.category
        """,
        (period,),
    ).fetchall()

    owner_rows = conn.execute(
        """
        SELECT
            owner,
            SUM(CASE WHEN amount > 0
                      AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                      THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN amount < 0
                      AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                      THEN amount ELSE 0 END) AS expense,
            COUNT(*) AS tx_count
        FROM transactions_resolved
        WHERE strftime('%Y-%m', date) = ?
        GROUP BY owner
        ORDER BY owner
        """,
        (period,),
    ).fetchall()

    totals = conn.execute(
        """
        SELECT
            SUM(CASE WHEN amount > 0
                      AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                      THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN amount < 0
                      AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                      THEN amount ELSE 0 END) AS expense,
            COUNT(*) AS tx_count
        FROM transactions_resolved
        WHERE strftime('%Y-%m', date) = ?
        """,
        (period,),
    ).fetchone()

    needs_review = conn.execute(
        """
        SELECT COUNT(*) FROM transactions_resolved
        WHERE strftime('%Y-%m', date) = ?
          AND (category IS NULL OR category = '')
        """,
        (period,),
    ).fetchone()[0]

    total_income = totals["income"] or 0.0
    total_expense = totals["expense"] or 0.0

    transfer_cats = {"Transfer", "Adjustment", "Ignored", "Opening Balance"}
    by_category = []
    for r in cat_rows:
        amt = r["total_amount"] or 0.0
        cat_name = r["category"] or "Uncategorised"
        if cat_name in transfer_cats:
            continue  # exclude internal transfers from category breakdown
        pct = (
            round(abs(amt) / abs(total_expense) * 100, 1)
            if total_expense and amt < 0
            else 0.0
        )
        by_category.append({
            "category": cat_name,
            "icon": r["icon"] or "",
            "sort_order": r["sort_order"] or 99,
            "amount": round(amt, 2),
            "count": r["tx_count"],
            "pct_of_expense": pct,
        })

    by_owner = []
    for r in owner_rows:
        inc = r["income"] or 0.0
        exp = r["expense"] or 0.0
        by_owner.append({
            "owner": r["owner"],
            "income": round(inc, 2),
            "expense": round(exp, 2),
            "net": round(inc + exp, 2),
            "transaction_count": r["tx_count"],
        })

    return {
        "year": year,
        "month": month,
        "period": period,
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "net": round(total_income + total_expense, 2),
        "transaction_count": totals["tx_count"] or 0,
        "needs_review": needs_review,
        "by_category": by_category,
        "by_owner": by_owner,
    }


def _monthly_flow_delta_rows(curr: dict, prev: dict) -> list[dict]:
    rows = [
        {
            "label": "Income",
            "curr": curr["total_income"],
            "prev": prev["total_income"],
            "delta": curr["total_income"] - prev["total_income"],
        },
        {
            "label": "Expense",
            "curr": abs(curr["total_expense"]),
            "prev": abs(prev["total_expense"]),
            "delta": abs(curr["total_expense"]) - abs(prev["total_expense"]),
        },
        {
            "label": "Net",
            "curr": curr["net"],
            "prev": prev["net"],
            "delta": curr["net"] - prev["net"],
        },
    ]
    for row in rows:
        row["pct"] = round((row["delta"] / abs(row["prev"])) * 100) if row["prev"] else None
    return rows


def _monthly_flow_category_deltas(curr: dict, prev: dict) -> list[dict]:
    excluded = {"Transfer", "Adjustment", "Ignored", "Opening Balance", "Uncategorised"}
    curr_map = {
        row["category"]: abs(row["amount"])
        for row in curr["by_category"]
        if row["amount"] < 0 and row["category"] not in excluded
    }
    prev_map = {
        row["category"]: abs(row["amount"])
        for row in prev["by_category"]
        if row["amount"] < 0 and row["category"] not in excluded
    }
    out = []
    for category in sorted(set(curr_map) | set(prev_map)):
        curr_amount = curr_map.get(category, 0.0)
        prev_amount = prev_map.get(category, 0.0)
        delta = curr_amount - prev_amount
        if abs(delta) < 0.005:
            continue
        pct = round((delta / prev_amount) * 100) if prev_amount else None
        out.append({
            "label": category,
            "curr": round(curr_amount, 2),
            "prev": round(prev_amount, 2),
            "delta": round(delta, 2),
            "pct": pct,
        })
    return sorted(out, key=lambda row: abs(row["delta"]), reverse=True)


def _monthly_flow_explanation_fallback(curr: dict, prev: dict) -> dict:
    net_change = curr["net"] - prev["net"]
    income_change = curr["total_income"] - prev["total_income"]
    expense_change = abs(curr["total_expense"]) - abs(prev["total_expense"])
    category_deltas = _monthly_flow_category_deltas(curr, prev)

    top_spend_up = next((row for row in category_deltas if row["delta"] > 0), None)
    top_spend_down = next((row for row in category_deltas if row["delta"] < 0), None)
    current_spending = sorted(
        [row for row in curr["by_category"] if row["amount"] < 0 and row["category"] not in {"Transfer", "Adjustment", "Ignored", "Opening Balance"}],
        key=lambda row: abs(row["amount"]),
        reverse=True,
    )

    net_direction = "improved" if net_change >= 0 else "weakened"
    income_direction = "rose" if income_change >= 0 else "fell"
    expense_direction = "rose" if expense_change >= 0 else "fell"

    summary_parts = [
        f"Net flow {net_direction} by {_fmt_idr_compact(abs(net_change))} in {curr['period']} versus {prev['period']}.",
        f"Income {income_direction} by {_fmt_idr_compact(abs(income_change))} and spending {expense_direction} by {_fmt_idr_compact(abs(expense_change))}.",
    ]
    if top_spend_up:
        summary_parts.append(
            f"The biggest spending increase was {top_spend_up['label']} at {_fmt_idr_compact(top_spend_up['delta'])} higher month over month."
        )
    elif top_spend_down:
        summary_parts.append(
            f"The biggest spending relief was {top_spend_down['label']} at {_fmt_idr_compact(abs(top_spend_down['delta']))} lower month over month."
        )

    drivers = [
        f"Income moved from {_fmt_idr_compact(prev['total_income'])} to {_fmt_idr_compact(curr['total_income'])}.",
        f"Expense moved from {_fmt_idr_compact(abs(prev['total_expense']))} to {_fmt_idr_compact(abs(curr['total_expense']))}.",
    ]
    if top_spend_up:
        pct_suffix = f" ({abs(top_spend_up['pct'])}%)" if top_spend_up["pct"] is not None else ""
        drivers.append(
            f"{top_spend_up['label']} spending was up {_fmt_idr_compact(top_spend_up['delta'])}{pct_suffix}."
        )
    if top_spend_down:
        pct_suffix = f" ({abs(top_spend_down['pct'])}%)" if top_spend_down["pct"] is not None else ""
        drivers.append(
            f"{top_spend_down['label']} spending was down {_fmt_idr_compact(abs(top_spend_down['delta']))}{pct_suffix}."
        )
    if current_spending:
        top_labels = ", ".join(
            f"{row['category']} {_fmt_idr_compact(abs(row['amount']))}"
            for row in current_spending[:2]
        )
        drivers.append(f"Top expense categories this month were {top_labels}.")

    return {
        "available": True,
        "source": "fallback",
        "model": None,
        "headline": f"Why {curr['period']} net flow changed",
        "summary": " ".join(summary_parts[:3]),
        "drivers": drivers[:4],
        "current_period": curr["period"],
        "previous_period": prev["period"],
        "net_change": round(net_change, 2),
        "income_change": round(income_change, 2),
        "expense_change": round(expense_change, 2),
        "rows": _monthly_flow_delta_rows(curr, prev),
        "category_deltas": category_deltas[:8],
    }


def _generate_monthly_flow_explanation_with_ollama(curr: dict, prev: dict, fallback: dict) -> dict:
    prompt = (
        "You are writing a monthly cash flow explanation for a personal finance dashboard.\n"
        "Use only the numbers provided.\n"
        "Return JSON only with keys: headline, summary, drivers.\n"
        "Rules:\n"
        "- headline: max 80 chars.\n"
        "- summary: 2 sentences, specific and plain English.\n"
        "- drivers: array of 3 to 4 short bullet strings.\n"
        "- Focus on income, expense, net flow, and the largest spending-category shifts.\n"
        "- Do not give advice or speculate beyond the data.\n\n"
        f"Current period: {curr['period']}\n"
        f"Previous period: {prev['period']}\n"
        f"Current income: {curr['total_income']:.2f} IDR\n"
        f"Previous income: {prev['total_income']:.2f} IDR\n"
        f"Current expense: {abs(curr['total_expense']):.2f} IDR\n"
        f"Previous expense: {abs(prev['total_expense']):.2f} IDR\n"
        f"Current net: {curr['net']:.2f} IDR\n"
        f"Previous net: {prev['net']:.2f} IDR\n"
        f"Top spending category deltas JSON: {json.dumps(fallback['category_deltas'][:6], ensure_ascii=True)}\n"
        f"Current top expense categories JSON: {json.dumps(sorted([row for row in curr['by_category'] if row['amount'] < 0], key=lambda row: abs(row['amount']), reverse=True)[:6], ensure_ascii=True)}"
    )
    from finance.ollama_utils import ollama_generate
    data = ollama_generate(
        _ollama_cfg.host, _ollama_cfg.model, prompt,
        _ollama_cfg.timeout_seconds, temperature=0.2, num_predict=280,
    )

    raw = (data.get("response") or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in Ollama response")
    parsed = json.loads(raw[start:end + 1])

    headline = str(parsed.get("headline") or fallback["headline"]).strip()[:80]
    summary = str(parsed.get("summary") or fallback["summary"]).strip()
    drivers = parsed.get("drivers")
    if not isinstance(drivers, list):
        drivers = fallback["drivers"]
    drivers = [str(item).strip() for item in drivers if str(item).strip()][:4] or fallback["drivers"]

    return {
        **fallback,
        "source": "ollama",
        "model": _ollama_cfg.model,
        "headline": headline or fallback["headline"],
        "summary": summary or fallback["summary"],
        "drivers": drivers,
    }


def _build_monthly_flow_explanation(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    use_ai: bool = False,
) -> dict:
    curr = _get_monthly_summary_data(conn, year, month)
    if not _comparison_period_started(year, month):
        return {"available": False, "reason": "no_previous_month", "period": curr["period"]}
    if curr["transaction_count"] == 0:
        return {"available": False, "reason": "no_data", "period": curr["period"]}
    prev_year, prev_month = _previous_year_month(year, month)
    prev = _get_monthly_summary_data(conn, prev_year, prev_month)
    fallback = _monthly_flow_explanation_fallback(curr, prev)
    if not use_ai:
        return fallback
    try:
        return _generate_monthly_flow_explanation_with_ollama(curr, prev, fallback)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log.info("Monthly flow explanation falling back to deterministic summary: %s", exc)
        return fallback
    except Exception as exc:
        log.warning("Unexpected monthly flow explanation error, using fallback: %s", exc)
        return fallback


def _fetch_monthly_label_amounts(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    *,
    positive: bool,
) -> list[dict]:
    comparator = ">" if positive else "<"
    rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(merchant), ''), NULLIF(TRIM(raw_description), ''), 'Unknown') AS label,
            SUM(CASE WHEN amount {comparator} 0 THEN ABS(amount) ELSE 0 END) AS total_amount
        FROM transactions_resolved
        WHERE strftime('%Y', date) = ?
          AND strftime('%m', date) = ?
          AND amount {comparator} 0
          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
        GROUP BY label
        HAVING ABS(total_amount) >= 0.5
        ORDER BY total_amount DESC, label
        """,
        (str(year), f"{month:02d}"),
    ).fetchall()
    return [{"label": row["label"], "amount": float(row["total_amount"] or 0)} for row in rows]


def _diff_monthly_amount_maps(curr_rows: list[dict], prev_rows: list[dict]) -> list[dict]:
    curr_map = {row["label"]: float(row.get("amount", 0) or 0) for row in curr_rows}
    prev_map = {row["label"]: float(row.get("amount", 0) or 0) for row in prev_rows}
    out: list[dict] = []
    for label in sorted(set(curr_map) | set(prev_map)):
        curr_amount = curr_map.get(label, 0.0)
        prev_amount = prev_map.get(label, 0.0)
        delta = curr_amount - prev_amount
        if abs(delta) < 0.5:
            continue
        out.append({
            "label": label,
            "prev": prev_amount,
            "curr": curr_amount,
            "delta": delta,
        })
    return sorted(out, key=lambda row: abs(row["delta"]), reverse=True)


def _build_monthly_flow_question_context(conn: sqlite3.Connection, year: int, month: int) -> dict:
    curr = _get_monthly_summary_data(conn, year, month)
    if not _comparison_period_started(year, month):
        return {"available": False, "reason": "no_previous_month", "period": curr["period"]}
    prev_year, prev_month = _previous_year_month(year, month)
    prev = _get_monthly_summary_data(conn, prev_year, prev_month)

    expense_item_diffs = _diff_monthly_amount_maps(
        _fetch_monthly_label_amounts(conn, year, month, positive=False),
        _fetch_monthly_label_amounts(conn, prev_year, prev_month, positive=False),
    )
    income_item_diffs = _diff_monthly_amount_maps(
        _fetch_monthly_label_amounts(conn, year, month, positive=True),
        _fetch_monthly_label_amounts(conn, prev_year, prev_month, positive=True),
    )

    category_deltas = _monthly_flow_category_deltas(curr, prev)
    expense_category_deltas = [row for row in category_deltas if row["delta"] > 0]
    spending_relief_deltas = [row for row in category_deltas if row["delta"] < 0]

    income_categories_curr = [
        {"label": row["category"], "amount": float(row["amount"] or 0)}
        for row in curr["by_category"]
        if row["amount"] > 0 and row["category"] not in {"Transfer", "Adjustment", "Ignored", "Opening Balance", "Uncategorised"}
    ]
    income_categories_prev = [
        {"label": row["category"], "amount": float(row["amount"] or 0)}
        for row in prev["by_category"]
        if row["amount"] > 0 and row["category"] not in {"Transfer", "Adjustment", "Ignored", "Opening Balance", "Uncategorised"}
    ]
    income_category_deltas = _diff_monthly_amount_maps(income_categories_curr, income_categories_prev)

    return {
        "available": True,
        "current_period": curr["period"],
        "previous_period": prev["period"],
        "summary_rows": _monthly_flow_delta_rows(curr, prev),
        "category_deltas": category_deltas,
        "expense_category_deltas": expense_category_deltas,
        "spending_relief_deltas": spending_relief_deltas,
        "income_category_deltas": income_category_deltas,
        "expense_item_diffs": expense_item_diffs,
        "income_item_diffs": income_item_diffs,
    }


def _fallback_monthly_flow_question_answer(question: str, context: dict) -> dict:
    q = (question or "").lower()
    if any(term in q for term in ("income", "salary", "bonus", "revenue", "earned")):
        candidates = context["income_item_diffs"] or context["income_category_deltas"]
        title = "Income drivers"
    elif any(term in q for term in ("shopping", "spend", "spending", "expense", "category")):
        candidates = (
            context["expense_item_diffs"]
            or context["expense_category_deltas"]
            or context["category_deltas"]
        )
        title = "Spending drivers"
    elif any(term in q for term in ("top item", "item-level", "biggest", "largest", "what changed")):
        candidates = sorted(
            context["expense_item_diffs"] + context["income_item_diffs"],
            key=lambda row: abs(row["delta"]),
            reverse=True,
        )
        title = "Top item-level changes"
    else:
        candidates = sorted(
            context["expense_item_diffs"]
            + context["income_item_diffs"]
            + context["category_deltas"],
            key=lambda row: abs(row["delta"]),
            reverse=True,
        )
        title = "Top drivers"

    top = candidates[:5]
    if not top:
        return {
            "available": True,
            "source": "fallback",
            "model": None,
            "title": title,
            "answer": "I could not find any month-over-month detail rows for that question in the selected monthly trend.",
            "bullets": [],
            "references": [],
        }

    bullets = []
    references = []
    for row in top:
        direction = "up" if row["delta"] > 0 else "down"
        bullets.append(
            f"{row['label']} was {direction} {_fmt_idr_compact(abs(row['delta']))} "
            f"from {_fmt_idr_compact(row['prev'])} to {_fmt_idr_compact(row['curr'])}."
        )
        references.append(row["label"])

    return {
        "available": True,
        "source": "fallback",
        "model": None,
        "title": title,
        "answer": (
            f"Here are the biggest month-over-month changes I found from "
            f"{context['previous_period']} to {context['current_period']} for that question."
        ),
        "bullets": bullets,
        "references": references,
    }


def _ask_monthly_flow_question_with_ollama(question: str, context: dict, history: list[dict]) -> dict:
    compact_context = {
        "current_period": context["current_period"],
        "previous_period": context["previous_period"],
        "summary_rows": context["summary_rows"],
        "top_expense_category_deltas": context["expense_category_deltas"][:10],
        "top_spending_relief_deltas": context["spending_relief_deltas"][:10],
        "top_income_category_deltas": context["income_category_deltas"][:10],
        "top_expense_item_diffs": context["expense_item_diffs"][:12],
        "top_income_item_diffs": context["income_item_diffs"][:12],
    }
    prompt = (
        "You answer follow-up questions about a user's monthly cash-flow change using only provided month-over-month data.\n"
        "Return JSON only with keys: title, answer, bullets, references.\n"
        "Rules:\n"
        "- answer: 2 to 4 sentences.\n"
        "- bullets: array of 2 to 5 short factual bullets with numbers when helpful.\n"
        "- references: array of categories or item names you relied on.\n"
        "- If the user asks what caused a rise or fall, name the biggest month-over-month drivers.\n"
        "- Do not invent transactions or reasons beyond the data.\n\n"
        f"Conversation history: {json.dumps(history[-4:], ensure_ascii=True)}\n"
        f"Question: {question}\n"
        f"Context JSON: {json.dumps(compact_context, ensure_ascii=True)}"
    )
    from finance.ollama_utils import ollama_generate
    data = ollama_generate(
        _ollama_cfg.host, _ollama_cfg.model, prompt,
        _ollama_cfg.timeout_seconds, temperature=0.2, num_predict=450,
    )

    raw = (data.get("response") or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in Ollama response")
    parsed = json.loads(raw[start:end + 1])

    bullets = parsed.get("bullets")
    references = parsed.get("references")
    return {
        "available": True,
        "source": "ollama",
        "model": _ollama_cfg.model,
        "title": str(parsed.get("title") or "AI explanation").strip()[:80],
        "answer": str(parsed.get("answer") or "").strip(),
        "bullets": [str(item).strip() for item in bullets if str(item).strip()][:5] if isinstance(bullets, list) else [],
        "references": [str(item).strip() for item in references if str(item).strip()][:8] if isinstance(references, list) else [],
    }


def _wealth_delta_rows(curr: dict, prev: dict) -> list[dict]:
    rows = [
        {
            "label": "Cash & Liquid",
            "curr": curr["savings_idr"] + curr["checking_idr"] + curr["money_market_idr"] + curr["physical_cash_idr"],
            "prev": prev["savings_idr"] + prev["checking_idr"] + prev["money_market_idr"] + prev["physical_cash_idr"],
            "is_liability": False,
        },
        {
            "label": "Investments",
            "curr": curr["bonds_idr"] + curr["stocks_idr"] + curr["mutual_funds_idr"] + curr["retirement_idr"] + curr["crypto_idr"],
            "prev": prev["bonds_idr"] + prev["stocks_idr"] + prev["mutual_funds_idr"] + prev["retirement_idr"] + prev["crypto_idr"],
            "is_liability": False,
        },
        {
            "label": "Real Estate",
            "curr": curr["real_estate_idr"],
            "prev": prev["real_estate_idr"],
            "is_liability": False,
        },
        {
            "label": "Physical Assets",
            "curr": curr["vehicles_idr"] + curr["gold_idr"] + curr["other_assets_idr"],
            "prev": prev["vehicles_idr"] + prev["gold_idr"] + prev["other_assets_idr"],
            "is_liability": False,
        },
        {
            "label": "Liabilities",
            "curr": curr["total_liabilities_idr"],
            "prev": prev["total_liabilities_idr"],
            "is_liability": True,
        },
    ]
    out = []
    for row in rows:
        delta = row["curr"] - row["prev"]
        # For liabilities, negate the delta so "debt decreased" is positive
        # (a positive contribution to net worth) and "debt increased" is negative.
        if row["is_liability"]:
            delta = -delta
        pct = round((delta / abs(row["prev"])) * 100) if row["prev"] else None
        out.append({**row, "delta": delta, "pct": pct})
    return out


def _wealth_explanation_fallback(curr: dict, prev: dict) -> dict:
    net_change = curr["net_worth_idr"] - prev["net_worth_idr"]
    rows = _wealth_delta_rows(curr, prev)
    ordered = sorted(rows, key=lambda row: abs(row["delta"]), reverse=True)
    direction = "rose" if net_change >= 0 else "fell"

    positives = [row for row in ordered if row["delta"] > 0]
    negatives = [row for row in ordered if row["delta"] < 0]

    lead_parts = []
    if positives:
        lead_parts.append(
            ", ".join(f"{row['label']} {_fmt_idr_compact(row['delta'])}" for row in positives[:2])
        )
    if negatives:
        lead_parts.append(
            "offset by " + ", ".join(f"{row['label']} {_fmt_idr_compact(abs(row['delta']))}" for row in negatives[:2])
        )

    summary = (
        f"Net worth {direction} by {_fmt_idr_compact(abs(net_change))} in "
        f"{curr['snapshot_date'][:7]} because "
        f"{'; '.join(lead_parts) if lead_parts else 'asset values moved higher overall'}."
    )

    drivers = []
    for row in ordered[:4]:
        direction = "increased" if row["delta"] > 0 else "decreased" if row["delta"] < 0 else "was flat"
        if row["delta"] == 0:
            drivers.append(f"{row['label']} was flat month over month.")
            continue
        pct_suffix = f" ({abs(row['pct'])}%)" if row["pct"] is not None else ""
        drivers.append(
            f"{row['label']} {direction} by {_fmt_idr_compact(abs(row['delta']))}{pct_suffix}."
        )

    return {
        "available": True,
        "source": "fallback",
        "model": None,
        "headline": f"Why net worth changed by {_fmt_idr_compact(abs(net_change))}",
        "summary": summary,
        "drivers": drivers,
        "net_change_idr": net_change,
        "current_snapshot_date": curr["snapshot_date"],
        "previous_snapshot_date": prev["snapshot_date"],
        "rows": rows,
    }


def _generate_wealth_explanation_with_ollama(curr: dict, prev: dict, fallback: dict) -> dict:
    rows = fallback["rows"]
    prompt = (
        "You are writing a net worth change explanation for a personal finance dashboard.\n"
        "Use only the numbers provided.\n"
        "Return JSON only with keys: headline, summary, drivers.\n"
        "Rules:\n"
        "- headline: max 80 chars.\n"
        "- summary: 2 sentences, specific and plain English.\n"
        "- drivers: array of 3 to 4 short bullet strings.\n"
        "- Mention the biggest positive driver and the main offset.\n"
        "- Do not give advice or speculate beyond the numbers.\n\n"
        f"Current month: {curr['snapshot_date']}\n"
        f"Previous month: {prev['snapshot_date']}\n"
        f"Net worth change: {curr['net_worth_idr'] - prev['net_worth_idr']:.2f} IDR\n"
        f"Current net worth: {curr['net_worth_idr']:.2f} IDR\n"
        f"Previous net worth: {prev['net_worth_idr']:.2f} IDR\n"
        "Component deltas:\n"
        + "\n".join(
            f"- {row['label']}: prev={row['prev']:.2f} IDR, curr={row['curr']:.2f} IDR, delta={row['delta']:.2f} IDR"
            for row in rows
        )
    )
    from finance.ollama_utils import ollama_generate
    data = ollama_generate(
        _ollama_cfg.host, _ollama_cfg.model, prompt,
        _ollama_cfg.timeout_seconds, temperature=0.2, num_predict=300,
    )

    raw = (data.get("response") or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in Ollama response")
    parsed = json.loads(raw[start:end + 1])

    headline = str(parsed.get("headline") or fallback["headline"]).strip()[:80]
    summary = str(parsed.get("summary") or fallback["summary"]).strip()
    drivers = parsed.get("drivers")
    if not isinstance(drivers, list):
        drivers = fallback["drivers"]
    drivers = [str(item).strip() for item in drivers if str(item).strip()][:4] or fallback["drivers"]

    return {
        **fallback,
        "source": "ollama",
        "model": _ollama_cfg.model,
        "headline": headline or fallback["headline"],
        "summary": summary or fallback["summary"],
        "drivers": drivers,
    }


def _fetch_snapshot_detail_rows(
    conn: sqlite3.Connection,
    snapshot_date: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    balances = conn.execute(
        "SELECT * FROM account_balances WHERE snapshot_date=? ORDER BY institution, account, owner",
        (snapshot_date,),
    ).fetchall()
    holdings = conn.execute(
        "SELECT * FROM holdings WHERE snapshot_date=? ORDER BY asset_group, asset_class, asset_name, owner",
        (snapshot_date,),
    ).fetchall()
    liabilities = conn.execute(
        "SELECT * FROM liabilities WHERE snapshot_date=? ORDER BY liability_type, liability_name, owner",
        (snapshot_date,),
    ).fetchall()
    return ([_row(r) for r in balances], [_row(r) for r in holdings], [_row(r) for r in liabilities])


def _get_previous_month_snapshot(conn: sqlite3.Connection, snapshot_date: str):
    if not _snapshot_date_allows_comparison(snapshot_date):
        return None
    return conn.execute(
        "SELECT * FROM net_worth_snapshots "
        "WHERE snapshot_date < ? AND strftime('%Y-%m', snapshot_date) < strftime('%Y-%m', ?) "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (snapshot_date, snapshot_date),
    ).fetchone()


def _diff_named_rows(
    curr_rows: list[dict],
    prev_rows: list[dict],
    key_fields: list[str],
    value_field: str,
    label_builder,
    extra_fields: list[str],
) -> list[dict]:
    prev_map = {tuple(row.get(k, "") for k in key_fields): row for row in prev_rows}
    curr_map = {tuple(row.get(k, "") for k in key_fields): row for row in curr_rows}
    all_keys = set(prev_map) | set(curr_map)

    diffs: list[dict] = []
    for key in all_keys:
        prev = prev_map.get(key, {})
        curr = curr_map.get(key, {})
        prev_value = float(prev.get(value_field, 0) or 0)
        curr_value = float(curr.get(value_field, 0) or 0)
        delta = curr_value - prev_value
        if abs(delta) < 0.5:
            continue
        item = {
            "label": label_builder(curr or prev),
            "prev": prev_value,
            "curr": curr_value,
            "delta": delta,
        }
        for field in extra_fields:
            item[field] = (curr or prev).get(field, "")
        diffs.append(item)
    return sorted(diffs, key=lambda row: abs(row["delta"]), reverse=True)


def _build_wealth_question_context(conn: sqlite3.Connection, snapshot_date: Optional[str]) -> dict:
    if snapshot_date:
        curr = conn.execute(
            "SELECT * FROM net_worth_snapshots WHERE snapshot_date=?",
            (snapshot_date,),
        ).fetchone()
    else:
        curr = conn.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()

    if not curr:
        return {"available": False, "reason": "no_snapshot"}

    prev = _get_previous_month_snapshot(conn, curr["snapshot_date"])
    if not prev:
        return {"available": False, "reason": "no_previous_month", "snapshot_date": curr["snapshot_date"]}

    curr_dict = _row(curr)
    prev_dict = _row(prev)
    balances_curr, holdings_curr, liabilities_curr = _fetch_snapshot_detail_rows(conn, curr["snapshot_date"])
    balances_prev, holdings_prev, liabilities_prev = _fetch_snapshot_detail_rows(conn, prev["snapshot_date"])

    balance_diffs = _diff_named_rows(
        balances_curr,
        balances_prev,
        ["institution", "account", "owner"],
        "balance_idr",
        lambda row: " / ".join([part for part in [row.get("institution", ""), row.get("account", ""), row.get("owner", "")] if part]),
        ["institution", "account", "owner", "account_type", "asset_group"],
    )
    holding_diffs = _diff_named_rows(
        holdings_curr,
        holdings_prev,
        ["asset_class", "asset_name", "owner"],
        "market_value_idr",
        lambda row: " / ".join([part for part in [row.get("asset_name", ""), row.get("institution", ""), row.get("owner", "")] if part]),
        ["asset_class", "asset_group", "asset_name", "institution", "owner"],
    )
    liability_diffs = _diff_named_rows(
        liabilities_curr,
        liabilities_prev,
        ["liability_type", "liability_name", "owner"],
        "balance_idr",
        lambda row: " / ".join([part for part in [row.get("liability_name", ""), row.get("institution", ""), row.get("owner", "")] if part]),
        ["liability_type", "liability_name", "institution", "owner"],
    )

    investment_item_diffs = [row for row in holding_diffs if row.get("asset_group") == "Investments"]
    new_holdings = [row for row in holding_diffs if row["prev"] == 0 and row["curr"] > 0]
    removed_holdings = [row for row in holding_diffs if row["prev"] > 0 and row["curr"] == 0]

    return {
        "available": True,
        "current_snapshot_date": curr["snapshot_date"],
        "previous_snapshot_date": prev["snapshot_date"],
        "net_change_idr": curr_dict["net_worth_idr"] - prev_dict["net_worth_idr"],
        "summary_rows": _wealth_delta_rows(curr_dict, prev_dict),
        "balance_diffs": balance_diffs,
        "holding_diffs": holding_diffs,
        "investment_item_diffs": investment_item_diffs,
        "liability_diffs": liability_diffs,
        "new_holdings": new_holdings,
        "removed_holdings": removed_holdings,
    }


def _fallback_wealth_question_answer(question: str, context: dict) -> dict:
    q = (question or "").lower()
    if any(term in q for term in ("invest", "bond", "stock", "mutual", "retire", "holding")):
        candidates = context["investment_item_diffs"] or context["holding_diffs"]
        title = "Investment drivers"
    elif any(term in q for term in ("cash", "liquid", "bank", "account", "balance")):
        candidates = context["balance_diffs"]
        title = "Cash account drivers"
    elif any(term in q for term in ("liabil", "debt", "loan", "mortgage", "credit card")):
        candidates = context["liability_diffs"]
        title = "Liability drivers"
    elif any(term in q for term in ("new", "added", "buy", "bought")):
        candidates = context["new_holdings"]
        title = "New positions"
    else:
        candidates = (
            context["investment_item_diffs"]
            + context["balance_diffs"]
            + context["liability_diffs"]
        )
        candidates = sorted(candidates, key=lambda row: abs(row["delta"]), reverse=True)
        title = "Top drivers"

    top = candidates[:5]
    if not top:
        return {
            "available": True,
            "source": "fallback",
            "model": None,
            "title": title,
            "answer": "I could not find any item-level month-over-month changes for that question in the selected snapshot.",
            "bullets": [],
            "references": [],
        }

    bullets = []
    references = []
    for row in top:
        direction = "up" if row["delta"] > 0 else "down"
        bullets.append(
            f"{row['label']} was {direction} {_fmt_idr_compact(abs(row['delta']))} "
            f"from {_fmt_idr_compact(row['prev'])} to {_fmt_idr_compact(row['curr'])}."
        )
        references.append(row["label"])

    return {
        "available": True,
        "source": "fallback",
        "model": None,
        "title": title,
        "answer": f"Here are the biggest item-level movements I found for that question across {context['previous_snapshot_date']} to {context['current_snapshot_date']}.",
        "bullets": bullets,
        "references": references,
    }


def _ask_wealth_question_with_ollama(question: str, context: dict, history: list[dict]) -> dict:
    compact_context = {
        "current_snapshot_date": context["current_snapshot_date"],
        "previous_snapshot_date": context["previous_snapshot_date"],
        "net_change_idr": context["net_change_idr"],
        "summary_rows": context["summary_rows"],
        "top_investment_item_diffs": context["investment_item_diffs"][:12],
        "top_balance_diffs": context["balance_diffs"][:12],
        "top_liability_diffs": context["liability_diffs"][:12],
        "new_holdings": context["new_holdings"][:8],
        "removed_holdings": context["removed_holdings"][:8],
    }
    prompt = (
        "You answer follow-up questions about a user's net worth change using only provided month-over-month data.\n"
        "Return JSON only with keys: title, answer, bullets, references.\n"
        "Rules:\n"
        "- answer: 2 to 4 sentences.\n"
        "- bullets: array of 2 to 5 short factual bullets with numbers when helpful.\n"
        "- references: array of item names or sections you relied on.\n"
        "- If the user asks what caused an increase, name the largest item-level drivers.\n"
        "- Do not invent transactions, prices, or reasons beyond the data.\n\n"
        f"Conversation history: {json.dumps(history[-4:], ensure_ascii=True)}\n"
        f"Question: {question}\n"
        f"Context JSON: {json.dumps(compact_context, ensure_ascii=True)}"
    )
    from finance.ollama_utils import ollama_generate
    data = ollama_generate(
        _ollama_cfg.host, _ollama_cfg.model, prompt,
        _ollama_cfg.timeout_seconds, temperature=0.2, num_predict=450,
    )

    raw = (data.get("response") or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in Ollama response")
    parsed = json.loads(raw[start:end + 1])

    bullets = parsed.get("bullets")
    references = parsed.get("references")
    return {
        "available": True,
        "source": "ollama",
        "model": _ollama_cfg.model,
        "title": str(parsed.get("title") or "AI explanation").strip()[:80],
        "answer": str(parsed.get("answer") or "").strip(),
        "bullets": [str(item).strip() for item in bullets if str(item).strip()][:5] if isinstance(bullets, list) else [],
        "references": [str(item).strip() for item in references if str(item).strip()][:8] if isinstance(references, list) else [],
    }


def _build_wealth_explanation(conn: sqlite3.Connection, snapshot_date: Optional[str], use_ai: bool = False) -> dict:
    if snapshot_date:
        curr = conn.execute(
            "SELECT * FROM net_worth_snapshots WHERE snapshot_date=?",
            (snapshot_date,),
        ).fetchone()
    else:
        curr = conn.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()

    if not curr:
        return {"available": False, "reason": "no_snapshot"}

    prev = _get_previous_month_snapshot(conn, curr["snapshot_date"])
    if not prev:
        return {"available": False, "reason": "no_previous_month", "snapshot_date": curr["snapshot_date"]}

    curr_dict = _row(curr)
    prev_dict = _row(prev)
    fallback = _wealth_explanation_fallback(curr_dict, prev_dict)

    if not use_ai:
        return fallback

    try:
        return _generate_wealth_explanation_with_ollama(curr_dict, prev_dict, fallback)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log.info("Wealth explanation falling back to deterministic summary: %s", exc)
        return fallback
    except Exception as exc:
        log.warning("Unexpected wealth explanation error, using fallback: %s", exc)
        return fallback


# ── Request / Response models ─────────────────────────────────────────────────

def _validate_snapshot_date(v: str) -> str:
    """Shared validator: reject non-YYYY-MM-DD snapshot_date strings."""
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"snapshot_date must be YYYY-MM-DD, got: {v!r}")
    return v


class AliasRequest(BaseModel):
    hash:             str             = Field(..., max_length=64)
    alias:            str             = Field(..., max_length=500)
    merchant:         str             = Field(..., max_length=200)
    category:         str             = Field(..., max_length=100)
    match_type:       str             = Field("exact", pattern=r"^(exact|contains|regex)$")
    apply_to_similar: bool            = True


class ImportRequest(BaseModel):
    dry_run:   bool = False
    overwrite: bool = False


class CategoryOverrideRequest(BaseModel):
    category:     str  = Field(..., max_length=100)
    notes:        str  = Field("", max_length=500)
    update_alias: bool = True


class CategoryUpsertRequest(BaseModel):
    category:          str            = Field(..., max_length=100)
    original_category: Optional[str]  = Field(None, max_length=100)
    icon:              str            = Field("", max_length=10)
    sort_order:        int            = Field(99, ge=0, le=9999)
    is_recurring:      bool           = False
    monthly_budget:    Optional[float] = Field(None, ge=0)
    category_group:    str            = Field("", max_length=100)
    subcategory:       str            = Field("", max_length=100)


class WealthQuestionRequest(BaseModel):
    snapshot_date: Optional[str] = None
    question:      str           = Field(..., max_length=1000)
    history:       list[dict]    = []

    @field_validator("snapshot_date")
    @classmethod
    def validate_snap(cls, v):
        if v is not None:
            _validate_snapshot_date(v)
        return v


class MonthlyFlowQuestionRequest(BaseModel):
    question: str        = Field(..., max_length=1000)
    history:  list[dict] = []


# ── /ping  (unauthenticated liveness probe for PWA heartbeat) ────────────────

@app.get("/ping")
def ping():
    from fastapi.responses import JSONResponse
    return JSONResponse(content={"ok": True}, headers={"Cache-Control": "no-store"})


# ── /api/health ───────────────────────────────────────────────────────────────

@app.get("/api/health", dependencies=[Depends(require_api_key)])
def health():
    with _db() as conn:
        tx_count  = conn.execute("SELECT COUNT(*) FROM transactions_resolved").fetchone()[0]
        sync_row  = conn.execute(
            "SELECT synced_at, transactions_count FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        needs_rev = conn.execute(
            "SELECT COUNT(*) FROM transactions_resolved WHERE category IS NULL OR category = ''"
        ).fetchone()[0]
    return {
        "status":            "ok",
        "read_only":         _READ_ONLY,
        "transaction_count": tx_count,
        "needs_review":      needs_rev,
        "last_sync":         sync_row["synced_at"] if sync_row else None,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }


# ── /api/owners ───────────────────────────────────────────────────────────────

@app.get("/api/owners", dependencies=[Depends(require_api_key)])
def get_owners():
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT owner FROM transactions "
            "WHERE owner != '' ORDER BY owner"
        ).fetchall()
    return [r[0] for r in rows]


# ── /api/categories ───────────────────────────────────────────────────────────

@app.get("/api/categories", dependencies=[Depends(require_api_key)])
def get_categories():
    with _db() as conn:
        rows = conn.execute(
            "SELECT category, icon, sort_order, is_recurring, monthly_budget, "
            "       category_group, subcategory "
            "FROM categories ORDER BY sort_order, category"
        ).fetchall()
        if rows:
            return [_row(r) for r in rows]
        # Fallback: distinct categories actually present in transactions
        rows = conn.execute(
            "SELECT DISTINCT category FROM transactions "
            "WHERE category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()
    return [
        {"category": r[0], "icon": "", "sort_order": 99,
         "is_recurring": 0, "monthly_budget": None,
         "category_group": "", "subcategory": ""}
        for r in rows
    ]


@app.post("/api/categories", dependencies=[Depends(require_api_key), Depends(require_writable)])
def post_category(req: CategoryUpsertRequest):
    category = (req.category or "").strip()
    original_category = (req.original_category or "").strip() or None
    if not category:
        raise HTTPException(400, "Category name is required")

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with _db() as conn:
        if original_category and original_category != category:
            existing = conn.execute(
                "SELECT 1 FROM categories WHERE category = ?",
                (category,),
            ).fetchone()
            if existing:
                raise HTTPException(409, f"Category already exists: {category}")

            conn.execute(
                """
                UPDATE categories
                SET category = ?, icon = ?, sort_order = ?, is_recurring = ?, monthly_budget = ?,
                    category_group = ?, subcategory = ?, synced_at = ?
                WHERE category = ?
                """,
                (
                    category,
                    req.icon,
                    req.sort_order,
                    int(req.is_recurring),
                    req.monthly_budget,
                    req.category_group,
                    req.subcategory,
                    synced_at,
                    original_category,
                ),
            )
            conn.execute("UPDATE transactions SET category = ? WHERE category = ?", (category, original_category))
            conn.execute("UPDATE category_overrides SET category = ? WHERE category = ?", (category, original_category))
            conn.execute("UPDATE merchant_aliases SET category = ? WHERE category = ?", (category, original_category))
        else:
            conn.execute(
                """
                INSERT INTO categories (category, icon, sort_order, is_recurring, monthly_budget, category_group, subcategory, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category) DO UPDATE SET
                    icon = excluded.icon,
                    sort_order = excluded.sort_order,
                    is_recurring = excluded.is_recurring,
                    monthly_budget = excluded.monthly_budget,
                    category_group = excluded.category_group,
                    subcategory = excluded.subcategory,
                    synced_at = excluded.synced_at
                """,
                (
                    category,
                    req.icon,
                    req.sort_order,
                    int(req.is_recurring),
                    req.monthly_budget,
                    req.category_group,
                    req.subcategory,
                    synced_at,
                ),
            )

        row = conn.execute(
            "SELECT category, icon, sort_order, is_recurring, monthly_budget, category_group, subcategory FROM categories WHERE category = ?",
            (category,),
        ).fetchone()

    return _row(row)


# ── /api/transactions ─────────────────────────────────────────────────────────

@app.get("/api/transactions", dependencies=[Depends(require_api_key)])
def get_transactions(
    year:     Optional[int] = Query(None, description="Filter by calendar year"),
    month:    Optional[int] = Query(None, ge=1, le=12, description="Filter by month (1–12)"),
    owner:    Optional[str] = Query(None, description="Owner name, or omit for all"),
    category: Optional[str] = Query(None, description="Exact category match"),
    category_group: Optional[str] = Query(None, description="Category group match"),
    uncategorised_only: bool = Query(False, description="Only transactions with no resolved category"),
    income_only: bool = Query(False, description="Only transactions with amount >= 0"),
    q:        Optional[str] = Query(None, description="Search raw_description and merchant"),
    limit:    int           = Query(100, ge=1, le=1000),
    offset:   int           = Query(0, ge=0),
):
    qp = _tx_where(year, month, owner, category, category_group, uncategorised_only, q, income_only=income_only)
    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transactions_resolved{qp.clause}", qp.params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM transactions_resolved{qp.clause} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
            qp.params + [limit, offset],
        ).fetchall()
    return {
        "total":        total,
        "limit":        limit,
        "offset":       offset,
        "transactions": [_row(r) for r in rows],
    }


@app.get("/api/transactions/foreign", dependencies=[Depends(require_api_key)])
def get_foreign_transactions(
    year:  Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    owner: Optional[str] = Query(None),
):
    """Transactions that were billed in a foreign currency."""
    qp = _tx_where(year, month, owner, category=None, category_group=None, uncategorised_only=False, q=None)
    if qp.clause:
        extra_clause = qp.clause + " AND original_currency IS NOT NULL"
    else:
        extra_clause = " WHERE original_currency IS NOT NULL"
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM transactions_resolved{extra_clause} ORDER BY date DESC",
            qp.params,
        ).fetchall()
    return [_row(r) for r in rows]


class _QueryParts(NamedTuple):
    clause: str   # always "" or " WHERE ..."
    params: list


def _tx_where(
    year:     Optional[int],
    month:    Optional[int],
    owner:    Optional[str],
    category: Optional[str],
    category_group: Optional[str],
    uncategorised_only: bool,
    q:        Optional[str],
    income_only: bool = False,
) -> _QueryParts:
    """Build a WHERE clause + params list for transaction queries."""
    conditions: list[str] = []
    params:     list      = []

    if year:
        conditions.append("strftime('%Y', date) = ?")
        params.append(str(year))
    if month:
        conditions.append("strftime('%m', date) = ?")
        params.append(f"{month:02d}")
    if owner:
        conditions.append("owner = ?")
        params.append(owner)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if category_group:
        conditions.append(
            "EXISTS (SELECT 1 FROM categories c WHERE c.category = transactions_resolved.category AND c.category_group = ?)"
        )
        params.append(category_group)
    if income_only:
        conditions.append("amount >= 0 AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))")
    if uncategorised_only:
        conditions.append("(category IS NULL OR TRIM(category) = '')")
    if q:
        escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("(raw_description LIKE ? ESCAPE '\\' OR merchant LIKE ? ESCAPE '\\')")
        params += [f"%{escaped_q}%", f"%{escaped_q}%"]

    clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return _QueryParts(clause, params)


# ── /api/summary ──────────────────────────────────────────────────────────────

@app.get("/api/summary/years", dependencies=[Depends(require_api_key)])
def get_available_years():
    """Return a list of calendar years that have transaction data."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT strftime('%Y', date) AS yr "
            "FROM transactions_resolved WHERE date != '' ORDER BY yr DESC"
        ).fetchall()
    return [int(r[0]) for r in rows if r[0]]


@app.get("/api/summary/year/{year}", dependencies=[Depends(require_api_key)])
def get_annual_summary(year: int):
    """Month-by-month income / expense breakdown for a full year."""
    with _db() as conn:
        month_rows = conn.execute(
            """
            SELECT
                CAST(strftime('%m', date) AS INTEGER) AS month,
                SUM(CASE WHEN amount > 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                          THEN amount ELSE 0 END)      AS income,
                SUM(CASE WHEN amount < 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                          THEN amount ELSE 0 END)      AS expense,
                COUNT(*)                               AS tx_count
            FROM transactions_resolved
            WHERE strftime('%Y', date) = ?
            GROUP BY month
            ORDER BY month
            """,
            (str(year),),
        ).fetchall()

        totals = conn.execute(
            """
            SELECT
                SUM(CASE WHEN amount > 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                          THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment','Ignored','Opening Balance'))
                          THEN amount ELSE 0 END) AS expense,
                COUNT(*) AS tx_count
            FROM transactions_resolved
            WHERE strftime('%Y', date) = ?
            """,
            (str(year),),
        ).fetchone()

    months = []
    for r in month_rows:
        inc = r["income"]  or 0.0
        exp = r["expense"] or 0.0
        months.append({
            "month":             r["month"],
            "income":            round(inc, 2),
            "expense":           round(exp, 2),
            "net":               round(inc + exp, 2),
            "transaction_count": r["tx_count"],
        })

    inc_total = totals["income"]  or 0.0
    exp_total = totals["expense"] or 0.0
    return {
        "year":              year,
        "total_income":      round(inc_total, 2),
        "total_expense":     round(exp_total, 2),
        "net":               round(inc_total + exp_total, 2),
        "transaction_count": totals["tx_count"] or 0,
        "by_month":          months,
    }


@app.get("/api/summary/{year}/{month}", dependencies=[Depends(require_api_key)])
def get_monthly_summary(year: int, month: int):
    """
    Full breakdown for one calendar month.

    Returns totals, per-category breakdown (with % of expense), and
    per-owner split.  Also includes needs_review count for the month.
    """
    with _db() as conn:
        return _get_monthly_summary_data(conn, year, month)


@app.get("/api/summary/{year}/{month}/explanation", dependencies=[Depends(require_api_key)])
def get_monthly_flow_explanation(year: int, month: int, ai: bool = Query(False)):
    """Explain the selected month's cash-flow movement.

    Without ?ai=1 returns the deterministic fallback instantly.
    With ?ai=1 calls Ollama for an AI-generated explanation (may be slow).
    """
    with _db() as conn:
        return _build_monthly_flow_explanation(conn, year, month, use_ai=ai)


@app.post("/api/summary/{year}/{month}/explanation/query", dependencies=[Depends(require_api_key)])
def query_monthly_flow_explanation(year: int, month: int, req: MonthlyFlowQuestionRequest):
    with _db() as conn:
        context = _build_monthly_flow_question_context(conn, year, month)
    if not context.get("available"):
        return context

    fallback = _fallback_monthly_flow_question_answer(req.question, context)
    try:
        answer = _ask_monthly_flow_question_with_ollama(req.question, context, req.history)
        if not answer.get("answer"):
            return fallback
        return answer
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log.info("Monthly flow question falling back to deterministic answer: %s", exc)
        return fallback
    except Exception as exc:
        log.warning("Unexpected monthly flow question error, using fallback: %s", exc)
        return fallback


# ── /api/review-queue ─────────────────────────────────────────────────────────

@app.get("/api/review-queue", dependencies=[Depends(require_api_key)])
def get_review_queue(limit: int = Query(50, ge=1, le=200)):
    """
    Return transactions that have no category assigned (Layer 4 — needs review).

    The PWA review queue uses this to show pending transactions to the user.
    After the user confirms a merchant/category, call POST /api/alias.
    """
    with _db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM transactions_resolved WHERE category IS NULL OR category = ''"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM transactions_resolved
            WHERE category IS NULL OR category = ''
            ORDER BY date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {
        "total":   total,
        "limit":   limit,
        "pending": [_row(r) for r in rows],
    }


# ── /api/review-queue/suggest ────────────────────────────────────────────────

@app.post("/api/review-queue/suggest", dependencies=[Depends(require_api_key), Depends(require_writable)])
def suggest_review_queue():
    """
    Run the full categorizer (L1→L3) on every null-category transaction
    that doesn't already have an AI suggestion.

    L1/L2 auto-match → apply category/merchant directly.
    L3 Ollama match  → write ollama_suggestion + suggested_merchant (user must confirm).
    L4               → no change.
    """
    from finance.categorizer import Categorizer

    with _db() as conn:
        alias_rows = conn.execute("SELECT * FROM merchant_aliases").fetchall()
    aliases = [dict(r) for r in alias_rows]

    cat = Categorizer(
        aliases=aliases,
        categories=[],
        ollama_host=_ollama_cfg.host,
        ollama_model=_ollama_cfg.model,
        ollama_timeout=_ollama_cfg.timeout_seconds,
    )

    with _db() as conn:
        rows = conn.execute(
            "SELECT hash, raw_description, owner, account FROM transactions_resolved "
            "WHERE (category IS NULL OR category = '') AND ollama_suggestion IS NULL"
        ).fetchall()

    applied = 0
    suggested = 0
    with _db() as conn:
        for row in rows:
            result = cat.categorize(row["raw_description"], row["owner"] or "", row["account"] or "")
            if result.confidence == "auto":   # L1/L2
                conn.execute(
                    "UPDATE transactions SET merchant=?, category=? WHERE hash=?",
                    (result.merchant, result.category, row["hash"]),
                )
                applied += 1
            elif result.confidence == "suggested":  # L3 Ollama
                conn.execute(
                    "UPDATE transactions SET ollama_suggestion=?, suggested_merchant=? WHERE hash=?",
                    (result.category, result.merchant, row["hash"]),
                )
                suggested += 1
        conn.commit()

    return {"ok": True, "applied": applied, "suggested": suggested}


# ── /api/alias ────────────────────────────────────────────────────────────────

@app.post("/api/alias", dependencies=[Depends(require_api_key), Depends(require_writable)])
def post_alias(req: AliasRequest):
    """
    Confirm a merchant alias from the review queue.

    1. Writes the alias to SQLite merchant_aliases (authoritative)
    2. Updates the target transaction via category_overrides (override layer)
    3. If apply_to_similar=true, also writes overrides for similar uncategorised rows
    """
    from datetime import datetime, timezone as _tz
    _now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Validate regex patterns at the boundary before storing
    if req.match_type == "regex":
        import re
        try:
            re.compile(req.alias)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    updated_hashes: list[str] = []
    updated_rows: list[dict] = []

    with _db() as conn:
        # 1. Write alias to SQLite merchant_aliases (SQLite-first)
        conn.execute(
            """INSERT OR IGNORE INTO merchant_aliases
               (merchant, alias, category, match_type, added_date, synced_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (req.merchant, req.alias, req.category, req.match_type, _now, _now),
        )
        conn.execute(
            "INSERT INTO audit_log (entity, entity_id, action, field, new_value, source) VALUES ('alias', ?, 'create', 'alias', ?, 'api')",
            (req.alias, req.category),
        )
        log.info("Alias saved to SQLite: %s → %s  [%s]", req.alias, req.merchant, req.category)

        # 2. Write category override for the target transaction
        conn.execute(
            """INSERT INTO category_overrides (hash, category, merchant, notes, updated_at, updated_by)
               VALUES (?, ?, ?, '', ?, 'user')
               ON CONFLICT(hash) DO UPDATE SET category=excluded.category,
               merchant=excluded.merchant, updated_at=excluded.updated_at""",
            (req.hash, req.category, req.merchant, _now),
        )
        # Also update transactions table directly for immediate consistency
        conn.execute(
            "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
            (req.merchant, req.category, req.hash),
        )
        conn.execute(
            "INSERT INTO audit_log (entity, entity_id, action, field, new_value, source) VALUES ('override', ?, 'create', 'category', ?, 'api')",
            (req.hash, req.category),
        )
        updated_hashes.append(req.hash)

        # 3. Apply to similar uncategorised transactions
        if req.apply_to_similar:
            if req.match_type == "exact":
                target = conn.execute(
                    "SELECT raw_description FROM transactions_resolved WHERE hash = ?",
                    (req.hash,),
                ).fetchone()
                if target:
                    similar = conn.execute(
                        """
                        SELECT hash FROM transactions_resolved
                        WHERE raw_description = ?
                          AND hash != ?
                          AND (category IS NULL OR category = '')
                        """,
                        (target["raw_description"], req.hash),
                    ).fetchall()
                    if similar:
                        similar_hashes = [r["hash"] for r in similar]
                        conn.executemany(
                            """INSERT INTO category_overrides (hash, category, merchant, notes, updated_at, updated_by)
                               VALUES (?, ?, ?, '', ?, 'alias_backfill')
                               ON CONFLICT(hash) DO UPDATE SET category=excluded.category,
                               merchant=excluded.merchant, updated_at=excluded.updated_at""",
                            [(h, req.category, req.merchant, _now) for h in similar_hashes],
                        )
                        conn.executemany(
                            "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
                            [(req.merchant, req.category, h) for h in similar_hashes],
                        )
                        updated_hashes.extend(similar_hashes)
                        log.info("Applied alias to %d similar uncategorised transactions.", len(similar_hashes))
            elif req.match_type == "contains":
                pattern = req.alias.strip().upper()
                similar = conn.execute(
                    """
                    SELECT hash, raw_description FROM transactions_resolved
                    WHERE UPPER(raw_description) LIKE ?
                      AND hash != ?
                      AND (category IS NULL OR category = '')
                    """,
                    (f"%{pattern}%", req.hash),
                ).fetchall()
                if similar:
                    similar_hashes = [r["hash"] for r in similar]
                    conn.executemany(
                        """INSERT INTO category_overrides (hash, category, merchant, notes, updated_at, updated_by)
                           VALUES (?, ?, ?, '', ?, 'alias_backfill')
                           ON CONFLICT(hash) DO UPDATE SET category=excluded.category,
                           merchant=excluded.merchant, updated_at=excluded.updated_at""",
                        [(h, req.category, req.merchant, _now) for h in similar_hashes],
                    )
                    conn.executemany(
                        "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
                        [(req.merchant, req.category, h) for h in similar_hashes],
                    )
                    updated_hashes.extend(similar_hashes)
                    log.info("Applied contains alias to %d similar uncategorised transactions.", len(similar_hashes))
            elif req.match_type == "regex":
                import re
                try:
                    pat = re.compile(req.alias, re.IGNORECASE)
                    uncategorised = conn.execute(
                        """
                        SELECT hash, raw_description FROM transactions_resolved
                        WHERE hash != ?
                          AND (category IS NULL OR category = '')
                        """,
                        (req.hash,),
                    ).fetchall()
                    similar_hashes = [r["hash"] for r in uncategorised if pat.search(r["raw_description"])]
                    if similar_hashes:
                        conn.executemany(
                            """INSERT INTO category_overrides (hash, category, merchant, notes, updated_at, updated_by)
                               VALUES (?, ?, ?, '', ?, 'alias_backfill')
                               ON CONFLICT(hash) DO UPDATE SET category=excluded.category,
                               merchant=excluded.merchant, updated_at=excluded.updated_at""",
                            [(h, req.category, req.merchant, _now) for h in similar_hashes],
                        )
                        conn.executemany(
                            "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
                            [(req.merchant, req.category, h) for h in similar_hashes],
                        )
                        updated_hashes.extend(similar_hashes)
                        log.info("Applied regex alias to %d similar uncategorised transactions.", len(similar_hashes))
                except re.error:
                    log.warning("Invalid regex in alias backfill: %s", req.alias)

        if updated_hashes:
            placeholders = ",".join("?" * len(updated_hashes))
            updated_rows = [
                dict(r) for r in conn.execute(
                    f"SELECT * FROM transactions WHERE hash IN ({placeholders})",
                    updated_hashes,
                ).fetchall()
            ]

        updated = conn.execute(
            "SELECT * FROM transactions_resolved WHERE hash = ?", (req.hash,)
        ).fetchone()

    return {
        "ok":            True,
        "updated_count": len(updated_hashes),
        "transaction":   _row(updated) if updated else None,
    }


# ── /api/backfill-aliases ──────────────────────────────────────────────────

@app.post("/api/backfill-aliases", dependencies=[Depends(require_api_key), Depends(require_writable)])
def backfill_aliases():
    """
    Re-apply all merchant aliases against uncategorised transactions in SQLite.
    This fixes rows that were imported before an alias was created.

    Returns the number of transactions updated.
    """
    import re as _re
    from finance.categorizer import alias_text_tokens, alias_tokens_match, normalize_alias_key

    # 1. Load aliases from SQLite (authoritative since Phase 2.5)
    with _db() as conn:
        alias_rows = conn.execute("SELECT * FROM merchant_aliases").fetchall()
    aliases = [dict(r) for r in alias_rows]
    if not aliases:
        return {"ok": True, "updated_count": 0, "detail": "no aliases found"}

    # Build lookup structures (same logic as Categorizer._load_aliases)
    exact: dict[str, list[tuple]] = {}
    contains: list[tuple] = []
    regexes: list[tuple] = []

    for row in aliases:
        alias_s = str(row.get("alias", "")).strip()
        merchant = str(row.get("merchant", "")).strip()
        category = str(row.get("category", "")).strip()
        mtype = str(row.get("match_type", "exact")).strip().lower()
        if not alias_s or not merchant:
            continue
        if mtype == "regex":
            try:
                regexes.append((_re.compile(alias_s, _re.IGNORECASE), merchant, category))
            except _re.error:
                pass
        elif mtype == "contains":
            contains.append((alias_text_tokens(alias_s), merchant, category))
        else:
            exact.setdefault(normalize_alias_key(alias_s), []).append((merchant, category))

    # 2. Scan uncategorised transactions and apply matches
    updated = 0
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT hash, raw_description FROM transactions_resolved
            WHERE category IS NULL OR category = ''
            """
        ).fetchall()

        for r in rows:
            desc = r["raw_description"].strip()
            desc_tokens = alias_text_tokens(desc)
            key = normalize_alias_key(desc)
            new_merchant = None
            new_category = None

            # Layer 1: exact
            if key in exact:
                new_merchant, new_category = exact[key][0]

            # Layer 1b: contains
            if not new_merchant:
                for substr_tokens, m, c in contains:
                    if alias_tokens_match(substr_tokens, desc_tokens):
                        new_merchant, new_category = m, c
                        break

            # Layer 2: regex
            if not new_merchant:
                for pat, m, c in regexes:
                    if pat.search(desc):
                        new_merchant, new_category = m, c
                        break

            if new_merchant and new_category:
                conn.execute(
                    "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
                    (new_merchant, new_category, r["hash"]),
                )
                updated += 1

    log.info("Backfill aliases: updated %d transactions.", updated)
    return {"ok": True, "updated_count": updated}


# ── /api/transaction/{hash}/category ──────────────────────────────────────────

@app.patch("/api/transaction/{tx_hash}/category", dependencies=[Depends(require_api_key), Depends(require_writable)])
def patch_transaction_category(tx_hash: str, req: CategoryOverrideRequest):
    """
    Manually override the category for a specific transaction.

    1. Writes to SQLite category_overrides (survives re-imports)
    2. Returns the updated transaction row via transactions_resolved view
    """
    from datetime import datetime, timezone as _tz
    _now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Validate category and fetch transaction
    with _db() as conn:
        if req.category != "Ignored":
            cat_row = conn.execute(
                "SELECT category FROM categories WHERE category = ?", (req.category,)
            ).fetchone()
            if not cat_row:
                raise HTTPException(400, f"Unknown category: {req.category!r}")

        tx = conn.execute(
            "SELECT * FROM transactions WHERE hash = ?", (tx_hash,)
        ).fetchone()
        if not tx:
            raise HTTPException(404, f"Transaction not found: {tx_hash}")

    also_updated = 0
    with _db() as conn:
        # 1. Write to SQLite category_overrides (SQLite-first, authoritative)
        conn.execute(
            """INSERT INTO category_overrides (hash, category, notes, updated_at, updated_by)
               VALUES (?, ?, ?, ?, 'user')
               ON CONFLICT(hash) DO UPDATE SET category=excluded.category,
               notes=excluded.notes, updated_at=excluded.updated_at""",
            (tx_hash, req.category, req.notes or "", _now),
        )
        conn.execute(
            "INSERT INTO audit_log (entity, entity_id, action, field, new_value, source) VALUES ('override', ?, 'update', 'category', ?, 'api')",
            (tx_hash, req.category),
        )
        # 2. Also update transactions table for direct consistency
        conn.execute("UPDATE transactions SET category = ? WHERE hash = ?", (req.category, tx_hash))
        if req.notes:
            conn.execute("UPDATE transactions SET notes = ? WHERE hash = ?", (req.notes, tx_hash))

        raw_desc = tx["raw_description"]
        merchant = tx["merchant"]

        if req.update_alias and raw_desc:
            existing = conn.execute(
                "SELECT category FROM merchant_aliases WHERE alias = ?", (raw_desc,)
            ).fetchone()
            if existing and existing["category"] != req.category:
                conn.execute(
                    "UPDATE merchant_aliases SET category = ? WHERE alias = ?",
                    (req.category, raw_desc),
                )
                log.info("Updated alias: %s → %s", raw_desc[:40], req.category)
            elif not existing:
                alias_merchant = merchant or raw_desc
                conn.execute(
                    "INSERT OR IGNORE INTO merchant_aliases (merchant, alias, category, match_type, synced_at) VALUES (?, ?, ?, ?, ?)",
                    (alias_merchant, raw_desc, req.category, "exact", _now),
                )
                log.info("New alias: %s → %s [%s]", raw_desc[:40], alias_merchant, req.category)

            # Apply override to all similar transactions with the same raw_description
            similar = conn.execute(
                """
                SELECT hash FROM transactions_resolved
                WHERE raw_description = ?
                  AND hash != ?
                  AND (category IS NULL OR category = '' OR category != ?)
                """,
                (raw_desc, tx_hash, req.category),
            ).fetchall()
            if similar:
                similar_hashes = [r["hash"] for r in similar]
                conn.executemany(
                    """INSERT INTO category_overrides (hash, category, notes, updated_at, updated_by)
                       VALUES (?, ?, '', ?, 'alias_backfill')
                       ON CONFLICT(hash) DO UPDATE SET category=excluded.category,
                       updated_at=excluded.updated_at""",
                    [(h, req.category, _now) for h in similar_hashes],
                )
                conn.executemany(
                    "UPDATE transactions SET category = ? WHERE hash = ?",
                    [(req.category, h) for h in similar_hashes],
                )
                also_updated = len(similar_hashes)
                log.info("Applied category to %d similar transactions.", also_updated)

        updated = conn.execute(
            "SELECT * FROM transactions_resolved WHERE hash = ?", (tx_hash,)
        ).fetchone()

    log.info("Category override: %s → %s", tx_hash[:16], req.category)

    return {
        "ok":            True,
        "also_updated":  also_updated,
        "transaction":   _row(updated) if updated else None,
    }


# ── /api/sync ─────────────────────────────────────────────────────────────────

@app.post("/api/sync", dependencies=[Depends(require_api_key)])
def post_sync():
    """
    SQLite is the authoritative store — no external sync needed.
    Returns the last import timestamp from import_log for the PWA Settings page.
    """
    with _db() as conn:
        tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        last_import = conn.execute(
            "SELECT import_date FROM import_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "ok":                 True,
        "noop":               True,
        "transactions_count": tx_count,
        "last_sync":          last_import["import_date"] if last_import else None,
    }


# ── /api/nas-sync ─────────────────────────────────────────────────────────────

@app.get("/api/backups/status", dependencies=[Depends(require_api_key)])
def get_backups_status():
    from finance.backup import get_backup_status
    return get_backup_status(_db_path)


@app.post("/api/backups/manual", dependencies=[Depends(require_api_key), Depends(require_writable)])
def post_manual_backup():
    from finance.backup import backup_db, get_backup_status
    path = backup_db(_db_path, kind="manual", sync_to_nas_enabled=False)
    return {
        "ok": True,
        "path": path,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": get_backup_status(_db_path),
    }

@app.get("/api/nas-sync/status", dependencies=[Depends(require_api_key)])
def get_nas_sync_status():
    """Return NAS sync configuration and last-sync timestamp."""
    from finance.backup import NAS_SYNC_TARGET, _load_sync_state
    state = _load_sync_state()
    return {
        "configured":    bool(NAS_SYNC_TARGET),
        "target":        NAS_SYNC_TARGET or None,
        "last_synced_at": state.get("last_nas_sync"),
    }


@app.post("/api/nas-sync", dependencies=[Depends(require_api_key), Depends(require_writable)])
def post_nas_sync():
    """
    Manually trigger a sync of the latest available backup to the NAS.
    Blocked in read-only mode. Always forces the sync (ignores 24h throttle).
    Requires NAS_SYNC_TARGET env var to be configured.
    """
    from finance.backup import sync_to_nas
    return sync_to_nas(_db_path, force=True)


# ── /api/import ───────────────────────────────────────────────────────────────

@app.post("/api/import", dependencies=[Depends(require_api_key), Depends(require_writable)])
def post_import(req: ImportRequest = ImportRequest()):
    """
    Import ALL_TRANSACTIONS.xlsx into SQLite.

    Reads the XLSX, skips duplicates (or overwrites with --overwrite),
    categorises new rows via the alias/Ollama pipeline, and writes directly
    to SQLite. SQLite is the authoritative store — no external sync needed.
    """
    xlsx_path = _finance_cfg.xlsx_input
    if not os.path.exists(xlsx_path):
        raise HTTPException(404, f"XLSX not found: {xlsx_path}")

    from finance.importer import direct_import as _direct_import
    from finance.categorizer import Categorizer

    categorizer = Categorizer(
        aliases=[],
        categories=[],
        ollama_host=_ollama_cfg.host,
        ollama_model=_ollama_cfg.model,
        ollama_timeout=_ollama_cfg.timeout_seconds,
    )

    stats = _direct_import(
        xlsx_path=xlsx_path,
        db_path=_db_path,
        categorizer=categorizer,
        overwrite=req.overwrite,
        dry_run=req.dry_run,
        import_file_label=os.path.basename(xlsx_path),
    )

    # Trigger a backup after a real import that added rows
    if not req.dry_run and stats.get("added", 0) > 0:
        try:
            from finance.backup import ensure_auto_backups
            ensure_auto_backups(_db_path)
        except Exception as _bkp_exc:
            log.warning("Post-import backup failed (non-fatal): %s", _bkp_exc)

    return {"ok": True, **stats}


# ── Stage 3: Wealth Management ───────────────────────────────────────────────

# Maps asset_class values (used in holdings table) to the asset group label
_ASSET_CLASS_GROUP: dict[str, str] = {
    "savings":       "Cash & Liquid",
    "checking":      "Cash & Liquid",
    "money_market":  "Cash & Liquid",
    "physical_cash": "Cash & Liquid",
    "stock":         "Investments",
    "mutual_fund":   "Investments",
    "bond":          "Investments",
    "retirement":    "Investments",
    "crypto":        "Investments",
    "real_estate":   "Real Estate",
    "vehicle":       "Physical Assets",
    "gold":          "Physical Assets",
    "other":         "Physical Assets",
}

# Asset classes whose values are stable and should auto-carry-forward month-to-month
CARRY_FORWARD_CLASSES = {"retirement", "real_estate", "vehicle", "gold", "other"}

# Maps account_type → net_worth_snapshots column
_ACCT_TYPE_COL: dict[str, str] = {
    "savings":       "savings_idr",
    "checking":      "checking_idr",
    "money_market":  "money_market_idr",
    "physical_cash": "physical_cash_idr",
}

# Maps asset_class → net_worth_snapshots column
_HOLDING_CLASS_COL: dict[str, str] = {
    "bond":        "bonds_idr",
    "stock":       "stocks_idr",
    "mutual_fund": "mutual_funds_idr",
    "retirement":  "retirement_idr",
    "crypto":      "crypto_idr",
    "real_estate": "real_estate_idr",
    "vehicle":     "vehicles_idr",
    "gold":        "gold_idr",
    "other":       "other_assets_idr",
}

# Maps liability_type → net_worth_snapshots column
_LIAB_TYPE_COL: dict[str, str] = {
    "mortgage":      "mortgages_idr",
    "personal_loan": "personal_loans_idr",
    "credit_card":   "credit_card_debt_idr",
    "taxes_owed":    "taxes_owed_idr",
    "other":         "other_liabilities_idr",
}


# ── Request models ─────────────────────────────────────────────────────────────

class BalanceUpsertRequest(BaseModel):
    snapshot_date: str           = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    institution:   str           = Field(..., max_length=100)
    account:       str           = Field(..., max_length=100)
    account_type:  str           = Field("savings", max_length=50)
    owner:         str           = Field("", max_length=100)
    currency:      str           = Field("IDR", max_length=10)
    balance:       float         = 0.0
    balance_idr:   float         = 0.0
    exchange_rate: float         = Field(0.0, ge=0)
    notes:         str           = Field("", max_length=500)

    @field_validator("snapshot_date")
    @classmethod
    def validate_snap(cls, v): return _validate_snapshot_date(v)


class HoldingUpsertRequest(BaseModel):
    snapshot_date:      str   = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    asset_class:        str   = Field(..., max_length=50)
    asset_name:         str   = Field(..., max_length=200)
    isin_or_code:       str   = Field("", max_length=20)
    institution:        str   = Field("", max_length=100)
    account:            str   = Field("", max_length=100)
    owner:              str   = Field("", max_length=100)
    currency:           str   = Field("IDR", max_length=10)
    quantity:           float = 0.0
    unit_price:         float = 0.0
    market_value:       float = 0.0
    market_value_idr:   float = 0.0
    cost_basis:         float = 0.0
    cost_basis_idr:     float = 0.0
    unrealised_pnl_idr: float = 0.0
    exchange_rate:      float = Field(0.0, ge=0)
    maturity_date:      str   = Field("", max_length=10)
    coupon_rate:        float = 0.0
    last_appraised_date: str  = Field("", max_length=10)
    notes:              str   = Field("", max_length=500)

    @field_validator("snapshot_date")
    @classmethod
    def validate_snap(cls, v): return _validate_snapshot_date(v)


class LiabilityUpsertRequest(BaseModel):
    snapshot_date:  str   = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    liability_type: str   = Field(..., max_length=50)
    liability_name: str   = Field(..., max_length=200)
    institution:    str   = Field("", max_length=100)
    account:        str   = Field("", max_length=100)
    owner:          str   = Field("", max_length=100)
    currency:       str   = Field("IDR", max_length=10)
    balance:        float = 0.0
    balance_idr:    float = 0.0
    due_date:       str   = Field("", max_length=10)
    notes:          str   = Field("", max_length=500)

    @field_validator("snapshot_date")
    @classmethod
    def validate_snap(cls, v): return _validate_snapshot_date(v)


class SnapshotRequest(BaseModel):
    snapshot_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes:         str = Field("", max_length=500)

    @field_validator("snapshot_date")
    @classmethod
    def validate_snap(cls, v): return _validate_snapshot_date(v)


# ── /api/wealth/balances ──────────────────────────────────────────────────────

@app.get("/api/wealth/balances", dependencies=[Depends(require_api_key)])
def get_balances(
    snapshot_date: Optional[str] = Query(None),
    account_type:  Optional[str] = Query(None),
    owner:         Optional[str] = Query(None),
):
    conditions, params = [], []
    if snapshot_date:
        conditions.append("snapshot_date = ?"); params.append(snapshot_date)
    if account_type:
        conditions.append("account_type = ?"); params.append(account_type)
    if owner and owner.lower() not in ("all", "both", ""):
        conditions.append("owner = ?"); params.append(owner)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM account_balances{where} "
            "ORDER BY snapshot_date DESC, institution, account",
            params,
        ).fetchall()
    return [_row(r) for r in rows]


@app.post("/api/wealth/balances", dependencies=[Depends(require_api_key), Depends(require_writable)])
def upsert_balance(req: BalanceUpsertRequest):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO account_balances
                (snapshot_date, institution, account, account_type, asset_group,
                 owner, currency, balance, balance_idr, exchange_rate, notes, import_date)
            VALUES (?, ?, ?, ?, 'Cash & Liquid', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, institution, account, owner)
            DO UPDATE SET
                account_type  = excluded.account_type,
                balance       = excluded.balance,
                balance_idr   = excluded.balance_idr,
                exchange_rate = excluded.exchange_rate,
                notes         = excluded.notes,
                import_date   = excluded.import_date
            """,
            (req.snapshot_date, req.institution, req.account, req.account_type,
             req.owner, req.currency, req.balance, req.balance_idr,
             req.exchange_rate, req.notes, today),
        )
        row = conn.execute(
            "SELECT * FROM account_balances "
            "WHERE snapshot_date=? AND institution=? AND account=? AND owner=?",
            (req.snapshot_date, req.institution, req.account, req.owner),
        ).fetchone()
    _auto_snapshot(req.snapshot_date)
    return {"ok": True, "balance": _row(row)}


@app.delete("/api/wealth/balances/{balance_id}", dependencies=[Depends(require_api_key), Depends(require_writable)])
def delete_balance(balance_id: int):
    with _db() as conn:
        row = conn.execute(
            "SELECT snapshot_date FROM account_balances WHERE id = ?", (balance_id,)
        ).fetchone()
        snap_date = row["snapshot_date"] if row else None
        conn.execute("DELETE FROM account_balances WHERE id = ?", (balance_id,))
    if snap_date:
        _auto_snapshot(snap_date)
    return {"ok": True}


# ── /api/wealth/holdings ──────────────────────────────────────────────────────

@app.get("/api/wealth/holdings", dependencies=[Depends(require_api_key)])
def get_holdings(
    snapshot_date: Optional[str] = Query(None),
    asset_class:   Optional[str] = Query(None),
    asset_group:   Optional[str] = Query(None),
    owner:         Optional[str] = Query(None),
):
    conditions, params = [], []
    if snapshot_date:
        conditions.append("snapshot_date = ?"); params.append(snapshot_date)
    if asset_class:
        conditions.append("asset_class = ?"); params.append(asset_class)
    if asset_group:
        conditions.append("asset_group = ?"); params.append(asset_group)
    if owner and owner.lower() not in ("all", "both", ""):
        conditions.append("owner = ?"); params.append(owner)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM holdings{where} "
            "ORDER BY snapshot_date DESC, asset_group, asset_class, asset_name",
            params,
        ).fetchall()
    return [_row(r) for r in rows]


@app.post("/api/wealth/holdings", dependencies=[Depends(require_api_key), Depends(require_writable)])
def upsert_holding(req: HoldingUpsertRequest):
    today       = datetime.now().strftime("%Y-%m-%d")
    asset_group = _ASSET_CLASS_GROUP.get(req.asset_class, "Investments")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO holdings
                (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
                 institution, account, owner, currency, quantity, unit_price,
                 market_value, market_value_idr, cost_basis, cost_basis_idr,
                 unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
                 last_appraised_date, notes, import_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
            DO UPDATE SET
                asset_group          = excluded.asset_group,
                isin_or_code         = excluded.isin_or_code,
                account              = excluded.account,
                currency             = excluded.currency,
                quantity             = excluded.quantity,
                unit_price           = excluded.unit_price,
                market_value         = excluded.market_value,
                market_value_idr     = excluded.market_value_idr,
                cost_basis           = excluded.cost_basis,
                cost_basis_idr       = excluded.cost_basis_idr,
                unrealised_pnl_idr   = excluded.unrealised_pnl_idr,
                exchange_rate        = excluded.exchange_rate,
                maturity_date        = excluded.maturity_date,
                coupon_rate          = excluded.coupon_rate,
                last_appraised_date  = excluded.last_appraised_date,
                notes                = excluded.notes,
                import_date          = excluded.import_date
            """,
            (req.snapshot_date, req.asset_class, asset_group, req.asset_name,
             req.isin_or_code, req.institution, req.account, req.owner,
             req.currency, req.quantity, req.unit_price, req.market_value,
             req.market_value_idr, req.cost_basis, req.cost_basis_idr,
             req.unrealised_pnl_idr, req.exchange_rate, req.maturity_date,
             req.coupon_rate, req.last_appraised_date, req.notes, today),
        )
        row = conn.execute(
            "SELECT * FROM holdings "
            "WHERE snapshot_date=? AND asset_class=? AND asset_name=? AND owner=? AND institution=?",
            (req.snapshot_date, req.asset_class, req.asset_name, req.owner, req.institution),
        ).fetchone()
    _auto_snapshot(req.snapshot_date)
    _cascade_holding_update(
        req.snapshot_date,
        req.asset_class,
        req.asset_name,
        req.owner,
        req.institution,
        req.account,
    )
    return {"ok": True, "holding": _row(row)}


class CarryForwardRequest(BaseModel):
    snapshot_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("snapshot_date")
    @classmethod
    def validate_snap(cls, v): return _validate_snapshot_date(v)


@app.post("/api/wealth/holdings/carry-forward", dependencies=[Depends(require_api_key), Depends(require_writable)])
def carry_forward_holdings(req: CarryForwardRequest):
    """
    For carry-forward asset classes (retirement, real_estate, vehicle, gold, other),
    copy any missing holdings from the most recent prior snapshot_date to
    req.snapshot_date.

    Existing target-month rows are preserved; only missing identities are filled.
    Safe to call multiple times (idempotent per holding identity).
    """
    today   = datetime.now().strftime("%Y-%m-%d")
    carried = 0
    with _db() as conn:
        for asset_class in CARRY_FORWARD_CLASSES:
            existing_keys = {
                (
                    row["asset_name"],
                    row["owner"],
                    row["institution"],
                )
                for row in conn.execute(
                    """
                    SELECT asset_name, owner, institution
                    FROM holdings
                    WHERE snapshot_date=? AND asset_class=?
                    """,
                    (req.snapshot_date, asset_class),
                ).fetchall()
            }
            prev_rows = conn.execute(
                "SELECT * FROM holdings WHERE asset_class=? AND snapshot_date < ? "
                "ORDER BY snapshot_date DESC",
                (asset_class, req.snapshot_date),
            ).fetchall()
            if not prev_rows:
                continue

            seen: set[tuple[str, str, str]] = set()
            for row in prev_rows:
                key = (row["asset_name"], row["owner"], row["institution"])
                if key in existing_keys or key in seen:
                    continue
                seen.add(key)
                carried_notes = (row["notes"] or "") + " [carried forward]"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO holdings
                        (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
                         institution, account, owner, currency, quantity, unit_price,
                         market_value, market_value_idr, cost_basis, cost_basis_idr,
                         unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
                         last_appraised_date, notes, import_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (req.snapshot_date, row["asset_class"], row["asset_group"],
                     row["asset_name"], row["isin_or_code"], row["institution"],
                     row["account"], row["owner"], row["currency"], row["quantity"],
                     row["unit_price"],
                     0,          # market_value: zeroed — needs re-appraisal
                     0,          # market_value_idr: zeroed — needs re-appraisal
                     row["cost_basis"], row["cost_basis_idr"],
                     0,          # unrealised_pnl_idr: zeroed — needs re-appraisal
                     row["exchange_rate"], row["maturity_date"], row["coupon_rate"],
                     row["last_appraised_date"], carried_notes, today),
                )
                carried += conn.execute("SELECT changes()").fetchone()[0]

    if carried > 0:
        _auto_snapshot(req.snapshot_date)

    return {"ok": True, "carried": carried}


@app.delete("/api/wealth/holdings/{holding_id}", dependencies=[Depends(require_api_key), Depends(require_writable)])
def delete_holding(holding_id: int):
    with _db() as conn:
        row = conn.execute(
            "SELECT snapshot_date FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
        snap_date = row["snapshot_date"] if row else None
        conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    if snap_date:
        _auto_snapshot(snap_date)
    return {"ok": True}



def _auto_snapshot(snapshot_date: str):
    """Re-aggregate net_worth_snapshot for snapshot_date after any mutation (best-effort)."""
    try:
        create_snapshot(SnapshotRequest(snapshot_date=snapshot_date))
    except Exception as exc:
        log.error("Auto-snapshot failed for %s (non-fatal): %s", snapshot_date, exc, exc_info=True)


def _cascade_holding_update(
    snapshot_date: str,
    asset_class: str,
    asset_name: str,
    owner: str,
    institution: str,
    account: str,
):
    """
    For carry-forward asset classes, propagate the updated values to all subsequent
    months that already have an entry for this specific holding identity.
    Called after upsert_holding when asset_class is in CARRY_FORWARD_CLASSES.
    """
    if asset_class not in CARRY_FORWARD_CLASSES:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    affected_dates: list[str] = []
    with _db() as conn:
        source = conn.execute(
            """
            SELECT * FROM holdings
            WHERE snapshot_date=? AND asset_class=? AND asset_name=? AND owner=?
              AND institution=? AND account=?
            """,
            (snapshot_date, asset_class, asset_name, owner, institution, account),
        ).fetchone()
        if not source:
            return
        subsequent = conn.execute(
            """
            SELECT snapshot_date FROM holdings
            WHERE asset_class=? AND asset_name=? AND owner=? AND institution=? AND account=?
              AND snapshot_date > ?
            ORDER BY snapshot_date ASC
            """,
            (asset_class, asset_name, owner, institution, account, snapshot_date),
        ).fetchall()
        for row in subsequent:
            target_date = row["snapshot_date"]
            conn.execute(
                """UPDATE holdings SET
                    market_value=?, market_value_idr=?, cost_basis=?, cost_basis_idr=?,
                    unrealised_pnl_idr=?, quantity=?, unit_price=?,
                    last_appraised_date=?, notes=?, import_date=?
                WHERE snapshot_date=? AND asset_class=? AND asset_name=? AND owner=?
                  AND institution=? AND account=?""",
                (source["market_value"], source["market_value_idr"],
                 source["cost_basis"], source["cost_basis_idr"],
                 source["unrealised_pnl_idr"], source["quantity"], source["unit_price"],
                 source["last_appraised_date"], source["notes"], today,
                 target_date, asset_class, asset_name, owner, institution, account),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                affected_dates.append(target_date)
    if affected_dates:
        for d in affected_dates:
            _auto_snapshot(d)


# ── /api/wealth/liabilities ───────────────────────────────────────────────────

@app.get("/api/wealth/liabilities", dependencies=[Depends(require_api_key)])
def get_liabilities(
    snapshot_date:  Optional[str] = Query(None),
    liability_type: Optional[str] = Query(None),
    owner:          Optional[str] = Query(None),
):
    conditions, params = [], []
    if snapshot_date:
        conditions.append("snapshot_date = ?"); params.append(snapshot_date)
    if liability_type:
        conditions.append("liability_type = ?"); params.append(liability_type)
    if owner and owner.lower() not in ("all", "both", ""):
        conditions.append("owner = ?"); params.append(owner)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM liabilities{where} "
            "ORDER BY snapshot_date DESC, liability_type, liability_name",
            params,
        ).fetchall()
    return [_row(r) for r in rows]


@app.post("/api/wealth/liabilities", dependencies=[Depends(require_api_key), Depends(require_writable)])
def upsert_liability(req: LiabilityUpsertRequest):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO liabilities
                (snapshot_date, liability_type, liability_name, institution, account,
                 owner, currency, balance, balance_idr, due_date, notes, import_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date, liability_type, liability_name, owner, institution, account)
            DO UPDATE SET
                institution  = excluded.institution,
                account      = excluded.account,
                currency     = excluded.currency,
                balance      = excluded.balance,
                balance_idr  = excluded.balance_idr,
                due_date     = excluded.due_date,
                notes        = excluded.notes,
                import_date  = excluded.import_date
            """,
            (req.snapshot_date, req.liability_type, req.liability_name,
             req.institution, req.account, req.owner, req.currency,
             req.balance, req.balance_idr, req.due_date, req.notes, today),
        )
        row = conn.execute(
            "SELECT * FROM liabilities "
            "WHERE snapshot_date=? AND liability_type=? AND liability_name=? AND owner=? "
            "AND institution=? AND account=?",
            (
                req.snapshot_date,
                req.liability_type,
                req.liability_name,
                req.owner,
                req.institution,
                req.account,
            ),
        ).fetchone()
    _auto_snapshot(req.snapshot_date)
    return {"ok": True, "liability": _row(row)}


@app.delete("/api/wealth/liabilities/{liability_id}", dependencies=[Depends(require_api_key), Depends(require_writable)])
def delete_liability(liability_id: int):
    with _db() as conn:
        row = conn.execute(
            "SELECT snapshot_date FROM liabilities WHERE id = ?", (liability_id,)
        ).fetchone()
        snap_date = row["snapshot_date"] if row else None
        conn.execute("DELETE FROM liabilities WHERE id = ?", (liability_id,))
    if snap_date:
        _auto_snapshot(snap_date)
    return {"ok": True}


# ── /api/wealth/snapshot ──────────────────────────────────────────────────────

@app.get("/api/wealth/snapshot/dates", dependencies=[Depends(require_api_key)])
def get_snapshot_dates():
    """
    All month-end dates that have any wealth data — snapshots OR raw entries in
    account_balances / holdings / liabilities — most recent first.
    This lets the PWA show date chips for months that have been imported but
    not yet snapshotted.
    """
    with _db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT snapshot_date FROM (
                SELECT snapshot_date FROM net_worth_snapshots
                UNION
                SELECT snapshot_date FROM account_balances
                UNION
                SELECT snapshot_date FROM holdings
                UNION
                SELECT snapshot_date FROM liabilities
            ) ORDER BY snapshot_date DESC
        """).fetchall()
    return [r[0] for r in rows]


@app.post("/api/wealth/snapshot", dependencies=[Depends(require_api_key), Depends(require_writable)])
def create_snapshot(req: SnapshotRequest):
    """
    Aggregate account_balances + holdings + liabilities for the given date
    into a net_worth_snapshots row (upsert).  Returns the saved snapshot.
    """
    sd = req.snapshot_date
    with _db() as conn:
        # ── Account balances → liquid sub-totals ──────────────────────────────
        bal_rows = conn.execute(
            "SELECT account_type, SUM(balance_idr) AS total "
            "FROM account_balances WHERE snapshot_date=? GROUP BY account_type",
            (sd,),
        ).fetchall()
        bal = {r["account_type"]: r["total"] or 0.0 for r in bal_rows}

        # ── Holdings → investment / tangible sub-totals ───────────────────────
        hold_rows = conn.execute(
            "SELECT asset_class, SUM(market_value_idr) AS total "
            "FROM holdings WHERE snapshot_date=? GROUP BY asset_class",
            (sd,),
        ).fetchall()
        hold = {r["asset_class"]: r["total"] or 0.0 for r in hold_rows}

        # ── Liabilities → sub-totals ──────────────────────────────────────────
        # Use the most recent balance per card as of snapshot_date (carry-forward),
        # so month-end snapshots reflect CC balances even when statement dates differ.
        liab_rows = conn.execute(
            """
            SELECT liability_type, SUM(balance_idr) AS total
            FROM (
                SELECT liability_type, liability_name, owner,
                       institution, account, balance_idr, MAX(snapshot_date) AS latest_date
                FROM liabilities
                WHERE snapshot_date <= ?
                GROUP BY liability_type, liability_name, owner, institution, account
            )
            GROUP BY liability_type
            """,
            (sd,),
        ).fetchall()
        liab = {r["liability_type"]: r["total"] or 0.0 for r in liab_rows}

        # ── Build totals ──────────────────────────────────────────────────────
        sv   = bal.get("savings", 0.0)
        chk  = bal.get("checking", 0.0)
        mm   = bal.get("money_market", 0.0)
        cash = bal.get("physical_cash", 0.0)

        bonds   = hold.get("bond", 0.0)
        stocks  = hold.get("stock", 0.0)
        mf      = hold.get("mutual_fund", 0.0)
        retire  = hold.get("retirement", 0.0)
        crypto  = hold.get("crypto", 0.0)
        realty  = hold.get("real_estate", 0.0)
        veh     = hold.get("vehicle", 0.0)
        gold    = hold.get("gold", 0.0)
        other_a = hold.get("other", 0.0)

        total_assets = sv + chk + mm + cash + bonds + stocks + mf + retire + crypto + realty + veh + gold + other_a

        mort     = liab.get("mortgage", 0.0)
        loans    = liab.get("personal_loan", 0.0)
        cc       = liab.get("credit_card", 0.0)
        tax      = liab.get("taxes_owed", 0.0)
        other_l  = liab.get("other", 0.0)
        total_liabilities = mort + loans + cc + tax + other_l

        net_worth = total_assets - total_liabilities

        # MoM: most recent snapshot in a PRIOR calendar month.
        # Using the immediately preceding snapshot would compare against intermediate
        # savings/CC-statement-date snapshots that have partial data (e.g. assets=0),
        # producing meaningless deltas.  Restricting to a different YYYY-MM ensures
        # the baseline is always the last complete snapshot of the previous month.
        prev = _get_previous_month_snapshot(conn, sd)
        mom = net_worth - (prev["net_worth_idr"] if prev else 0.0)

        conn.execute(
            """
            INSERT INTO net_worth_snapshots
                (snapshot_date,
                 savings_idr, checking_idr, money_market_idr, physical_cash_idr,
                 bonds_idr, stocks_idr, mutual_funds_idr, retirement_idr, crypto_idr,
                 real_estate_idr, vehicles_idr, gold_idr, other_assets_idr,
                 total_assets_idr,
                 mortgages_idr, personal_loans_idr, credit_card_debt_idr,
                 taxes_owed_idr, other_liabilities_idr, total_liabilities_idr,
                 net_worth_idr, mom_change_idr, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date) DO UPDATE SET
                savings_idr           = excluded.savings_idr,
                checking_idr          = excluded.checking_idr,
                money_market_idr      = excluded.money_market_idr,
                physical_cash_idr     = excluded.physical_cash_idr,
                bonds_idr             = excluded.bonds_idr,
                stocks_idr            = excluded.stocks_idr,
                mutual_funds_idr      = excluded.mutual_funds_idr,
                retirement_idr        = excluded.retirement_idr,
                crypto_idr            = excluded.crypto_idr,
                real_estate_idr       = excluded.real_estate_idr,
                vehicles_idr          = excluded.vehicles_idr,
                gold_idr              = excluded.gold_idr,
                other_assets_idr      = excluded.other_assets_idr,
                total_assets_idr      = excluded.total_assets_idr,
                mortgages_idr         = excluded.mortgages_idr,
                personal_loans_idr    = excluded.personal_loans_idr,
                credit_card_debt_idr  = excluded.credit_card_debt_idr,
                taxes_owed_idr        = excluded.taxes_owed_idr,
                other_liabilities_idr = excluded.other_liabilities_idr,
                total_liabilities_idr = excluded.total_liabilities_idr,
                net_worth_idr         = excluded.net_worth_idr,
                mom_change_idr        = excluded.mom_change_idr,
                notes                 = excluded.notes
            """,
            (sd, sv, chk, mm, cash,
             bonds, stocks, mf, retire, crypto,
             realty, veh, gold, other_a, total_assets,
             mort, loans, cc, tax, other_l, total_liabilities,
             net_worth, mom, req.notes),
        )
        snap = conn.execute(
            "SELECT * FROM net_worth_snapshots WHERE snapshot_date=?", (sd,)
        ).fetchone()

    return {"ok": True, "snapshot": _row(snap)}


# ── /api/wealth/history ───────────────────────────────────────────────────────

@app.get("/api/wealth/history", dependencies=[Depends(require_api_key)])
def get_wealth_history(limit: int = Query(24, ge=1, le=60)):
    """Net worth snapshots oldest-first for trend chart (latest `limit` entries)."""
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM net_worth_snapshots
                ORDER BY snapshot_date DESC LIMIT ?
            ) ORDER BY snapshot_date ASC
            """,
            (limit,),
        ).fetchall()
    return [_row(r) for r in rows]


# ── /api/wealth/summary ───────────────────────────────────────────────────────

@app.get("/api/wealth/summary", dependencies=[Depends(require_api_key)])
def get_wealth_summary(
    snapshot_date: Optional[str] = Query(None),
    owner:         Optional[str] = Query(None),
):
    """
    Full wealth summary for a given snapshot date (defaults to most recent).

    Returns the snapshot row plus all balances, holdings, and liabilities
    for that date so the PWA can render the dashboard in a single call.
    """
    with _db() as conn:
        if snapshot_date:
            snap = conn.execute(
                "SELECT * FROM net_worth_snapshots WHERE snapshot_date=?",
                (snapshot_date,),
            ).fetchone()
        else:
            snap = conn.execute(
                "SELECT * FROM net_worth_snapshots ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()

        sd = snapshot_date or (snap["snapshot_date"] if snap else None)

        owner_cond = ""
        owner_params: list = []
        if owner and owner.lower() not in ("all", "both", ""):
            owner_cond = " AND owner=?"
            owner_params = [owner]

        if sd:
            balances = conn.execute(
                f"SELECT * FROM account_balances WHERE snapshot_date=?{owner_cond} "
                "ORDER BY institution, account",
                [sd] + owner_params,
            ).fetchall()
            holdings_rows = conn.execute(
                f"SELECT * FROM holdings WHERE snapshot_date=?{owner_cond} "
                "ORDER BY asset_group, asset_class, asset_name",
                [sd] + owner_params,
            ).fetchall()
            liab_rows = conn.execute(
                f"SELECT * FROM liabilities WHERE snapshot_date=?{owner_cond} "
                "ORDER BY liability_type, liability_name",
                [sd] + owner_params,
            ).fetchall()
        else:
            balances, holdings_rows, liab_rows = [], [], []

        dates = conn.execute("""
            SELECT DISTINCT snapshot_date FROM (
                SELECT snapshot_date FROM net_worth_snapshots
                UNION
                SELECT snapshot_date FROM account_balances
                UNION
                SELECT snapshot_date FROM holdings
                UNION
                SELECT snapshot_date FROM liabilities
            ) ORDER BY snapshot_date DESC LIMIT 24
        """).fetchall()

    return {
        "snapshot":      _row(snap) if snap else None,
        "snapshot_date": sd,
        "balances":      [_row(r) for r in balances],
        "holdings":      [_row(r) for r in holdings_rows],
        "liabilities":   [_row(r) for r in liab_rows],
        "dates":         [r[0] for r in dates],
    }


@app.get("/api/wealth/explanation", dependencies=[Depends(require_api_key)])
def get_wealth_explanation(snapshot_date: Optional[str] = Query(None), ai: bool = Query(False)):
    """Explain the selected month's net worth movement.

    Without ?ai=1 returns the deterministic fallback instantly.
    With ?ai=1 calls Ollama for an AI-generated explanation (may be slow).
    """
    with _db() as conn:
        return _build_wealth_explanation(conn, snapshot_date, use_ai=ai)


@app.post("/api/wealth/explanation/query", dependencies=[Depends(require_api_key)])
def query_wealth_explanation(req: WealthQuestionRequest):
    """Answer follow-up questions about the selected month's wealth movement."""
    with _db() as conn:
        context = _build_wealth_question_context(conn, req.snapshot_date)
        if not context.get("available"):
            return context
        try:
            return _ask_wealth_question_with_ollama(req.question, context, req.history)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            log.info("Wealth Q&A falling back to deterministic answer: %s", exc)
            return _fallback_wealth_question_answer(req.question, context)
        except Exception as exc:
            log.warning("Unexpected wealth Q&A error, using fallback: %s", exc)
            return _fallback_wealth_question_answer(req.question, context)


# ── PDF Local Processing ───────────────────────────────────────────────────────
# The finance-api container can see pdf_inbox / pdf_unlocked via the mounted
# ./data:/app/data volume.  PDF parsing itself lives in the bridge (port 9100
# on the host); these endpoints scan local folders and proxy files to it.
#
#   GET  /api/pdf/local-files              list PDFs in both inbox dirs
#   POST /api/pdf/process-local            upload one file to bridge + trigger parse
#   GET  /api/pdf/local-status/{job_id}    proxy bridge /pdf/status/<id>
# ──────────────────────────────────────────────────────────────────────────────
import asyncio  as _asyncio
import uuid     as _uuid
import urllib.request as _urllib_req
import urllib.error   as _urllib_err
import json     as _json
import pathlib  as _pl

# Bridge location — override via BRIDGE_URL env var when running outside Docker
_BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://host.docker.internal:9100").rstrip("/")

# Data dir is the directory containing finance.db (mounted from ./data)
_DATA_DIR = _pl.Path(_db_path).parent

_PDF_FOLDERS: dict[str, _pl.Path] = {
    "pdf_inbox":    _DATA_DIR / "pdf_inbox",
    "pdf_unlocked": _DATA_DIR / "pdf_unlocked",
}
_PDF_REGISTRY_DB = _DATA_DIR / "processed_files.db"

def _read_bridge_token() -> str:
    """Read the bridge bearer token from the secrets mount (best-effort)."""
    for candidate in [
        _pl.Path("/app/secrets/bridge.token"),
        _pl.Path(os.environ.get("BRIDGE_TOKEN_FILE", "/app/secrets/bridge.token")),
    ]:
        if candidate.exists():
            return candidate.read_text().strip()
    return ""


def _sha256_file(path: _pl.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_registry_status(status: str, error: str = "") -> str | None:
    normalized = str(status or "").strip().lower()
    error_text = str(error or "").lower()
    if normalized == "error" and "no such file or directory" in error_text:
        return "done"
    if normalized == "ok":
        return "done"
    if normalized == "error":
        return "error"
    return None

def _bridge_headers(token: str) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _bridge_get(path: str, token: str) -> dict:
    """Synchronous bridge GET — run via asyncio.to_thread."""
    req = _urllib_req.Request(
        f"{_BRIDGE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    with _urllib_req.urlopen(req, timeout=15) as r:
        return _json.loads(r.read())

def _bridge_post_json(path: str, body: dict, token: str) -> dict:
    """Synchronous bridge POST (JSON) — run via asyncio.to_thread."""
    data = _json.dumps(body).encode()
    req = _urllib_req.Request(
        f"{_BRIDGE_URL}{path}", data=data,
        headers={
            "Content-Type": "application/json",
            **( {"Authorization": f"Bearer {token}"} if token else {} ),
        }, method="POST",
    )
    with _urllib_req.urlopen(req, timeout=60) as r:
        return _json.loads(r.read())

def _bridge_upload(path: str, filename: str, file_bytes: bytes, token: str) -> dict:
    """Synchronous multipart upload to bridge — run via asyncio.to_thread."""
    boundary = "----FinanceBridge" + _uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = _urllib_req.Request(
        f"{_BRIDGE_URL}{path}", data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            **( {"Authorization": f"Bearer {token}"} if token else {} ),
        }, method="POST",
    )
    with _urllib_req.urlopen(req, timeout=60) as r:
        return _json.loads(r.read())


@app.get("/api/pdf/local-files")
async def pdf_local_files(_auth=Depends(require_api_key)):
    """
    List all PDF files found recursively in pdf_inbox and pdf_unlocked under the
    data directory.

    Returns a list of { folder, filename, relative_path, size_kb, mtime } objects
    sorted by relative path so nested folders remain predictable in the workspace.
    """
    results = []
    for folder, dir_path in _PDF_FOLDERS.items():
        if not dir_path.is_dir():
            continue
        for f in dir_path.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".pdf":
                stat = f.stat()
                results.append({
                    "folder": folder,
                    "filename": f.name,
                    "relative_path": f.relative_to(dir_path).as_posix(),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "mtime": stat.st_mtime,
                })
    results.sort(key=lambda x: (x["folder"], x["relative_path"].lower()))
    return results


@app.get("/api/pdf/local-workspace")
async def pdf_local_workspace(_auth=Depends(require_api_key)):
    """
    List local PDFs together with their most recent processing attempt from the bridge.

    Returns:
      {
        "files": [
          {
            "folder": "pdf_inbox",
            "filename": "statement.pdf",
            "size_kb": 123.4,
            "mtime": 1712700000.0,
            "last_processed_at": "2026-04-08T11:22:33+00:00" | null,
            "last_status": "done" | "error" | "pending" | null,
            "last_error": "..." | ""
          }
        ]
      }
    """
    files = await pdf_local_files(_auth)

    token = _read_bridge_token()
    try:
        jobs_res = await _asyncio.to_thread(_bridge_get, "/pdf/jobs?limit=200", token)
        jobs = jobs_res.get("jobs", [])
    except Exception as exc:
        log.warning("Could not load bridge PDF job history: %s", exc)
        jobs = []

    registry_by_hash: dict[str, sqlite3.Row] = {}
    if _PDF_REGISTRY_DB.exists():
        try:
            con = sqlite3.connect(_PDF_REGISTRY_DB)
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT sha256, processed_at, status, error
                FROM processed_files
            """).fetchall()
            con.close()
            registry_by_hash = {str(row["sha256"]): row for row in rows}
        except Exception as exc:
            log.warning("Could not load PDF registry: %s", exc)

    latest_by_key: dict[tuple[str, str], dict] = {}
    for job in jobs:
        folder = str(job.get("folder", "")).strip()
        relative_path = str(job.get("relative_path") or job.get("filename", "")).strip()
        created_at = str(job.get("created_at", "")).strip()
        if not folder or not relative_path or not created_at:
            continue
        key = (folder, relative_path)
        current = latest_by_key.get(key)
        if current is None or created_at > str(current.get("created_at", "")):
            latest_by_key[key] = job

    merged = []
    for item in files:
        file_path = _PDF_FOLDERS[item["folder"]] / item["relative_path"]
        file_hash = ""
        try:
            file_hash = _sha256_file(file_path)
        except Exception as exc:
            log.warning("Could not hash local PDF %s: %s", file_path, exc)

        job = latest_by_key.get((item["folder"], item["relative_path"]))
        registry = registry_by_hash.get(file_hash) if file_hash else None

        job_time = str(job.get("created_at", "")) if job else ""
        registry_time = str(registry["processed_at"]) if registry else ""
        use_registry = bool(registry and registry_time and registry_time >= job_time)

        merged.append({
            **item,
            "sha256": file_hash,
            "last_processed_at": (
                registry["processed_at"] if use_registry
                else (job.get("created_at") if job else (registry["processed_at"] if registry else None))
            ),
            "last_status": (
                _normalize_registry_status(registry["status"], registry["error"]) if use_registry
                else (job.get("status") if job else _normalize_registry_status(registry["status"], registry["error"]) if registry else None)
            ),
            "last_error": (
                (
                    "" if _normalize_registry_status(registry["status"], registry["error"]) == "done"
                    else str(registry["error"] or "")
                ) if use_registry
                else (
                    job.get("error", "") if job
                    else (
                        "" if registry and _normalize_registry_status(registry["status"], registry["error"]) == "done"
                        else str(registry["error"] or "") if registry else ""
                    )
                )
            ),
        })

    return {"files": merged}


class _ProcessLocalReq(BaseModel):
    folder: str   # "pdf_inbox" or "pdf_unlocked"
    filename: str | None = None
    relative_path: str | None = None

@app.post("/api/pdf/process-local", dependencies=[Depends(require_writable)])
async def pdf_process_local(req: _ProcessLocalReq, _auth=Depends(require_api_key)):
    """
    Ask the bridge to process a PDF by folder+relative path — no byte upload needed.
    Both the bridge (Mac host) and this container share the ./data volume, so the
    bridge can read the file directly from its own configured inbox/unlocked dirs.
    Returns { job_id } — poll /api/pdf/local-status/<job_id> for progress.
    """
    dir_path = _PDF_FOLDERS.get(req.folder)
    if not dir_path:
        raise HTTPException(400, f"Unknown folder '{req.folder}'. Use pdf_inbox or pdf_unlocked.")

    relative_path = str(req.relative_path or req.filename or "").strip()
    if not relative_path:
        raise HTTPException(400, "relative_path is required.")
    if not relative_path.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are supported.")
    if relative_path.startswith("/") or any(part == ".." for part in _pl.Path(relative_path).parts):
        raise HTTPException(400, "Invalid relative_path.")

    file_path = (dir_path / relative_path).resolve()
    root_path = dir_path.resolve()
    if file_path != root_path and root_path not in file_path.parents:
        raise HTTPException(400, "relative_path escapes the allowed folder.")
    if not file_path.is_file():
        raise HTTPException(404, f"File not found: {req.folder}/{relative_path}")

    token = _read_bridge_token()

    # Bridge reads the file directly from disk — avoids multipart upload entirely.
    try:
        result = await _asyncio.to_thread(
            _bridge_post_json,
            "/pdf/process-file",
            {"folder": req.folder, "relative_path": relative_path},
            token,
        )
    except _urllib_err.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise HTTPException(502, f"Bridge error {e.code}: {body}")
    except Exception as e:
        raise HTTPException(502, f"Bridge unreachable: {e}")

    job_id = result.get("job_id")
    if not job_id:
        raise HTTPException(502, f"Bridge returned no job_id: {result}")

    return {"job_id": job_id, "filename": req.filename, "folder": req.folder}


@app.get("/api/pdf/local-status/{job_id}")
async def pdf_local_status(job_id: str, _auth=Depends(require_api_key)):
    """Proxy bridge /pdf/status/<job_id> so the PWA only needs one origin."""
    token = _read_bridge_token()
    try:
        return await _asyncio.to_thread(_bridge_get, f"/pdf/status/{job_id}", token)
    except _urllib_err.HTTPError as e:
        raise HTTPException(502, f"Bridge status error: {e.code} {e.reason}")
    except Exception as e:
        raise HTTPException(502, f"Bridge unreachable: {e}")


@app.get("/api/pipeline/status", dependencies=[Depends(require_api_key)])
async def pipeline_status():
    token = _read_bridge_token()
    try:
        return await _asyncio.to_thread(_bridge_get, "/pipeline/status", token)
    except _urllib_err.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise HTTPException(502, f"Bridge status error {e.code}: {body}")
    except Exception as e:
        raise HTTPException(502, f"Bridge unreachable: {e}")


@app.post("/api/pipeline/run", dependencies=[Depends(require_api_key), Depends(require_writable)])
async def pipeline_run():
    token = _read_bridge_token()
    try:
        return await _asyncio.to_thread(_bridge_post_json, "/pipeline/run", {}, token)
    except _urllib_err.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise HTTPException(502, f"Bridge run error {e.code}: {body}")
    except Exception as e:
        raise HTTPException(502, f"Bridge unreachable: {e}")


# ── Document Completeness Audit ─────────────────────────────────────────────

import re as _re

def _parse_pdf_entity(filename: str) -> dict | None:
    """Parse a PDF filename into {entity_key, entity_label, month_key, info}.

    Supported patterns (derived from actual pdf_inbox contents):
      BCA_2171138631_MM_YYYY.pdf       → entity "BCA - 2171138631", info "Stmt"
      BCA_CC_YYYYMMDD.pdf              → entity "BCA - CC", info "CC"
      CIMBNiagaCCYYYYMMDD.pdf          → entity "CIMB - CC", info "CC"
      CIMBNiagaCSYYYYMMDD.pdf          → entity "CIMB - CS", info "CS"
      IPOT_PORTFOLIO_YYYY-MM-DD.pdf    → entity "IPOT - Portfolio", info "Portfolio"
      IPOT_STATEMENT_YYYY-MM-DD.pdf    → entity "IPOT - Statement", info "Statement"
      Maybank_CC_YYYYMMDD.pdf          → entity "Maybank - CC", info "CC"
      Maybank_CS_YYYYMMDD.pdf          → entity "Maybank - CS", info "CS"
      Permata_Helen_MM_YYYY.pdf        → entity "Permata - Helen", info "Stmt"
      Permata_Helen_ME_MM_YYYY.pdf     → entity "Permata - Helen ME", info "ME"
      Permata_Gandrik_MM_YYYY.pdf      → entity "Permata - Gandrik", info "Stmt"
      Permata_RDN_MM_YYYY.pdf          → entity "Permata - RDN", info "RDN"
      Permata_Black_YYYYMMDD.pdf       → entity "Permata - Black", info "CC"
      Permata_Infinite_YYYYMMDD.pdf    → entity "Permata - Infinite", info "CC"
      Stockbit_SOA_YYYY_MM.pdf         → entity "Stockbit - SOA", info "SOA"
      SOA_BNI_SEKURITAS_*_MonYYYY.pdf  → entity "BNI Sekuritas - SOA", info "SOA"
      BCA_5500346622_MM_YYYY.pdf       → entity "BCA - 5500346622", info "Stmt"
    """
    name = filename
    stem = name.rsplit(".", 1)[0] if "." in name else name

    # ── Pattern: SOA_BNI_SEKURITAS_{id}_{Mon}{YYYY}.pdf ──
    m = _re.match(r"SOA_BNI_SEKURITAS_\w+_(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})", stem, _re.I)
    if m:
        month_map = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
                     "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}
        mon = month_map[m.group(1).lower()]
        yr = m.group(2)
        return {"entity_key": "bni-sekuritas-soa", "entity_label": "BNI Sekuritas - SOA",
                "month_key": f"{yr}-{mon}", "info": "SOA"}

    # ── Pattern: Bank_OwnerOrType_MM_YYYY.pdf (3 trailing numeric tokens) ──
    m = _re.match(r"([A-Za-z]+)_([^_]+)_(\d{2})_(\d{4})$", stem)
    if m:
        bank = m.group(1)
        subtype = m.group(2)
        mm, yyyy = m.group(3), m.group(4)
        if int(mm) <= 12 and 2020 <= int(yyyy) <= 2099:
            type_labels = {"CC": "CC", "CS": "CS", "ME": "ME", "RDN": "RDN", "Black": "CC", "Infinite": "CC"}
            info = type_labels.get(subtype, "Stmt")
            display_label = subtype if subtype in type_labels else subtype
            return {"entity_key": f"{bank.lower()}-{subtype.lower()}",
                    "entity_label": f"{bank} - {display_label}", "month_key": f"{yyyy}-{mm}", "info": info}

    # ── Pattern: Bank_Owner_Type_MM_YYYY.pdf (4 trailing tokens, e.g. Permata_Helen_ME) ──
    m = _re.match(r"([A-Za-z]+)_([A-Za-z]+)_([A-Za-z]+)_(\d{2})_(\d{4})$", stem)
    if m:
        bank = m.group(1)
        owner = m.group(2)
        stype = m.group(3)
        mm, yyyy = m.group(4), m.group(5)
        if int(mm) <= 12 and 2020 <= int(yyyy) <= 2099:
            type_labels = {"ME": "ME", "CC": "CC", "CS": "CS", "RDN": "RDN"}
            info = type_labels.get(stype, stype)
            return {"entity_key": f"{bank.lower()}-{owner.lower()}-{stype.lower()}",
                    "entity_label": f"{bank} - {owner} {stype}", "month_key": f"{yyyy}-{mm}", "info": info}

    # ── Pattern: Bank_Type_YYYYMMDD.pdf (date at end, no underscores in date) ──
    m = _re.match(r"([A-Za-z]+)_([A-Za-z]+)_(\d{8})$", stem)
    if m:
        bank = m.group(1)
        stype = m.group(2)
        ds = m.group(3)
        yr, mo = ds[:4], ds[4:6]
        if int(mo) <= 12 and 2020 <= int(yr) <= 2099:
            _cc_types = {"Black", "Infinite", "Platinum", "Gold"}
            info = "CC" if stype in _cc_types else stype.upper()
            return {"entity_key": f"{bank.lower()}-{stype.lower()}",
                    "entity_label": f"{bank} - {stype}", "month_key": f"{yr}-{mo}", "info": info}

    # ── Pattern: BankTypeYYYYMMDD.pdf (no underscores, fused) ──
    m = _re.match(r"([A-Za-z]+?)([A-Z]{2})(\d{8})$", stem)
    if m:
        bank = m.group(1)
        stype = m.group(2)
        ds = m.group(3)
        yr, mo = ds[:4], ds[4:6]
        if int(mo) <= 12 and 2020 <= int(yr) <= 2099:
            return {"entity_key": f"{bank.lower()}-{stype.lower()}",
                    "entity_label": f"{bank} - {stype}", "month_key": f"{yr}-{mo}", "info": stype.upper()}

    # ── Pattern: Name_Type_YYYY-MM-DD.pdf ──
    m = _re.match(r"([A-Za-z]+)_([A-Za-z_]+)_(\d{4})-(\d{2})-(\d{2})$", stem)
    if m:
        bank = m.group(1)
        stype = m.group(2).replace("_", " ").title()
        yr, mo = m.group(3), m.group(4)
        return {"entity_key": f"{bank.lower()}-{m.group(2).lower()}",
                "entity_label": f"{bank} - {stype}", "month_key": f"{yr}-{mo}", "info": stype}

    # ── Pattern: Name_Type_YYYY_MM.pdf ──
    m = _re.match(r"([A-Za-z]+)_([A-Za-z]+)_(\d{4})_(\d{2})$", stem)
    if m:
        bank = m.group(1)
        stype = m.group(2)
        yr, mo = m.group(3), m.group(4)
        if int(mo) <= 12 and 2020 <= int(yr) <= 2099:
            return {"entity_key": f"{bank.lower()}-{stype.lower()}",
                    "entity_label": f"{bank} - {stype}", "month_key": f"{yr}-{mo}", "info": stype.upper()}

    return None


@app.get("/api/audit/completeness")
async def audit_completeness(
    start_month: str = "", end_month: str = "", _auth=Depends(require_api_key)
):
    """
    Document Completeness Audit.

    Scans pdf_inbox + pdf_unlocked for PDF filenames, parses entity + month
    from each filename, and returns a grid: rows=entities, columns=months,
    cells=list of matching files with info labels.

    Query params:
      start_month  — YYYY-MM (defaults to 3 months before end_month)
      end_month    — YYYY-MM (defaults to current month)

    Returns:
      {
        "months": ["2026-01", "2026-02", "2026-03"],
        "month_labels": ["Jan 2026", "Feb 2026", "Mar 2026"],
        "entities": [
          {
            "key": "bca-2171138631",
            "label": "BCA - 2171138631",
            "months": {
              "2026-01": [{"filename": "...", "info": "Stmt", "folder": "pdf_inbox"}],
              "2026-02": null,
              "2026-03": [{"filename": "...", "info": "Stmt", "folder": "pdf_inbox"}]
            }
          },
          ...
        ]
      }
    """
    from datetime import date as _date

    today = _date.today()
    # Determine end_month
    if end_month and len(end_month) == 7:
        ey, em = int(end_month[:4]), int(end_month[5:7])
    else:
        ey, em = today.year, today.month

    # Determine start_month — default to 3 months back from end_month
    if start_month and len(start_month) == 7:
        sy, sm = int(start_month[:4]), int(start_month[5:7])
    else:
        # 3 months including end_month → go back 2 more
        sm_val = em - 2
        sy = ey
        if sm_val <= 0:
            sm_val += 12
            sy -= 1
        sy, sm = sy, sm_val

    # Build month list
    months = []
    cursor_y, cursor_m = sy, sm
    while (cursor_y, cursor_m) <= (ey, em):
        months.append(f"{cursor_y}-{str(cursor_m).zfill(2)}")
        cursor_m += 1
        if cursor_m > 12:
            cursor_m = 1
            cursor_y += 1

    if len(months) > 3:
        months = months[-3:]

    _MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    month_labels = [f"{_MONTH_NAMES[int(m[5:7])-1]} {m[:4]}" for m in months]

    # Scan all PDFs
    all_files = []
    for folder, dir_path in _PDF_FOLDERS.items():
        if not dir_path.is_dir():
            continue
        for f in dir_path.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".pdf":
                all_files.append({"filename": f.name, "folder": folder})

    # Parse and group
    entity_map: dict[str, dict] = {}  # key → {label, months: {month_key: [files]}}
    for f in all_files:
        parsed = _parse_pdf_entity(f["filename"])
        if not parsed:
            continue
        ek = parsed["entity_key"]
        mk = parsed["month_key"]
        if mk not in months:
            continue
        if ek not in entity_map:
            entity_map[ek] = {"label": parsed["entity_label"], "months": {}}
        entity_map[ek]["months"].setdefault(mk, []).append({
            "filename": f["filename"],
            "info": parsed["info"],
            "folder": f["folder"],
        })

    # Sort entities alphabetically by label
    entities = []
    for ek in sorted(entity_map, key=lambda k: entity_map[k]["label"]):
        ent = entity_map[ek]
        month_data = {}
        for m in months:
            files = ent["months"].get(m)
            month_data[m] = files if files else None
        entities.append({"key": ek, "label": ent["label"], "months": month_data})

    return {"months": months, "month_labels": month_labels, "entities": entities}


# ── AI AMA — natural-language transaction filter ──────────────────────────────

_AI_QUERY_SYSTEM = """You are a transaction filter assistant for a personal finance app. Given a natural language query, return ONLY a JSON object.

RULES:
- The `category` field already assigned to each transaction is the authoritative source — rely on it, not the amount or description.
- A transfer from a family member is NOT income, even if the amount is large.
- "Opening Balance", "Saldo Awal", and similar entries are Adjustments — NOT income.
- SYSTEM categories (Transfer, Adjustment, Cash Withdrawal, etc.) are NEVER income and NEVER expenses. The DATA CONTEXT block below lists them exactly.
- income_only: true is MANDATORY whenever the user asks about income, earnings, salary, or money received.
- expense_only: true is MANDATORY whenever the user asks about spending, expenses, or costs.
- "biggest income" → sort: "amount_desc", income_only: true
- "biggest spending" or "biggest expense" → sort: "amount_asc", expense_only: true
- Owners in this dataset: "Gandrik" or "Helen"

Return a JSON object with any applicable fields:
- year: integer (e.g. 2026)
- month: integer 1-12
- owner: "Gandrik" or "Helen"
- category: exact category name (only if targeting one specific category)
- q: string (text search in description/merchant)
- sort: "amount_asc" (biggest expenses first) | "amount_desc" (highest income first) | "date_asc" | "date_desc"
- limit: integer (max rows to return)
- income_only: true — keep ONLY income categories; exclude all SYSTEM categories
- expense_only: true — keep ONLY expense categories; exclude all SYSTEM categories

Return only valid JSON with no explanation."""


class _AiQueryRequest(BaseModel):
    query: str


@app.post("/api/ai/query", dependencies=[Depends(require_api_key)])
async def ai_query(body: _AiQueryRequest):
    """Translate a natural-language query into transaction filter criteria via Ollama."""
    import asyncio as _asyncio_local
    import json as _json
    from finance.ollama_utils import ollama_generate

    query = body.query.strip()
    if not query:
        raise HTTPException(400, "query must not be empty")

    # Build live category context from SQLite so the model knows exact taxonomy
    _INCOME_SUBCATS = {
        "Earned Income", "Investment Income", "Interest Income",
        "Capital Gains", "Passive Income", "Other Income",
    }
    try:
        with get_conn(_db_path) as conn:
            cat_rows = conn.execute(
                "SELECT category, category_group, subcategory FROM categories ORDER BY sort_order"
            ).fetchall()
        income_cats, system_cats, expense_cats = [], [], []
        for row in cat_rows:
            cat = row["category"]
            sub = row["subcategory"] or ""
            grp = row["category_group"] or ""
            if sub in _INCOME_SUBCATS:
                income_cats.append(cat)
            elif grp == "System / Tracking":
                system_cats.append(cat)
            else:
                expense_cats.append(cat)
        category_context = (
            "DATA CONTEXT — categories in this database:\n"
            f"INCOME (set income_only: true): {', '.join(income_cats) or 'Earned Income, Investment Income, Interest Income'}\n"
            f"SYSTEM — NOT income or expense (always excluded by income_only/expense_only): "
            f"{', '.join(system_cats) or 'Transfer, Adjustment, Ignored, Cash Withdrawal'}\n"
            f"EXPENSE (set expense_only: true): {', '.join(expense_cats[:20])}"
        )
    except Exception:
        category_context = ""

    system_block = _AI_QUERY_SYSTEM
    if category_context:
        system_block = f"{_AI_QUERY_SYSTEM}\n\n{category_context}"

    prompt = f"<system>\n{system_block}\n</system>\n\nQuery: {query}"

    try:
        data = await _asyncio_local.to_thread(
            ollama_generate,
            _ollama_cfg.host,
            _ollama_cfg.model,
            prompt,
            _ollama_cfg.timeout_seconds,
            format_json=True,
            num_predict=150,
        )
    except Exception as e:
        raise HTTPException(502, f"Ollama unavailable: {e}")

    raw = data.get("response", "")
    try:
        parsed = _json.loads(raw)
    except Exception:
        raise HTTPException(502, f"Ollama returned non-JSON: {raw[:200]}")

    known_keys = {"year", "month", "owner", "category", "q", "sort", "limit", "income_only", "expense_only"}
    filtered = {k: v for k, v in parsed.items() if k in known_keys and v is not None}
    if not filtered:
        raise HTTPException(422, f"No usable filters in response: {raw[:200]}")

    return filtered


# ── PWA static files (must be last — mounted after all /api/* routes) ─────────
# Serves pwa/dist/ at "/" so the dashboard is accessible at the same origin.
# In dev: run `npm run dev` in pwa/ instead (uses Vite proxy to :8090).
import pathlib as _pathlib
from starlette.responses import FileResponse as _FileResponse

_pwa_dist = _pathlib.Path(__file__).parent.parent / "pwa" / "dist"
if _pwa_dist.is_dir():
    _index_html = str(_pwa_dist / "index.html")

    # Mount static files first (JS, CSS, icons, manifest, service worker).
    # html=True makes "/" serve index.html.
    app.mount("/assets", StaticFiles(directory=str(_pwa_dist / "assets")), name="pwa-assets")
    if (_pwa_dist / "icons").is_dir():
        app.mount("/icons", StaticFiles(directory=str(_pwa_dist / "icons")), name="pwa-icons")

    # Serve root-level PWA files (SW, manifest) that must live at "/"
    app.mount("/pwa-root", StaticFiles(directory=str(_pwa_dist)), name="pwa-root")

    @app.get("/manifest.webmanifest")
    async def _pwa_manifest():
        return _FileResponse(str(_pwa_dist / "manifest.webmanifest"))

    @app.get("/registerSW.js")
    async def _pwa_register_sw():
        return _FileResponse(str(_pwa_dist / "registerSW.js"))

    @app.get("/sw.js")
    async def _pwa_sw():
        resp = _FileResponse(str(_pwa_dist / "sw.js"), media_type="application/javascript")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    # Workbox runtime (filename includes a build hash)
    for _wb in _pwa_dist.glob("workbox-*.js"):
        def _make_wb(fp=str(_wb), name=_wb.name):
            async def handler():
                return _FileResponse(fp, media_type="application/javascript")
            handler.__name__ = f"_wb_{name.replace('.', '_').replace('-', '_')}"
            return handler
        app.get(f"/{_wb.name}")(_make_wb())

    # SPA catch-all: Vue Router uses HTML5 history mode, so paths like
    # /transactions, /dashboard, /settings must all return index.html.
    # This MUST be the very last route — after all /api/* and static mounts.
    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        resp = _FileResponse(_index_html, media_type="text/html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    log.info("PWA static files served from %s", _pwa_dist)
