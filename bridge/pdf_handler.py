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

Jobs run synchronously (the bridge is single-threaded). For large PDFs the
/pdf/process call may take a few seconds — the UI polls /pdf/status.
"""
import os
import json
import uuid
import logging
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# These are set by init_pdf_handler() called from bridge/server.py
_config = {}
_db_path = ""

# Short-lived map of opaque attachment_id → absolute file path.
# Populated by handle_attachments(); consumed by handle_process().
_attachment_paths: dict[str, str] = {}


def init_pdf_handler(config: dict, db_path: str):
    """Called once at bridge startup to inject config."""
    global _config, _db_path
    _config = config
    _db_path = db_path
    _init_jobs_db()


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


# ── Endpoint handlers (called by bridge/server.py router) ───────────────────
def handle_upload(request_body: bytes, content_type: str) -> tuple[int, dict]:
    """
    POST /pdf/upload
    Saves uploaded PDF to inbox dir, auto-detects bank, creates job.
    Returns job_id for subsequent /pdf/process call.
    """
    # Parse multipart — minimal implementation without external deps
    try:
        file_bytes, filename, password = _parse_multipart(request_body, content_type)
    except Exception as e:
        return 400, {"error": f"Multipart parse failed: {e}"}

    if not file_bytes:
        return 400, {"error": "No file field in request"}

    # Save to inbox
    inbox_dir = _config.get("pdf_inbox_dir", "data/pdf_inbox")
    os.makedirs(inbox_dir, exist_ok=True)
    safe_name = Path(filename).name  # strip any path components
    dest = os.path.join(inbox_dir, safe_name)
    # Avoid overwriting — append timestamp suffix if name conflicts
    if os.path.exists(dest):
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        dest = os.path.join(inbox_dir, f"{Path(safe_name).stem}_{ts}.pdf")
    with open(dest, "wb") as f:
        f.write(file_bytes)

    # Auto-detect bank/type
    bank, stmt_type = "Unknown", "unknown"
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from parsers.router import detect_bank_and_type
        bank, stmt_type = detect_bank_and_type(dest)
    except Exception as e:
        log.warning(f"Could not detect bank/type for {safe_name}: {e}")

    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id": job_id,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "source_path": dest,
        "bank": bank,
        "stmt_type": stmt_type,
        "period": "",
        "output_path": "",
        "error": "",
        "log": "",
    }
    _upsert_job(job)

    # If password provided at upload time, process immediately
    if password:
        _run_job(job_id, password)
        job = _get_job(job_id)

    return 200, {
        "job_id": job_id,
        "filename": safe_name,
        "bank": bank,
        "stmt_type": stmt_type,
        "status": job["status"],
    }


def handle_process(body: dict) -> tuple[int, dict]:
    """
    POST /pdf/process  {"job_id": "abc123", "password": "secret"}
                    OR {"attachment_id": "opaque", "password": "secret"}
    Triggers processing of an already-uploaded job or a scanned attachment.
    """
    password = body.get("password", "")

    attachment_id = body.get("attachment_id", "")
    if attachment_id:
        file_path = _attachment_paths.get(attachment_id)
        if not file_path:
            return 404, {"error": "attachment not found or scan expired; re-scan and try again"}
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "pending",
            "source_path": file_path,
            "bank": "", "stmt_type": "", "period": "",
            "output_path": "", "error": "", "log": "",
        }
        _upsert_job(job)
        _run_job(job_id, password)
        job = _get_job(job_id)
        return 200, {"job_id": job_id, "status": job["status"], "error": job.get("error", "")}

    job_id = body.get("job_id", "")
    if not job_id:
        return 400, {"error": "job_id or attachment_id required"}

    job = _get_job(job_id)
    if not job:
        return 404, {"error": "job not found"}
    if job["status"] == "done":
        return 200, {"job_id": job_id, "status": "done", "output_path": job["output_path"]}

    _run_job(job_id, password)
    job = _get_job(job_id)
    return 200, {"job_id": job_id, "status": job["status"], "error": job.get("error", "")}


def handle_status(job_id: str) -> tuple[int, dict]:
    """GET /pdf/status/<job_id>"""
    job = _get_job(job_id)
    if not job:
        return 404, {"error": "job not found"}
    result = {k: job[k] for k in ("job_id","status","bank","stmt_type","period","error")}
    if job["status"] == "done":
        result["download_url"] = f"/pdf/download/{job_id}"
    return 200, result


def handle_download(job_id: str) -> tuple[int, bytes, str]:
    """GET /pdf/download/<job_id> — returns (status, bytes, filename)"""
    job = _get_job(job_id)
    if not job:
        return 404, b'{"error":"not found"}', "application/json"
    if job["status"] != "done" or not job["output_path"]:
        return 400, b'{"error":"not ready"}', "application/json"
    output_path = job["output_path"]
    if not os.path.exists(output_path):
        return 404, b'{"error":"file missing"}', "application/json"
    with open(output_path, "rb") as f:
        data = f.read()
    filename = Path(output_path).name
    return 200, data, filename


def handle_jobs(limit: int = 50) -> tuple[int, dict]:
    """GET /pdf/jobs"""
    jobs = _list_jobs(limit)
    # Don't expose full paths
    safe = []
    for j in jobs:
        safe.append({
            "job_id": j["job_id"],
            "created_at": j["created_at"],
            "status": j["status"],
            "bank": j["bank"],
            "stmt_type": j["stmt_type"],
            "period": j["period"],
            "filename": Path(j["source_path"] or "").name,
            "error": j["error"],
        })
    return 200, {"jobs": safe}


def handle_attachments() -> tuple[int, dict]:
    """GET /pdf/attachments — list auto-detected bank PDFs from Mail.app"""
    global _attachment_paths
    try:
        from bridge.attachment_scanner import AttachmentScanner
        scanner = AttachmentScanner(
            mail_root="~/Library/Mail",
            seen_db_path=_config.get("attachment_seen_db", "data/seen_attachments.db"),
        )
        pending = scanner.scan(lookback_days=_config.get("attachment_lookback_days", 60))
        # Rebuild the opaque-ID map on every scan so stale IDs expire naturally.
        _attachment_paths = {}
        result = []
        for a in pending:
            aid = str(uuid.uuid4())
            _attachment_paths[aid] = a.file_path
            result.append({
                "attachment_id": aid,
                "filename": a.filename,
                "bank": a.bank_name,
                "received": a.received_date,
                "size_kb": round(a.size_bytes / 1024, 1),
            })
        return 200, {"attachments": result}
    except Exception as e:
        return 500, {"error": str(e)}


# ── Job runner ────────────────────────────────────────────────────────────────
def _run_job(job_id: str, password: str):
    """Execute the full PDF→XLS pipeline for a job. Updates DB in place."""
    job = _get_job(job_id)
    if not job:
        return

    job["status"] = "running"
    _upsert_job(job)

    logs = []
    try:
        src_path = job["source_path"]

        # ── Step 1: unlock if encrypted ──────────────────────────────────
        from bridge.pdf_unlock import is_encrypted, unlock_pdf
        unlocked_path = src_path
        if is_encrypted(src_path):
            if not password:
                password = _get_bank_password(job["bank"])
            if not password:
                raise ValueError(f"PDF is encrypted but no password provided for {job['bank']}")
            unlocked_dir = _config.get("pdf_unlocked_dir", "data/pdf_unlocked")
            unlocked_path = unlock_pdf(src_path, password, unlocked_dir)
            logs.append(f"Unlocked: {Path(unlocked_path).name}")
        else:
            logs.append("PDF not encrypted — no unlock needed")

        # ── Step 2: parse ─────────────────────────────────────────────────
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from parsers.router import detect_and_parse
        owner_mappings = _config.get("owner_mappings", {})
        result = detect_and_parse(unlocked_path, owner_mappings=owner_mappings)
        logs.append(f"Parsed: {result.bank} {result.statement_type} "
                    f"{result.period_start}–{result.period_end} "
                    f"({len(result.transactions)} transactions)")
        if result.raw_errors:
            logs.append(f"Parser warnings: {result.raw_errors}")

        # ── Step 2.5: auto-upsert closing balance → account_balances ─────
        #   For savings / consolidated statements, write each savings-type
        #   account's closing balance into the Stage 3 account_balances table
        #   so the Wealth dashboard picks it up without manual data entry.
        #   Covers: BCA savings, Permata savings, CIMB Niaga consol, Maybank consol.
        if result.statement_type in ("savings", "consol", "consolidated") and result.accounts:
            _upsert_closing_balance(result, logs)

        # ── Step 2.6: auto-upsert bond holdings → holdings ────────────────
        #   For statements that include Rekening Investasi Obligasi data,
        #   write each bond position into the Stage 3 holdings table so the
        #   Wealth dashboard shows government bond values and P/L.
        if getattr(result, "bonds", None):
            _upsert_bond_holdings(result, logs)

        # ── Step 3: export XLS ────────────────────────────────────────────
        from exporters.xls_writer import export
        output_dir = _config.get("xls_output_dir", "output/xls")
        output_path, _ = export(result, output_dir, owner_mappings)
        logs.append(f"Exported: {Path(output_path).name}")

        job["status"] = "done"
        job["bank"] = result.bank
        job["stmt_type"] = result.statement_type
        job["period"] = f"{result.period_start}–{result.period_end}"
        job["output_path"] = output_path
        job["error"] = ""
        job["log"] = "\n".join(logs)

    except Exception as e:
        log.error(f"Job {job_id} failed: {e}")
        job["status"] = "error"
        job["error"] = str(e)
        job["log"] = "\n".join(logs) + f"\n{traceback.format_exc()}"

    _upsert_job(job)


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
                f"Auto-imported from Permata statement ({snapshot_date})"
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
                ON CONFLICT(snapshot_date, asset_class, asset_name, owner)
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
                "Permata", "", result.owner or "",
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


def _get_bank_password(bank_name: str) -> str:
    """Load bank PDF password from secrets/banks.toml."""
    try:
        import tomllib
        secrets_path = _config.get("bank_passwords_file", "secrets/banks.toml")
        with open(secrets_path, "rb") as f:
            secrets = tomllib.load(f)
        # Normalize bank name to a TOML key: "Maybank" → "maybank"
        key = bank_name.lower().replace(" ", "_")
        return secrets.get("passwords", {}).get(key, "")
    except Exception as e:
        log.warning(f"Could not load bank password for {bank_name}: {e}")
        return ""


# ── Multipart parser (no external deps) ──────────────────────────────────────
def _parse_multipart(body: bytes, content_type: str) -> tuple[bytes, str, str]:
    """
    Minimal multipart/form-data parser.
    Returns (file_bytes, filename, password).
    """
    import re as _re
    boundary_match = _re.search(r"boundary=([^\s;]+)", content_type)
    if not boundary_match:
        raise ValueError("No boundary in Content-Type")
    boundary = ("--" + boundary_match.group(1)).encode()

    file_bytes = b""
    filename = "upload.pdf"
    password = ""

    parts = body.split(boundary)
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        header, _, content = part.partition(b"\r\n\r\n")
        content = content.rstrip(b"\r\n--")
        header_str = header.decode("utf-8", errors="replace")

        name_match = _re.search(r'name="([^"]+)"', header_str)
        fname_match = _re.search(r'filename="([^"]+)"', header_str)
        field_name = name_match.group(1) if name_match else ""

        if field_name == "file" and fname_match:
            filename = fname_match.group(1)
            file_bytes = content
        elif field_name == "password":
            password = content.decode("utf-8", errors="replace").strip()

    return file_bytes, filename, password
