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
  POST  /api/alias                  {hash, alias, merchant, category, match_type, apply_to_similar}
  PATCH /api/transaction/{hash}/category  {category, notes?}
  POST  /api/sync
  POST  /api/import                 {dry_run?, overwrite?}

All read endpoints query SQLite only (data/finance.db).
Write endpoints (alias, sync, import) also touch Google Sheets.

Start with:  python3 -m finance.server
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, NamedTuple, Optional

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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

_db_path: str              = _finance_cfg.sqlite_db
_sheets: SheetsClient | None = None  # lazy — created on first write request


def _get_sheets() -> SheetsClient:
    global _sheets
    if _sheets is None:
        _sheets = SheetsClient(_sheets_cfg)
    return _sheets


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


@app.middleware("http")
async def add_no_store_for_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


# ── Auth ─────────────────────────────────────────────────────────────────────

def require_api_key(x_api_key: str = Header(default="")):
    """Validate X-Api-Key header against FINANCE_API_KEY env var.

    Set FINANCE_API_KEY in the environment before starting the server.
    If the env var is unset the server refuses all protected requests so
    deployments without a key fail loudly rather than silently open.
    """
    expected = os.environ.get("FINANCE_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="FINANCE_API_KEY not configured")
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


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


class CategoryOverrideRequest(BaseModel):
    category:     str
    notes:        str  = ""
    update_alias: bool = True   # also update Merchant Aliases tab so future imports auto-categorise


# ── /api/health ───────────────────────────────────────────────────────────────

@app.get("/api/health", dependencies=[Depends(require_api_key)])
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


# ── /api/transactions ─────────────────────────────────────────────────────────

@app.get("/api/transactions", dependencies=[Depends(require_api_key)])
def get_transactions(
    year:     Optional[int] = Query(None, description="Filter by calendar year"),
    month:    Optional[int] = Query(None, ge=1, le=12, description="Filter by month (1–12)"),
    owner:    Optional[str] = Query(None, description="Owner name, or omit for all"),
    category: Optional[str] = Query(None, description="Exact category match"),
    q:        Optional[str] = Query(None, description="Search raw_description and merchant"),
    limit:    int           = Query(100, ge=1, le=1000),
    offset:   int           = Query(0, ge=0),
):
    qp = _tx_where(year, month, owner, category, q)
    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transactions{qp.clause}", qp.params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM transactions{qp.clause} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
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
    qp = _tx_where(year, month, owner, category=None, q=None)
    if qp.clause:
        extra_clause = qp.clause + " AND original_currency IS NOT NULL"
    else:
        extra_clause = " WHERE original_currency IS NOT NULL"
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM transactions{extra_clause} ORDER BY date DESC",
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
    q:        Optional[str],
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
    if owner and owner.lower() not in ("all", "both", ""):
        conditions.append("owner = ?")
        params.append(owner)
    if category:
        conditions.append("category = ?")
        params.append(category)
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
            "FROM transactions WHERE date != '' ORDER BY yr DESC"
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
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END)      AS income,
                SUM(CASE WHEN amount < 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END)      AS expense,
                COUNT(*)                               AS tx_count
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
                SUM(CASE WHEN amount > 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END) AS expense,
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


