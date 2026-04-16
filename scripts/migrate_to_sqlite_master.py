#!/usr/bin/env python3
"""
One-time migration: copy Category Overrides, Import Log, and Owner Mappings
from Google Sheets / settings.toml into the SQLite authoritative store.

Usage::

    python3 scripts/migrate_to_sqlite_master.py --dry-run   # preview only
    python3 scripts/migrate_to_sqlite_master.py              # execute migration

Prerequisites:
    - Run ``python3 -m finance.sync`` first to ensure SQLite is current.
    - Google Sheets credentials must be available (service account or OAuth).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

# ── Ensure project root is on sys.path ───────────────────────────────────────
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finance.config import load_config, get_finance_config, get_sheets_config
from finance.db import open_db
from finance.sheets import SheetsClient

log = logging.getLogger(__name__)


def migrate_overrides(conn, sheets: SheetsClient, *, dry_run: bool) -> int:
    """Migrate Category Overrides tab → category_overrides table."""
    overrides = sheets.read_overrides()
    if not overrides:
        log.info("No category overrides found in Sheets.")
        return 0

    existing = {
        row[0]
        for row in conn.execute("SELECT hash FROM category_overrides").fetchall()
    }
    new_count = 0
    for tx_hash, data in overrides.items():
        if tx_hash in existing:
            continue
        if dry_run:
            log.info("  [dry-run] Would migrate override: %s → %s", tx_hash[:16], data["category"])
            new_count += 1
            continue
        conn.execute(
            """INSERT OR IGNORE INTO category_overrides
               (hash, category, merchant, notes, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?, 'migration')""",
            (
                tx_hash,
                data["category"],
                None,  # merchant not stored in Sheets overrides
                data.get("notes", ""),
                data.get("updated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            ),
        )
        # Audit log entry
        conn.execute(
            """INSERT INTO audit_log (entity, entity_id, action, field, new_value, source)
               VALUES ('override', ?, 'create', 'category', ?, 'migration')""",
            (tx_hash, data["category"]),
        )
        new_count += 1

    if not dry_run:
        conn.commit()
    log.info("Category overrides: %d migrated (%d already existed)", new_count, len(existing))
    return new_count


def migrate_import_log(conn, sheets: SheetsClient, *, dry_run: bool) -> int:
    """Migrate Import Log tab → import_log table."""
    qtab = f"'{sheets.cfg.import_log_tab}'"
    try:
        rows = sheets._get(f"{qtab}!A:G")
    except Exception as e:
        log.warning("Could not read Import Log from Sheets: %s", e)
        return 0

    if len(rows) < 2:
        log.info("No import log entries found in Sheets.")
        return 0

    count = 0
    for row in rows[1:]:
        r = list(row) + [""] * (7 - len(row))
        import_date = (r[0] or "").strip()
        import_file = (r[1] or "").strip()
        if not import_date or not import_file:
            continue

        if dry_run:
            log.info("  [dry-run] Would migrate import log: %s %s", import_date, import_file)
            count += 1
            continue

        conn.execute(
            """INSERT INTO import_log
               (import_date, import_file, rows_added, rows_skipped, rows_total, duration_s, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                import_date,
                import_file,
                _safe_int(r[2]),
                _safe_int(r[3]),
                _safe_int(r[4]),
                _safe_float(r[5]),
                (r[6] or "").strip(),
            ),
        )
        count += 1

    if not dry_run:
        conn.commit()
    log.info("Import log: %d entries migrated", count)
    return count


def migrate_owner_mappings(conn, cfg: dict, *, dry_run: bool) -> int:
    """Migrate [owners] from settings.toml → owner_mappings table."""
    owners = cfg.get("owners", {})
    if not owners:
        log.info("No [owners] section found in settings.toml.")
        return 0

    existing = {
        row[0]
        for row in conn.execute("SELECT substring_match FROM owner_mappings").fetchall()
    }
    count = 0
    for substring, label in owners.items():
        if substring in existing:
            log.info("  Owner mapping already exists: %s → %s", substring, label)
            continue
        if dry_run:
            log.info("  [dry-run] Would migrate owner: %s → %s", substring, label)
            count += 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO owner_mappings (substring_match, owner_label) VALUES (?, ?)",
            (substring, label),
        )
        conn.execute(
            """INSERT INTO audit_log (entity, entity_id, action, field, new_value, source)
               VALUES ('owner_mapping', ?, 'create', 'owner_label', ?, 'migration')""",
            (substring, label),
        )
        count += 1

    if not dry_run:
        conn.commit()
    log.info("Owner mappings: %d migrated (%d already existed)", count, len(existing))
    return count


def _safe_int(val) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Sheets data to SQLite authoritative store"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--db", help="Override SQLite DB path")
    parser.add_argument("--settings", help="Override settings.toml path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.settings)
    fin_cfg = get_finance_config(cfg)
    db_path = args.db or fin_cfg.sqlite_db

    log.info("Database: %s", db_path)
    log.info("Mode: %s", "DRY RUN" if args.dry_run else "LIVE")
    log.info("")

    conn = open_db(db_path)

    # Verify schema version
    ver = conn.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1").fetchone()
    if not ver or ver[0] < 2:
        log.error("Schema version < 2. Run the API server once to apply schema v2 first.")
        sys.exit(1)
    log.info("Schema version: %d ✓", ver[0])

    # Connect to Sheets
    sheets_cfg = get_sheets_config(cfg)
    sheets = SheetsClient(sheets_cfg)

    # ── Run migrations ───────────────────────────────────────────────────────
    totals = {}
    totals["overrides"] = migrate_overrides(conn, sheets, dry_run=args.dry_run)
    totals["import_log"] = migrate_import_log(conn, sheets, dry_run=args.dry_run)
    totals["owners"] = migrate_owner_mappings(conn, cfg, dry_run=args.dry_run)

    # ── Summary ──────────────────────────────────────────────────────────────
    log.info("")
    log.info("── Migration Summary ──")
    for key, count in totals.items():
        log.info("  %s: %d rows", key, count)

    if not args.dry_run:
        # Verification counts
        log.info("")
        log.info("── Verification ──")
        for table in ["category_overrides", "import_log", "owner_mappings"]:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            log.info("  %s: %d rows in DB", table, row[0])

    conn.close()
    log.info("")
    log.info("Done." if not args.dry_run else "Dry run complete — no changes made.")


if __name__ == "__main__":
    main()
