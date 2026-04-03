"""
Stage 2-A — Google Sheets → SQLite sync engine.

Usage
─────
  python3 -m finance.sync           # full sync (replaces all DB data)
  python3 -m finance.sync --status  # show last sync time and counts, then exit
  python3 -m finance.sync -v        # verbose / debug logging

What it does
────────────
  1. Reads all rows from 4 Google Sheets tabs:
       Transactions, Merchant Aliases, Categories, Currency Codes
  2. Replaces all rows in the local SQLite read cache (data/finance.db)
       within a single transaction — the DB is never in a partial state
  3. Appends a row to sync_log with counts and duration

The DB is a pure cache of what's in Sheets — delete data/finance.db and
re-run this script to start fresh at any time.

The FastAPI server calls finance.sync.sync() after a successful import, and
exposes POST /api/sync so the PWA can trigger it manually.
"""
from __future__ import annotations
import argparse
import logging
import sys
import time
from datetime import datetime

from finance.config import load_config, get_finance_config, get_sheets_config
from finance.db import open_db
from finance.sheets import SheetsClient
from finance.categorizer import migrate_category

log = logging.getLogger(__name__)


# ── Core sync function ────────────────────────────────────────────────────────

def sync(db_path: str, sheets_client: SheetsClient) -> dict:
    """
    Pull all data from Google Sheets into the SQLite read cache.

    All four tables are replaced atomically inside one SQLite transaction.

    Returns a stats dict::

        {
            "transactions_count": int,
            "aliases_count":      int,
            "categories_count":   int,
            "currencies_count":   int,
            "duration_s":         float,
            "synced_at":          str,   # YYYY-MM-DD HH:MM:SS
        }
    """
    t0  = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log.info("Opening SQLite DB: %s", db_path)
    conn = open_db(db_path)

    # ── Pull from Sheets ──────────────────────────────────────────────────────
    log.info("Reading Transactions tab …")
    tx_rows    = _read_transactions(sheets_client)
    log.info("  %d transaction rows", len(tx_rows))

    log.info("Reading Merchant Aliases tab …")
    alias_rows = _read_aliases(sheets_client)
    log.info("  %d alias rows", len(alias_rows))

    log.info("Reading Categories tab …")
    cat_rows   = _read_categories(sheets_client)
    log.info("  %d category rows", len(cat_rows))

    log.info("Reading Currency Codes tab …")
    cur_rows   = _read_currencies(sheets_client)
    log.info("  %d currency rows", len(cur_rows))

    log.info("Reading Category Overrides tab …")
    overrides  = sheets_client.read_overrides()
    log.info("  %d override rows", len(overrides))

    # ── Write to SQLite (single atomic transaction) ───────────────────────────
    # Deduplicate by hash — keep first occurrence (Sheets order = import order)
    seen_hashes: set[str] = set()
    deduped_tx: list[dict] = []
    for r in tx_rows:
        if r["hash"] not in seen_hashes:
            seen_hashes.add(r["hash"])
            deduped_tx.append(r)
    if len(deduped_tx) < len(tx_rows):
        log.warning(
            "Deduplicated %d → %d transaction rows (duplicate hashes in Sheets).",
            len(tx_rows), len(deduped_tx),
        )
    tx_rows = deduped_tx

    # Migrate legacy category names to new taxonomy
    migrated = 0
    for r in tx_rows:
        old_cat = r.get("category")
        new_cat = migrate_category(old_cat)
        if new_cat != old_cat:
            r["category"] = new_cat
            migrated += 1
    if migrated:
        log.info("Migrated %d transaction(s) from legacy category names.", migrated)

    # Apply category overrides — these take priority over auto-categorised values
    if overrides:
        applied = 0
        for r in tx_rows:
            ov = overrides.get(r["hash"])
            if ov:
                r["category"] = ov["category"]
                if ov.get("notes"):
                    r["notes"] = ov["notes"]
                applied += 1
        if applied:
            log.info("Applied %d category override(s).", applied)

    log.info("Writing to SQLite …")
    with conn:
        conn.execute("DELETE FROM transactions")
        if tx_rows:
            conn.executemany(
                """
                INSERT INTO transactions
                    (date, amount, original_currency, original_amount,
                     exchange_rate, raw_description, merchant, category,
                     institution, account, owner, notes, hash,
                     import_date, import_file, synced_at)
                VALUES
                    (:date, :amount, :original_currency, :original_amount,
                     :exchange_rate, :raw_description, :merchant, :category,
                     :institution, :account, :owner, :notes, :hash,
                     :import_date, :import_file, :synced_at)
                """,
                [{**r, "synced_at": now} for r in tx_rows],
            )

        conn.execute("DELETE FROM merchant_aliases")
        if alias_rows:
            conn.executemany(
                """
                INSERT INTO merchant_aliases
                    (merchant, alias, category, match_type, added_date,
                     owner_filter, account_filter, synced_at)
                VALUES
                    (:merchant, :alias, :category, :match_type, :added_date,
                     :owner_filter, :account_filter, :synced_at)
                """,
                [{**r, "synced_at": now} for r in alias_rows],
            )

        conn.execute("DELETE FROM categories")
        if cat_rows:
            conn.executemany(
                """
                INSERT INTO categories
                    (category, icon, sort_order, is_recurring, monthly_budget,
                     category_group, subcategory, synced_at)
                VALUES
                    (:category, :icon, :sort_order, :is_recurring, :monthly_budget,
                     :category_group, :subcategory, :synced_at)
                """,
                [{**r, "synced_at": now} for r in cat_rows],
            )

        conn.execute("DELETE FROM currency_codes")
        if cur_rows:
            conn.executemany(
                """
                INSERT INTO currency_codes
                    (currency_code, currency_name, symbol, flag_emoji,
                     country_hints, decimal_places, synced_at)
                VALUES
                    (:currency_code, :currency_name, :symbol, :flag_emoji,
                     :country_hints, :decimal_places, :synced_at)
                """,
                [{**r, "synced_at": now} for r in cur_rows],
            )

        duration = round(time.time() - t0, 2)
        conn.execute(
            """
            INSERT INTO sync_log
                (synced_at, transactions_count, aliases_count,
                 categories_count, currencies_count, duration_s)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, len(tx_rows), len(alias_rows),
             len(cat_rows), len(cur_rows), duration),
        )

    conn.close()
    log.info("Sync complete in %.2fs.", duration)

    return {
        "transactions_count": len(tx_rows),
        "aliases_count":      len(alias_rows),
        "categories_count":   len(cat_rows),
        "currencies_count":   len(cur_rows),
        "duration_s":         duration,
        "synced_at":          now,
    }


# ── Sheets tab readers ────────────────────────────────────────────────────────

def _read_transactions(client: SheetsClient) -> list[dict]:
    """Read Transactions tab (columns A–O) → list of row dicts."""
    rows = client._get(f"{client.cfg.transactions_tab}!A:O")
    if len(rows) < 2:
        return []
    # Column order matches TRANSACTIONS_HEADERS in sheets.py:
    # date(0) amount(1) original_currency(2) original_amount(3) exchange_rate(4)
    # raw_description(5) merchant(6) category(7) institution(8) account(9)
    # owner(10) notes(11) hash(12) import_date(13) import_file(14)
    result = []
    for row in rows[1:]:
        r = list(row) + [""] * (15 - len(row))
        hash_ = (r[12] or "").strip()
        if not hash_:
            continue  # malformed row — skip
        try:
            amount = float(r[1]) if r[1] != "" else 0.0
        except (ValueError, TypeError):
            amount = 0.0
        result.append({
            "date":              (r[0]  or "").strip(),
            "amount":            amount,
            "original_currency": (r[2]  or "").strip() or None,
            "original_amount":   _float_or_none(r[3]),
            "exchange_rate":     _float_or_none(r[4]),
            "raw_description":   (r[5]  or "").strip(),
            "merchant":          (r[6]  or "").strip() or None,
            "category":          (r[7]  or "").strip() or None,
            "institution":       (r[8]  or "").strip(),
            "account":           (r[9]  or "").strip(),
            "owner":             (r[10] or "").strip(),
            "notes":             (r[11] or "").strip(),
            "hash":              hash_,
            "import_date":       (r[13] or "").strip(),
            "import_file":       (r[14] or "").strip(),
        })
    return result


def _read_aliases(client: SheetsClient) -> list[dict]:
    """Read Merchant Aliases tab (A–G) → list of row dicts."""
    rows = client._get(f"{client.cfg.aliases_tab}!A:G")
    if len(rows) < 2:
        return []
    result = []
    for row in rows[1:]:
        r = list(row) + [""] * (7 - len(row))
        if not r[0] and not r[1]:
            continue
        result.append({
            "merchant":       (r[0] or "").strip(),
            "alias":          (r[1] or "").strip(),
            "category":       (r[2] or "").strip() or None,
            "match_type":     (r[3] or "exact").strip(),
            "added_date":     (r[4] or "").strip(),
            "owner_filter":   (r[5] or "").strip(),
            "account_filter": (r[6] or "").strip(),
        })
    return result


def _read_categories(client: SheetsClient) -> list[dict]:
    """Read Categories tab (A–G) → list of row dicts."""
    rows = client._get(f"{client.cfg.categories_tab}!A:G")
    if len(rows) < 2:
        return []
    result = []
    for row in rows[1:]:
        r = list(row) + [""] * (7 - len(row))
        if not r[0]:
            continue
        try:
            sort_order = int(r[2]) if r[2] else 99
        except (ValueError, TypeError):
            sort_order = 99
        result.append({
            "category":       (r[0] or "").strip(),
            "icon":           (r[1] or "").strip(),
            "sort_order":     sort_order,
            "is_recurring":   1 if str(r[3]).upper() == "TRUE" else 0,
            "monthly_budget": _float_or_none(r[4]),
            "category_group": (r[5] or "").strip(),
            "subcategory":    (r[6] or "").strip(),
        })
    return result


def _read_currencies(client: SheetsClient) -> list[dict]:
    """Read Currency Codes tab (A–F) → list of row dicts."""
    rows = client._get(f"{client.cfg.currency_tab}!A:F")
    if len(rows) < 2:
        return []
    result = []
    for row in rows[1:]:
        r = list(row) + [""] * (6 - len(row))
        if not r[0]:
            continue
        try:
            decimal_places = int(r[5]) if r[5] != "" else 2
        except (ValueError, TypeError):
            decimal_places = 2
        result.append({
            "currency_code": (r[0] or "").strip().upper(),
            "currency_name": (r[1] or "").strip(),
            "symbol":        (r[2] or "").strip(),
            "flag_emoji":    (r[3] or "").strip(),
            "country_hints": (r[4] or "").strip(),
            "decimal_places": decimal_places,
        })
    return result


def _float_or_none(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync Google Sheets → SQLite read cache (data/finance.db).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m finance.sync             # full sync
  python3 -m finance.sync --status    # last sync info
  python3 -m finance.sync -v          # verbose output
        """,
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show last sync time and row counts, then exit",
    )
    parser.add_argument(
        "--config", metavar="PATH",
        help="Path to settings.toml (default: config/settings.toml)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    cfg         = load_config(args.config)
    finance_cfg = get_finance_config(cfg)
    sheets_cfg  = get_sheets_config(cfg)

    if args.status:
        conn = open_db(finance_cfg.sqlite_db)
        row = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            print(
                f"\nLast sync:    {row['synced_at']}\n"
                f"Transactions: {row['transactions_count']}\n"
                f"Aliases:      {row['aliases_count']}\n"
                f"Categories:   {row['categories_count']}\n"
                f"Currencies:   {row['currencies_count']}\n"
                f"Duration:     {row['duration_s']}s\n"
            )
        else:
            print("\nNo sync has been run yet. Run: python3 -m finance.sync\n")
        return

    client = SheetsClient(sheets_cfg)
    stats  = sync(finance_cfg.sqlite_db, client)

    print(
        f"\n✅  Sync complete\n"
        f"   Transactions:  {stats['transactions_count']}\n"
        f"   Aliases:       {stats['aliases_count']}\n"
        f"   Categories:    {stats['categories_count']}\n"
        f"   Currencies:    {stats['currencies_count']}\n"
        f"   Duration:      {stats['duration_s']}s\n"
        f"   Synced at:     {stats['synced_at']}\n"
    )


if __name__ == "__main__":
    main()
