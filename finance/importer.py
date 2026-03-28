"""
Stage 2 — XLSX → Google Sheets import module.

Usage
─────
  python3 -m finance.importer              # import ALL_TRANSACTIONS.xlsx
  python3 -m finance.importer --dry-run    # parse + categorize, no writes
  python3 -m finance.importer --overwrite  # re-import; replace rows by hash
  python3 -m finance.importer --file PATH  # use a specific xlsx file
  python3 -m finance.importer -v           # verbose / debug logging

What it does
────────────
  1. Opens ALL_TRANSACTIONS.xlsx (Stage 1 output — immutable, never edited)
  2. Maps columns to the Stage 2 schema (see GUIDE.md §26.1)
  3. Generates a SHA-256 dedup hash per transaction
  4. Skips rows already in Google Sheets (hash match)
  5. Runs the 4-layer categorization engine
  6. Batch-appends new rows to the Transactions tab
  7. Logs the import run to the Import Log tab

Reimport safety
───────────────
  If the Google Sheet is in a bad state, delete the affected rows in the Sheet
  and re-run this script.  The XLSX is the immutable ground truth.  Use
  --overwrite only when you explicitly want to replace existing Sheets data.
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from typing import Optional

import openpyxl

from finance.config import (
    load_config,
    get_finance_config,
    get_sheets_config,
    get_ollama_finance_config,
    get_anthropic_finance_config,
)
from finance.models import FinanceTransaction, parse_xlsx_date
from finance.sheets import SheetsClient
from finance.categorizer import Categorizer

log = logging.getLogger(__name__)

# ── ALL_TRANSACTIONS.xlsx column positions (0-based) ─────────────────────────
# Header row: Owner | Month | Bank | Statement Type | Tgl. Transaksi |
#             Tgl. Tercatat | Keterangan | Currency | Jumlah Valuta Asing |
#             Kurs (RP) | Jumlah (IDR) | Tipe | Saldo (IDR) | Nomor Rekening/Kartu
_C_OWNER       = 0
_C_MONTH       = 1   # not imported (derived from date)
_C_BANK        = 2
_C_STMT_TYPE   = 3   # not imported (context only)
_C_DATE_TX     = 4
_C_DATE_POST   = 5   # skipped per design (transaction date only)
_C_DESC        = 6
_C_CURRENCY    = 7
_C_FOREIGN_AMT = 8
_C_EXCHANGE_RT = 9
_C_AMOUNT_IDR  = 10
_C_TIPE        = 11
_C_BALANCE     = 12  # not imported
_C_ACCOUNT_NUM = 13

_EXPECTED_HEADERS = [
    "Owner", "Month", "Bank", "Statement Type",
    "Tgl. Transaksi", "Tgl. Tercatat", "Keterangan",
    "Currency", "Jumlah Valuta Asing", "Kurs (RP)",
    "Jumlah (IDR)", "Tipe", "Saldo (IDR)", "Nomor Rekening/Kartu",
]


# ── Core import logic ─────────────────────────────────────────────────────────

def run(
    xlsx_path: str,
    sheets_client: SheetsClient,
    categorizer: Categorizer,
    overwrite: bool = False,
    dry_run: bool = False,
    import_file_label: str = "",
) -> dict:
    """
    Import ALL_TRANSACTIONS.xlsx into Google Sheets.

    Returns a stats dict:
        added       int   rows written to Sheets
        skipped     int   rows skipped (duplicate hash)
        total       int   total valid rows in XLSX
        parse_err   int   rows that could not be parsed
        by_layer    dict  {1: n, 2: n, 3: n, 4: n}
        duration_s  float elapsed seconds
        dry_run     bool  True if no writes were made
    """
    t0 = time.time()
    label = import_file_label or os.path.basename(xlsx_path)

    # ── 1. Open XLSX ──────────────────────────────────────────────────────────
    log.info("Opening %s …", xlsx_path)
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)

    if "ALL_TRANSACTIONS" not in wb.sheetnames:
        wb.close()
        raise ValueError(
            f"Sheet 'ALL_TRANSACTIONS' not found in {xlsx_path}. "
            f"Available sheets: {wb.sheetnames}"
        )

    ws = wb["ALL_TRANSACTIONS"]
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not all_rows:
        log.warning("ALL_TRANSACTIONS sheet is empty — nothing to import.")
        return _stats(0, 0, 0, 0, {}, 0.0, dry_run)

    # Validate header
    header = [str(v).strip() if v else "" for v in all_rows[0]]
    _warn_if_header_mismatch(header)

    data_rows = all_rows[1:]
    log.info("Read %d data rows from XLSX.", len(data_rows))

    # ── 2. Load existing hashes from Sheets ───────────────────────────────────
    if not dry_run:
        if overwrite:
            log.info("--overwrite: fetching existing hashes + row numbers …")
            hash_to_row = sheets_client.read_existing_hashes_with_rows()
            existing_hashes = set(hash_to_row.keys())
        else:
            log.info("Fetching existing hashes …")
            existing_hashes = sheets_client.read_existing_hashes()
            hash_to_row = {}
        log.info("  %d hashes already in Sheets.", len(existing_hashes))
    else:
        existing_hashes = set()
        hash_to_row = {}

    # ── 3. Reload aliases + categories into the categorizer ───────────────────
    if not dry_run:
        log.info("Loading merchant aliases from Sheets …")
        aliases    = sheets_client.read_aliases()
        categories = sheets_client.read_categories()
        categorizer.reload_aliases(aliases)
        if categories:
            categorizer.categories = categories
        log.info(
            "  %d exact aliases  |  %d regex aliases  |  %d categories",
            len(categorizer._exact),
            len(categorizer._regex),
            len(categorizer.categories),
        )

    # ── 4. Parse rows ─────────────────────────────────────────────────────────
    new_txns:       list[FinanceTransaction] = []
    overwrite_txns: list[FinanceTransaction] = []
    skipped     = 0
    parse_errors = 0
    by_layer:    dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}

    for xlsx_row_idx, raw in enumerate(data_rows, start=2):
        try:
            txn = _parse_row(raw, label)
        except Exception as exc:
            log.warning("Row %d: parse error — %s", xlsx_row_idx, exc)
            parse_errors += 1
            continue

        if txn is None:
            continue  # blank or incomplete row

        if txn.hash in existing_hashes:
            if overwrite:
                result = categorizer.categorize(txn.raw_description)
                txn.merchant = result.merchant
                txn.category = result.category
                by_layer[result.layer] += 1
                overwrite_txns.append(txn)
            else:
                skipped += 1
            continue

        # Categorize new row
        result = categorizer.categorize(txn.raw_description)
        txn.merchant = result.merchant
        txn.category = result.category
        by_layer[result.layer] += 1
        new_txns.append(txn)

    total_valid = len(data_rows) - parse_errors
    log.info(
        "Parsed: %d valid | %d new | %d duplicate (skipped) | %d parse errors",
        total_valid, len(new_txns), skipped, parse_errors,
    )
    log.info(
        "Categorization: L1 auto=%d  L2 auto=%d  L3 suggested=%d  L4 review=%d",
        by_layer[1], by_layer[2], by_layer[3], by_layer[4],
    )

    if dry_run:
        log.info("[DRY RUN] Would append %d rows. No writes performed.", len(new_txns))
        stats = _stats(0, skipped, total_valid, parse_errors, by_layer,
                       time.time() - t0, dry_run=True)
        stats["would_add"] = len(new_txns)
        return stats

    # ── 5. Write to Sheets ────────────────────────────────────────────────────
    added = 0

    if new_txns:
        log.info("Appending %d new rows to Sheets …", len(new_txns))
        added = sheets_client.append_transactions(new_txns)

    if overwrite and overwrite_txns:
        log.info("Overwriting %d existing rows …", len(overwrite_txns))
        sheets_client.overwrite_transactions(overwrite_txns, hash_to_row)

    # ── 6. Log import ─────────────────────────────────────────────────────────
    duration = time.time() - t0
    notes = (
        f"L1={by_layer[1]} L2={by_layer[2]} "
        f"L3={by_layer[3]} review={by_layer[4]}"
    )
    if parse_errors:
        notes += f" parse_err={parse_errors}"
    if overwrite and overwrite_txns:
        notes += f" overwritten={len(overwrite_txns)}"

    sheets_client.log_import(
        import_file=label,
        rows_added=added,
        rows_skipped=skipped,
        rows_total=total_valid,
        duration_s=duration,
        notes=notes,
    )

    return _stats(added, skipped, total_valid, parse_errors, by_layer, duration)


# ── Row parser ────────────────────────────────────────────────────────────────

def _parse_row(row: tuple, import_file: str) -> Optional[FinanceTransaction]:
    """
    Convert one ALL_TRANSACTIONS.xlsx data row to a FinanceTransaction.
    Returns None for blank / incomplete rows (silently skipped).
    """
    if not row or all(v is None or v == "" for v in row):
        return None

    def cell(idx):
        return row[idx] if idx < len(row) else None

    owner       = _str(cell(_C_OWNER))
    institution = _str(cell(_C_BANK))
    raw_desc    = _str(cell(_C_DESC))
    account_num = _str(cell(_C_ACCOUNT_NUM))
    tipe        = _str(cell(_C_TIPE)).lower()        # "debit" / "credit"
    currency    = _str(cell(_C_CURRENCY)).upper() or "IDR"

    # Skip rows missing the three essential identifying fields
    if not raw_desc or not owner or not institution:
        return None

    date_str = parse_xlsx_date(cell(_C_DATE_TX))
    if not date_str:
        return None

    # IDR amount with sign
    amount_raw = _float(cell(_C_AMOUNT_IDR))
    if amount_raw is None:
        return None
    # Debit = expense (negative), Credit = income/refund (positive)
    amount = -abs(amount_raw) if tipe == "debit" else abs(amount_raw)

    # Foreign currency fields
    if currency == "IDR":
        original_currency = None
        original_amount   = None
        exchange_rate     = None
    else:
        original_currency = currency
        original_amount   = _float(cell(_C_FOREIGN_AMT))
        exchange_rate     = _float(cell(_C_EXCHANGE_RT))
        # Derive exchange rate if missing but both amounts are present
        if exchange_rate is None and original_amount:
            try:
                exchange_rate = abs(amount_raw) / abs(original_amount)
            except ZeroDivisionError:
                pass

    return FinanceTransaction(
        date=date_str,
        amount=amount,
        original_currency=original_currency,
        original_amount=original_amount,
        exchange_rate=exchange_rate,
        raw_description=raw_desc,
        merchant=None,
        category=None,
        institution=institution,
        account=account_num,
        owner=owner,
        import_file=import_file,
    )


# ── Small helpers ─────────────────────────────────────────────────────────────

def _str(val) -> str:
    return str(val).strip() if val is not None else ""


def _float(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        f = float(val)
        return f if f != 0.0 else None
    except (TypeError, ValueError):
        return None


def _stats(
    added: int, skipped: int, total: int, parse_errors: int,
    by_layer: dict, duration: float, dry_run: bool = False,
) -> dict:
    return {
        "added":      added,
        "skipped":    skipped,
        "total":      total,
        "parse_err":  parse_errors,
        "by_layer":   by_layer,
        "duration_s": round(duration, 2),
        "dry_run":    dry_run,
    }


def _warn_if_header_mismatch(actual: list[str]):
    mismatches = [
        (i, exp, act)
        for i, (exp, act) in enumerate(zip(_EXPECTED_HEADERS, actual))
        if exp != act
    ]
    if mismatches:
        log.warning(
            "XLSX header mismatch (column mapping may be wrong):\n%s",
            "\n".join(f"  col {i}: expected '{e}', got '{a}'"
                      for i, e, a in mismatches),
        )


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Import ALL_TRANSACTIONS.xlsx (Stage 1 output) into Google Sheets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m finance.importer               # standard import (skip duplicates)
  python3 -m finance.importer --dry-run     # preview without writing
  python3 -m finance.importer --overwrite   # re-import; replace rows by hash
  python3 -m finance.importer --file /path/to/file.xlsx
        """,
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Replace existing Sheets rows that match by hash (default: skip them)",
    )
    parser.add_argument(
        "--file", metavar="PATH",
        help="XLSX file to import (default: finance.xlsx_input from settings.toml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and categorize; print summary without writing to Sheets",
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

    cfg           = load_config()
    finance_cfg   = get_finance_config(cfg)
    sheets_cfg    = get_sheets_config(cfg)
    ollama_cfg    = get_ollama_finance_config(cfg)
    anthropic_cfg = get_anthropic_finance_config(cfg)

    xlsx_path = args.file or finance_cfg.xlsx_input
    if not os.path.exists(xlsx_path):
        log.error("XLSX file not found: %s", xlsx_path)
        sys.exit(1)

    sheets     = SheetsClient(sheets_cfg)
    categorizer = Categorizer(
        aliases=[],      # loaded fresh inside run()
        categories=[],   # loaded fresh inside run()
        ollama_host=ollama_cfg.host,
        ollama_model=ollama_cfg.model,
        ollama_timeout=ollama_cfg.timeout_seconds,
        anthropic_api_key=anthropic_cfg.api_key,
        anthropic_model=anthropic_cfg.model,
    )

    stats = run(
        xlsx_path=xlsx_path,
        sheets_client=sheets,
        categorizer=categorizer,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        import_file_label=os.path.basename(xlsx_path),
    )

    # ── Print summary ─────────────────────────────────────────────────────────
    print()
    if args.dry_run:
        print(
            f"[DRY RUN]  Would add {stats.get('would_add', 0)} rows  |  "
            f"{stats['skipped']} duplicates skipped  |  "
            f"{stats['total']} total rows in XLSX"
        )
    else:
        print(
            f"Import complete\n"
            f"  Added:      {stats['added']}\n"
            f"  Skipped:    {stats['skipped']}  (already in Sheets)\n"
            f"  Total XLSX: {stats['total']}\n"
            f"  Parse err:  {stats['parse_err']}\n"
            f"  Duration:   {stats['duration_s']}s\n"
            f"\n"
            f"  Categorization breakdown:\n"
            f"    L1 auto:      {stats['by_layer'].get(1, 0)}\n"
            f"    L2 auto:      {stats['by_layer'].get(2, 0)}\n"
            f"    L3 suggested: {stats['by_layer'].get(3, 0)}\n"
            f"    L4 review:    {stats['by_layer'].get(4, 0)}"
        )
        if stats["by_layer"].get(4, 0) > 0:
            print(
                f"\n  ⚠  {stats['by_layer'][4]} transaction(s) need review in the PWA."
            )


if __name__ == "__main__":
    main()
