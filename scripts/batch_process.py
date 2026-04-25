#!/usr/bin/env python3
"""
batch_process.py — Automatic, idempotent PDF→XLS processor for bank statements.

MODES
  One-shot (default): scan inbox once, process new files, exit.
  Watch mode        : continuously poll inbox and process new files as they arrive.

USAGE
  # Process whatever is in pdf_inbox right now, then exit
  python3 scripts/batch_process.py

  # Watch pdf_inbox forever; Ctrl-C to stop
  python3 scripts/batch_process.py --watch

  # Wipe XLS output first, then process everything fresh
  python3 scripts/batch_process.py --clear-output

  # Detect bank/type only, no parsing or XLS writing
  python3 scripts/batch_process.py --dry-run

  # Show full tracebacks on errors
  python3 scripts/batch_process.py -v

  # Tune timing
  python3 scripts/batch_process.py --watch --poll-secs 15 --stable-secs 8

IDEMPOTENCY
  Every file (PDF and ZIP) is SHA-256 hashed before processing.
  Results are stored in data/processed_files.db (SQLite).
  The same file content is never processed twice, even after restart,
  even if the file is renamed or re-added to the inbox.

FILE STABILITY
  A file is only processed once its size has not changed for --stable-secs
  seconds (default 5). This prevents processing a file mid-copy.

ZIP HANDLING
  ZIP files are extracted into a temp subfolder of pdf_inbox, then each
  contained PDF is processed individually with the same hash dedup rules.
"""
import argparse
import hashlib
import logging
import os
import shutil
import signal
import sqlite3
import sys
import time
import tomllib
import traceback
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# ── Project root & sys.path ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Default paths ─────────────────────────────────────────────────────────────
INBOX_DIR     = PROJECT_ROOT / "data" / "pdf_inbox"
UNLOCKED_DIR  = PROJECT_ROOT / "data" / "pdf_unlocked"
XLS_DIR       = PROJECT_ROOT / "output" / "xls"
LOG_DIR       = PROJECT_ROOT / "logs"
BANKS_TOML    = PROJECT_ROOT / "secrets" / "banks.toml"
SETTINGS_TOML = PROJECT_ROOT / "config" / "settings.toml"
REGISTRY_DB   = PROJECT_ROOT / "data" / "processed_files.db"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("batch")
log.setLevel(logging.DEBUG)

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_log_formatter)
_console.setLevel(logging.INFO)
log.addHandler(_console)

_file_handler = logging.FileHandler(LOG_DIR / "batch_process.log", encoding="utf-8")
_file_handler.setFormatter(_log_formatter)
_file_handler.setLevel(logging.DEBUG)
log.addHandler(_file_handler)


# ══════════════════════════════════════════════════════════════════════════════
# Registry — SQLite-backed processed-file tracker
# ══════════════════════════════════════════════════════════════════════════════

