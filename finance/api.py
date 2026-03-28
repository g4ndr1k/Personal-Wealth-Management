"""
Stage 2-B — FastAPI backend for the personal finance dashboard.

Endpoints
─────────
  GET  /api/health
  GET  /api/owners
  GET  /api/categories
  GET  /api/transactions           ?year= &month= &owner= &category= &q= &limit= &offset=
  GET  /api/transactions/foreign   ?year= &month= &owner=
  GET  /api/summary/years
  GET  /api/summary/year/{year}
  GET  /api/summary/{year}/{month}
  GET  /api/review-queue           ?limit=
  POST /api/alias                  {hash, alias, merchant, category, match_type, apply_to_similar}
  POST /api/sync
  POST /api/import                 {dry_run?, overwrite?}

All read endpoints query SQLite only (data/finance.db).
Write endpoints (alias, sync, import) also touch Google Sheets.

Start with:  python3 -m finance.server
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finance.config import (
    load_config,
    get_finance_config,
    get_fastapi_config,
    get_sheets_config,
    get_ollama_finance_config,
    get_anthropic_finance_config,
)
from finance.db import open_db
from finance.sheets import SheetsClient

log = logging.getLogger(__name__)


# ── Module-level singletons (initialised once at import / startup) ────────────

_cfg           = load_config()
_finance_cfg   = get_finance_config(_cfg)
_sheets_cfg    = get_sheets_config(_cfg)
_fastapi_cfg   = get_fastapi_config(_cfg)
_ollama_cfg    = get_ollama_finance_config(_cfg)
_anthropic_cfg = get_anthropic_finance_config(_cfg)

_db_path: str        = _finance_cfg.sqlite_db
_sheets: SheetsClient = SheetsClient(_sheets_cfg)   # lazy OAuth — no network call yet


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Personal Finance API",
    version="2.1.0",
    description="Stage 2 finance dashboard backend — reads SQLite, writes Google Sheets.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_fastapi_cfg.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _row(row: sqlite3.Row) -> dict:
    return dict(row)


# ── Request / Response models ─────────────────────────────────────────────────

class AliasRequest(BaseModel):
    hash:             str
    alias:            str   # raw_description pattern to match (written to Sheets)
    merchant:         str   # canonical merchant name
    category:         str
    match_type:       str   = "exact"   # "exact" | "regex"
    apply_to_similar: bool  = True      # also update uncategorised rows with same raw_desc


class ImportRequest(BaseModel):
    dry_run:   bool = False
    overwrite: bool = False


# ── /api/health ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    with _db() as conn:
        tx_count  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        sync_row  = conn.execute(
            "SELECT synced_at, transactions_count FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        needs_rev = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE category IS NULL OR category = ''"
        ).fetchone()[0]
    return {
        "status":            "ok",
        "transaction_count": tx_count,
        "needs_review":      needs_rev,
        "last_sync":         sync_row["synced_at"] if sync_row else None,
        "timestamp":         datetime.now().isoformat(),
    }


# ── /api/owners ───────────────────────────────────────────────────────────────

@app.get("/api/owners")
def get_owners():
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT owner FROM transactions "
            "WHERE owner != '' ORDER BY owner"
        ).fetchall()
    return [r[0] for r in rows]


# ── /api/categories ───────────────────────────────────────────────────────────

@app.get("/api/categories")
def get_categories():
    with _db() as conn:
        rows = conn.execute(
            "SELECT category, icon, sort_order, is_recurring, monthly_budget "
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
         "is_recurring": 0, "monthly_budget": None}
        for r in rows
    ]


# ── /api/transactions ─────────────────────────────────────────────────────────

@app.get("/api/transactions")
def get_transactions(
    year:     Optional[int] = Query(None, description="Filter by calendar year"),
    month:    Optional[int] = Query(None, ge=1, le=12, description="Filter by month (1–12)"),
    owner:    Optional[str] = Query(None, description="Owner name, or omit for all"),
    category: Optional[str] = Query(None, description="Exact category match"),
    q:        Optional[str] = Query(None, description="Search raw_description and merchant"),
    limit:    int           = Query(100, ge=1, le=1000),
    offset:   int           = Query(0, ge=0),
):
    where, params = _tx_where(year, month, owner, category, q)
    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transactions{where}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM transactions{where} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {
        "total":        total,
        "limit":        limit,
        "offset":       offset,
        "transactions": [_row(r) for r in rows],
    }


@app.get("/api/transactions/foreign")
def get_foreign_transactions(
    year:  Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    owner: Optional[str] = Query(None),
):
    """Transactions that were billed in a foreign currency."""
    where, params = _tx_where(year, month, owner, category=None, q=None)
    if where:
        where += " AND original_currency IS NOT NULL"
    else:
        where = " WHERE original_currency IS NOT NULL"
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM transactions{where} ORDER BY date DESC",
            params,
        ).fetchall()
    return [_row(r) for r in rows]


def _tx_where(
    year:     Optional[int],
    month:    Optional[int],
    owner:    Optional[str],
    category: Optional[str],
    q:        Optional[str],
) -> tuple[str, list]:
    """Build a WHERE clause + params list for transaction queries."""
    conditions: list[str] = []
    params:     list      = []

    if year:
        conditions.append("strftime('%Y', date) = ?")
        params.append(str(year))
    if month:
        conditions.append("strftime('%m', date) = ?")
        params.append(f"{month:02d}")
    if owner and owner.lower() not in ("all", "both", ""):
        conditions.append("owner = ?")
        params.append(owner)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if q:
        conditions.append("(raw_description LIKE ? OR merchant LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


# ── /api/summary ──────────────────────────────────────────────────────────────

@app.get("/api/summary/years")
def get_available_years():
    """Return a list of calendar years that have transaction data."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT strftime('%Y', date) AS yr "
            "FROM transactions WHERE date != '' ORDER BY yr DESC"
        ).fetchall()
    return [int(r[0]) for r in rows if r[0]]


