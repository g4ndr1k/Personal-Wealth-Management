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

Precision note
──────────────
  Monetary values use SQLite REAL (IEEE 754 double).  For household-scale
  IDR amounts (< 100 billion), float precision is sufficient (integers up
  to 2**53 are exactly representable).  If sub-IDR precision or multi-
  currency aggregation becomes needed, migrate to INTEGER (cents).

Foreign-key note
────────────────
  PRAGMA foreign_keys=ON is set for forward compatibility, but no table
  currently declares REFERENCES or FOREIGN KEY constraints.

executescript note
─────────────────
  conn.executescript() implicitly COMMITs any pending transaction before
  running the supplied SQL.  This is safe during open_db() (no pending
  transaction exists), but be careful if called elsewhere.
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
    synced_at         TEXT    NOT NULL,
    ollama_suggestion TEXT,
    suggested_merchant TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_date        ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_yearmonth   ON transactions(strftime('%Y-%m', date));
CREATE INDEX IF NOT EXISTS idx_tx_category    ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_tx_owner       ON transactions(owner);
CREATE INDEX IF NOT EXISTS idx_tx_institution ON transactions(institution);
CREATE INDEX IF NOT EXISTS idx_tx_account     ON transactions(account);

CREATE TABLE IF NOT EXISTS merchant_aliases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant       TEXT    NOT NULL,
    alias          TEXT    NOT NULL,
    category       TEXT,
    match_type     TEXT    DEFAULT 'exact',
    added_date     TEXT,
    owner_filter   TEXT    DEFAULT '',
    account_filter TEXT    DEFAULT '',
    synced_at      TEXT    NOT NULL,
    UNIQUE(alias, owner_filter, account_filter)
);

CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT    UNIQUE NOT NULL,
    icon            TEXT    DEFAULT '',
    sort_order      INTEGER DEFAULT 99,
    is_recurring    INTEGER DEFAULT 0,
    monthly_budget  REAL,
    category_group  TEXT    DEFAULT '',
    subcategory     TEXT    DEFAULT '',
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

