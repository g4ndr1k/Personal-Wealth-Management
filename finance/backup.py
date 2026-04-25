"""
SQLite backup utility for the finance database.

Creates tiered backups under ~/agentic-ai/data/backups:
  - hourly  (max 24)
  - daily   (max 31)
  - weekly  (max 5)
  - monthly (max 12)
  - manual  (max 10)

Auto backups are maintained by ensure_auto_backups() and the scheduler helpers.
NAS sync always streams the latest available backup file.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from datetime import datetime as _real_datetime
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_DIR = str((Path.home() / "agentic-ai" / "data" / "backups").expanduser())
LEGACY_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

BACKUP_TIERS: dict[str, dict] = {
    "hourly": {
        "label": "Hourly",
        "max_sets": 24,
        "interval": timedelta(hours=1),
    },
    "daily": {
        "label": "Daily",
        "max_sets": 31,
        "interval": timedelta(days=1),
    },
    "weekly": {
        "label": "Weekly",
        "max_sets": 5,
        "interval": timedelta(weeks=1),
    },
    "monthly": {
        "label": "Monthly",
        "max_sets": 12,
        "interval": timedelta(days=31),
    },
    "manual": {
        "label": "Manual",
        "max_sets": 10,
        "interval": None,
    },
}
AUTO_BACKUP_TIERS = ("hourly", "daily", "weekly", "monthly")
SCHEDULER_POLL_SECONDS = 300

# Set this env var to enable NAS sync.
# Examples:
#   user@192.168.1.10:/volume1/finance/finance_readonly.db   (SSH cat pipe, default port 22)
#   /Volumes/finance/finance_readonly.db                     (SMB local mount, shutil.copy2)
NAS_SYNC_TARGET: str = os.environ.get("NAS_SYNC_TARGET", "")
NAS_SYNC_SSH_PORT: str = os.environ.get("NAS_SYNC_SSH_PORT", "22")

_NAS_SYNC_STATE_FILE = PROJECT_ROOT / "data" / ".nas_sync_state.json"
_scheduler_lock = threading.Lock()
_scheduler_stop_event: threading.Event | None = None
_scheduler_thread: threading.Thread | None = None


def backup_db(
    db_path: str,
    backup_dir: str | None = None,
    *,
    kind: str = "manual",
    sync_to_nas_enabled: bool | None = None,
) -> str:
    """Create a timestamped SQLite backup for the requested tier."""
    _validate_kind(kind)
    backup_root = _resolve_backup_dir(backup_dir)
    tier_dir = backup_root / kind
    tier_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest_path = tier_dir / f"finance_{kind}_{ts}.db"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    try:
        os.chmod(dest_path, 0o600)
    except OSError:
        pass

    log.info("Backup created: %s", dest_path)
    _prune_backups(tier_dir, BACKUP_TIERS[kind]["max_sets"])
    _remove_empty_legacy_root(backup_root)

    should_sync = NAS_SYNC_TARGET and (sync_to_nas_enabled if sync_to_nas_enabled is not None else kind in AUTO_BACKUP_TIERS)
    if should_sync:
        try:
            result = sync_to_nas(db_path, force=False, backup_dir=str(backup_root))
            if result.get("skipped"):
                log.debug("NAS sync skipped: %s", result)
        except Exception as exc:
            log.warning("NAS auto-sync failed (non-fatal): %s", exc)

    return str(dest_path)


def ensure_auto_backups(db_path: str, backup_dir: str | None = None) -> dict:
    """Create any due hourly/daily/weekly/monthly backups."""
    now = datetime.now()
    created: list[str] = []
    backup_dir = str(_resolve_backup_dir(backup_dir))

    if not Path(db_path).exists():
        return {"ok": False, "created": [], "reason": f"DB not found: {db_path}"}

    status = get_backup_status(db_path, backup_dir=backup_dir, now=now)
    for kind in AUTO_BACKUP_TIERS:
        if status[kind]["status"] in ("missing", "due"):
            created.append(backup_db(db_path, backup_dir=backup_dir, kind=kind, sync_to_nas_enabled=False))

    if created and NAS_SYNC_TARGET:
        try:
            sync_to_nas(db_path, force=False, backup_dir=backup_dir)
        except Exception as exc:
            log.warning("NAS auto-sync failed (non-fatal): %s", exc)

    return {
        "ok": True,
        "created": created,
        "status": get_backup_status(db_path, backup_dir=backup_dir, now=datetime.now()),
    }


def get_backup_status(db_path: str, backup_dir: str | None = None, now: datetime | None = None) -> dict:
    """Return retention/status metadata for each backup tier."""
    now = now or datetime.now()
    backup_root = _resolve_backup_dir(backup_dir)
    payload = {"backup_root": str(backup_root)}

    for kind, spec in BACKUP_TIERS.items():
        tier_dir = backup_root / kind
        files = _list_tier_backups(tier_dir, kind)
        latest = files[0] if files else None
        latest_at = _file_datetime(latest) if latest else None
        interval: timedelta | None = spec["interval"]
        next_due_at = latest_at + interval if latest_at and interval else None
        status = "missing"
        if latest_at:
            if interval is None or now < next_due_at:
                status = "ok"
            else:
                status = "due"

        payload[kind] = {
            "key": kind,
            "label": spec["label"],
            "path": str(tier_dir),
            "max_sets": spec["max_sets"],
            "count": len(files),
            "status": status,
            "latest_at": latest_at.isoformat() if latest_at else None,
            "next_due_at": next_due_at.isoformat() if next_due_at else None,
            "latest_file": str(latest) if latest else None,
        }

    latest_any = find_latest_backup(db_path, backup_dir=backup_dir)
    payload["latest_backup"] = str(latest_any) if latest_any else None
    return payload


def find_latest_backup(db_path: str, backup_dir: str | None = None) -> Path | None:
    backup_root = _resolve_backup_dir(backup_dir)
    candidates: list[Path] = []
    for kind in BACKUP_TIERS:
        candidates.extend(_list_tier_backups(backup_root / kind, kind))

    if backup_root.exists():
        candidates.extend(sorted(backup_root.glob("finance_*.db"), key=_safe_mtime, reverse=True))
    if LEGACY_BACKUP_DIR.exists() and LEGACY_BACKUP_DIR != backup_root:
        candidates.extend(sorted(LEGACY_BACKUP_DIR.glob("finance_*.db"), key=_safe_mtime, reverse=True))

    candidates = [path for path in candidates if path.exists()]
    if not candidates:
        return None
    return max(candidates, key=_safe_mtime)


def sync_to_nas(db_path: str, force: bool = False, backup_dir: str | None = None) -> dict:
    """Stream the latest available backup to the NAS target."""
    if not NAS_SYNC_TARGET:
        return {
            "ok": False,
            "skipped": True,
            "error": "NAS_SYNC_TARGET not configured",
        }

    if not force:
        state = _load_sync_state()
        last = state.get("last_nas_sync")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                delta = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if delta < 86400:
                    return {
                        "ok": True,
                        "skipped": True,
                        "seconds_until_next": int(86400 - delta),
                        "last_synced_at": last,
                        "source": state.get("last_source"),
                    }
            except ValueError:
                pass

    latest_backup = find_latest_backup(db_path, backup_dir=backup_dir)
    source = latest_backup or Path(db_path)
    log.info("NAS sync: %s → %s", source, NAS_SYNC_TARGET)

    ssh_key = os.environ.get("NAS_SYNC_KEY_PATH", "/run/secrets/nas_sync_key")
    ssh_base = [
        "ssh",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={Path.home() / '.ssh' / 'known_hosts'}",
        "-p", NAS_SYNC_SSH_PORT,
    ]
    if Path(ssh_key).exists():
        ssh_base += ["-i", ssh_key]
    elif os.environ.get("NAS_SYNC_KEY_PATH"):
        # Key path was explicitly configured but file is missing — fail loudly
        raise FileNotFoundError(f"NAS sync key not found at configured path: {ssh_key}")

    if ":" in NAS_SYNC_TARGET:
        ssh_dest, remote_path = NAS_SYNC_TARGET.rsplit(":", 1)
        # Escape remote path to prevent shell injection on the NAS
        quoted_path = shlex.quote(remote_path)
        with open(source, "rb") as f:
            result = subprocess.run(
                ssh_base + [ssh_dest, f"cat > {quoted_path}"],
                stdin=f,
                capture_output=True,
                timeout=120,
            )
    else:
        try:
            target_path = Path(NAS_SYNC_TARGET).expanduser()
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_path)
            result = type("R", (), {"returncode": 0, "stderr": b""})()
        except Exception as exc:
            result = type("R", (), {"returncode": 1, "stderr": str(exc).encode()})()

    now = datetime.now(timezone.utc).isoformat()

    if result.returncode == 0:
        _save_sync_state({"last_nas_sync": now, "last_source": str(source)})
        return {
            "ok": True,
            "skipped": False,
            "target": NAS_SYNC_TARGET,
            "synced_at": now,
            "source": str(source),
            "error": None,
        }

    stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
    log.error("NAS sync failed (rc=%d): %s", result.returncode, stderr[:300])
    return {
        "ok": False,
        "skipped": False,
        "target": NAS_SYNC_TARGET,
        "synced_at": now,
        "source": str(source),
        "error": stderr[:300] or f"ssh-cat exited with code {result.returncode}",
    }


def start_backup_scheduler(db_path: str, backup_dir: str | None = None) -> None:
    global _scheduler_stop_event, _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            return
        _scheduler_stop_event = threading.Event()
        resolved_backup_dir = str(_resolve_backup_dir(backup_dir))
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            args=(db_path, resolved_backup_dir, _scheduler_stop_event),
            name="finance-backup-scheduler",
            daemon=True,
        )
        _scheduler_thread.start()


def stop_backup_scheduler() -> None:
    global _scheduler_stop_event, _scheduler_thread
    with _scheduler_lock:
        if _scheduler_stop_event:
            _scheduler_stop_event.set()
        if _scheduler_thread and _scheduler_thread.is_alive():
            _scheduler_thread.join(timeout=2)
        _scheduler_stop_event = None
        _scheduler_thread = None


def _scheduler_loop(db_path: str, backup_dir: str, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            ensure_auto_backups(db_path, backup_dir=backup_dir)
        except Exception as exc:
            log.warning("Auto backup scheduler failed: %s", exc)
        stop_event.wait(SCHEDULER_POLL_SECONDS)


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


def _resolve_backup_dir(backup_dir: str | None) -> Path:
    env_override = os.environ.get("FINANCE_BACKUP_DIR", "").strip()
    return Path(backup_dir or env_override or DEFAULT_BACKUP_DIR).expanduser()


def _prune_backups(tier_dir: Path, max_backups: int) -> None:
    backups = sorted(tier_dir.glob("finance_*.db"), key=_safe_mtime, reverse=True)
    for old in backups[max_backups:]:
        old.unlink(missing_ok=True)
        log.info("Pruned old backup: %s", old.name)


def _list_tier_backups(tier_dir: Path, kind: str) -> list[Path]:
    if not tier_dir.exists():
        return []
    return sorted(tier_dir.glob(f"finance_{kind}_*.db"), key=_safe_mtime, reverse=True)


def _file_datetime(path: Path | None) -> datetime | None:
    if path is None:
        return None
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 4:
        try:
            return _real_datetime.strptime("_".join(parts[-2:]), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return _real_datetime.fromtimestamp(path.stat().st_mtime)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def _remove_empty_legacy_root(backup_root: Path) -> None:
    legacy_files = sorted(backup_root.glob("finance_*.db"), key=_safe_mtime, reverse=True)
    for old in legacy_files:
        old.unlink(missing_ok=True)


def _validate_kind(kind: str) -> None:
    if kind not in BACKUP_TIERS:
        raise ValueError(f"Unknown backup kind: {kind}")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Backup the finance SQLite database")
    parser.add_argument("--db", default="data/finance.db", help="Path to the finance DB")
    parser.add_argument("--dir", default=DEFAULT_BACKUP_DIR, help="Backup directory")
    parser.add_argument("--kind", choices=tuple(BACKUP_TIERS.keys()), default="manual", help="Backup tier to create")
    args = parser.parse_args()

    path = backup_db(args.db, args.dir, kind=args.kind)
    print(f"Backup saved: {path}")