@app.get("/api/summary/year/{year}")
def get_annual_summary(year: int):
    """Month-by-month income / expense breakdown for a full year."""
    with _db() as conn:
        month_rows = conn.execute(
            """
            SELECT
                CAST(strftime('%m', date) AS INTEGER)            AS month,
                SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS expense,
                COUNT(*)                                          AS tx_count
            FROM transactions
            WHERE strftime('%Y', date) = ?
            GROUP BY month
            ORDER BY month
            """,
            (str(year),),
        ).fetchall()

        totals = conn.execute(
            """
            SELECT
                SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS expense,
                COUNT(*) AS tx_count
            FROM transactions
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


@app.get("/api/summary/{year}/{month}")
def get_monthly_summary(year: int, month: int):
    """
    Full breakdown for one calendar month.

    Returns totals, per-category breakdown (with % of expense), and
    per-owner split.  Also includes needs_review count for the month.
    """
    if not (1 <= month <= 12):
        raise HTTPException(400, f"month must be 1–12, got {month}")

    period = f"{year}-{month:02d}"

    with _db() as conn:
        # ── Category breakdown ────────────────────────────────────────────────
        cat_rows = conn.execute(
            """
            SELECT
                COALESCE(t.category, '') AS category,
                c.icon,
                c.sort_order,
                SUM(t.amount)            AS total_amount,
                COUNT(*)                 AS tx_count
            FROM transactions t
            LEFT JOIN categories c ON t.category = c.category
            WHERE strftime('%Y-%m', t.date) = ?
            GROUP BY t.category
            ORDER BY c.sort_order NULLS LAST, t.category
            """,
            (period,),
        ).fetchall()

        # ── Per-owner totals ──────────────────────────────────────────────────
        owner_rows = conn.execute(
            """
            SELECT
                owner,
                SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS expense,
                COUNT(*) AS tx_count
            FROM transactions
            WHERE strftime('%Y-%m', date) = ?
            GROUP BY owner
            ORDER BY owner
            """,
            (period,),
        ).fetchall()

        # ── Grand totals ──────────────────────────────────────────────────────
        totals = conn.execute(
            """
            SELECT
                SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS expense,
                COUNT(*) AS tx_count
            FROM transactions
            WHERE strftime('%Y-%m', date) = ?
            """,
            (period,),
        ).fetchone()

        needs_review = conn.execute(
            """
            SELECT COUNT(*) FROM transactions
            WHERE strftime('%Y-%m', date) = ?
              AND (category IS NULL OR category = '')
            """,
            (period,),
        ).fetchone()[0]

    total_income  = totals["income"]  or 0.0
    total_expense = totals["expense"] or 0.0

    by_category = []
    for r in cat_rows:
        amt = r["total_amount"] or 0.0
        pct = (
            round(abs(amt) / abs(total_expense) * 100, 1)
            if total_expense and amt < 0
            else 0.0
        )
        by_category.append({
            "category":       r["category"] or "Uncategorised",
            "icon":           r["icon"]       or "",
            "sort_order":     r["sort_order"] or 99,
            "amount":         round(amt, 2),
            "count":          r["tx_count"],
            "pct_of_expense": pct,
        })

    by_owner = []
    for r in owner_rows:
        inc = r["income"]  or 0.0
        exp = r["expense"] or 0.0
        by_owner.append({
            "owner":             r["owner"],
            "income":            round(inc, 2),
            "expense":           round(exp, 2),
            "net":               round(inc + exp, 2),
            "transaction_count": r["tx_count"],
        })

    return {
        "year":              year,
        "month":             month,
        "period":            period,
        "total_income":      round(total_income, 2),
        "total_expense":     round(total_expense, 2),
        "net":               round(total_income + total_expense, 2),
        "transaction_count": totals["tx_count"] or 0,
        "needs_review":      needs_review,
        "by_category":       by_category,
        "by_owner":          by_owner,
    }


# ── /api/review-queue ─────────────────────────────────────────────────────────

@app.get("/api/review-queue")
def get_review_queue(limit: int = Query(50, ge=1, le=200)):
    """
    Return transactions that have no category assigned (Layer 4 — needs review).

    The PWA review queue uses this to show pending transactions to the user.
    After the user confirms a merchant/category, call POST /api/alias.
    """
    with _db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE category IS NULL OR category = ''"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM transactions
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


# ── /api/alias ────────────────────────────────────────────────────────────────

@app.post("/api/alias")
def post_alias(req: AliasRequest):
    """
    Confirm a merchant alias from the review queue.

    1. Writes the alias to the Merchant Aliases tab in Google Sheets
    2. Updates the specific transaction in SQLite (by hash)
    3. If apply_to_similar=true, also updates uncategorised transactions
       that share the exact same raw_description

    The next sync will persist these local SQLite edits back from Sheets
    (the importer will use the new alias on future imports too).
    """
    # 1. Write to Google Sheets
    _sheets.append_alias(
        merchant=req.merchant,
        alias=req.alias,
        category=req.category,
        match_type=req.match_type,
    )
    log.info("Alias saved: %s → %s  [%s]", req.alias, req.merchant, req.category)

    updated_hashes: list[str] = []

    with _db() as conn:
        # 2. Update the target transaction
        conn.execute(
            "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
            (req.merchant, req.category, req.hash),
        )
        updated_hashes.append(req.hash)

        # 3. Apply to similar uncategorised transactions (exact match only)
        if req.apply_to_similar and req.match_type == "exact":
            target = conn.execute(
                "SELECT raw_description FROM transactions WHERE hash = ?",
                (req.hash,),
            ).fetchone()
            if target:
                similar = conn.execute(
                    """
                    SELECT hash FROM transactions
                    WHERE raw_description = ?
                      AND hash != ?
                      AND (category IS NULL OR category = '')
                    """,
                    (target["raw_description"], req.hash),
                ).fetchall()
                if similar:
                    similar_hashes = [r["hash"] for r in similar]
                    conn.executemany(
                        "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
                        [(req.merchant, req.category, h) for h in similar_hashes],
                    )
                    updated_hashes.extend(similar_hashes)
                    log.info(
                        "Applied alias to %d similar uncategorised transactions.",
                        len(similar_hashes),
                    )

        # Return the updated row
        updated = conn.execute(
            "SELECT * FROM transactions WHERE hash = ?", (req.hash,)
        ).fetchone()

    return {
        "ok":            True,
        "updated_count": len(updated_hashes),
        "transaction":   _row(updated) if updated else None,
    }


# ── /api/sync ─────────────────────────────────────────────────────────────────

@app.post("/api/sync")
def post_sync():
    """Pull all data from Google Sheets into the local SQLite cache."""
    from finance.sync import sync as _sync
    stats = _sync(_db_path, _sheets)
    return {"ok": True, **stats}


# ── /api/import ───────────────────────────────────────────────────────────────

@app.post("/api/import")
def post_import(req: ImportRequest = ImportRequest()):
    """
    Trigger the Stage 1 → Sheets importer (finance.importer).

    Reads ALL_TRANSACTIONS.xlsx, skips duplicates (or overwrites with
    --overwrite), categorises new rows, and appends to Sheets.

    After a successful non-dry-run import that adds rows, automatically
    syncs Sheets → SQLite so the dashboard reflects the new data immediately.
    """
    xlsx_path = _finance_cfg.xlsx_input
    if not os.path.exists(xlsx_path):
        raise HTTPException(404, f"XLSX not found: {xlsx_path}")

    from finance.importer import run as _import_run
    from finance.categorizer import Categorizer
    from finance.sync import sync as _sync

    categorizer = Categorizer(
        aliases=[],
        categories=[],
        ollama_host=_ollama_cfg.host,
        ollama_model=_ollama_cfg.model,
        ollama_timeout=_ollama_cfg.timeout_seconds,
        anthropic_api_key=_anthropic_cfg.api_key,
        anthropic_model=_anthropic_cfg.model,
    )

    stats = _import_run(
        xlsx_path=xlsx_path,
        sheets_client=_sheets,
        categorizer=categorizer,
        overwrite=req.overwrite,
        dry_run=req.dry_run,
        import_file_label=os.path.basename(xlsx_path),
    )

    # Auto-sync after a real import that added rows
    if not req.dry_run and stats.get("added", 0) > 0:
        log.info("Auto-syncing after import …")
        sync_stats = _sync(_db_path, _sheets)
        stats["sync"] = sync_stats

    return {"ok": True, **stats}


# ── PWA static files (must be last — mounted after all /api/* routes) ─────────
# Serves pwa/dist/ at "/" so the dashboard is accessible at the same origin.
# In dev: run `npm run dev` in pwa/ instead (uses Vite proxy to :8090).
import pathlib as _pathlib
_pwa_dist = _pathlib.Path(__file__).parent.parent / "pwa" / "dist"
if _pwa_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_pwa_dist), html=True), name="pwa")
    log.info("PWA static files served from %s", _pwa_dist)