@app.get("/api/summary/{year}/{month}", dependencies=[Depends(require_api_key)])
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

        # ── Per-owner totals (Internal Transfer excluded from income/expense) ──
        owner_rows = conn.execute(
            """
            SELECT
                owner,
                SUM(CASE WHEN amount > 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END) AS expense,
                COUNT(*) AS tx_count
            FROM transactions
            WHERE strftime('%Y-%m', date) = ?
            GROUP BY owner
            ORDER BY owner
            """,
            (period,),
        ).fetchall()

        # ── Grand totals (Internal Transfer + Opening Balance excluded) ────
        totals = conn.execute(
            """
            SELECT
                SUM(CASE WHEN amount > 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END) AS income,
                SUM(CASE WHEN amount < 0
                          AND (category IS NULL OR category NOT IN ('Transfer','Adjustment'))
                          THEN amount ELSE 0 END) AS expense,
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

    TRANSFER_CATS = {"Transfer", "Adjustment"}  # excluded from income/expense % calculation
    by_category = []
    for r in cat_rows:
        amt  = r["total_amount"] or 0.0
        cat_name = r["category"] or "Uncategorised"
        pct = (
            round(abs(amt) / abs(total_expense) * 100, 1)
            if total_expense and amt < 0 and cat_name not in TRANSFER_CATS
            else 0.0
        )
        by_category.append({
            "category":       cat_name,
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

@app.get("/api/review-queue", dependencies=[Depends(require_api_key)])
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

@app.post("/api/alias", dependencies=[Depends(require_api_key)])
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
    _get_sheets().append_alias(
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


# ── /api/transaction/{hash}/category ──────────────────────────────────────────

@app.patch("/api/transaction/{tx_hash}/category", dependencies=[Depends(require_api_key)])
def patch_transaction_category(tx_hash: str, req: CategoryOverrideRequest):
    """
    Manually override the category for a specific transaction.

    1. Writes to the Category Overrides tab in Google Sheets (survives re-imports)
    2. Updates the transaction in SQLite immediately (no sync needed)
    3. Returns the updated transaction row
    """
    # Validate category exists
    with _db() as conn:
        cat_row = conn.execute(
            "SELECT category FROM categories WHERE category = ?", (req.category,)
        ).fetchone()
        if not cat_row:
            raise HTTPException(400, f"Unknown category: {req.category!r}")

        # Verify transaction exists
        tx = conn.execute(
            "SELECT * FROM transactions WHERE hash = ?", (tx_hash,)
        ).fetchone()
        if not tx:
            raise HTTPException(404, f"Transaction not found: {tx_hash}")

    # Write override to Google Sheets (persistent, survives re-sync)
    _get_sheets().write_override(tx_hash, req.category, req.notes)

    # Update SQLite immediately so the dashboard reflects it now
    also_updated = 0
    with _db() as conn:
        conn.execute(
            "UPDATE transactions SET category = ? WHERE hash = ?",
            (req.category, tx_hash),
        )
        if req.notes:
            conn.execute(
                "UPDATE transactions SET notes = ? WHERE hash = ?",
                (req.notes, tx_hash),
            )

        raw_desc = tx["raw_description"]
        merchant = tx["merchant"]

        # Also update the Merchant Aliases tab so future imports auto-categorise
        if req.update_alias and raw_desc:
            # Check if an alias already exists for this raw_description
            existing = conn.execute(
                "SELECT category FROM merchant_aliases WHERE alias = ?",
                (raw_desc,),
            ).fetchone()
            if existing and existing["category"] != req.category:
                # Update the existing alias in Sheets
                _get_sheets().update_alias_category(raw_desc, req.category)
                conn.execute(
                    "UPDATE merchant_aliases SET category = ? WHERE alias = ?",
                    (req.category, raw_desc),
                )
                log.info("Updated alias: %s → %s", raw_desc[:40], req.category)
            elif not existing:
                # Create new alias
                alias_merchant = merchant or raw_desc
                _get_sheets().append_alias(
                    merchant=alias_merchant,
                    alias=raw_desc,
                    category=req.category,
                    match_type="exact",
                )
                conn.execute(
                    "INSERT OR IGNORE INTO merchant_aliases (merchant, alias, category, match_type) VALUES (?, ?, ?, ?)",
                    (alias_merchant, raw_desc, req.category, "exact"),
                )
                log.info("New alias: %s → %s [%s]", raw_desc[:40], alias_merchant, req.category)

            # Apply to all other transactions with the same raw_description
            similar = conn.execute(
                """
                SELECT hash FROM transactions
                WHERE raw_description = ?
                  AND hash != ?
                  AND (category IS NULL OR category = '' OR category != ?)
                """,
                (raw_desc, tx_hash, req.category),
            ).fetchall()
            if similar:
                similar_hashes = [r["hash"] for r in similar]
                conn.executemany(
                    "UPDATE transactions SET category = ? WHERE hash = ?",
                    [(req.category, h) for h in similar_hashes],
                )
                also_updated = len(similar_hashes)
                log.info("Applied category to %d similar transactions.", also_updated)

        updated = conn.execute(
            "SELECT * FROM transactions WHERE hash = ?", (tx_hash,)
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
    """Pull all data from Google Sheets into the local SQLite cache."""
    from finance.sync import sync as _sync
    stats = _sync(_db_path, _get_sheets())
    return {"ok": True, **stats}


# ── /api/import ───────────────────────────────────────────────────────────────

@app.post("/api/import", dependencies=[Depends(require_api_key)])
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
        sheets_client=_get_sheets(),
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
    snapshot_date: str
    institution:   str
    account:       str
    account_type:  str   = "savings"
    owner:         str   = ""
    currency:      str   = "IDR"
    balance:       float = 0.0
    balance_idr:   float = 0.0
    exchange_rate: float = 0.0
    notes:         str   = ""


class HoldingUpsertRequest(BaseModel):
    snapshot_date:      str
    asset_class:        str
    asset_name:         str
    isin_or_code:       str   = ""
    institution:        str   = ""
    account:            str   = ""
    owner:              str   = ""
    currency:           str   = "IDR"
    quantity:           float = 0.0
    unit_price:         float = 0.0
    market_value:       float = 0.0
    market_value_idr:   float = 0.0
    cost_basis:         float = 0.0
    cost_basis_idr:     float = 0.0
    unrealised_pnl_idr: float = 0.0
    exchange_rate:      float = 0.0
    maturity_date:        str   = ""
    coupon_rate:          float = 0.0
    last_appraised_date:  str   = ""
    notes:                str   = ""


class LiabilityUpsertRequest(BaseModel):
    snapshot_date:  str
    liability_type: str
    liability_name: str
    institution:    str   = ""
    account:        str   = ""
    owner:          str   = ""
    currency:       str   = "IDR"
    balance:        float = 0.0
    balance_idr:    float = 0.0
    due_date:       str   = ""
    notes:          str   = ""


class SnapshotRequest(BaseModel):
    snapshot_date: str
    notes:         str = ""


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


@app.post("/api/wealth/balances", dependencies=[Depends(require_api_key)])
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


@app.delete("/api/wealth/balances/{balance_id}", dependencies=[Depends(require_api_key)])
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


@app.post("/api/wealth/holdings", dependencies=[Depends(require_api_key)])
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
            ON CONFLICT(snapshot_date, asset_class, asset_name, owner)
            DO UPDATE SET
                asset_group          = excluded.asset_group,
                isin_or_code         = excluded.isin_or_code,
                institution          = excluded.institution,
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
            "WHERE snapshot_date=? AND asset_class=? AND asset_name=? AND owner=?",
            (req.snapshot_date, req.asset_class, req.asset_name, req.owner),
        ).fetchone()
    _sync_holdings_to_sheets()
    _auto_snapshot(req.snapshot_date)
    return {"ok": True, "holding": _row(row)}


class CarryForwardRequest(BaseModel):
    snapshot_date: str


@app.post("/api/wealth/holdings/carry-forward", dependencies=[Depends(require_api_key)])
def carry_forward_holdings(req: CarryForwardRequest):
    """
    For carry-forward asset classes (retirement, real_estate, vehicle, gold, other),
    copy holdings from the most recent prior snapshot_date to req.snapshot_date
    — but only for classes that have ZERO entries on req.snapshot_date.
    Safe to call multiple times (idempotent per class).
    """
    today   = datetime.now().strftime("%Y-%m-%d")
    carried = 0
    with _db() as conn:
        for asset_class in CARRY_FORWARD_CLASSES:
            existing = conn.execute(
                "SELECT COUNT(*) FROM holdings WHERE snapshot_date=? AND asset_class=?",
                (req.snapshot_date, asset_class),
            ).fetchone()[0]
            if existing > 0:
                continue

            prev_rows = conn.execute(
                "SELECT * FROM holdings WHERE asset_class=? AND snapshot_date < ? "
                "ORDER BY snapshot_date DESC",
                (asset_class, req.snapshot_date),
            ).fetchall()
            if not prev_rows:
                continue

            seen: set = set()
            for row in prev_rows:
                key = (row["asset_name"], row["owner"])
                if key in seen:
                    continue
                seen.add(key)
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
                     row["unit_price"], row["market_value"], row["market_value_idr"],
                     row["cost_basis"], row["cost_basis_idr"], row["unrealised_pnl_idr"],
                     row["exchange_rate"], row["maturity_date"], row["coupon_rate"],
                     row["last_appraised_date"], row["notes"], today),
                )
                carried += conn.execute("SELECT changes()").fetchone()[0]

    if carried > 0:
        _sync_holdings_to_sheets()
        _auto_snapshot(req.snapshot_date)

    return {"ok": True, "carried": carried}


@app.delete("/api/wealth/holdings/{holding_id}", dependencies=[Depends(require_api_key)])
def delete_holding(holding_id: int):
    with _db() as conn:
        row = conn.execute(
            "SELECT snapshot_date FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
        snap_date = row["snapshot_date"] if row else None
        conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    _sync_holdings_to_sheets()
    if snap_date:
        _auto_snapshot(snap_date)
    return {"ok": True}


def _sync_holdings_to_sheets():
    """Write all holdings to the Holdings Google Sheet tab (best-effort)."""
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT snapshot_date, asset_class, asset_group, asset_name, institution, "
                "owner, currency, market_value_idr, cost_basis_idr, unrealised_pnl_idr, "
                "last_appraised_date, notes, import_date FROM holdings "
                "ORDER BY snapshot_date DESC, asset_group, asset_class, asset_name"
            ).fetchall()
        sheet_rows = [list(r) for r in rows]
        _get_sheets().write_holdings(sheet_rows)
    except Exception as exc:
        log.warning("Holdings Sheets sync failed (non-fatal): %s", exc)


def _auto_snapshot(snapshot_date: str):
    """Re-aggregate net_worth_snapshot for snapshot_date after any mutation (best-effort)."""
    try:
        create_snapshot(SnapshotRequest(snapshot_date=snapshot_date))
    except Exception as exc:
        log.warning("Auto-snapshot failed for %s (non-fatal): %s", snapshot_date, exc)


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


@app.post("/api/wealth/liabilities", dependencies=[Depends(require_api_key)])
def upsert_liability(req: LiabilityUpsertRequest):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO liabilities
                (snapshot_date, liability_type, liability_name, institution, account,
                 owner, currency, balance, balance_idr, due_date, notes, import_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date, liability_type, liability_name, owner)
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
            "WHERE snapshot_date=? AND liability_type=? AND liability_name=? AND owner=?",
            (req.snapshot_date, req.liability_type, req.liability_name, req.owner),
        ).fetchone()
    _auto_snapshot(req.snapshot_date)
    return {"ok": True, "liability": _row(row)}


@app.delete("/api/wealth/liabilities/{liability_id}", dependencies=[Depends(require_api_key)])
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


@app.post("/api/wealth/snapshot", dependencies=[Depends(require_api_key)])
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
        liab_rows = conn.execute(
            "SELECT liability_type, SUM(balance_idr) AS total "
            "FROM liabilities WHERE snapshot_date=? GROUP BY liability_type",
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

        # MoM: previous snapshot
        prev = conn.execute(
            "SELECT net_worth_idr FROM net_worth_snapshots "
            "WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1",
            (sd,),
        ).fetchone()
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

def _read_bridge_token() -> str:
    """Read the bridge bearer token from the secrets mount (best-effort)."""
    for candidate in [
        _pl.Path("/app/secrets/bridge.token"),
        _pl.Path(os.environ.get("BRIDGE_TOKEN_FILE", "/app/secrets/bridge.token")),
    ]:
        if candidate.exists():
            return candidate.read_text().strip()
    return ""

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
    with _urllib_req.urlopen(req, timeout=120) as r:
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
    List all PDF files found in pdf_inbox and pdf_unlocked under the data directory.
    Returns a list of { folder, filename, size_kb, mtime } objects sorted by mtime desc.
    """
    results = []
    for folder, dir_path in _PDF_FOLDERS.items():
        if not dir_path.is_dir():
            continue
        for f in dir_path.iterdir():
            if f.is_file() and f.suffix.lower() == ".pdf":
                stat = f.stat()
                results.append({
                    "folder":   folder,
                    "filename": f.name,
                    "size_kb":  round(stat.st_size / 1024, 1),
                    "mtime":    stat.st_mtime,
                })
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


class _ProcessLocalReq(BaseModel):
    folder:   str   # "pdf_inbox" or "pdf_unlocked"
    filename: str

@app.post("/api/pdf/process-local")
async def pdf_process_local(req: _ProcessLocalReq, _auth=Depends(require_api_key)):
    """
    Ask the bridge to process a PDF by folder+filename — no byte upload needed.
    Both the bridge (Mac host) and this container share the ./data volume, so the
    bridge can read the file directly from its own configured inbox/unlocked dirs.
    Returns { job_id } — poll /api/pdf/local-status/<job_id> for progress.
    """
    dir_path = _PDF_FOLDERS.get(req.folder)
    if not dir_path:
        raise HTTPException(400, f"Unknown folder '{req.folder}'. Use pdf_inbox or pdf_unlocked.")

    if not (dir_path / req.filename).is_file():
        raise HTTPException(404, f"File not found: {req.folder}/{req.filename}")
    if not req.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are supported.")

    token = _read_bridge_token()

    # Bridge reads the file directly from disk — avoids multipart upload entirely.
    try:
        result = await _asyncio.to_thread(
            _bridge_post_json,
            "/pdf/process-file",
            {"folder": req.folder, "filename": req.filename},
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
        return _FileResponse(str(_pwa_dist / "sw.js"), media_type="application/javascript")

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
        return _FileResponse(_index_html, media_type="text/html")

    log.info("PWA static files served from %s", _pwa_dist)
