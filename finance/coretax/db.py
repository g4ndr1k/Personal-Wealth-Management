"""CoreTax DB schema, migration, and CRUD helpers.

Nine tables in data/finance.db:
  - coretax_rows              (one row per asset per SPT year)
  - coretax_taxpayer          (per-year metadata)
  - coretax_mappings          (global learned PWM→CoreTax mapping rules)
  - coretax_import_staging    (preview area for prior-year import)
  - coretax_asset_codes       (kode lookup)
  - coretax_reconcile_runs    (every reconcile invocation persisted)
  - coretax_unmatched_pwm     (PWM rows that didn't map, scoped to a run)
  - coretax_row_components    (per-component breakdown for many-to-one matches)
  - coretax_rejected_suggestions  (negative learning — user-rejected suggestions)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

# ── Schema DDL ────────────────────────────────────────────────────────────────

CORETAX_SCHEMA = """
CREATE TABLE IF NOT EXISTS coretax_asset_codes (
    kode                  TEXT PRIMARY KEY,
    label                 TEXT NOT NULL,
    kind                  TEXT NOT NULL CHECK (kind IN ('asset','liability')),
    default_carry_forward INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS coretax_rows (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_year              INTEGER NOT NULL,
    kind                  TEXT    NOT NULL CHECK (kind IN ('asset','liability')),
    stable_key            TEXT    NOT NULL,
    kode_harta            TEXT,
    asset_type_label      TEXT,
    keterangan            TEXT,
    owner                 TEXT,
    institution           TEXT,
    account_number_masked TEXT,
    external_ref          TEXT,
    acquisition_year      INTEGER,
    prior_amount_idr      REAL,
    current_amount_idr    REAL,
    market_value_idr      REAL,
    prior_amount_source   TEXT CHECK (prior_amount_source   IN ('imported','carried_forward','manual','unset')),
    current_amount_source TEXT CHECK (current_amount_source IN ('carried_forward','auto_reconciled','manual','unset')),
    market_value_source   TEXT CHECK (market_value_source   IN ('imported','auto_reconciled','manual','unset')),
    amount_locked         INTEGER NOT NULL DEFAULT 0,
    market_value_locked   INTEGER NOT NULL DEFAULT 0,
    locked_reason         TEXT,
    last_user_edited_at   TEXT,
    last_mapping_id       INTEGER,
    notes_internal        TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE(tax_year, stable_key)
);

CREATE TABLE IF NOT EXISTS coretax_taxpayer (
    tax_year              INTEGER PRIMARY KEY,
    nama_wajib_pajak      TEXT,
    npwp                  TEXT,
    notes                 TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coretax_mappings (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    match_kind              TEXT,
    match_value             TEXT,
    target_kode_harta       TEXT,
    target_kind             TEXT,
    target_stable_key       TEXT,
    target_keterangan_template TEXT,
    confidence              REAL DEFAULT 1.0,
    created_from_tax_year   INTEGER,
    last_used_tax_year      INTEGER,
    hits                    INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(match_kind, match_value)
);

CREATE TABLE IF NOT EXISTS coretax_import_staging (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    staging_batch_id         TEXT NOT NULL,
    target_tax_year          INTEGER NOT NULL,
    source_file_name         TEXT NOT NULL,
    source_sheet_name        TEXT NOT NULL,
    source_row_no            INTEGER NOT NULL,
    source_col_b_kode        TEXT,
    source_col_c_keterangan  TEXT,
    source_col_d_acq_year    TEXT,
    source_col_e_value       TEXT,
    source_col_f_value       TEXT,
    source_col_g_value       TEXT,
    source_col_h_note        TEXT,
    parsed_kode_harta        TEXT,
    parsed_keterangan        TEXT,
    parsed_acquisition_year  INTEGER,
    parsed_prior_amount_idr  REAL,
    parsed_carry_amount_idr  REAL,
    parsed_market_value_idr  REAL,
    parsed_kind              TEXT CHECK (parsed_kind IN ('asset','liability')),
    proposed_stable_key      TEXT,
    rule_default_carry_forward INTEGER,
    user_override_carry_forward INTEGER,
    parse_warning            TEXT,
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_staging_batch ON coretax_import_staging(staging_batch_id);

CREATE TABLE IF NOT EXISTS coretax_reconcile_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_year              INTEGER NOT NULL,
    fs_start_month        TEXT,
    fs_end_month          TEXT,
    snapshot_date         TEXT,
    created_at            TEXT NOT NULL,
    summary_json          TEXT NOT NULL,
    trace_json            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coretax_unmatched_pwm (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    reconcile_run_id      INTEGER NOT NULL REFERENCES coretax_reconcile_runs(id) ON DELETE CASCADE,
    tax_year              INTEGER NOT NULL,
    source_kind           TEXT NOT NULL CHECK (source_kind IN ('account_balance','holding','liability')),
    proposed_stable_key   TEXT,
    payload_json          TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_unmatched_run ON coretax_unmatched_pwm(reconcile_run_id);
CREATE INDEX IF NOT EXISTS idx_unmatched_year ON coretax_unmatched_pwm(tax_year);

CREATE TABLE IF NOT EXISTS coretax_row_components (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    reconcile_run_id          INTEGER NOT NULL REFERENCES coretax_reconcile_runs(id) ON DELETE CASCADE,
    tax_year                  INTEGER NOT NULL,
    target_stable_key         TEXT NOT NULL,
    source_kind               TEXT NOT NULL,
    match_kind                TEXT NOT NULL,
    match_value               TEXT NOT NULL,
    component_amount_idr      REAL,
    component_market_value_idr REAL,
    pwm_label                 TEXT NOT NULL,
    confidence_level          TEXT NOT NULL,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_row_components_target ON coretax_row_components(target_stable_key, tax_year);
CREATE INDEX IF NOT EXISTS idx_row_components_run ON coretax_row_components(reconcile_run_id);

CREATE TABLE IF NOT EXISTS coretax_rejected_suggestions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_kind                  TEXT NOT NULL,
    match_value                 TEXT NOT NULL,
    rejected_target_stable_key  TEXT NOT NULL,
    rule                        TEXT NOT NULL,
    rejected_at                 TEXT NOT NULL,
    note                        TEXT,
    UNIQUE(match_kind, match_value, rejected_target_stable_key)
);
"""

# ── Seed data for coretax_asset_codes ─────────────────────────────────────────

ASSET_CODE_SEED = [
    ("012", "Tabungan",            "asset", 0),
    ("034", "Obligasi",            "asset", 0),
    ("036", "Reksadana",           "asset", 0),
    ("039", "Saham",               "asset", 0),
    ("038", "Penyertaan Modal",    "asset", 1),
    ("042", "Motor",               "asset", 1),
    ("043", "Mobil",               "asset", 1),
    ("051", "Logam mulia",         "asset", 1),
    ("061", "Tanah & Bangunan",    "asset", 1),
]


# ── Migration hook ────────────────────────────────────────────────────────────

def ensure_coretax_tables(conn) -> None:
    """Create coretax tables and seed asset codes. Idempotent."""
    conn.executescript(CORETAX_SCHEMA)
    # Backfill: add target_stable_key column to coretax_mappings if missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(coretax_mappings)").fetchall()}
    if "target_stable_key" not in cols:
        conn.execute("ALTER TABLE coretax_mappings ADD COLUMN target_stable_key TEXT")
    # Phase 1 migration: add confidence_level, confidence_score, source,
    # fingerprint_raw, last_used_at, years_used, times_confirmed columns
    _maybe_add_col(conn, cols, "coretax_mappings", "confidence_level", "TEXT NOT NULL DEFAULT 'HIGH'")
    _maybe_add_col(conn, cols, "coretax_mappings", "confidence_score", "REAL NOT NULL DEFAULT 1.0")
    _maybe_add_col(conn, cols, "coretax_mappings", "source", "TEXT NOT NULL DEFAULT 'manual'")
    _maybe_add_col(conn, cols, "coretax_mappings", "fingerprint_raw", "TEXT")
    _maybe_add_col(conn, cols, "coretax_mappings", "last_used_at", "TEXT")
    _maybe_add_col(conn, cols, "coretax_mappings", "years_used", "INTEGER NOT NULL DEFAULT 0")
    _maybe_add_col(conn, cols, "coretax_mappings", "times_confirmed", "INTEGER NOT NULL DEFAULT 0")
    # Seed asset codes (INSERT OR IGNORE is idempotent)
    conn.executemany(
        "INSERT OR IGNORE INTO coretax_asset_codes (kode, label, kind, default_carry_forward) VALUES (?, ?, ?, ?)",
        ASSET_CODE_SEED,
    )
    conn.commit()


def _maybe_add_col(conn, existing_cols: set, table: str, col: str, col_def: str) -> None:
    """Add a column to a table if it doesn't already exist."""
    if col not in existing_cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
        existing_cols.add(col)


# ── Timestamp helper ──────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Stable-key generation ─────────────────────────────────────────────────────

def make_stable_key_manual(kode_harta: str, keterangan: str, acquisition_year: int | None) -> str:
    """Generate a stable key for manually-entered or imported-without-match rows."""
    slug = _slug(keterangan or "")
    year_part = str(acquisition_year or 0)
    uid = uuid.uuid4().hex[:8]
    return f"manual:{kode_harta}:{slug}:{year_part}:{uid}"


def make_stable_key_cash(institution: str, account_number: str) -> str:
    norm_inst = _norm(institution)
    norm_acct = _norm(account_number)
    return f"pwm:account:{norm_inst}:{norm_acct}"


def make_stable_key_investment(asset_class: str, institution: str, external_ref: str, owner: str) -> str:
    norm_class = _norm(asset_class)
    norm_inst = _norm(institution)
    norm_ref = _norm(external_ref) if external_ref else _norm("")
    norm_owner = _norm(owner)
    return f"pwm:holding:{norm_class}:{norm_inst}:{norm_ref}:{norm_owner}"


def make_stable_key_liability(liability_type: str, liability_name: str, owner: str) -> str:
    return f"pwm:liability:{_norm(liability_type)}:{_norm(liability_name)}:{_norm(owner)}"


def _norm(text: str) -> str:
    return (text or "").strip().lower().replace(" ", "-")


def _slug(text: str) -> str:
    import re
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:40]


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def get_rows_for_year(conn, tax_year: int, kind: str | None = None) -> list[dict]:
    """Return all coretax_rows for a tax_year, optionally filtered by kind."""
    if kind:
        rows = conn.execute(
            "SELECT * FROM coretax_rows WHERE tax_year = ? AND kind = ? ORDER BY kode_harta, id",
            (tax_year, kind),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM coretax_rows WHERE tax_year = ? ORDER BY kode_harta, id",
            (tax_year,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_row_by_id(conn, row_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM coretax_rows WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row else None


def insert_row(conn, **fields) -> int:
    """Insert a coretax_rows record. Returns the new row id."""
    now = _utcnow()
    fields.setdefault("created_at", now)
    fields.setdefault("updated_at", now)
    fields.setdefault("amount_locked", 0)
    fields.setdefault("market_value_locked", 0)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields.keys())
    cur = conn.execute(f"INSERT INTO coretax_rows ({cols}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def update_row(conn, row_id: int, **fields) -> bool:
    """Update arbitrary fields on a coretax_rows record."""
    if not fields:
        return False
    fields["updated_at"] = _utcnow()
    sets = ", ".join(f"{k} = :{k}" for k in fields.keys())
    fields["_id"] = row_id
    conn.execute(f"UPDATE coretax_rows SET {sets} WHERE id = :_id", fields)
    conn.commit()
    return True


def delete_row(conn, row_id: int) -> bool:
    cur = conn.execute("DELETE FROM coretax_rows WHERE id = ?", (row_id,))
    conn.commit()
    return cur.rowcount > 0


def get_taxpayer(conn, tax_year: int) -> dict | None:
    row = conn.execute("SELECT * FROM coretax_taxpayer WHERE tax_year = ?", (tax_year,)).fetchone()
    return dict(row) if row else None


def upsert_taxpayer(conn, tax_year: int, nama_wajib_pajak: str | None = None,
                    npwp: str | None = None, notes: str | None = None) -> None:
    now = _utcnow()
    existing = get_taxpayer(conn, tax_year)
    if existing:
        sets = {"updated_at": now}
        if nama_wajib_pajak is not None:
            sets["nama_wajib_pajak"] = nama_wajib_pajak
        if npwp is not None:
            sets["npwp"] = npwp
        if notes is not None:
            sets["notes"] = notes
        sql = ", ".join(f"{k} = ?" for k in sets.keys())
        conn.execute(f"UPDATE coretax_taxpayer SET {sql} WHERE tax_year = ?",
                     list(sets.values()) + [tax_year])
    else:
        conn.execute(
            "INSERT INTO coretax_taxpayer (tax_year, nama_wajib_pajak, npwp, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tax_year, nama_wajib_pajak or "", npwp or "", notes or "", now, now),
        )
    conn.commit()


def get_asset_codes(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM coretax_asset_codes ORDER BY kode").fetchall()
    return [dict(r) for r in rows]


def get_mappings(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM coretax_mappings ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def upsert_mapping(conn, match_kind: str, match_value: str,
                   target_kode_harta: str, target_kind: str,
                   target_stable_key: str | None = None,
                   target_keterangan_template: str | None = None,
                   confidence: float = 1.0,
                   created_from_tax_year: int | None = None) -> int:
    """Upsert a learned mapping. Returns the mapping id."""
    now = _utcnow()
    existing = conn.execute(
        "SELECT id, hits FROM coretax_mappings WHERE match_kind = ? AND match_value = ?",
        (match_kind, match_value),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE coretax_mappings SET
               target_kode_harta = ?, target_kind = ?, target_stable_key = ?,
               target_keterangan_template = ?,
               confidence = ?, created_from_tax_year = ?, updated_at = ?
               WHERE id = ?""",
            (target_kode_harta, target_kind, target_stable_key,
             target_keterangan_template,
             confidence, created_from_tax_year, now, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO coretax_mappings
           (match_kind, match_value, target_kode_harta, target_kind,
            target_stable_key, target_keterangan_template,
            confidence, created_from_tax_year,
            last_used_tax_year, hits, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?)""",
        (match_kind, match_value, target_kode_harta, target_kind,
         target_stable_key, target_keterangan_template,
         confidence, created_from_tax_year, now, now),
    )
    conn.commit()
    return cur.lastrowid


def increment_mapping_hit(conn, mapping_id: int, tax_year: int) -> None:
    """Increment hits and update last_used_tax_year for a mapping."""
    conn.execute(
        "UPDATE coretax_mappings SET hits = hits + 1, last_used_tax_year = ?, updated_at = ? WHERE id = ?",
        (tax_year, _utcnow(), mapping_id),
    )
    conn.commit()


def delete_mapping(conn, mapping_id: int) -> bool:
    cur = conn.execute("DELETE FROM coretax_mappings WHERE id = ?", (mapping_id,))
    conn.commit()
    return cur.rowcount > 0


def insert_reconcile_run(conn, tax_year: int, fs_start_month: str,
                         fs_end_month: str, snapshot_date: str | None,
                         summary: dict, trace: list) -> int:
    """Insert a reconcile run record. Returns the run id."""
    now = _utcnow()
    import json
    cur = conn.execute(
        """INSERT INTO coretax_reconcile_runs
           (tax_year, fs_start_month, fs_end_month, snapshot_date, created_at, summary_json, trace_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tax_year, fs_start_month, fs_end_month, snapshot_date, now,
         json.dumps(summary, ensure_ascii=False), json.dumps(trace, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def get_reconcile_runs(conn, tax_year: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM coretax_reconcile_runs WHERE tax_year = ? ORDER BY id DESC",
        (tax_year,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_reconcile_run(conn, tax_year: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM coretax_reconcile_runs WHERE tax_year = ? ORDER BY id DESC LIMIT 1",
        (tax_year,),
    ).fetchone()
    return dict(row) if row else None


def insert_unmatched_pwm(conn, reconcile_run_id: int, tax_year: int,
                         source_kind: str, payload: dict,
                         proposed_stable_key: str | None = None) -> int:
    import json
    now = _utcnow()
    cur = conn.execute(
        """INSERT INTO coretax_unmatched_pwm
           (reconcile_run_id, tax_year, source_kind, proposed_stable_key, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (reconcile_run_id, tax_year, source_kind, proposed_stable_key,
         json.dumps(payload, ensure_ascii=False), now),
    )
    conn.commit()
    return cur.lastrowid


def get_unmatched_for_run(conn, run_id: int) -> list[dict]:
    import json
    rows = conn.execute(
        "SELECT * FROM coretax_unmatched_pwm WHERE reconcile_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"])
        result.append(d)
    return result


def get_staging_batch(conn, batch_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM coretax_import_staging WHERE staging_batch_id = ? ORDER BY source_row_no",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_staging_row(conn, row_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM coretax_import_staging WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row else None


def delete_staging_batch(conn, batch_id: str) -> int:
    cur = conn.execute("DELETE FROM coretax_import_staging WHERE staging_batch_id = ?", (batch_id,))
    conn.commit()
    return cur.rowcount


def update_staging_row(conn, row_id: int, **fields) -> bool:
    if not fields:
        return False
    sets = ", ".join(f"{k} = :{k}" for k in fields.keys())
    fields["_id"] = row_id
    conn.execute(f"UPDATE coretax_import_staging SET {sets} WHERE id = :_id", fields)
    conn.commit()
    return True


def get_summary_for_year(conn, tax_year: int) -> dict:
    """Return summary stats: totals by kode, lock counts, coverage %."""
    rows = conn.execute(
        "SELECT * FROM coretax_rows WHERE tax_year = ? ORDER BY kode_harta, id",
        (tax_year,),
    ).fetchall()

    total_rows = len(rows)
    amount_locked_count = sum(1 for r in rows if r["amount_locked"])
    mv_locked_count = sum(1 for r in rows if r["market_value_locked"])
    filled_count = sum(1 for r in rows if r["current_amount_idr"] is not None)
    by_kode: dict[str, dict] = {}
    for r in rows:
        kode = r["kode_harta"] or "unknown"
        bucket = by_kode.setdefault(kode, {"kode": kode, "label": "", "count": 0,
                                            "total_prior": 0.0, "total_current": 0.0,
                                            "total_market": 0.0})
        bucket["count"] += 1
        bucket["total_prior"] += r["prior_amount_idr"] or 0.0
        bucket["total_current"] += r["current_amount_idr"] or 0.0
        bucket["total_market"] += r["market_value_idr"] or 0.0

    coverage_pct = round(filled_count / total_rows * 100, 1) if total_rows else 0.0

    return {
        "tax_year": tax_year,
        "total_rows": total_rows,
        "filled_rows": filled_count,
        "amount_locked_count": amount_locked_count,
        "market_value_locked_count": mv_locked_count,
        "coverage_pct": coverage_pct,
        "by_kode": list(by_kode.values()),
    }


# ── Phase 1: New CRUD helpers for mapping-first reconciliation ──────────────


def assign_mapping(conn, match_kind: str, match_value: str,
                   target_kode_harta: str, target_kind: str,
                   target_stable_key: str,
                   source: str = "manual",
                   confidence_score: float = 1.0,
                   confidence_level: str | None = None,
                   fingerprint_raw: str | None = None,
                   target_keterangan_template: str | None = None,
                   created_from_tax_year: int | None = None) -> int:
    """Insert or update a mapping.  Single write path for all mapping operations.

    Returns the mapping id.
    """
    if confidence_level is None:
        from finance.coretax.confidence import derive_level
        confidence_level = derive_level(confidence_score)
    now = _utcnow()
    existing = conn.execute(
        "SELECT id FROM coretax_mappings WHERE match_kind = ? AND match_value = ?",
        (match_kind, match_value),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE coretax_mappings SET
               target_kode_harta=?, target_kind=?, target_stable_key=?,
               target_keterangan_template=?, source=?,
               confidence_score=?, confidence_level=?,
               fingerprint_raw=?, updated_at=?
               WHERE id=?""",
            (target_kode_harta, target_kind, target_stable_key,
             target_keterangan_template, source,
             confidence_score, confidence_level,
             fingerprint_raw, now, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO coretax_mappings
           (match_kind, match_value, target_kode_harta, target_kind,
            target_stable_key, target_keterangan_template,
            source, confidence_score, confidence_level,
            fingerprint_raw, created_from_tax_year,
            last_used_tax_year, hits, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (match_kind, match_value, target_kode_harta, target_kind,
         target_stable_key, target_keterangan_template,
         source, confidence_score, confidence_level,
         fingerprint_raw, created_from_tax_year,
         None, 0, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_mappings_grouped(conn, tax_year: int | None = None,
                          bucket: str | None = None,
                          source_kind: str | None = None,
                          limit: int = 100, cursor: str | None = None) -> dict:
    """Return mappings grouped by target_stable_key with pagination.

    Returns { items: [...], next_cursor: str|None, total_estimate: int }.
    """
    import base64, json
    limit = min(max(limit, 1), 1000)
    offset = 0
    if cursor:
        try:
            decoded = json.loads(base64.b64decode(cursor))
            offset = int(decoded.get("offset", 0))
        except Exception:
            pass

    rows = conn.execute(
        """SELECT m.*, cr.keterangan AS target_keterangan, cr.kode_harta AS target_kode
           FROM coretax_mappings m
           LEFT JOIN coretax_rows cr ON cr.stable_key = m.target_stable_key
           ORDER BY m.target_stable_key, m.id
           LIMIT ? OFFSET ?""",
        (limit + 1, offset),
    ).fetchall()

    has_next = len(rows) > limit
    rows = rows[:limit]
    total = conn.execute("SELECT COUNT(*) AS c FROM coretax_mappings").fetchone()["c"]

    next_cursor = None
    if has_next:
        next_cursor = base64.b64encode(json.dumps({"offset": offset + limit}).encode()).decode()

    return {"items": [dict(r) for r in rows], "next_cursor": next_cursor, "total_estimate": total}


def find_stale_mappings(conn, tax_year: int) -> list[dict]:
    """Find mappings whose target_stable_key no longer exists in coretax_rows
    for the given tax_year.  Returns list of mapping dicts with stale_reason."""
    rows = conn.execute(
        """SELECT m.*
           FROM coretax_mappings m
           LEFT JOIN coretax_rows cr ON cr.stable_key = m.target_stable_key AND cr.tax_year = ?
           WHERE cr.id IS NULL""",
        (tax_year,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["stale_reason"] = "target_missing"
        result.append(d)
    return result


def find_lifecycle_mappings(conn, tax_year: int) -> dict:
    """Classify mappings into lifecycle buckets.

    Returns { STALE: [...], WEAK: [...], UNUSED: [...], ORPHANED: [...] }.
    """
    from finance.coretax.confidence import MEDIUM_THRESHOLD, STALE_YEAR_THRESHOLD

    all_mappings = conn.execute("SELECT * FROM coretax_mappings ORDER BY id").fetchall()
    row_keys = {r["stable_key"] for r in conn.execute(
        "SELECT DISTINCT stable_key FROM coretax_rows WHERE tax_year = ?", (tax_year,)
    ).fetchall()}

    buckets: dict[str, list[dict]] = {"STALE": [], "WEAK": [], "UNUSED": [], "ORPHANED": []}

    for m in all_mappings:
        m = dict(m)
        target_key = m.get("target_stable_key")
        score = float(m.get("confidence_score", 1.0))
        years_used = int(m.get("years_used", 0))
        last_used_year = m.get("last_used_tax_year")
        created_year = m.get("created_from_tax_year")

        # STALE: target row doesn't exist for this tax_year
        if target_key and target_key not in row_keys:
            m["lifecycle_bucket"] = "STALE"
            buckets["STALE"].append(m)
            continue

        # WEAK: confidence_score decayed below MEDIUM_THRESHOLD
        if score < MEDIUM_THRESHOLD:
            m["lifecycle_bucket"] = "WEAK"
            buckets["WEAK"].append(m)
            continue

        # UNUSED: not used in last N reconcile runs
        if last_used_year is None and created_year is not None:
            years_since_creation = tax_year - created_year
            if years_since_creation >= STALE_YEAR_THRESHOLD:
                m["lifecycle_bucket"] = "UNUSED"
                buckets["UNUSED"].append(m)
                continue
        elif last_used_year is not None:
            years_since_use = tax_year - last_used_year
            if years_since_use >= STALE_YEAR_THRESHOLD:
                m["lifecycle_bucket"] = "UNUSED"
                buckets["UNUSED"].append(m)
                continue

    return buckets


def update_mapping_confidence(conn, mapping_id: int, **fields) -> bool:
    """Update confidence-related fields on a mapping."""
    if not fields:
        return False
    fields["updated_at"] = _utcnow()
    sets = ", ".join(f"{k} = :{k}" for k in fields.keys())
    fields["_id"] = mapping_id
    conn.execute(f"UPDATE coretax_mappings SET {sets} WHERE id = :_id", fields)
    conn.commit()
    return True


def find_rename_candidates(conn, tax_year: int, stale_mappings: list[dict] | None = None) -> list[dict]:
    """Find potential renames for stale signature-based mappings.

    For each stale mapping with match_kind in (holding_signature, liability_signature),
    tries to find a live PWM item that matches on institution + owner but has a
    different asset_name / liability_name.
    """
    from finance.coretax.fingerprint import derive, is_volatile
    from finance.coretax.pwm_universe import snapshot

    if stale_mappings is None:
        stale_mappings = find_stale_mappings(conn, tax_year)

    pwm_items = snapshot(conn, tax_year)
    # Index by (source_kind, institution_norm, owner_norm)
    pwm_index: dict[tuple, list[dict]] = {}
    for item in pwm_items:
        inst = (item.get("institution") or "").strip().lower()
        owner = (item.get("owner") or "").strip().lower()
        key = (item["source_kind"], inst, owner)
        pwm_index.setdefault(key, []).append(item)

    candidates = []
    for m in stale_mappings:
        mk = m.get("match_kind", "")
        if not is_volatile(mk):
            continue
        raw = m.get("fingerprint_raw") or ""
        parts = raw.split(":")
        # holding_signature: asset_class:institution:asset_name:owner
        # liability_signature: liability_type:liability_name:owner
        if mk == "holding_signature" and len(parts) >= 4:
            inst = parts[1]
            owner = parts[3]
            key = ("holding", inst, owner)
        elif mk == "liability_signature" and len(parts) >= 3:
            inst = ""
            owner = parts[2]
            key = ("liability", inst, owner)
        else:
            continue

        for item in pwm_index.get(key, []):
            fp = derive(item)
            if fp.match_value != m["match_value"]:
                candidates.append({
                    "mapping_id": m["id"],
                    "old_match_value": m["match_value"],
                    "new_match_value": fp.match_value,
                    "new_fingerprint_raw": fp.fingerprint_raw,
                    "pwm_label": _pwm_label(item),
                })

    return candidates


def _pwm_label(item: dict) -> str:
    """Build a display label for a PWM item."""
    kind = item.get("source_kind", "")
    if kind == "account_balance":
        return f"{item.get('institution', '')} / {item.get('account', '')}"
    if kind == "holding":
        return f"{item.get('institution', '')} / {item.get('asset_name', '')}"
    if kind == "liability":
        return f"{item.get('liability_type', '')} / {item.get('liability_name', '')}"
    return str(item.get("source_id", ""))


# ── Component breakdown helpers ─────────────────────────────────────────────


def replace_row_components_for_targets(conn, run_id: int, tax_year: int,
                                       target_stable_keys: list[str],
                                       components: list[dict]) -> None:
    """Replace component rows for the given targets in the given run.

    This is the single write path for component breakdowns from reconcile.
    Deletes existing components for (run_id, target_stable_key) pairs,
    then inserts the new ones.
    """
    now = _utcnow()
    for key in target_stable_keys:
        conn.execute(
            "DELETE FROM coretax_row_components WHERE reconcile_run_id = ? AND target_stable_key = ?",
            (run_id, key),
        )
    for comp in components:
        conn.execute(
            """INSERT INTO coretax_row_components
               (reconcile_run_id, tax_year, target_stable_key, source_kind,
                match_kind, match_value, component_amount_idr,
                component_market_value_idr, pwm_label, confidence_level, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, tax_year, comp["target_stable_key"], comp["source_kind"],
             comp["match_kind"], comp["match_value"],
             comp.get("component_amount_idr"),
             comp.get("component_market_value_idr"),
             comp["pwm_label"], comp["confidence_level"], now),
        )
    conn.commit()


def list_row_components(conn, target_stable_key: str, tax_year: int,
                        run_id: int | None = None) -> list[dict]:
    """List component breakdown for a target row."""
    if run_id:
        rows = conn.execute(
            """SELECT * FROM coretax_row_components
               WHERE target_stable_key = ? AND tax_year = ? AND reconcile_run_id = ?
               ORDER BY id""",
            (target_stable_key, tax_year, run_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM coretax_row_components
               WHERE target_stable_key = ? AND tax_year = ?
               ORDER BY reconcile_run_id DESC, id""",
            (target_stable_key, tax_year),
        ).fetchall()
    return [dict(r) for r in rows]


def list_component_history(conn, match_kind: str, match_value: str,
                           limit: int = 100) -> list[dict]:
    """Reverse trace: where has this PWM fingerprint been routed across runs/years."""
    rows = conn.execute(
        """SELECT rc.*, crr.tax_year AS run_tax_year, crr.created_at AS run_created_at
           FROM coretax_row_components rc
           JOIN coretax_reconcile_runs crr ON crr.id = rc.reconcile_run_id
           WHERE rc.match_kind = ? AND rc.match_value = ?
           ORDER BY crr.tax_year DESC, crr.id DESC
           LIMIT ?""",
        (match_kind, match_value, min(limit, 1000)),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Rejected suggestions helpers ────────────────────────────────────────────


def insert_rejected_suggestion(conn, match_kind: str, match_value: str,
                               rejected_target_stable_key: str, rule: str,
                               note: str | None = None) -> int:
    """Record a rejected suggestion so it stops being proposed."""
    now = _utcnow()
    cur = conn.execute(
        """INSERT OR IGNORE INTO coretax_rejected_suggestions
           (match_kind, match_value, rejected_target_stable_key, rule, rejected_at, note)
           VALUES (?,?,?,?,?,?)""",
        (match_kind, match_value, rejected_target_stable_key, rule, now, note),
    )
    conn.commit()
    return cur.lastrowid


def get_rejected_suggestions(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM coretax_rejected_suggestions ORDER BY rejected_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_rejected_suggestion(conn, suggestion_id: int) -> bool:
    cur = conn.execute("DELETE FROM coretax_rejected_suggestions WHERE id = ?", (suggestion_id,))
    conn.commit()
    return cur.rowcount > 0


def get_rejected_pairs(conn) -> set[tuple[str, str, str]]:
    """Return set of (match_kind, match_value, rejected_target_stable_key) for filtering suggestions."""
    rows = conn.execute(
        "SELECT match_kind, match_value, rejected_target_stable_key FROM coretax_rejected_suggestions"
    ).fetchall()
    return {(r["match_kind"], r["match_value"], r["rejected_target_stable_key"]) for r in rows}


# ── Unmapped PWM computation ────────────────────────────────────────────────


def compute_unmapped_pwm(conn, tax_year: int, snapshot_date: str | None = None) -> list[dict]:
    """Compute unmapped PWM items fresh against current data.

    Unlike the per-run unmatched_pwm table, this always reflects the current
    mapping state so it never goes stale.
    """
    from finance.coretax.fingerprint import derive
    from finance.coretax.pwm_universe import snapshot

    pwm_items = snapshot(conn, tax_year, snapshot_date)
    mappings = get_mappings(conn)
    mapping_keys = {(m["match_kind"], m["match_value"]) for m in mappings}

    unmapped = []
    for item in pwm_items:
        fp = derive(item)
        if (fp.match_kind, fp.match_value) not in mapping_keys:
            unmapped.append({
                "source_kind": item["source_kind"],
                "source_id": item.get("source_id"),
                "match_kind": fp.match_kind,
                "match_value": fp.match_value,
                "fingerprint_raw": fp.fingerprint_raw,
                "payload": item,
                "pwm_label": _pwm_label(item),
            })
    return unmapped