-- ── Stage 3: Wealth Management ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS account_balances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT    NOT NULL,
    institution     TEXT    NOT NULL,
    account         TEXT    NOT NULL,
    account_type    TEXT    DEFAULT 'savings',
    asset_group     TEXT    DEFAULT 'Cash & Liquid',
    owner           TEXT    DEFAULT '',
    currency        TEXT    DEFAULT 'IDR',
    balance         REAL    DEFAULT 0,
    balance_idr     REAL    DEFAULT 0,
    exchange_rate   REAL    DEFAULT 1.0,
    notes           TEXT    DEFAULT '',
    import_date     TEXT    DEFAULT '',
    UNIQUE(snapshot_date, institution, account, owner)
);
CREATE INDEX IF NOT EXISTS idx_balances_date  ON account_balances(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_balances_owner ON account_balances(owner);

CREATE TABLE IF NOT EXISTS holdings (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date        TEXT    NOT NULL,
    asset_class          TEXT    NOT NULL,
    asset_group          TEXT    DEFAULT 'Investments',
    asset_name           TEXT    NOT NULL,
    isin_or_code         TEXT    DEFAULT '',
    institution          TEXT    DEFAULT '',
    account              TEXT    DEFAULT '',
    owner                TEXT    DEFAULT '',
    currency             TEXT    DEFAULT 'IDR',
    quantity             REAL    DEFAULT 0,
    unit_price           REAL    DEFAULT 0,
    market_value         REAL    DEFAULT 0,
    market_value_idr     REAL    DEFAULT 0,
    cost_basis           REAL    DEFAULT 0,
    cost_basis_idr       REAL    DEFAULT 0,
    unrealised_pnl_idr   REAL    DEFAULT 0,
    exchange_rate        REAL    DEFAULT 1.0,
    maturity_date        TEXT    DEFAULT '',
    coupon_rate          REAL    DEFAULT 0,
    last_appraised_date  TEXT    DEFAULT '',
    notes                TEXT    DEFAULT '',
    import_date          TEXT    DEFAULT '',
    UNIQUE(snapshot_date, asset_class, asset_name, owner, institution)
);
CREATE INDEX IF NOT EXISTS idx_holdings_date  ON holdings(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_holdings_class ON holdings(asset_class);
CREATE INDEX IF NOT EXISTS idx_holdings_group ON holdings(asset_group);
CREATE INDEX IF NOT EXISTS idx_holdings_owner ON holdings(owner);

CREATE TABLE IF NOT EXISTS liabilities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date        TEXT    NOT NULL,
    liability_type       TEXT    NOT NULL,
    liability_name       TEXT    NOT NULL,
    institution          TEXT    DEFAULT '',
    account              TEXT    DEFAULT '',
    owner                TEXT    DEFAULT '',
    currency             TEXT    DEFAULT 'IDR',
    balance              REAL    DEFAULT 0,
    balance_idr          REAL    DEFAULT 0,
    due_date             TEXT    DEFAULT '',
    notes                TEXT    DEFAULT '',
    import_date          TEXT    DEFAULT '',
    UNIQUE(snapshot_date, liability_type, liability_name, owner, institution, account)
);
CREATE INDEX IF NOT EXISTS idx_liabilities_date  ON liabilities(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_liabilities_owner ON liabilities(owner);

CREATE TABLE IF NOT EXISTS net_worth_snapshots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date           TEXT    NOT NULL UNIQUE,
    savings_idr             REAL    DEFAULT 0,
    checking_idr            REAL    DEFAULT 0,
    money_market_idr        REAL    DEFAULT 0,
    physical_cash_idr       REAL    DEFAULT 0,
    bonds_idr               REAL    DEFAULT 0,
    stocks_idr              REAL    DEFAULT 0,
    mutual_funds_idr        REAL    DEFAULT 0,
    retirement_idr          REAL    DEFAULT 0,
    crypto_idr              REAL    DEFAULT 0,
    real_estate_idr         REAL    DEFAULT 0,
    vehicles_idr            REAL    DEFAULT 0,
    gold_idr                REAL    DEFAULT 0,
    other_assets_idr        REAL    DEFAULT 0,
    total_assets_idr        REAL    DEFAULT 0,
    mortgages_idr           REAL    DEFAULT 0,
    personal_loans_idr      REAL    DEFAULT 0,
    credit_card_debt_idr    REAL    DEFAULT 0,
    taxes_owed_idr          REAL    DEFAULT 0,
    other_liabilities_idr   REAL    DEFAULT 0,
    total_liabilities_idr   REAL    DEFAULT 0,
    net_worth_idr           REAL    DEFAULT 0,
    mom_change_idr          REAL    DEFAULT 0,
    notes                   TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_nw_date ON net_worth_snapshots(snapshot_date);
"""

HOLDINGS_TABLE_SQL = """
CREATE TABLE holdings (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date        TEXT    NOT NULL,
    asset_class          TEXT    NOT NULL,
    asset_group          TEXT    DEFAULT 'Investments',
    asset_name           TEXT    NOT NULL,
    isin_or_code         TEXT    DEFAULT '',
    institution          TEXT    DEFAULT '',
    account              TEXT    DEFAULT '',
    owner                TEXT    DEFAULT '',
    currency             TEXT    DEFAULT 'IDR',
    quantity             REAL    DEFAULT 0,
    unit_price           REAL    DEFAULT 0,
    market_value         REAL    DEFAULT 0,
    market_value_idr     REAL    DEFAULT 0,
    cost_basis           REAL    DEFAULT 0,
    cost_basis_idr       REAL    DEFAULT 0,
    unrealised_pnl_idr   REAL    DEFAULT 0,
    exchange_rate        REAL    DEFAULT 1.0,
    maturity_date        TEXT    DEFAULT '',
    coupon_rate          REAL    DEFAULT 0,
    last_appraised_date  TEXT    DEFAULT '',
    notes                TEXT    DEFAULT '',
    import_date          TEXT    DEFAULT '',
    UNIQUE(snapshot_date, asset_class, asset_name, owner, institution)
);
"""

LIABILITIES_TABLE_SQL = """
CREATE TABLE liabilities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date        TEXT    NOT NULL,
    liability_type       TEXT    NOT NULL,
    liability_name       TEXT    NOT NULL,
    institution          TEXT    DEFAULT '',
    account              TEXT    DEFAULT '',
    owner                TEXT    DEFAULT '',
    currency             TEXT    DEFAULT 'IDR',
    balance              REAL    DEFAULT 0,
    balance_idr          REAL    DEFAULT 0,
    due_date             TEXT    DEFAULT '',
    notes                TEXT    DEFAULT '',
    import_date          TEXT    DEFAULT '',
    UNIQUE(snapshot_date, liability_type, liability_name, owner, institution, account)
);
"""


def _table_sql(conn: sqlite3.Connection, table_name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return (row[0] or "") if row else ""


def _rebuild_holdings_table(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE holdings RENAME TO holdings_old")
    conn.execute(HOLDINGS_TABLE_SQL)
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
             institution, account, owner, currency, quantity, unit_price,
             market_value, market_value_idr, cost_basis, cost_basis_idr,
             unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
             last_appraised_date, notes, import_date)
        SELECT
            snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
            institution, account, owner, currency, quantity, unit_price,
            market_value, market_value_idr, cost_basis, cost_basis_idr,
            unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
            last_appraised_date, notes, import_date
        FROM holdings_old
        ORDER BY id
        """
    )
    conn.execute("DROP TABLE holdings_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_date  ON holdings(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_class ON holdings(asset_class)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_group ON holdings(asset_group)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_owner ON holdings(owner)")


