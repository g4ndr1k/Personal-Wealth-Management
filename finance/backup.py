"""
SQLite backup utility for the finance database.

Uses Python's sqlite3.Connection.backup() API which is safe with WAL mode
and does not require locking out other connections.

Also provides sync_to_nas() to stream the latest backup to a NAS target via SSH cat pipe,
controlled by the NAS_SYNC_TARGET environment variable.

Usage::

    python3 -m finance.backup                 # backup to default dir
    python3 -m finance.backup --db path.db    # custom DB path
    python3 -m finance.backup --max 30        # keep at most 30 backups
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = "data/backups"
DEFAULT_MAX_BACKUPS = 30

# Set this env var to enable NAS sync.
# Examples:
#   user@192.168.1.10:/volume1/finance/finance_readonly.db   (SSH cat pipe, port 68)
#   /Volumes/finance/finance_readonly.db                     (SMB local mount, shutil.copy2)
NAS_SYNC_TARGET: str = os.environ.get("NAS_SYNC_TARGET", "")

_NAS_SYNC_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / ".nas_sync_state.json"


def backup_db(
    db_path: str,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    max_backups: int = DEFAULT_MAX_BACKUPS,
) -> str:
    """
    Create a timestamped SQLite backup using the online backup API.

    Returns the path to the new backup file.
    After the backup, auto-syncs to NAS (throttled to once per 24h).
    """
    backup_dir_path = Path(backup_dir)
    backup_dir_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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

    # Auto-sync to NAS once per 24h (throttled; non-fatal)
    if NAS_SYNC_TARGET:
        try:
            result = sync_to_nas(db_path, force=False)
            if result.get("skipped"):
                log.debug("NAS sync skipped (throttled): %s", result)
        except Exception as exc:
            log.warning("NAS auto-sync failed (non-fatal): %s", exc)

    return str(dest_path)


def sync_to_nas(db_path: str, force: bool = False) -> dict:
    """
    Stream the latest backup to the NAS target via SSH cat pipe.

    Throttled to once per 24h unless force=True.
    Returns a result dict with keys: ok, skipped, target, synced_at, error.

    NAS_SYNC_TARGET must be set (e.g. via env var) for this to do anything.
    """
    if not NAS_SYNC_TARGET:
        return {
            "ok": False,
            "skipped": True,
            "error": "NAS_SYNC_TARGET not configured",
        }

    # Throttle: skip if last sync was < 24h ago (unless forced)
    if not force:
        state = _load_sync_state()
        last = state.get("last_nas_sync")
        if last:
            try:
                delta = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds()
                if delta < 86400:
                    return {
                        "ok": True,
                        "skipped": True,
                        "seconds_until_next": int(86400 - delta),
                        "last_synced_at": last,
                    }
            except ValueError:
                pass  # malformed timestamp — proceed with sync

    # Use the latest backup file to avoid syncing a live WAL-mode DB
    backup_dir = Path(db_path).parent / "backups"
    backups = sorted(backup_dir.glob("finance_*.db"), reverse=True)
    source = str(backups[0]) if backups else db_path

    log.info("NAS sync: %s → %s", source, NAS_SYNC_TARGET)

    # Parse target: "user@host:path" or "host:path" or local path
    # Use SSH cat pipe instead of rsync to avoid Synology rsync protocol restrictions
    ssh_key = "/run/secrets/nas_sync_key"
    ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", "-p", "68"]
    if Path(ssh_key).exists():
        ssh_base += ["-i", ssh_key]

    # NAS_SYNC_TARGET format: user@host:/path/to/file
    if ":" in NAS_SYNC_TARGET:
        ssh_dest, remote_path = NAS_SYNC_TARGET.rsplit(":", 1)
        # Stream file over SSH: cat source | ssh user@host "cat > /remote/path"
        with open(source, "rb") as f:
            result = subprocess.run(
                ssh_base + [ssh_dest, f"cat > {remote_path}"],
                stdin=f,
                capture_output=True,
                timeout=120,
            )
    else:
        # Local path (e.g. SMB mount)
        import shutil
        try:
            shutil.copy2(source, NAS_SYNC_TARGET)
            result = type("R", (), {"returncode": 0, "stderr": b""})()
        except Exception as exc:
            result = type("R", (), {"returncode": 1, "stderr": str(exc).encode()})()

    now = datetime.utcnow().isoformat()

    if result.returncode == 0:
        _save_sync_state({"last_nas_sync": now})
        log.info("NAS sync complete.")
        return {
            "ok": True,
            "skipped": False,
            "target": NAS_SYNC_TARGET,
            "synced_at": now,
            "error": None,
        }
    else:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        log.error("NAS sync failed (rc=%d): %s", result.returncode, stderr[:300])
        return {
            "ok": False,
            "skipped": False,
            "target": NAS_SYNC_TARGET,
            "synced_at": now,
            "error": stderr[:300] or f"ssh-cat exited with code {result.returncode}",
        }


def _load_sync_state() -> dict:
    try:
        if _NAS_SYNC_STATE_FILE.exists():
            return json.loads(_NAS_SYNC_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_sync_state(state: dict) -> None:
    try:
        _NAS_SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _NAS_SYNC_STATE_FILE.write_text(json.dumps(state))
    except Exception as exc:
        log.warning("Could not save NAS sync state: %s", exc)


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