class Registry:
    """
    Persistent, restart-safe store of every file we've seen.
    Primary key is SHA-256 of file content — renames are invisible to us.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS processed_files (
            sha256       TEXT PRIMARY KEY,
            filename     TEXT    NOT NULL,
            source_path  TEXT    NOT NULL,
            first_seen   TEXT    NOT NULL,
            processed_at TEXT    NOT NULL,
            bank         TEXT    DEFAULT '',
            stmt_type    TEXT    DEFAULT '',
            period       TEXT    DEFAULT '',
            transactions INTEGER DEFAULT 0,
            output_file  TEXT    DEFAULT '',
            status       TEXT    NOT NULL,
            error        TEXT    DEFAULT '',
            error_category TEXT  DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS zip_members (
            zip_sha256   TEXT NOT NULL,
            pdf_filename TEXT NOT NULL,
            pdf_sha256   TEXT DEFAULT '',
            extracted_at TEXT NOT NULL,
            PRIMARY KEY (zip_sha256, pdf_filename)
        );
    """

    def __init__(self, db_path: Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # ── Internal ──────────────────────────────────────────────────────────
    @contextmanager
    def _con(self):
        """Yield a short-lived connection that is always closed on exit.

        NOTE: ``with sqlite3.connect(...) as con`` only manages transactions
        (commit / rollback), it does **not** close the connection.  Using it
        as a plain return value and then in a ``with`` block leaks a file
        descriptor every call.  This wrapper guarantees ``close()`` is called.
        """
        con = sqlite3.connect(self._path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _migrate(self):
        with self._con() as con:
            for stmt in self._SCHEMA.split(";"):
                s = stmt.strip()
                if s:
                    con.execute(s)
            columns = {
                row["name"]
                for row in con.execute(
                    "PRAGMA table_info(processed_files)"
                ).fetchall()
            }
            if "error_category" not in columns:
                con.execute(
                    "ALTER TABLE processed_files ADD COLUMN error_category TEXT DEFAULT ''"
                )

    # ── Public API ────────────────────────────────────────────────────────
    def seen(self, sha256: str) -> bool:
        """Return True if this hash has already been processed (status='ok')."""
        with self._con() as con:
            row = con.execute(
                "SELECT status FROM processed_files WHERE sha256=?", (sha256,)
            ).fetchone()
        return row is not None and row["status"] == "ok"

    def attempted(self, sha256: str) -> bool:
        """Return True if we've ever tried this hash (any status)."""
        with self._con() as con:
            row = con.execute(
                "SELECT 1 FROM processed_files WHERE sha256=?", (sha256,)
            ).fetchone()
        return row is not None

    def record(self, sha256: str, filename: str, source_path: str,
               status: str, bank: str = "", stmt_type: str = "",
               period: str = "", transactions: int = 0,
               output_file: str = "", error: str = "",
               error_category: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            con.execute("""
                INSERT INTO processed_files
                    (sha256, filename, source_path, first_seen, processed_at,
                     bank, stmt_type, period, transactions, output_file, status, error, error_category)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(sha256) DO UPDATE SET
                    processed_at=excluded.processed_at,
                    status=excluded.status,
                    bank=excluded.bank,
                    stmt_type=excluded.stmt_type,
                    period=excluded.period,
                    transactions=excluded.transactions,
                    output_file=excluded.output_file,
                    error=excluded.error,
                    error_category=excluded.error_category
            """, (sha256, filename, source_path, now, now,
                  bank, stmt_type, period, transactions, output_file, status, error, error_category))

    def record_zip_member(self, zip_sha256: str, pdf_filename: str, pdf_sha256: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            con.execute("""
                INSERT OR IGNORE INTO zip_members
                    (zip_sha256, pdf_filename, pdf_sha256, extracted_at)
                VALUES (?,?,?,?)
            """, (zip_sha256, pdf_filename, pdf_sha256, now))

    def summary(self) -> dict:
        with self._con() as con:
            row = con.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status='ok')    AS ok,
                    COUNT(*) FILTER (WHERE status='error') AS errors,
                    SUM(transactions)                      AS total_txns
                FROM processed_files
            """).fetchone()
        return dict(row) if row else {"ok": 0, "errors": 0, "total_txns": 0}

    def list_errors(self) -> list[dict]:
        with self._con() as con:
            rows = con.execute("""
                SELECT filename, error, processed_at
                FROM processed_files WHERE status='error'
                ORDER BY processed_at DESC
            """).fetchall()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_stable(path: Path, stable_secs: int) -> bool:
    """
    Return True only when the file exists, is non-empty, and its size has
    not changed for stable_secs seconds.  Guards against mid-copy reads.
    """
    try:
        size_before = path.stat().st_size
        if size_before == 0:
            return False
        time.sleep(stable_secs)
        size_after = path.stat().st_size
        return size_before == size_after and size_after > 0
    except OSError:
        return False


def load_passwords() -> dict[str, str]:
    if not BANKS_TOML.exists():
        log.warning("secrets/banks.toml not found — encrypted PDFs will fail")
        return {}
    with open(BANKS_TOML, "rb") as f:
        data = tomllib.load(f)
    return data.get("passwords", {})


def load_owner_mappings() -> dict[str, str]:
    if not SETTINGS_TOML.exists():
        return {}
    with open(SETTINGS_TOML, "rb") as f:
        data = tomllib.load(f)
    return data.get("owners", {})


def get_bank_password(bank_name: str, passwords: dict[str, str]) -> str:
    key = bank_name.lower().replace(" ", "_")
    return passwords.get(key, "")


def clear_output(xls_dir: Path):
    if xls_dir.exists():
        removed = list(xls_dir.glob("*.xlsx"))
        for f in removed:
            f.unlink()
        log.info(f"[CLEAR] Removed {len(removed)} .xlsx file(s) from {xls_dir}")
    else:
        log.info(f"[CLEAR] {xls_dir} does not exist, nothing to clear")


# ══════════════════════════════════════════════════════════════════════════════
# ZIP extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_zip(zf_path: Path, inbox: Path, registry: Registry,
                zip_sha256: str) -> list[Path]:
    """
    Extract all PDFs from a ZIP into inbox/_extracted/.
    Returns list of extracted PDF paths (may be empty).
    Skips members already recorded for this ZIP.
    """
    extract_dir = inbox / "_extracted"
    extract_dir.mkdir(exist_ok=True)
    extracted: list[Path] = []

    try:
        with zipfile.ZipFile(zf_path, "r") as z:
            members = [m for m in z.namelist() if m.lower().endswith(".pdf")]
            if not members:
                log.info(f"  [ZIP] No PDFs inside {zf_path.name}")
                return []

            for member in members:
                # Use basename only — flatten nested dirs inside ZIP
                safe_name = Path(member).name
                out_path = extract_dir / safe_name

                # If name collision, prefix with zip stem
                if out_path.exists():
                    out_path = extract_dir / f"{zf_path.stem}__{safe_name}"

                # Write to a temp path first, then rename (atomic-ish)
                tmp_path = out_path.with_suffix(".tmp")
                try:
                    with z.open(member) as src, open(tmp_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    tmp_path.rename(out_path)
                    log.info(f"  [ZIP] Extracted: {safe_name}")
                    registry.record_zip_member(zip_sha256, safe_name)
                    extracted.append(out_path)
                except Exception as e:
                    log.error(f"  [ZIP] Failed to extract {member}: {e}")
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)

    except zipfile.BadZipFile as e:
        log.error(f"  [ZIP] Bad ZIP file {zf_path.name}: {e}")
    except Exception as e:
        log.error(f"  [ZIP] Unexpected error for {zf_path.name}: {e}")

    return extracted


# ══════════════════════════════════════════════════════════════════════════════
# PDF processing pipeline
# ══════════════════════════════════════════════════════════════════════════════

def process_pdf(pdf_path: Path, passwords: dict, owner_mappings: dict,
                unlocked_dir: Path, xls_dir: Path,
                dry_run: bool = False, verbose: bool = False) -> dict:
    """
    Unlock → parse → export one PDF.
    Returns result dict; never raises.
    """
    result = {
        "file": pdf_path.name,
        "status": "error",
        "bank": "Unknown",
        "stmt_type": "unknown",
        "period": "",
        "transactions": 0,
        "output_file": "",
        "error": "",
    }

    try:
        from bridge.pdf_unlock import is_encrypted, unlock_pdf
        from parsers.router import detect_and_parse, detect_bank_and_type
        from parsers.router import UnknownStatementError
        from exporters.xls_writer import export

        work_path = str(pdf_path)

        # ── Quick bank hint (needed before unlock for password lookup) ────
        bank_hint = "Unknown"
        try:
            bank_hint, _ = detect_bank_and_type(work_path)
        except Exception:
            pass

        # ── Unlock ────────────────────────────────────────────────────────
        if is_encrypted(work_path):
            password = get_bank_password(bank_hint, passwords)

            if password:
                work_path = unlock_pdf(work_path, password, str(unlocked_dir))
                log.info(f"    Unlocked ({bank_hint} password matched)")
            else:
                # Try every known password as fallback
                unlocked = False
                for key, pwd in passwords.items():
                    if not pwd:
                        continue
                    try:
                        work_path = unlock_pdf(work_path, pwd, str(unlocked_dir))
                        log.info(f"    Unlocked (fallback password '{key}' worked)")
                        unlocked = True
                        break
                    except Exception:
                        continue
                if not unlocked:
                    raise ValueError(
                        "Encrypted PDF: no password matched. "
                        "Add the correct password to secrets/banks.toml."
                    )
        else:
            log.debug(f"    Not encrypted")

        if dry_run:
            bank, stmt_type = detect_bank_and_type(work_path)
            result["bank"] = bank
            result["stmt_type"] = stmt_type
            result["status"] = "dry-run"
            return result

        # ── Parse ─────────────────────────────────────────────────────────
        parsed = detect_and_parse(work_path, owner_mappings=owner_mappings)
        result["bank"]         = parsed.bank
        result["stmt_type"]    = parsed.statement_type
        result["period"]       = f"{parsed.period_start} – {parsed.period_end}"
        result["transactions"] = len(parsed.transactions)

        if parsed.raw_errors:
            log.warning(f"    Parser warnings: {parsed.raw_errors}")

        # ── Export ────────────────────────────────────────────────────────
        output_path, _ = export(parsed, str(xls_dir), owner_mappings)
        result["output_file"] = Path(output_path).name
        result["status"] = "ok"

    except Exception as e:
        result["error"] = str(e)
        if verbose:
            log.debug(traceback.format_exc())
        else:
            log.debug(f"    Full traceback: {traceback.format_exc()}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Core scan — one pass over the inbox
# ══════════════════════════════════════════════════════════════════════════════

def scan_once(inbox: Path, registry: Registry, passwords: dict,
              owner_mappings: dict, stable_secs: int,
              dry_run: bool, verbose: bool) -> tuple[int, int]:
    """
    Scan inbox for ZIPs and PDFs.  Process anything new and stable.
    Returns (processed_count, skipped_count).
    """
    processed = skipped = 0

    # ── 1. Handle ZIPs first ──────────────────────────────────────────────
    for zf_path in sorted(inbox.rglob("*.zip")):
        if not is_stable(zf_path, stable_secs):
            log.debug(f"[WAIT] {zf_path.name} — not stable yet, deferring")
            continue

        zip_sha = sha256_file(zf_path)

        if registry.seen(zip_sha):
            log.debug(f"[SKIP] {zf_path.name} — already extracted (sha={zip_sha[:8]})")
            skipped += 1
            continue

        log.info(f"[ZIP ] {zf_path.name}  (sha={zip_sha[:8]})")
        extracted = extract_zip(zf_path, inbox, registry, zip_sha)

        if extracted:
            # Record the ZIP itself as 'ok' so we don't re-extract it
            registry.record(zip_sha, zf_path.name, str(zf_path), status="ok")
            processed += 1
        else:
            registry.record(zip_sha, zf_path.name, str(zf_path),
                            status="error", error="No PDFs found inside ZIP")

    # ── 2. Handle PDFs recursively ────────────────────────────────────────
    pdf_sources = sorted(
        path for path in inbox.rglob("*.pdf")
        if path.is_file()
    )

    for pdf_path in pdf_sources:
        if not is_stable(pdf_path, stable_secs):
            log.debug(f"[WAIT] {pdf_path.name} — not stable yet, deferring")
            continue

        pdf_sha = sha256_file(pdf_path)

        if registry.seen(pdf_sha):
            log.debug(f"[SKIP] {pdf_path.name} — already processed (sha={pdf_sha[:8]})")
            skipped += 1
            continue

        if registry.attempted(pdf_sha):
            # Previously failed — retry this run
            log.info(f"[RETRY] {pdf_path.name}  (sha={pdf_sha[:8]})")
        else:
            log.info(f"[PDF ] {pdf_path.name}  (sha={pdf_sha[:8]})")

        r = process_pdf(
            pdf_path, passwords, owner_mappings,
            UNLOCKED_DIR, XLS_DIR,
            dry_run=dry_run, verbose=verbose,
        )

        registry.record(
            sha256=pdf_sha,
            filename=pdf_path.name,
            source_path=str(pdf_path),
            status=r["status"],
            bank=r["bank"],
            stmt_type=r["stmt_type"],
            period=r["period"],
            transactions=r["transactions"],
            output_file=r["output_file"],
            error=r["error"],
        )

        if r["status"] == "ok":
            log.info(
                f"  ✓  {r['bank']} {r['stmt_type']}  |  "
                f"{r['period']}  |  {r['transactions']} txns  →  {r['output_file']}"
            )
            processed += 1
        elif r["status"] == "dry-run":
            log.info(f"  ~  {r['bank']} / {r['stmt_type']}  (dry-run)")
            processed += 1
        else:
            log.error(f"  ✗  {r['error']}")
            processed += 1  # counts as "handled", just with failure

    return processed, skipped


# ══════════════════════════════════════════════════════════════════════════════
# Watch loop
# ══════════════════════════════════════════════════════════════════════════════

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    log.info("Shutdown signal received — finishing current scan then exiting")
    _shutdown = True


def watch_loop(inbox: Path, registry: Registry, passwords: dict,
               owner_mappings: dict, poll_secs: int, stable_secs: int,
               dry_run: bool, verbose: bool):
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info(f"[WATCH] Monitoring {inbox}  (poll={poll_secs}s  stable={stable_secs}s)")
    log.info("[WATCH] Press Ctrl-C to stop")

    while not _shutdown:
        processed, skipped = scan_once(
            inbox, registry, passwords, owner_mappings,
            stable_secs, dry_run, verbose,
        )
        if processed:
            log.info(f"[SCAN ] {processed} processed, {skipped} skipped this pass")

        # Sleep in small ticks so Ctrl-C is responsive
        for _ in range(poll_secs * 2):
            if _shutdown:
                break
            time.sleep(0.5)

    log.info("[WATCH] Stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Automatic, idempotent bank PDF → XLS processor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--inbox", default=str(INBOX_DIR),
                    help="PDF inbox directory to watch/scan")
    ap.add_argument("--watch", action="store_true",
                    help="Run as a daemon; poll inbox continuously")
    ap.add_argument("--poll-secs", type=int, default=10, metavar="N",
                    help="Seconds between inbox scans in watch mode (default: 10)")
    ap.add_argument("--stable-secs", type=int, default=5, metavar="N",
                    help="Seconds a file must be unchanged before processing (default: 5)")
    ap.add_argument("--clear-output", action="store_true",
                    help="Delete all .xlsx files before starting")
    ap.add_argument("--reset-registry", action="store_true",
                    help="Wipe the processed-files registry (reprocess everything)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect bank/type only; skip parsing and XLS export")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Print full tracebacks on errors")
    ap.add_argument("--status", action="store_true",
                    help="Print registry summary and exit")
    args = ap.parse_args()

    if args.verbose:
        _console.setLevel(logging.DEBUG)

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    UNLOCKED_DIR.mkdir(parents=True, exist_ok=True)
    XLS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Registry ──────────────────────────────────────────────────────────
    registry = Registry(REGISTRY_DB)

    if args.reset_registry:
        REGISTRY_DB.unlink(missing_ok=True)
        registry = Registry(REGISTRY_DB)
        log.info("[RESET] Processed-files registry cleared")

    # ── Status report and exit ────────────────────────────────────────────
    if args.status:
        s = registry.summary()
        print(f"\n  Registry: {REGISTRY_DB}")
        print(f"  OK        : {s['ok']}")
        print(f"  Errors    : {s['errors']}")
        print(f"  Total txns: {s['total_txns'] or 0}")
        errs = registry.list_errors()
        if errs:
            print(f"\n  Failed files:")
            for e in errs:
                print(f"    {e['filename']:<45} {e['error']}")
        print()
        return

    # ── Optionally clear output ───────────────────────────────────────────
    if args.clear_output:
        clear_output(XLS_DIR)

    passwords     = load_passwords()
    owner_mappings = load_owner_mappings()

    log.info(
        f"Loaded {len(passwords)} password(s), "
        f"{len(owner_mappings)} owner mapping(s)  |  "
        f"registry={REGISTRY_DB.name}"
    )

    # ── Run ───────────────────────────────────────────────────────────────
    if args.watch:
        watch_loop(
            inbox, registry, passwords, owner_mappings,
            poll_secs=args.poll_secs,
            stable_secs=args.stable_secs,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    else:
        processed, skipped = scan_once(
            inbox, registry, passwords, owner_mappings,
            stable_secs=args.stable_secs,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        # ── Final summary ─────────────────────────────────────────────────
        s = registry.summary()
        print(f"\n{'─'*58}")
        print(f"  This run : {processed} processed, {skipped} already-done skipped")
        print(f"  All-time : {s['ok']} OK  |  {s['errors']} errors  "
              f"|  {s['total_txns'] or 0} total transactions")
        print(f"{'─'*58}")

        if s["ok"]:
            print(f"\n  Output files in {XLS_DIR}/")
            for f in sorted(XLS_DIR.glob("*.xlsx")):
                size_kb = f.stat().st_size / 1024
                print(f"    {f.name:<42} {size_kb:>6.0f} KB")

        errs = registry.list_errors()
        if errs:
            print(f"\n  Failed ({len(errs)}):")
            for e in errs:
                print(f"    {e['filename']:<45} {e['error']}")

        print()


if __name__ == "__main__":
    main()
