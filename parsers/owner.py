"""
Owner detection — maps PDF customer names to canonical owner labels.

Mappings are loaded from the ``owner_mappings`` SQLite table (preferred),
falling back to settings.toml ``[owners]`` section, then hardcoded defaults.

Matching is case-insensitive substring. First match wins.
Falls back to "Unknown" if no match found.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_DEFAULT_MAPPINGS = {
    "Emanuel": "Gandrik",
    "Dian Pratiwi": "Helen",
}


def detect_owner(customer_name: str, mappings: dict | None = None) -> str:
    """
    Return canonical owner label for a customer name string.

    If *mappings* is ``None``, attempts to load from the SQLite
    ``owner_mappings`` table, then falls back to ``_DEFAULT_MAPPINGS``.
    """
    if mappings is None:
        mappings = _load_from_db() or _DEFAULT_MAPPINGS
    name_lower = customer_name.lower()
    for substring, owner in mappings.items():
        if substring.lower() in name_lower:
            return owner
    return "Unknown"


def _load_from_db() -> dict | None:
    """Load owner mappings from SQLite. Returns None on failure."""
    try:
        from finance.config import load_config, get_finance_config
        from finance.db import open_db

        cfg = load_config()
        db_path = get_finance_config(cfg).sqlite_db
        conn = open_db(db_path)
        rows = conn.execute(
            "SELECT substring_match, owner_label FROM owner_mappings"
        ).fetchall()
        conn.close()
        if rows:
            return {r[0]: r[1] for r in rows}
        return None
    except Exception as e:
        log.debug("Could not load owner mappings from DB: %s", e)
        return None
