"""
SQLite schema and connection helpers for Stage 2 finance read cache.

The DB is a throw-away read cache — safe to delete and rebuild anytime by
running:  python3 -m finance.sync

Schema notes
────────────
  transactions   — mirror of the Google Sheets Transactions tab
  merchant_aliases — mirror of Merchant Aliases tab
  categories     — mirror of Categories tab
  currency_codes — mirror of Currency Codes tab
  sync_log       — one row per sync run (for /api/health and --status)
"""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    date              TEXT    NOT NULL,
    amount            REAL    NOT NULL,
    original_currency TEXT,
    original_amount   REAL,
    exchange_rate     REAL,
    raw_description   TEXT    NOT NULL,
    merchant          TEXT,
    category          TEXT,
    institution       TEXT    NOT NULL,
    account           TEXT,
    owner             TEXT    NOT NULL,
    notes             TEXT    DEFAULT '',
    hash              TEXT    UNIQUE NOT NULL,
    import_date       TEXT,
    import_file       TEXT,
    synced_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tx_date      ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_yearmonth ON transactions(strftime('%Y-%m', date));
CREATE INDEX IF NOT EXISTS idx_tx_category  ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_tx_owner     ON transactions(owner);
CREATE INDEX IF NOT EXISTS idx_tx_hash      ON transactions(hash);

CREATE TABLE IF NOT EXISTS merchant_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant    TEXT    NOT NULL,
    alias       TEXT    NOT NULL,
    category    TEXT,
    match_type  TEXT    DEFAULT 'exact',
    added_date  TEXT,
    synced_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT    UNIQUE NOT NULL,
    icon            TEXT    DEFAULT '',
    sort_order      INTEGER DEFAULT 99,
    is_recurring    INTEGER DEFAULT 0,
    monthly_budget  REAL,
    synced_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS currency_codes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    currency_code   TEXT    UNIQUE NOT NULL,
    currency_name   TEXT,
    symbol          TEXT,
    flag_emoji      TEXT,
    country_hints   TEXT,
    decimal_places  INTEGER DEFAULT 2,
    synced_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at           TEXT    NOT NULL,
    transactions_count  INTEGER DEFAULT 0,
    aliases_count       INTEGER DEFAULT 0,
    categories_count    INTEGER DEFAULT 0,
    currencies_count    INTEGER DEFAULT 0,
    duration_s          REAL,
    notes               TEXT    DEFAULT ''
);
"""


def open_db(db_path: str) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database at *db_path*.

    Creates parent directories as needed.  Applies WAL mode for concurrent
    reads while the sync engine writes.  Returns a connection with
    row_factory = sqlite3.Row so results behave like dicts.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def get_conn(db_path: str):
    """
    Context manager that opens a connection, commits on clean exit,
    rolls back on exception, and always closes.

    Usage::

        with get_conn(db_path) as conn:
            conn.execute(...)
    """
    conn = open_db(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
