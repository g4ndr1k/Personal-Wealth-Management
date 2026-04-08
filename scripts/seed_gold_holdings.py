"""
scripts/seed_gold_holdings.py — Seed Antam physical gold bar holdings into the
Stage 3 finance.db holdings table.

Inventory (14 bars, 3 weight classes):
  100 gr × 5 bars  KSQ 052, DE 074, OR 023, IMA 022, IMA 021
   50 gr × 5 bars  JP 060, JM 037, JM 038, ID 002, BB 063
   25 gr × 4 bars  BXJ 055, FB 024, BDP 013, ACO 073

Prices are fetched from the fawazahmed0 XAU/IDR API (international spot price
divided by 31.1035 grams/troy-oz), the same source used by bridge/fx_rate.py.
This is NOT Antam's specific sell price — Antam prices are typically 5–15%
above international spot.  You can re-run this script at any time to refresh
prices.

Usage:
    PYTHONPATH=/path/to/agentic-ai python3 scripts/seed_gold_holdings.py
    PYTHONPATH=...                python3 scripts/seed_gold_holdings.py --dry-run
    PYTHONPATH=...                python3 scripts/seed_gold_holdings.py --owner Helen
    PYTHONPATH=...                python3 scripts/seed_gold_holdings.py --from 2026-03

All month-ends from the start month to the current month are processed.
Re-running is idempotent: rows are updated with ON CONFLICT DO UPDATE so prices
are refreshed without creating duplicates.
"""
from __future__ import annotations

import argparse
import calendar
import sqlite3
import sys
import tomllib
from datetime import date as _date, datetime as _dt
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bridge.gold_price import get_gold_price_idr_per_gram, TROY_OZ_TO_GRAMS

# ── Inventory ─────────────────────────────────────────────────────────────────
# (weight_grams, num_bars, isin_or_code, asset_name, certificate_list)
WEIGHT_CLASSES: list[tuple[int, int, str, str, str]] = [
    (
        100, 5,
        "ANTAM-100g",
        "Antam Gold Bar 100gr",
        "KSQ 052, DE 074, OR 023, IMA 022, IMA 021",
    ),
    (
        50, 5,
        "ANTAM-50g",
        "Antam Gold Bar 50gr",
        "JP 060, JM 037, JM 038, ID 002, BB 063",
    ),
    (
        25, 4,
        "ANTAM-25g",
        "Antam Gold Bar 25gr",
        "BXJ 055, FB 024, BDP 013, ACO 073",
    ),
]

DEFAULT_OWNER = "Gandrik"
DEFAULT_START  = (2026, 1)   # January 2026


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_db_path(override: str | None) -> str:
    """Return the finance.db path from settings.toml or the --db override."""
    if override:
        return override
    settings_path = PROJECT_ROOT / "config" / "settings.toml"
    with open(settings_path, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["finance"]["sqlite_db"]


def _month_ends_from(start_year: int, start_month: int) -> list[str]:
    """
    Return a list of month-end dates (YYYY-MM-DD) from the given start month
    up to and including the current calendar month.
    """
    results: list[str] = []
    today = _date.today()
    y, m = start_year, start_month
    while _date(y, m, 1) <= _date(today.year, today.month, 1):
        last_day = calendar.monthrange(y, m)[1]
        results.append(f"{y}-{m:02d}-{last_day:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return results


def _parse_start(from_arg: str | None) -> tuple[int, int]:
    """Parse --from YYYY-MM into (year, month).  Defaults to DEFAULT_START."""
    if not from_arg:
        return DEFAULT_START
    try:
        parts = from_arg.strip().split("-")
        return int(parts[0]), int(parts[1])
    except Exception:
        print(f"ERROR: --from must be YYYY-MM, got: {from_arg!r}", file=sys.stderr)
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Antam gold bar holdings into finance.db."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print rows that would be written without touching the DB.",
    )
    parser.add_argument(
        "--owner", default=DEFAULT_OWNER,
        help=f"Owner name to stamp on each row (default: {DEFAULT_OWNER}).",
    )
    parser.add_argument(
        "--db", default=None,
        help="Override path to finance.db (default: read from config/settings.toml).",
    )
    parser.add_argument(
        "--from", dest="start", default=None, metavar="YYYY-MM",
        help="First month to seed (default: 2026-01).",
    )
    args = parser.parse_args()

    db_path   = _load_db_path(args.db)
    owner     = args.owner
    dry_run   = args.dry_run
    start_y, start_m = _parse_start(args.start)

    month_ends = _month_ends_from(start_y, start_m)
    today_str  = _dt.now().strftime("%Y-%m-%d")

    print(f"Gold bar seeder — {'DRY RUN ' if dry_run else ''}owner={owner!r}")
    print(f"  DB: {db_path}")
    print(f"  Months: {month_ends[0]} … {month_ends[-1]}  ({len(month_ends)} snapshots)")
    print(f"  Weight classes: {[wc[3] for wc in WEIGHT_CLASSES]}")
    print()

    con = None if dry_run else sqlite3.connect(db_path)
    total_upserted = 0
    total_skipped  = 0

    for month_end in month_ends:
        price_per_gram = get_gold_price_idr_per_gram(month_end)
        if price_per_gram is None:
            print(f"  {month_end}  WARNING: could not fetch XAU/IDR — skipping this month")
            total_skipped += 1
            continue

        print(f"  {month_end}  XAU/IDR spot → IDR {price_per_gram:,.2f} / gram")
        month_count = 0

        for weight_g, qty, code, name, certs in WEIGHT_CLASSES:
            unit_price   = price_per_gram * weight_g
            market_value = unit_price * qty
            note = (
                f"Certs: {certs} | "
                f"XAU/IDR spot ÷ {TROY_OZ_TO_GRAMS} × {weight_g}g "
                f"= IDR {unit_price:,.0f} ({month_end})"
            )

            if dry_run:
                print(
                    f"    [DRY RUN] {name} × {qty} bars  "
                    f"unit_price={unit_price:,.0f}  "
                    f"market_value={market_value:,.0f}"
                )
            else:
                con.execute("""
                    INSERT INTO holdings
                        (snapshot_date, asset_class, asset_group, asset_name,
                         isin_or_code, institution, account, owner,
                         currency, quantity, unit_price,
                         market_value, market_value_idr,
                         cost_basis, cost_basis_idr, unrealised_pnl_idr,
                         exchange_rate, maturity_date, coupon_rate,
                         notes, import_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
                    DO UPDATE SET
                        unit_price         = excluded.unit_price,
                        market_value       = excluded.market_value,
                        market_value_idr   = excluded.market_value_idr,
                        notes              = excluded.notes,
                        import_date        = excluded.import_date
                """, (
                    month_end,
                    "gold", "Physical Assets", name,
                    code, "Physical", "", owner,
                    "IDR", float(qty), unit_price,
                    market_value, market_value,
                    0.0, 0.0, 0.0,
                    1.0, "", 0.0,
                    note, today_str,
                ))
                print(
                    f"    {name} × {qty}  "
                    f"unit={unit_price:,.0f}  mktval={market_value:,.0f}"
                )
                month_count += 1

        if not dry_run:
            con.commit()
            total_upserted += month_count

    if con:
        con.close()

    print()
    if dry_run:
        print(f"DRY RUN complete — {len(month_ends)} months, "
              f"{total_skipped} skipped.  No DB writes.")
    else:
        expected = len(month_ends) * len(WEIGHT_CLASSES)
        print(f"Done — upserted {total_upserted} / {expected} rows "
              f"({total_skipped} months skipped due to missing price).")


if __name__ == "__main__":
    main()
