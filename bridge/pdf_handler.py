"""
pdf_handler.py — new HTTP endpoints added to the bridge for PDF→XLS processing.

Registers these routes into bridge/server.py:
  POST /pdf/upload          multipart/form-data, field "file", optional field "password"
  POST /pdf/process         JSON {"job_id": "...", "password": "..."}  (for auto-detected)
  GET  /pdf/status/<job_id> job progress and result
  GET  /pdf/download/<job_id> download the produced XLS
  GET  /pdf/jobs            list recent jobs
  GET  /pdf/attachments     list auto-detected bank PDFs from Mail.app

All endpoints require the same bearer token as the rest of the bridge.

Jobs are queued and executed in background worker threads. Clients submit work
then poll /pdf/status for progress instead of blocking the HTTP request until
PDF parsing/export completes.
"""
import os
import uuid
import queue
import logging
import sqlite3
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
log = logging.getLogger(__name__)

# These are set by init_pdf_handler() called from bridge/server.py
_config = {}
_db_path = ""

_running_jobs: set[str] = set()
_running_jobs_lock = threading.Lock()
_queued_jobs: set[str] = set()
_queued_jobs_lock = threading.Lock()
_job_queue: queue.Queue[tuple[str, str]] = queue.Queue()
_job_worker_started = False
_job_worker_lock = threading.Lock()


def init_pdf_handler(config: dict, db_path: str):
    """Called once at bridge startup to inject config."""
    global _config, _db_path
    _config = config
    _db_path = db_path
    _init_jobs_db()
    _ensure_job_worker()
def _mark_job_running(job_id: str) -> bool:
    with _running_jobs_lock:
        if job_id in _running_jobs:
            return False
        _running_jobs.add(job_id)
        return True


def _mark_job_finished(job_id: str) -> None:
    with _running_jobs_lock:
        _running_jobs.discard(job_id)


def _mark_job_queued(job_id: str) -> bool:
    with _queued_jobs_lock:
        if job_id in _queued_jobs:
            return False
        _queued_jobs.add(job_id)
        return True


def _mark_job_dequeued(job_id: str) -> None:
    with _queued_jobs_lock:
        _queued_jobs.discard(job_id)


def _ensure_job_worker() -> None:
    global _job_worker_started
    with _job_worker_lock:
        if _job_worker_started:
            return
        worker = threading.Thread(
            target=_job_worker_loop,
            name="pdf-job-worker",
            daemon=True,
        )
        worker.start()
        _job_worker_started = True


def _job_worker_loop() -> None:
    while True:
        job_id, password = _job_queue.get()
        try:
            _mark_job_dequeued(job_id)
            _run_job(job_id, password)
        except Exception:
            log.exception("Unhandled exception while processing queued PDF job %s", job_id)
        finally:
            _job_queue.task_done()


def _pdf_folder_map() -> dict[str, str]:
    return {
        "pdf_inbox": _config.get("pdf_inbox_dir", ""),
        "pdf_unlocked": _config.get("pdf_unlocked_dir", ""),
    }


def _resolve_pdf_relative_path(folder: str, relative_path: str) -> tuple[str, str]:
    folder_map = _pdf_folder_map()
    root_dir = folder_map.get(folder, "")
    if not root_dir:
        raise ValueError(f"Unknown or unconfigured folder '{folder}'. Use pdf_inbox or pdf_unlocked.")
    if not relative_path:
        raise ValueError("relative_path is required")
    if relative_path.startswith("/"):
        raise ValueError("relative_path must stay inside the configured folder")

    relative = Path(relative_path)
    if any(part == ".." for part in relative.parts):
        raise ValueError("relative_path must stay inside the configured folder")

    root_real = os.path.realpath(root_dir)
    file_real = os.path.realpath(os.path.join(root_real, *relative.parts))
    if file_real != root_real and not file_real.startswith(root_real + os.sep):
        raise ValueError("relative_path must stay inside the configured folder")

    return root_real, file_real


def _describe_pdf_source(source_path: str) -> tuple[str, str, str]:
    source_real = os.path.realpath(source_path or "")
    for folder, root_dir in _pdf_folder_map().items():
        if not root_dir:
            continue
        root_real = os.path.realpath(root_dir)
        if source_real == root_real or source_real.startswith(root_real + os.sep):
            relative = os.path.relpath(source_real, root_real).replace(os.sep, "/")
            return folder, relative, Path(source_real).name
    return "", Path(source_real).name, Path(source_real).name


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
def _run_verification(pdf_path: str, result, logs: list[str]) -> None:
    if not _config.get("verify_enabled", True):
        return

    from bridge.pdf_verify import verify_statement

    verification = verify_statement(
        pdf_path,
        result,
        ollama_host=_config.get("verify_ollama_host", "http://localhost:11434"),
        model=_config.get("verify_model", _config.get("parser_llm_model", "gemma4:e4b")),
        timeout_seconds=int(_config.get("verify_timeout_seconds", 120)),
    )

    deterministic = verification["deterministic_checks"]
    llm = verification["llm"]
    logs.append(
        "Verifier: "
        f"{llm.get('status', 'warn')} / {llm.get('recommended_action', 'proceed_with_review')} "
        f"- {llm.get('summary', 'No summary')}"
    )
    if deterministic.get("has_issues"):
        logs.append(
            "Verifier deterministic checks flagged issues: "
            f"date_out_of_period={deterministic.get('date_out_of_period_count', 0)}, "
            f"missing_account={deterministic.get('missing_account_number_count', 0)}, "
            f"invalid_tx_type={deterministic.get('invalid_tx_type_count', 0)}, "
            f"missing_fx={deterministic.get('missing_exchange_rate_count', 0)}, "
            f"running_balance={len(deterministic.get('running_balance_issues', []))}, "
            f"summary_reconciliation={len(deterministic.get('summary_reconciliation_issues', []))}"
        )

    issues = llm.get("issues", [])[:3]
    for issue in issues:
        logs.append(
            "Verifier issue: "
            f"{issue.get('severity', 'low')} {issue.get('type', 'issue')} - "
            f"{issue.get('message', '')}"
        )

    verify_mode = str(_config.get("verify_mode", "warn")).lower()
    if verify_mode == "block" and llm.get("recommended_action") == "block":
        raise ValueError(f"Verifier blocked statement: {llm.get('summary', 'unknown reason')}")