def _rebuild_liabilities_table(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE liabilities RENAME TO liabilities_old")
    conn.execute(LIABILITIES_TABLE_SQL)
    conn.execute(
        """
        INSERT INTO liabilities
            (snapshot_date, liability_type, liability_name, institution, account,
             owner, currency, balance, balance_idr, due_date, notes, import_date)
        SELECT
            snapshot_date, liability_type, liability_name, institution, account,
            owner, currency, balance, balance_idr, due_date, notes, import_date
        FROM liabilities_old
        ORDER BY id
        """
    )
    conn.execute("DROP TABLE liabilities_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_liabilities_date  ON liabilities(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_liabilities_owner ON liabilities(owner)")


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Read the current schema version, or 0 if the table doesn't exist yet."""
    try:
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _needs_holdings_migration(conn: sqlite3.Connection) -> bool:
    ver = _get_schema_version(conn)
    if ver >= 1:
        return False
    # Fallback for databases created before version tracking
    sql = _table_sql(conn, "holdings").replace(" ", "")
    return bool(sql) and "UNIQUE(snapshot_date,asset_class,asset_name,owner)" in sql


def _needs_liabilities_migration(conn: sqlite3.Connection) -> bool:
    ver = _get_schema_version(conn)
    if ver >= 1:
        return False
    # Fallback for databases created before version tracking
    sql = _table_sql(conn, "liabilities").replace(" ", "")
    return bool(sql) and "UNIQUE(snapshot_date,liability_type,liability_name,owner)" in sql


def open_db(db_path: str) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database at *db_path*.

    Creates parent directories as needed.  Applies WAL mode for concurrent
    reads while the sync engine writes.  Returns a connection with
    row_factory = sqlite3.Row so results behave like dicts.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    if _needs_holdings_migration(conn):
        _rebuild_holdings_table(conn)
    if _needs_liabilities_migration(conn):
        _rebuild_liabilities_table(conn)
    # Additive migrations for columns added after initial schema deployment.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(merchant_aliases)")}
    for col, definition in [
        ("owner_filter",   "TEXT DEFAULT ''"),
        ("account_filter", "TEXT DEFAULT ''"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE merchant_aliases ADD COLUMN {col} {definition}")
    # Additive migration: AI suggestion columns on transactions
    tx_cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
    for col, definition in [
        ("ollama_suggestion",  "TEXT"),
        ("suggested_merchant", "TEXT"),
    ]:
        if col not in tx_cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} {definition}")
    conn.commit()
    # Prune sync_log entries older than 90 days
    conn.execute(
        "DELETE FROM sync_log WHERE synced_at < datetime('now', '-90 days')"
    )
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
