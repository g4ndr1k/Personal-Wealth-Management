"""PWM universe snapshot — single source of truth for reading PWM source data.

All code that needs the current PWM universe (unmapped lookup, fingerprint
validation, lifecycle ORPHANED check) must call ``snapshot()`` instead of
querying account_balances/holdings/liabilities directly.

Returns plain dicts with ``source_kind`` set, ready for ``fingerprint.derive()``.
"""
from __future__ import annotations

from typing import Any


def snapshot(conn, tax_year: int | None = None, snapshot_date: str | None = None) -> list[dict]:
    """Load all PWM source rows for a given snapshot context.

    If *snapshot_date* is provided, filters by that date.  Otherwise loads
    the latest available snapshot date from ``account_balances``.

    Returns a flat list of dicts, each carrying ``source_kind``.
    """
    if not snapshot_date:
        row = conn.execute(
            "SELECT MAX(snapshot_date) AS sd FROM account_balances"
        ).fetchone()
        snapshot_date = row["sd"] if row else None
    if not snapshot_date:
        return []

    items: list[dict] = []
    items.extend(_load_cash(conn, snapshot_date))
    items.extend(_load_holdings(conn, snapshot_date))
    items.extend(_load_liabilities(conn, snapshot_date))
    return items


def snapshot_dates(conn) -> list[str]:
    """Return all distinct snapshot dates in descending order."""
    rows = conn.execute(
        "SELECT DISTINCT snapshot_date FROM account_balances ORDER BY snapshot_date DESC"
    ).fetchall()
    return [r["snapshot_date"] for r in rows]


# ── Private loaders ─────────────────────────────────────────────────────────


def _load_cash(conn, snapshot_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, institution, account, owner, currency, balance_idr
           FROM account_balances
           WHERE snapshot_date = ?
           ORDER BY institution, account, owner""",
        (snapshot_date,),
    ).fetchall()
    return [
        {
            "source_kind": "account_balance",
            "source_id": row["id"],
            "institution": (row["institution"] or "").strip(),
            "account": (row["account"] or "").strip(),
            "owner": (row["owner"] or "").strip(),
            "currency": row["currency"] or "IDR",
            "value": float(row["balance_idr"] or 0.0),
        }
        for row in rows
    ]


def _load_holdings(conn, snapshot_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, asset_class, institution, owner, currency,
                  asset_name, isin_or_code,
                  cost_basis_idr, market_value_idr
           FROM holdings
           WHERE snapshot_date = ?
           ORDER BY institution, owner, asset_class""",
        (snapshot_date,),
    ).fetchall()
    return [
        {
            "source_kind": "holding",
            "source_id": row["id"],
            "asset_class": (row["asset_class"] or "").strip(),
            "institution": (row["institution"] or "").strip(),
            "owner": (row["owner"] or "").strip(),
            "currency": row["currency"] or "IDR",
            "asset_name": (row["asset_name"] or "").strip(),
            "isin_or_code": (row["isin_or_code"] or "").strip(),
            "cost_basis_idr": float(row["cost_basis_idr"] or 0.0),
            "market_value_idr": float(row["market_value_idr"] or 0.0),
        }
        for row in rows
    ]


def _load_liabilities(conn, snapshot_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, liability_type, liability_name, institution, owner,
                  balance_idr
           FROM liabilities
           WHERE snapshot_date = ?
           ORDER BY liability_type, owner""",
        (snapshot_date,),
    ).fetchall()
    return [
        {
            "source_kind": "liability",
            "source_id": row["id"],
            "liability_type": (row["liability_type"] or "").strip(),
            "liability_name": (row["liability_name"] or "").strip(),
            "institution": (row["institution"] or "").strip(),
            "owner": (row["owner"] or "").strip(),
            "balance_idr": float(row["balance_idr"] or 0.0),
        }
        for row in rows
    ]
