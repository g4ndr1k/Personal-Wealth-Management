"""
Delete all transactions for a given institution from Google Sheets + SQLite.

Google Sheets is the authoritative store; SQLite is a read cache.
After running this, re-sync the cache with:
  python3 -m finance.sync

Usage:
  python3 -m finance.delete_institution --institution "Stockbit Sekuritas"
  python3 -m finance.delete_institution --institution "Stockbit Sekuritas" --dry-run
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import sys

from finance.config import load_config, get_finance_config, get_sheets_config
from finance.sheets import SheetsClient

log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Delete all transactions for a given institution from Sheets + SQLite.",
    )
    parser.add_argument(
        "--institution", required=True,
        help='Institution name to delete, e.g. "Stockbit Sekuritas"',
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview rows to delete without making any changes",
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

    cfg         = load_config()
    finance_cfg = get_finance_config(cfg)
    sheets_cfg  = get_sheets_config(cfg)

    institution = args.institution

    # ── Google Sheets ──────────────────────────────────────────────────────────
    sheets = SheetsClient(sheets_cfg)

    log.info("Scanning Transactions tab for institution='%s' …", institution)
    rows = sheets._get(f"{sheets_cfg.transactions_tab}!A:O")
    matches = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        r = list(row) + [""] * (15 - len(row))
        if r[8].strip() == institution:
            matches.append((i + 1, r))  # (1-indexed sheet row, row data)

    print(f"\nFound {len(matches)} row(s) in Sheets for institution='{institution}'")
    if matches:
        for row_num, r in matches[:10]:
            print(f"  row {row_num:4d}  {r[0]}  {r[5][:60]}")
        if len(matches) > 10:
            print(f"  … and {len(matches) - 10} more")

    # ── SQLite ─────────────────────────────────────────────────────────────────
    sqlite_rows = 0
    try:
        con = sqlite3.connect(finance_cfg.sqlite_db)
        cur = con.execute(
            "SELECT COUNT(*) FROM transactions WHERE institution = ?",
            (institution,),
        )
        sqlite_rows = cur.fetchone()[0]
        con.close()
        print(f"Found {sqlite_rows} row(s) in SQLite for institution='{institution}'")
    except Exception as exc:
        log.warning("SQLite check failed (non-fatal): %s", exc)

    if args.dry_run:
        print("\n[DRY RUN] No changes made.")
        return

    if not matches and sqlite_rows == 0:
        print("Nothing to delete.")
        return

    # ── Confirm and delete ─────────────────────────────────────────────────────
    if matches:
        log.info("Deleting %d rows from Sheets …", len(matches))
        deleted_sheets = sheets.delete_rows_by_institution(institution)
        print(f"Deleted {deleted_sheets} row(s) from Sheets.")
    else:
        print("No Sheets rows to delete.")

    if sqlite_rows > 0:
        try:
            con = sqlite3.connect(finance_cfg.sqlite_db)
            con.execute(
                "DELETE FROM transactions WHERE institution = ?",
                (institution,),
            )
            con.commit()
            con.close()
            print(f"Deleted {sqlite_rows} row(s) from SQLite.")
        except Exception as exc:
            log.error("SQLite delete failed: %s", exc)
            sys.exit(1)

    print(
        "\nDone. Re-sync the SQLite cache with:\n"
        "  python3 -m finance.sync"
    )


if __name__ == "__main__":
    main()