def get_bank_password(bank_name: str) -> str:
    key = (bank_name or "").lower().replace(" ", "_")
    passwords_file = _config.get("bank_passwords_file", "")
    if not passwords_file or not os.path.exists(passwords_file):
        return ""
    try:
        import tomllib

        with open(passwords_file, "rb") as f:
            data = tomllib.load(f)
        return data.get("passwords", {}).get(key, "")
    except Exception:
        return ""


def run_pdf_pipeline_file(src_path: str, password: str = "", bank_hint: str = "") -> dict:
    """Run the shared PDF pipeline and return structured results."""
    logs: list[str] = []
    result = {
        "status": "error",
        "bank": bank_hint or "Unknown",
        "stmt_type": "unknown",
        "period": "",
        "output_path": "",
        "output_file": "",
        "transactions": 0,
        "error": "",
        "log": "",
    }

    try:
        from bridge.pdf_unlock import is_encrypted, unlock_pdf
        from parsers.router import detect_and_parse, detect_bank_and_type
        from exporters.xls_writer import export

        unlocked_path = src_path
        detected_bank = bank_hint or "Unknown"
        try:
            detected_bank, _ = detect_bank_and_type(src_path)
            result["bank"] = detected_bank
        except Exception:
            pass

        if is_encrypted(src_path):
            if not password:
                password = get_bank_password(detected_bank)
            if not password:
                raise ValueError(
                    f"PDF is encrypted but no password provided for {detected_bank}"
                )
            unlocked_dir = _config.get("pdf_unlocked_dir", "data/pdf_unlocked")
            unlocked_path = unlock_pdf(src_path, password, unlocked_dir)
            logs.append(f"Unlocked: {Path(unlocked_path).name}")
        else:
            logs.append("PDF not encrypted — no unlock needed")

        owner_mappings = _config.get("owner_mappings", {})
        parsed = detect_and_parse(unlocked_path, owner_mappings=owner_mappings)
        result["bank"] = parsed.bank
        result["stmt_type"] = parsed.statement_type
        result["period"] = f"{parsed.period_start}–{parsed.period_end}"
        result["transactions"] = len(parsed.transactions)
        logs.append(
            f"Parsed: {parsed.bank} {parsed.statement_type} "
            f"{parsed.period_start}–{parsed.period_end} "
            f"({len(parsed.transactions)} transactions)"
        )
        if parsed.raw_errors:
            logs.append(f"Parser warnings: {parsed.raw_errors}")
        _run_verification(unlocked_path, parsed, logs)

        if parsed.statement_type in ("savings", "consol", "consolidated") and parsed.accounts:
            _upsert_closing_balance(parsed, logs)

        if parsed.statement_type == "cc" and parsed.accounts:
            _check_cc_reconciliation(parsed, logs)
            _upsert_cc_liability(parsed, logs)

        if getattr(parsed, "bonds", None):
            _upsert_bond_holdings(parsed, logs)

        if parsed.statement_type in ("consol", "consolidated"):
            _upsert_fund_holdings(parsed, logs)

        if getattr(parsed, "holdings", None):
            _upsert_investment_holdings(parsed, logs)

        if parsed.statement_type in ("portfolio", "statement") and parsed.accounts:
            _upsert_closing_balance(parsed, logs)

        output_dir = _config.get("xls_output_dir", "output/xls")
        output_path, _ = export(parsed, output_dir, owner_mappings)
        logs.append(f"Exported: {Path(output_path).name}")

        result["status"] = "done"
        result["output_path"] = output_path
        result["output_file"] = Path(output_path).name
    except Exception as exc:
        log.error("PDF pipeline failed for %s: %s", src_path, exc)
        result["error"] = str(exc)
        logs.append(traceback.format_exc())

    result["log"] = "\n".join(logs)
    return result


