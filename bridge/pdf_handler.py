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
    Triggers processing of an already-uploaded job.
    """
    job_id = body.get("job_id", "")
    password = body.get("password", "")
    if not job_id:
        return 400, {"error": "job_id required"}

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
    try:
        from bridge.attachment_scanner import AttachmentScanner
        scanner = AttachmentScanner(
            mail_root="~/Library/Mail",
            seen_db_path=_config.get("attachment_seen_db", "data/seen_attachments.db"),
        )
        pending = scanner.scan(lookback_days=_config.get("attachment_lookback_days", 60))
        return 200, {"attachments": [
            {
                "file_path": a.file_path,
                "filename": a.filename,
                "bank": a.bank_name,
                "received": a.received_date,
                "size_kb": round(a.size_bytes / 1024, 1),
            }
            for a in pending
        ]}
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
