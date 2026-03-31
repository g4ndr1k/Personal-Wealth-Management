"""
Sync PDF processing registry → 'PDF Import Log' Google Sheet tab.

Reads data/processed_files.db (Stage 1 registry), compares each month against
the expected monthly PDF manifest, and writes a checklist to the sheet.

Columns written:
  month          — YYYY-MM (e.g. 2025-01)
  label          — Human-readable source name (e.g. "Permata Credit Card")
  expected       — How many PDFs are expected each month for this source
  actual         — How many were actually processed this month
  status         — ✓ Complete | ⚠ Partial (n/m) | ✗ Missing
  files          — Comma-separated filenames actually processed
  last_processed — ISO timestamp of the most recent successful parse

Usage
─────
  # Sync all months on record
  python3 -m finance.pdf_log_sync

  # Sync only the 6 most recent calendar months
  python3 -m finance.pdf_log_sync --months 6

  # Custom registry path
  python3 -m finance.pdf_log_sync --registry /path/to/processed_files.db

  # Custom config
  python3 -m finance.pdf_log_sync --config config/settings.toml
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from finance.config import load_config, get_sheets_config
from finance.sheets import SheetsClient

log = logging.getLogger(__name__)

# Default registry path — matches batch_process.py REGISTRY_DB
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_DB = _PROJECT_ROOT / "data" / "processed_files.db"

_INDONESIAN_MONTHS = {
    "januari": "01", "februari": "02", "maret": "03",
    "april": "04",   "mei": "05",      "juni": "06",
    "juli": "07",    "agustus": "08",  "september": "09",
    "oktober": "10", "november": "11", "desember": "12",
}

# ── Expected monthly PDF manifest ─────────────────────────────────────────────
#
# Each entry: (bank, stmt_type, expected_count, label)
#
#   bank / stmt_type must match the values stored in the registry by Stage 1
#   parsers (see parsers/*.py for the exact strings).
#
#   expected_count is the number of distinct PDF files you should receive
#   every month for this bank+type combination.
#
# Gandrik Permata:
#   1. Permata Black CC        (Gandrik + Helen transactions in one PDF)
#   2. PermataVisa Infinite CC (Gandrik + Helen transactions in one PDF)
#   3. Savings & Investments statement (Gandrik)
#   4. RDN IDR statement (Gandrik)
# Helen Permata:
#   5. Savings & Investments statement (Helen)
#   6. Savings statement — 2nd account (Helen)
# BCA Gandrik:
#   7. BCA Credit Card (Gandrik)
#   8. BCA Rekening Tahapan (Gandrik)
# BCA Helen:
#   9. BCA Rekening Tahapan — account 1 (Helen)
#  10. BCA Rekening Tahapan — account 2 (Helen)
# Niaga Gandrik:
#  11. CIMB Niaga Credit Card (Gandrik)
#  12. CIMB Niaga Combined Portfolio (Gandrik)
# Maybank Gandrik:
#  13. Maybank Savings & Investment (Gandrik)
#  14. Maybank Credit Card (Gandrik)

EXPECTED_MANIFEST: list[tuple[str, str, int, str]] = [
    ("Permata",    "cc",           2, "Permata Credit Card"),
    ("Permata",    "savings",      4, "Permata Savings & RDN"),
    ("BCA",        "cc",           1, "BCA Credit Card"),
    ("BCA",        "savings",      3, "BCA Savings (Tahapan)"),
    ("CIMB Niaga", "cc",           1, "Niaga Credit Card"),
    ("CIMB Niaga", "consol",       1, "Niaga Consolidated"),
    ("Maybank",    "consolidated", 1, "Maybank Savings"),
    ("Maybank",    "cc",           1, "Maybank Credit Card"),
]

# Total expected PDFs per month (for display in summary)
TOTAL_EXPECTED = sum(e for _, _, e, _ in EXPECTED_MANIFEST)


# ── Registry helpers ──────────────────────────────────────────────────────────

def _extract_month(period: str) -> str:
    """Extract YYYY-MM from the period end date stored in the registry.

    The registry stores period as "DD/MM/YYYY – DD/MM/YYYY".  We use the
    *end* date (second match) because that reflects the statement print date —
    CC billing cycles can span multiple months so the start date is not a
    reliable indicator of which month the statement belongs to.

    Falls back to YYYY-MM-DD ISO format if the parser stored that instead.
    """
    if not period:
        return ""
    # DD/MM/YYYY — find ALL matches, use the last one (period end)
    matches = re.findall(r"(\d{2})/(\d{2})/(\d{4})", period)
    if matches:
        d, mo, yr = matches[-1]
        return f"{yr}-{mo}"
    # YYYY-MM-DD ISO fallback — also use the last occurrence
    matches = re.findall(r"(\d{4})-(\d{2})-\d{2}", period)
    if matches:
        yr, mo = matches[-1]
        return f"{yr}-{mo}"
    return ""


def _extract_month_from_filename(filename: str) -> str:
    """Fallback: extract YYYY-MM from the filename when the period field is empty.

    Handles:
      - CIMB Niaga: "credit card billing statement_19-01-2026_*.pdf"
      - Permata:    "E-Statement__Februari 2026 - *.pdf"
      - Permata:    "E-Statement-2__Maret 2026 - *.pdf"
    """
    name = filename or ""
    # DD-MM-YYYY embedded in filename (CIMB Niaga pattern)
    m = re.search(r"_(\d{2})-(\d{2})-(\d{4})", name)
    if m:
        return f"{m.group(3)}-{m.group(2)}"
    # "<IndonesianMonth> YYYY" (Permata pattern)
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", name)
    if m:
        mon_str = m.group(1).lower()
        year    = m.group(2)
        mon_num = _INDONESIAN_MONTHS.get(mon_str)
        if mon_num:
            return f"{year}-{mon_num}"
    return ""


def _query_registry(db_path: Path) -> list[dict]:
    """Return all successfully processed (status='ok') PDF records."""
    if not db_path.exists():
        log.warning("Registry DB not found: %s", db_path)
        return []
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT filename, bank, stmt_type, period, processed_at, transactions
                FROM   processed_files
                WHERE  status = 'ok'
                  AND  bank     != ''
                  AND  stmt_type != ''
                ORDER BY processed_at
            """).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.error("Failed to read registry: %s", e)
        return []


