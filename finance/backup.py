"""
SQLite backup utility for the finance database.

Uses Python's sqlite3.Connection.backup() API which is safe with WAL mode
and does not require locking out other connections.

Usage::

    python3 -m finance.backup                 # backup to default dir
    python3 -m finance.backup --db path.db    # custom DB path
    python3 -m finance.backup --max 30        # keep at most 30 backups
"""
from __future__ import annotations

import os
import sqlite3
import datetime
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = "data/backups"
DEFAULT_MAX_BACKUPS = 30


def backup_db(
    db_path: str,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    max_backups: int = DEFAULT_MAX_BACKUPS,
) -> str:
    """
    Create a timestamped SQLite backup using the online backup API.

    Returns the path to the new backup file.
    """
    backup_dir_path = Path(backup_dir)
    backup_dir_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = backup_dir_path / f"finance_{ts}.db"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # Restrictive permissions on backup file
    try:
        os.chmod(dest_path, 0o600)
    except OSError:
        pass

    log.info("Backup created: %s", dest_path)

    # Prune old backups beyond max_backups
    _prune_backups(backup_dir_path, max_backups)

    return str(dest_path)


def _prune_backups(backup_dir: Path, max_backups: int) -> None:
    """Remove oldest backup files beyond the retention limit."""
    backups = sorted(
        backup_dir.glob("finance_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[max_backups:]:
        old.unlink()
        log.info("Pruned old backup: %s", old.name)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Backup the finance SQLite database")
    parser.add_argument("--db", default="data/finance.db", help="Path to the finance DB")
    parser.add_argument("--dir", default=DEFAULT_BACKUP_DIR, help="Backup directory")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_BACKUPS, help="Max backups to keep")
    args = parser.parse_args()

    path = backup_db(args.db, args.dir, args.max)
    print(f"Backup saved: {path}")
