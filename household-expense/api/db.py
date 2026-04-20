"""SQLite database layer — schema, connection, and initialization."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("HOUSEHOLD_DB_PATH", "/app/data/household.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS household_transactions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_txn_id       TEXT    UNIQUE NOT NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    txn_datetime        TEXT    NOT NULL,
    amount              INTEGER NOT NULL CHECK(amount > 0),
    currency            TEXT    NOT NULL DEFAULT 'IDR',
    category_code       TEXT    NOT NULL REFERENCES household_categories(code),
    merchant            TEXT    DEFAULT '',
    description         TEXT    DEFAULT '',
    payment_method      TEXT    NOT NULL DEFAULT 'cash',
    cash_pool_id        TEXT    REFERENCES cash_pools(id),
    recorded_by         TEXT    NOT NULL,
    note                TEXT    DEFAULT '',
    reconcile_status    TEXT    NOT NULL DEFAULT 'pending',
    matched_pwm_txn_id  TEXT    DEFAULT NULL,
    reconciled_at       TEXT    DEFAULT NULL,
    is_deleted          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_htx_txn_datetime ON household_transactions(txn_datetime);
CREATE INDEX IF NOT EXISTS idx_htx_reconcile    ON household_transactions(reconcile_status);
CREATE INDEX IF NOT EXISTS idx_htx_category     ON household_transactions(category_code);
CREATE INDEX IF NOT EXISTS idx_htx_deleted      ON household_transactions(is_deleted);
CREATE INDEX IF NOT EXISTS idx_htx_pool         ON household_transactions(cash_pool_id);

CREATE TABLE IF NOT EXISTS household_categories (
    code        TEXT    PRIMARY KEY,
    label_id    TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 99,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cash_pools (
    id                TEXT    PRIMARY KEY,
    name              TEXT    NOT NULL,
    funded_amount     INTEGER NOT NULL,
    funded_at         TEXT    NOT NULL,
    remaining_amount  INTEGER NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'active',
    notes             TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_users (
    id              TEXT    PRIMARY KEY,
    username        TEXT    UNIQUE NOT NULL,
    display_name    TEXT    NOT NULL,
    password_hash   TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL
);
"""


def _set_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA secure_delete=ON")


def init_db(db_path: str = DB_PATH) -> None:
    """Create schema if tables don't exist yet."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _set_pragmas(conn)
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db(db_path: str = DB_PATH):
    """Yield a connection with pragmas set; auto-commit on clean exit."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _set_pragmas(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