# ── Log row builder ───────────────────────────────────────────────────────────

def build_log_rows(db_path: Path, months: int = 0) -> list[list]:
    """Build sheet rows for the PDF Import Log tab.

    Args:
        db_path: Path to processed_files.db
        months:  If > 0, only emit the N most recent calendar months.

    Returns:
        List of [month, label, expected, actual, status, files, last_processed]
        sorted by month DESC, then in manifest order within each month.
    """
    records = _query_registry(db_path)

    # Group by (month, bank, stmt_type)
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    skipped = 0
    for r in records:
        month = _extract_month(r["period"])
        if not month:
            month = _extract_month_from_filename(r["filename"])
        if not month:
            skipped += 1
            log.debug("Skipping %s — could not determine month from period %r or filename",
                      r["filename"], r["period"])
            continue
        grouped[(month, r["bank"], r["stmt_type"])].append(r)

    if skipped:
        log.warning("%d record(s) skipped — unparseable period field.", skipped)

    # Determine which months to emit — ignore anything before 2026
    all_months = sorted(
        {k[0] for k in grouped if k[0] >= "2026-01"},
        reverse=True,
    )
    if months > 0:
        all_months = all_months[:months]

    rows: list[list] = []
    for month in all_months:
        for bank, stmt_type, expected, label in EXPECTED_MANIFEST:
            recs = grouped.get((month, bank, stmt_type), [])
            actual = len(recs)

            if actual == 0:
                status = "✗ Missing"
            elif actual < expected:
                status = f"⚠ Partial ({actual}/{expected})"
            else:
                status = "✓ Complete"

            files = ", ".join(r["filename"] for r in recs)
            last_processed = max((r["processed_at"] for r in recs), default="")

            rows.append([month, label, expected, actual, status, files, last_processed])

    return rows


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Sync PDF processing registry to the 'PDF Import Log' sheet tab.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--months", type=int, default=0, metavar="N",
        help="Only sync the N most recent months (default: all)",
    )
    ap.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_DB), metavar="PATH",
        help=f"Path to processed_files.db (default: {DEFAULT_REGISTRY_DB})",
    )
    ap.add_argument(
        "--config", default=None, metavar="PATH",
        help="Path to settings.toml (default: config/settings.toml)",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Print the rows that would be written without touching the sheet.",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging.",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    db_path = Path(args.registry)
    rows = build_log_rows(db_path, months=args.months)

    if not rows:
        log.info("No processed PDFs found in registry — nothing to write.")
        return

    # Summary
    total = len(rows)
    missing  = sum(1 for r in rows if r[4].startswith("✗"))
    partial  = sum(1 for r in rows if r[4].startswith("⚠"))
    complete = sum(1 for r in rows if r[4].startswith("✓"))
    log.info(
        "Registry → %d rows across %d months  "
        "(✓ %d complete  ⚠ %d partial  ✗ %d missing)",
        total,
        len({r[0] for r in rows}),
        complete, partial, missing,
    )

    if args.dry_run:
        header = ["month", "label", "expected", "actual", "status", "files", "last_processed"]
        print("\n" + "  ".join(f"{h:<22}" for h in header))
        print("─" * 120)
        for r in rows:
            print("  ".join(str(v)[:22].ljust(22) for v in r))
        print(f"\n{total} rows total  (dry-run — sheet not updated)\n")
        return

    raw_cfg    = load_config(args.config)
    sheets_cfg = get_sheets_config(raw_cfg)
    client     = SheetsClient(sheets_cfg)

    log.info("Writing to '%s' tab …", sheets_cfg.pdf_import_log_tab)
    client.write_pdf_import_log(rows)

    log.info("Done.")
    if missing or partial:
        log.warning(
            "%d missing and %d partial entries — some PDFs were not processed.",
            missing, partial,
        )


if __name__ == "__main__":
    main()