# ── DB ────────────────────────────────────────────────────────────────────────
def _init_jobs_db():
    con = sqlite3.connect(_db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pdf_jobs (
            job_id      TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            source_path TEXT,
            bank        TEXT,
            stmt_type   TEXT,
            period      TEXT,
            output_path TEXT,
            error       TEXT,
            log         TEXT
        )
    """)
    con.commit()
    con.close()


def _get_job(job_id: str) -> Optional[dict]:
    con = sqlite3.connect(_db_path)
    row = con.execute("SELECT * FROM pdf_jobs WHERE job_id=?", (job_id,)).fetchone()
    con.close()
    if not row:
        return None
    cols = ["job_id","created_at","status","source_path","bank","stmt_type",
            "period","output_path","error","log"]
    return dict(zip(cols, row))


def _upsert_job(job: dict):
    con = sqlite3.connect(_db_path)
    con.execute("""
        INSERT OR REPLACE INTO pdf_jobs
        (job_id,created_at,status,source_path,bank,stmt_type,period,output_path,error,log)
        VALUES (:job_id,:created_at,:status,:source_path,:bank,:stmt_type,
                :period,:output_path,:error,:log)
    """, job)
    con.commit()
    con.close()


def _list_jobs(limit: int = 50) -> list[dict]:
    con = sqlite3.connect(_db_path)
    rows = con.execute(
        "SELECT * FROM pdf_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    cols = ["job_id","created_at","status","source_path","bank","stmt_type",
            "period","output_path","error","log"]
    return [dict(zip(cols, r)) for r in rows]

def handle_process_file(body: dict) -> tuple[int, dict]:
    """
    POST /pdf/process-file  {"folder": "pdf_inbox", "relative_path": "foo.pdf", "password": ""}
    Resolves the file from the configured inbox/unlocked directories — no upload needed.
    Queues the job for background execution; returns {job_id, status}.
    """
    folder = body.get("folder", "").strip()
    relative_path = str(body.get("relative_path") or body.get("filename") or "").strip()
    password = body.get("password", "")

    if not relative_path:
        return 400, {"error": "relative_path is required"}
    try:
        _, file_path = _resolve_pdf_relative_path(folder, relative_path)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    if not os.path.isfile(file_path):
        return 404, {"error": f"File not found: {folder}/{relative_path}"}
    if not relative_path.lower().endswith(".pdf"):
        return 400, {"error": "Only .pdf files are supported"}

    # Auto-detect bank/type
    bank, stmt_type = "Unknown", "unknown"
    try:
        from parsers.router import detect_bank_and_type
        bank, stmt_type = detect_bank_and_type(file_path)
    except Exception as e:
        log.warning("Could not detect bank/type for %s: %s", relative_path, e)

    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id":      job_id,
        "created_at":  _utc_now_iso(),
        "status":      "pending",
        "source_path": file_path,
        "bank":        bank,
        "stmt_type":   stmt_type,
        "period":      "",
        "output_path": "",
        "error":       "",
        "log":         "",
    }
    _upsert_job(job)
    _queue_job(job_id, password)
    job = _get_job(job_id)
    return 200, {
        "job_id":   job_id,
        "filename": Path(file_path).name,
        "relative_path": relative_path,
        "folder":   folder,
        "status":   job["status"],
        "error":    job.get("error", ""),
    }


def handle_status(job_id: str) -> tuple[int, dict]:
    """GET /pdf/status/<job_id>"""
    job = _get_job(job_id)
    if not job:
        return 404, {"error": "job not found"}
    result = {
        k: job[k]
        for k in ("job_id", "created_at", "status", "bank", "stmt_type", "period", "error", "log")
    }
    return 200, result


def handle_jobs(limit: int = 50) -> tuple[int, dict]:
    """GET /pdf/jobs"""
    jobs = _list_jobs(limit)
    # Don't expose full paths
    safe = []
    for j in jobs:
        folder, relative_path, filename = _describe_pdf_source(j["source_path"] or "")
        safe.append({
            "job_id": j["job_id"],
            "created_at": j["created_at"],
            "status": j["status"],
            "bank": j["bank"],
            "stmt_type": j["stmt_type"],
            "period": j["period"],
            "folder": folder,
            "filename": filename,
            "relative_path": relative_path,
            "error": j["error"],
        })
    return 200, {"jobs": safe}


# ── Job runner ────────────────────────────────────────────────────────────────
def _run_job(job_id: str, password: str):
    """Execute the full PDF→XLS pipeline for a job. Updates DB in place."""
    if not _mark_job_running(job_id):
        return

    job = _get_job(job_id)
    if not job:
        _mark_job_finished(job_id)
        return

    job["status"] = "running"
    _upsert_job(job)

    try:
        src_path = job["source_path"]
        result = run_pdf_pipeline_file(src_path, password=password, bank_hint=job["bank"])

        job["status"] = result["status"]
        job["bank"] = result["bank"]
        job["stmt_type"] = result["stmt_type"]
        job["period"] = result["period"]
        job["output_path"] = result["output_path"]
        job["error"] = result["error"]
        job["log"] = result["log"]

    except Exception as e:
        log.error(f"Job {job_id} failed: {e}")
        job["status"] = "error"
        job["error"] = str(e)
        job["log"] = traceback.format_exc()
    finally:
        _upsert_job(job)
        _mark_job_finished(job_id)


def _queue_job(job_id: str, password: str) -> None:
    """Enqueue a job for single-worker background processing."""
    _ensure_job_worker()
    job = _get_job(job_id)
    if not job:
        return
    if job["status"] == "done":
        return
    if job["status"] == "running":
        return
    if not _mark_job_queued(job_id):
        return
    if job["status"] != "pending":
        job["status"] = "pending"
        _upsert_job(job)
    _job_queue.put((job_id, password))


# ── Savings-account classification helpers ────────────────────────────────────
# Used to filter consolidated statements (Maybank, CIMB) so only cash/savings
# accounts land in account_balances; bonds/funds/CC are skipped here.

_SAVINGS_KEYWORDS = (
    "tabungan", "save", "giro", "deposito", "rekening", "xtra",
    "ikhlas", "payroll", "simpan", "tahapan", "hajj", "bca",
)
_NON_SAVINGS_KEYWORDS = (
    "obligasi", "bond", "reksa dana", "mutual", "unit trust",
    "kartu kredit", "credit card", "kartu", "kredit",
)


def _is_savings_account(product_name: str) -> bool:
    """Return True if the product looks like a savings / cash account."""
    name = product_name.lower()
    # Explicit non-savings take priority
    if any(kw in name for kw in _NON_SAVINGS_KEYWORDS):
        return False
    return any(kw in name for kw in _SAVINGS_KEYWORDS)


def _guess_account_type(product_name: str) -> str:
    """Map product name to account_balances.account_type."""
    name = product_name.lower()
    if "giro" in name:
        return "checking"
    if "deposito" in name:
        return "money_market"
    return "savings"


def _upsert_closing_balance(result, logs: list):
    """
    After parsing a savings / consol / consolidated statement, upsert the
    closing balance for every savings-type account into the Stage 3
    account_balances table so the Wealth dashboard reflects it automatically.

    Handles:
      BCA savings        (statement_type="savings")  — single account per PDF
      Permata savings    (statement_type="savings")  — may have multiple accounts;
                          period_end is stored on each AccountSummary, not on result
      CIMB Niaga consol  (statement_type="consol")   — multiple accounts with numbers
      Maybank consol     (statement_type="consolidated") — savings/bonds/funds/CC mixed;
                          savings entries identified by product_name keywords

    Runs silently on failure so a DB issue never breaks the main XLS export.
    """
    finance_db = _config.get("finance_sqlite_db", "")
    if not finance_db:
        logs.append("Closing-balance upsert skipped: finance_sqlite_db not configured")
        return

    if not result.accounts:
        logs.append("Closing-balance upsert skipped: no accounts in result")
        return

    try:
        import re as _re
        import sqlite3 as _sqlite3
        from datetime import datetime as _dt

        owner_mappings = _config.get("owner_mappings", {})
        today = _dt.now().strftime("%Y-%m-%d")
        con = _sqlite3.connect(finance_db)
        upserted = 0

        for acct in result.accounts:
            # ── Snapshot date ─────────────────────────────────────────────
            # Permata parser sets period_end on each AccountSummary (not on
            # the StatementResult), so check per-account first.
            raw_date = acct.period_end or result.period_end or result.print_date or ""
            dm = _re.match(r"(\d{2})/(\d{2})/(\d{4})", raw_date)
            if not dm:
                continue
            snapshot_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

            # ── Closing balance check ──────────────────────────────────────
            if not acct.closing_balance:
                continue

            # ── Account identifier ─────────────────────────────────────────
            account_id = acct.account_number
            if not account_id:
                # Consolidated statements (Maybank) have no per-account number
                # for savings products — use product_name as the identifier,
                # but only if it genuinely looks like a savings account.
                if not _is_savings_account(acct.product_name):
                    continue
                account_id = acct.product_name[:80]
            else:
                # For consolidated statements with account numbers (e.g. CIMB
                # savings accounts), still filter out non-savings products.
                if result.statement_type in ("consol", "consolidated"):
                    if not _is_savings_account(acct.product_name):
                        continue

            # ── Owner ──────────────────────────────────────────────────────
            # Permata / CIMB parsers derive result.owner from the PDF header.
            # Fall back to owner_mappings keyed by account number.
            owner = result.owner or owner_mappings.get(str(account_id), "")

            # ── Currency & IDR value ───────────────────────────────────────
            currency = acct.currency or "IDR"

            if currency == "IDR":
                balance_idr   = acct.closing_balance
                exchange_rate = 1.0
                note = f"Auto-imported from {result.bank} {result.statement_type} statement"

            elif getattr(acct, "closing_balance_idr", 0.0) > 0:
                # ── Priority 1: bank's own IDR equivalent from the PDF ────────
                # e.g. Permata "Ringkasan Rekening / Saldo Rupiah" column.
                # This is the authoritative rate — never call an external API
                # when the bank has already done the conversion for us.
                balance_idr   = acct.closing_balance_idr
                exchange_rate = (
                    round(balance_idr / acct.closing_balance, 6)
                    if acct.closing_balance else 0.0
                )
                note = (
                    f"Auto-imported from {result.bank} {result.statement_type} statement"
                    f" — {currency} {acct.closing_balance:,.2f} × {exchange_rate:,.4f}"
                    f" = IDR {balance_idr:,.2f} (bank rate from PDF, {snapshot_date})"
                )
                logs.append(
                    f"  Bank rate (PDF): 1 {currency} = {exchange_rate:,.4f} IDR"
                )

            else:
                # ── Priority 2: historical FX API (fallback) ──────────────────
                # Used when the PDF does not contain an IDR equivalent column
                # (e.g. CIMB Niaga, Maybank, BCA foreign-currency accounts).
                from bridge.fx_rate import get_rate_safe
                exchange_rate = get_rate_safe(currency, "IDR", snapshot_date)
                if exchange_rate > 0:
                    balance_idr = round(acct.closing_balance * exchange_rate, 2)
                    note = (
                        f"Auto-imported from {result.bank} {result.statement_type} statement"
                        f" — {currency} {acct.closing_balance:,.2f} × {exchange_rate:,.4f}"
                        f" = IDR {balance_idr:,.2f} (market rate as of {snapshot_date})"
                    )
                    logs.append(
                        f"  FX API: 1 {currency} = {exchange_rate:,.4f} IDR on {snapshot_date}"
                    )
                else:
                    balance_idr   = 0.0
                    exchange_rate = 0.0
                    note = (
                        f"Auto-imported from {result.bank} {result.statement_type} statement"
                        f" — balance in {currency}, FX rate unavailable; update IDR manually"
                    )
                    logs.append(
                        f"  FX rate {currency}→IDR on {snapshot_date} unavailable; balance_idr=0"
                    )

            # ── Upsert ────────────────────────────────────────────────────
            con.execute("""
                INSERT INTO account_balances
                    (snapshot_date, institution, account, account_type, asset_group,
                     owner, currency, balance, balance_idr, exchange_rate, notes, import_date)
                VALUES (?, ?, ?, ?, 'Cash & Liquid', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, institution, account, owner)
                DO UPDATE SET
                    balance        = excluded.balance,
                    balance_idr    = excluded.balance_idr,
                    currency       = excluded.currency,
                    exchange_rate  = excluded.exchange_rate,
                    notes          = excluded.notes,
                    import_date    = excluded.import_date
            """, (
                snapshot_date, result.bank, str(account_id),
                _guess_account_type(acct.product_name),
                owner, currency,
                acct.closing_balance, balance_idr, exchange_rate, note, today,
            ))
            upserted += 1
            logs.append(
                f"  Saved {acct.closing_balance:,.2f} {currency} "
                f"(≈ IDR {balance_idr:,.2f}) "
                f"→ account_balances [{snapshot_date}] {result.bank} {account_id}"
            )

        con.commit()
        con.close()
        if upserted:
            logs.append(f"Closing-balance: upserted {upserted} account(s) for {result.bank}")
        else:
            logs.append(f"Closing-balance: no savings accounts found for {result.bank}")

    except Exception as exc:
        logs.append(f"Closing-balance upsert warning: {exc}")


def _upsert_cc_liability(result, logs: list):
    """
    After parsing a credit card statement, upsert the outstanding balance
    (closing_balance = total amount due) into the Stage 3 liabilities table
    so the Wealth dashboard reflects CC debt as a liability.

    Runs silently on failure so a DB issue never breaks the main XLS export.
    """
    finance_db = _config.get("finance_sqlite_db", "")
    if not finance_db:
        logs.append("CC liability upsert skipped: finance_sqlite_db not configured")
        return

    if not result.accounts:
        logs.append("CC liability upsert skipped: no accounts in result")
        return

    try:
        import re as _re
        import sqlite3 as _sqlite3
        from datetime import datetime as _dt

        owner_mappings = _config.get("owner_mappings", {})
        today = _dt.now().strftime("%Y-%m-%d")
        con = _sqlite3.connect(finance_db)
        upserted = 0

        for acct in result.accounts:
            # ── Snapshot date ─────────────────────────────────────────────
            raw_date = acct.period_end or result.period_end or result.print_date or ""
            dm = _re.match(r"(\d{2})/(\d{2})/(\d{4})", raw_date)
            if not dm:
                continue
            snapshot_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

            # ── Outstanding balance check ──────────────────────────────────
            # Negative closing_balance = CR (overpayment, bank owes customer) → no liability
            if acct.closing_balance <= 0:
                if acct.closing_balance < 0:
                    logs.append(
                        f"  Skipped CC liability for {result.bank} {acct.account_number}"
                        f": CR balance {acct.closing_balance:,.0f} (overpayment, not a liability)"
                    )
                continue

            # ── Owner ──────────────────────────────────────────────────────
            # Try result.owner first, then name-based lookup from customer_name,
            # then account-number lookup (for savings-style owner_mappings).
            owner = result.owner
            if not owner:
                customer_name = getattr(result, "customer_name", "") or ""
                for keyword, mapped_owner in owner_mappings.items():
                    if keyword.upper() in customer_name.upper():
                        owner = mapped_owner
                        break
            if not owner:
                owner = owner_mappings.get(str(acct.account_number or ""), "")

            # ── Extra fields ───────────────────────────────────────────────
            min_payment = acct.extra.get("min_payment", 0) if acct.extra else 0
            due_date    = acct.extra.get("due_date", "") if acct.extra else ""
            notes = (
                f"Auto-imported from {result.bank} CC statement"
                + (f"; min_payment={min_payment:,.0f}" if min_payment else "")
            )

            # ── Upsert ────────────────────────────────────────────────────
            con.execute("""
                INSERT INTO liabilities
                    (snapshot_date, liability_type, liability_name, institution,
                     account, owner, currency, balance, balance_idr,
                     due_date, notes, import_date)
                VALUES (?, 'credit_card', ?, ?, ?, ?, 'IDR', ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, liability_type, liability_name, owner)
                DO UPDATE SET
                    balance     = excluded.balance,
                    balance_idr = excluded.balance_idr,
                    due_date    = excluded.due_date,
                    notes       = excluded.notes,
                    import_date = excluded.import_date
            """, (
                snapshot_date,
                acct.product_name or str(acct.account_number or ""),
                result.bank, str(acct.account_number or ""), owner,
                acct.closing_balance, acct.closing_balance,
                due_date, notes, today,
            ))
            upserted += 1
            logs.append(
                f"  Saved CC liability {acct.closing_balance:,.0f} IDR"
                f" → liabilities [{snapshot_date}] {result.bank} {acct.account_number}"
            )

        con.commit()
        con.close()
        if upserted:
            logs.append(f"CC liability: upserted {upserted} card(s) for {result.bank}")
        else:
            logs.append(f"CC liability: no accounts with balance found for {result.bank}")

    except Exception as exc:
        logs.append(f"CC liability upsert warning: {exc}")


def _check_cc_reconciliation(result, logs: list):
    """
    Deterministic check: closing_balance ≈ opening_balance + total_debit - total_credit.

    Permata CC also includes bunga (interest/fees) in extra, so the formula is:
      closing ≈ opening + purchases - payments + interest

    Skips gracefully if opening_balance and total_debit/credit are all zero
    (e.g. Maybank/BCA which don't populate those fields directly).
    """
    if result.statement_type != "cc" or not result.accounts:
        return
    for acct in result.accounts:
        opening  = acct.opening_balance
        debit    = acct.total_debit
        credit   = acct.total_credit
        bunga    = (acct.extra or {}).get("bunga", 0.0)
        closing  = acct.closing_balance

        # Skip if we don't have enough data to verify
        if opening == 0.0 and debit == 0.0 and credit == 0.0:
            continue

        expected = opening + debit - credit + bunga
        diff = abs(closing - expected)
        card_id = acct.account_number or acct.product_name
        if diff > 1.0:
            logs.append(
                f"[VERIFY] CC balance mismatch for {result.bank} {card_id}: "
                f"closing={closing:,.0f}, "
                f"expected={expected:,.0f} "
                f"(opening={opening:,.0f} + debit={debit:,.0f}"
                f" - credit={credit:,.0f} + bunga={bunga:,.0f}), "
                f"diff={diff:,.0f}"
            )
        else:
            logs.append(
                f"[VERIFY] CC balance OK for {result.bank} {card_id}: "
                f"{closing:,.0f} IDR"
            )


def _upsert_bond_holdings(result, logs: list):
    """
    After parsing a Permata savings statement, upsert each bond from the
    'Rekening Investasi Obligasi' summary table into the Stage 3 holdings
    table so the Wealth dashboard shows government bond values automatically.

    Field mapping
    ─────────────
      asset_class        = "bond"
      asset_group        = "Investments"
      asset_name         = bond.product_name  (e.g. "FR0097", "INDON47")
      isin_or_code       = bond.product_name
      institution        = "Permata"
      currency           = bond.currency
      quantity           = bond.face_value     (nominal / par value)
      unit_price         = bond.market_price   (% of face value, e.g. 104.734)
      market_value       = bond.market_value   (in original currency)
      market_value_idr   = bond.market_value_idr (Saldo Rupiah from PDF)
      cost_basis         = market_value - unrealised_pl  (in original currency)
      cost_basis_idr     = market_value_idr - unrealised_pnl_idr
      unrealised_pnl_idr = unrealised_pl × statement_fx_rate
      exchange_rate      = bond.statement_fx_rate  (1.0 for IDR bonds)
      notes              = summary of market price and FX rate
    """
    finance_db = _config.get("finance_sqlite_db", "")
    if not finance_db:
        logs.append("Bond-holdings upsert skipped: finance_sqlite_db not configured")
        return

    if not getattr(result, "bonds", None):
        return

    try:
        import re as _re
        import sqlite3 as _sqlite3
        from datetime import datetime as _dt

        today = _dt.now().strftime("%Y-%m-%d")

        # Snapshot date = last day of the statement period.
        # Permata's "Tanggal Laporan" (print_date) is often the 1st of the
        # following month; the authoritative period-end lives on each
        # AccountSummary.  Fall back to print_date only if no accounts.
        raw_date = ""
        if getattr(result, "accounts", None):
            raw_date = result.accounts[0].period_end or ""
        if not raw_date:
            raw_date = result.print_date or result.period_end or ""
        dm = _re.match(r"(\d{2})/(\d{2})/(\d{4})", raw_date)
        if not dm:
            logs.append("Bond-holdings upsert skipped: could not determine snapshot date")
            return
        snapshot_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

        con = _sqlite3.connect(finance_db)
        upserted = 0

        for bond in result.bonds:
            fx   = bond.statement_fx_rate
            # Unrealized P/L in IDR (for USD bonds, convert via statement FX rate)
            unreal_pnl_idr = round(bond.unrealised_pl * fx, 2)

            # Cost basis = market value minus unrealized gain/loss
            cost_basis     = bond.market_value - bond.unrealised_pl
            cost_basis_idr = round(bond.market_value_idr - unreal_pnl_idr, 2)

            note = (
                f"Auto-imported from {result.bank} statement ({snapshot_date})"
                f" | Market price: {bond.market_price:.3f}%"
                f" | Unrealized P/L: {bond.unrealised_pl_pct:+.2f}%"
            )
            if bond.currency != "IDR":
                note += f" | FX (bank rate): 1 {bond.currency} = {fx:,.4f} IDR"

            con.execute("""
                INSERT INTO holdings
                    (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
                     institution, account, owner, currency, quantity, unit_price,
                     market_value, market_value_idr, cost_basis, cost_basis_idr,
                     unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
                     notes, import_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
                DO UPDATE SET
                    isin_or_code       = excluded.isin_or_code,
                    institution        = excluded.institution,
                    currency           = excluded.currency,
                    quantity           = excluded.quantity,
                    unit_price         = excluded.unit_price,
                    market_value       = excluded.market_value,
                    market_value_idr   = excluded.market_value_idr,
                    cost_basis         = excluded.cost_basis,
                    cost_basis_idr     = excluded.cost_basis_idr,
                    unrealised_pnl_idr = excluded.unrealised_pnl_idr,
                    exchange_rate      = excluded.exchange_rate,
                    notes              = excluded.notes,
                    import_date        = excluded.import_date
            """, (
                snapshot_date, "bond", "Investments",
                bond.product_name, bond.product_name,
                result.bank, "", result.owner or "",
                bond.currency,
                bond.face_value, bond.market_price,
                bond.market_value, bond.market_value_idr,
                cost_basis, cost_basis_idr,
                unreal_pnl_idr, fx,
                "", 0.0,          # maturity_date, coupon_rate — not in summary table
                note, today,
            ))
            upserted += 1
            logs.append(
                f"  Bond {bond.product_name} ({bond.currency}) "
                f"face={bond.face_value:,.0f}  price={bond.market_price:.3f}%"
                f"  mktval_idr={bond.market_value_idr:,.0f}"
                f"  P/L {bond.unrealised_pl_pct:+.2f}%"
            )

        con.commit()
        con.close()
        if upserted:
            logs.append(f"Bonds: upserted {upserted} position(s) for {result.bank} [{snapshot_date}]")

    except Exception as exc:
        logs.append(f"Bond-holdings upsert warning: {exc}")


def _upsert_fund_holdings(result, logs: list):
    """
    Upsert Reksa Dana (mutual fund) positions from a consolidated statement
    (e.g. Maybank) into the Stage 3 holdings table.

    Fund accounts are identified by AccountSummary entries where
    extra["account_category"] == "mutual_fund" — set by the Maybank parser.

    Field mapping
    ─────────────
      asset_class        = "mutual_fund"
      asset_group        = "Investments"
      asset_name         = account.product_name
      isin_or_code       = account.product_name
      institution        = result.bank
      currency           = account.currency
      quantity           = extra["units"]            (number of fund units)
      unit_price         = market_value_idr / units  (computed NAV per unit)
      market_value       = market_value_idr          (IDR-denominated)
      market_value_idr   = account.closing_balance
      cost_basis_idr     = market_value_idr − unrealised_pnl_idr
      unrealised_pnl_idr = extra["unrealized_gain_loss"]
      exchange_rate      = 1.0                       (IDR funds)
      notes              = fund type, growth %, import date
    """
    finance_db = _config.get("finance_sqlite_db", "")
    if not finance_db:
        logs.append("Fund-holdings upsert skipped: finance_sqlite_db not configured")
        return

    fund_accounts = [
        a for a in getattr(result, "accounts", [])
        if a.extra.get("account_category") == "mutual_fund"
    ]
    if not fund_accounts:
        return

    try:
        import re as _re
        import sqlite3 as _sqlite3
        from datetime import datetime as _dt

        today = _dt.now().strftime("%Y-%m-%d")

        # Snapshot date: prefer period_end from accounts, fall back to result fields
        raw_date = ""
        if getattr(result, "accounts", None):
            raw_date = result.accounts[0].period_end or ""
        if not raw_date:
            raw_date = result.print_date or result.period_end or ""
        dm = _re.match(r"(\d{2})/(\d{2})/(\d{4})", raw_date)
        if not dm:
            logs.append("Fund-holdings upsert skipped: could not determine snapshot date")
            return
        snapshot_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

        con = _sqlite3.connect(finance_db)
        upserted = 0

        for acc in fund_accounts:
            market_value_idr  = acc.closing_balance or 0.0
            units             = acc.extra.get("units") or 0.0
            unrealised_pnl_idr = acc.extra.get("unrealized_gain_loss") or 0.0
            fund_type         = acc.extra.get("type", "")
            growth_pct        = acc.extra.get("growth_pct", "")

            # Computed NAV per unit
            unit_price = round(market_value_idr / units, 4) if units else 0.0
            # Cost basis = market value minus unrealized P/L
            cost_basis_idr = round(market_value_idr - unrealised_pnl_idr, 2)

            note = f"Auto-imported from {result.bank} statement ({snapshot_date})"
            if fund_type:
                note += f" | Type: {fund_type}"
            if growth_pct:
                note += f" | Growth: {growth_pct}"

            con.execute("""
                INSERT INTO holdings
                    (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
                     institution, account, owner, currency, quantity, unit_price,
                     market_value, market_value_idr, cost_basis, cost_basis_idr,
                     unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
                     notes, import_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
                DO UPDATE SET
                    isin_or_code       = excluded.isin_or_code,
                    institution        = excluded.institution,
                    currency           = excluded.currency,
                    quantity           = excluded.quantity,
                    unit_price         = excluded.unit_price,
                    market_value       = excluded.market_value,
                    market_value_idr   = excluded.market_value_idr,
                    cost_basis         = excluded.cost_basis,
                    cost_basis_idr     = excluded.cost_basis_idr,
                    unrealised_pnl_idr = excluded.unrealised_pnl_idr,
                    exchange_rate      = excluded.exchange_rate,
                    notes              = excluded.notes,
                    import_date        = excluded.import_date
            """, (
                snapshot_date, "mutual_fund", "Investments",
                acc.product_name, acc.product_name,
                result.bank, "", result.owner or "",
                acc.currency,
                units, unit_price,
                market_value_idr, market_value_idr,  # market_value = IDR for IDR funds
                cost_basis_idr, cost_basis_idr,
                unrealised_pnl_idr, 1.0,
                "", 0.0,
                note, today,
            ))
            upserted += 1
            logs.append(
                f"  Fund {acc.product_name}"
                + (f" [{fund_type}]" if fund_type else "")
                + f"  units={units:,.2f}  mktval_idr={market_value_idr:,.0f}"
                + f"  P/L {unrealised_pnl_idr:+,.0f}"
            )

        con.commit()
        con.close()
        if upserted:
            logs.append(f"Funds: upserted {upserted} position(s) for {result.bank} [{snapshot_date}]")

    except Exception as exc:
        logs.append(f"Fund-holdings upsert warning: {exc}")


def _upsert_investment_holdings(result, logs: list):
    """
    Upsert IPOT equity and mutual-fund positions from a portfolio PDF into the
    Stage 3 holdings table, then gap-fill any missing months up to today.

    Gap-fill logic
    ──────────────
    When only a January PDF exists (no February PDF yet), we replicate the
    January holdings into February so the Wealth dashboard shows a continuous
    history.  The fill stops at the first month that already has IPOT holdings
    (i.e. where a later PDF was already processed) and never overwrites existing
    rows (INSERT OR IGNORE).  When the real February PDF is later processed it
    overwrites the placeholder via ON CONFLICT DO UPDATE.

    Field mapping
    ─────────────
      asset_class        = holding.asset_class  ("stock" or "mutual_fund")
      asset_group        = "Investments"
      asset_name         = holding.asset_name
      isin_or_code       = holding.isin_or_code
      institution        = "IPOT"
      account            = client code (from result.accounts[0].account_number)
      owner              = result.owner
      currency           = "IDR"
      quantity           = holding.quantity
      unit_price         = holding.unit_price   (Close price or Last NAV)
      market_value       = holding.market_value_idr
      market_value_idr   = holding.market_value_idr
      cost_basis         = holding.cost_basis_idr
      cost_basis_idr     = holding.cost_basis_idr
      unrealised_pnl_idr = holding.unrealised_pnl_idr
      exchange_rate      = 1.0  (all IDR)
    """
    finance_db = _config.get("finance_sqlite_db", "")
    if not finance_db:
        logs.append("Investment-holdings upsert skipped: finance_sqlite_db not configured")
        return

    holdings = getattr(result, "holdings", None)
    if not holdings:
        return

    try:
        import re as _re
        import sqlite3 as _sqlite3
        from datetime import datetime as _dt, date as _date
        from calendar import monthrange

        today = _dt.now().strftime("%Y-%m-%d")

        # Snapshot date: parse period_end DD/MM/YYYY → YYYY-MM-DD
        raw_date = result.period_end or result.print_date or ""
        dm = _re.match(r"(\d{2})/(\d{2})/(\d{4})", raw_date)
        if not dm:
            logs.append("Investment-holdings upsert skipped: could not determine snapshot date")
            return
        snapshot_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

        # Client code from the RDN AccountSummary
        account_id = ""
        if getattr(result, "accounts", None):
            account_id = result.accounts[0].account_number or ""

        con = _sqlite3.connect(finance_db)
        upserted = 0

        # ── Upsert the snapshot date's holdings ───────────────────────────────
        for h in holdings:
            note = (
                f"Auto-imported from {result.bank} portfolio ({snapshot_date})"
                f" | {h.asset_class.title()}: {h.isin_or_code}"
                f" | qty={h.quantity:,.0f}  price={h.unit_price:,.2f}"
                f"  cost={h.cost_basis_idr:,.0f}  mktval={h.market_value_idr:,.0f}"
            )
            con.execute("""
                INSERT INTO holdings
                    (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
                     institution, account, owner, currency, quantity, unit_price,
                     market_value, market_value_idr, cost_basis, cost_basis_idr,
                     unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
                     notes, import_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
                DO UPDATE SET
                    isin_or_code       = excluded.isin_or_code,
                    institution        = excluded.institution,
                    account            = excluded.account,
                    currency           = excluded.currency,
                    quantity           = excluded.quantity,
                    unit_price         = excluded.unit_price,
                    market_value       = excluded.market_value,
                    market_value_idr   = excluded.market_value_idr,
                    cost_basis         = excluded.cost_basis,
                    cost_basis_idr     = excluded.cost_basis_idr,
                    unrealised_pnl_idr = excluded.unrealised_pnl_idr,
                    exchange_rate      = excluded.exchange_rate,
                    notes              = excluded.notes,
                    import_date        = excluded.import_date
            """, (
                snapshot_date,
                h.asset_class, "Investments",
                h.asset_name, h.isin_or_code,
                result.bank, account_id, result.owner or "",
                "IDR",
                h.quantity, h.unit_price,
                h.market_value_idr, h.market_value_idr,
                h.cost_basis_idr, h.cost_basis_idr,
                h.unrealised_pnl_idr, 1.0,
                "", 0.0,
                note, today,
            ))
            upserted += 1
            logs.append(
                f"  {h.asset_class} {h.isin_or_code} ({h.asset_name[:30]})"
                f"  qty={h.quantity:,.0f}  price={h.unit_price:,.2f}"
                f"  mktval_idr={h.market_value_idr:,.0f}"
                f"  P/L {h.unrealised_pnl_idr:+,.0f}"
            )

        con.commit()
        if upserted:
            logs.append(
                f"Investments: upserted {upserted} position(s) for {result.bank} [{snapshot_date}]"
            )

        # ── Gap-fill: copy holdings forward into months with no data ─────────
        # Parse snapshot_date into year/month
        snap_y, snap_m, snap_d = (int(x) for x in snapshot_date.split("-"))
        today_dt = _dt.now().date()

        # Advance one month at a time from the snapshot month
        cur_y, cur_m = snap_y, snap_m
        filled = 0

        while True:
            # Move to next calendar month
            if cur_m == 12:
                cur_y, cur_m = cur_y + 1, 1
            else:
                cur_m += 1

            # Stop once we reach the current month (don't fill future months)
            if _date(cur_y, cur_m, 1) > _date(today_dt.year, today_dt.month, 1):
                break

            # Month-end date for the candidate fill month
            last_day = monthrange(cur_y, cur_m)[1]
            fill_date = f"{cur_y}-{cur_m:02d}-{last_day:02d}"

            # Check if holdings for this institution already exist for this month-end
            existing = con.execute(
                "SELECT COUNT(*) FROM holdings WHERE snapshot_date=? AND institution=? AND owner=?",
                (fill_date, result.bank, result.owner or ""),
            ).fetchone()[0]

            if existing > 0:
                # A later PDF was already processed — stop filling
                logs.append(
                    f"  Gap-fill: {fill_date} already has {result.bank} data — stopping carry-forward"
                )
                break

            # Insert carry-forward copies (INSERT OR IGNORE so a concurrent
            # real import always wins)
            for h in holdings:
                carry_note = (
                    f"Carried forward from {snapshot_date} (no {fill_date[:7]} PDF yet)"
                    f" | {h.asset_class.title()}: {h.isin_or_code}"
                )
                con.execute("""
                    INSERT OR IGNORE INTO holdings
                        (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
                         institution, account, owner, currency, quantity, unit_price,
                         market_value, market_value_idr, cost_basis, cost_basis_idr,
                         unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
                         notes, import_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    fill_date,
                    h.asset_class, "Investments",
                    h.asset_name, h.isin_or_code,
                    result.bank, account_id, result.owner or "",
                    "IDR",
                    h.quantity, h.unit_price,
                    h.market_value_idr, h.market_value_idr,
                    h.cost_basis_idr, h.cost_basis_idr,
                    h.unrealised_pnl_idr, 1.0,
                    "", 0.0,
                    carry_note, today,
                ))
            filled += 1
            logs.append(f"  Gap-fill: carried {len(holdings)} holding(s) forward to {fill_date}")

        if filled:
            logs.append(f"Investments: gap-filled {filled} month(s) after {snapshot_date}")

        con.commit()
        con.close()

    except Exception as exc:
        logs.append(f"Investment-holdings upsert warning: {exc}")
