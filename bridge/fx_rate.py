"""
fx_rate.py — Historical foreign-exchange rate fetcher for the PDF pipeline.

Uses the fawazahmed0/currency-api (free, no API key required, historical support):
  Primary:  https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/{from}.json
  Fallback: https://currency-api.pages.dev/v1/currencies/{from}.json?date={date}

Returns the exchange rate as a float (units of `to_currency` per 1 unit of
`from_currency`).  Results are cached per (from, to, date) in a module-level
dict so each unique rate is only fetched once per process lifetime.

Example
-------
>>> rate = get_rate("USD", "IDR", "2026-01-31")
>>> print(rate)   # ~16300
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# Module-level cache: (from_cur, to_cur, date_str) → rate
_cache: dict[tuple[str, str, str], float] = {}


def get_rate(
    from_currency: str,
    to_currency: str,
    date_str: str,          # YYYY-MM-DD
    timeout: int = 8,
) -> Optional[float]:
    """
    Return the exchange rate from `from_currency` to `to_currency` on `date_str`.

    If the exact date is unavailable (weekend / holiday), the API automatically
    returns the nearest prior business day rate — this is fine for month-end
    statement balances.

    Returns None if the rate cannot be fetched.
    """
    from_cur = from_currency.upper()
    to_cur   = to_currency.upper()

    # Identical currencies → trivial
    if from_cur == to_cur:
        return 1.0

    cache_key = (from_cur, to_cur, date_str)
    if cache_key in _cache:
        return _cache[cache_key]

    rate = _fetch_primary(from_cur, to_cur, date_str, timeout)
    if rate is None:
        rate = _fetch_fallback(from_cur, to_cur, date_str, timeout)

    if rate is not None:
        _cache[cache_key] = rate
        log.info(f"FX rate {from_cur}→{to_cur} on {date_str}: {rate:,.4f}")
    else:
        log.warning(f"Could not fetch FX rate {from_cur}→{to_cur} for {date_str}")

    return rate


def _fetch_primary(from_cur: str, to_cur: str, date_str: str, timeout: int) -> Optional[float]:
    """CDN-hosted JSON via jsdelivr (primary)."""
    url = (
        f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date_str}"
        f"/v1/currencies/{from_cur.lower()}.json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agentic-ai/fx-rate"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        # data = {"date": "...", "usd": {"idr": 16300.5, ...}}
        rates = data.get(from_cur.lower(), {})
        rate  = rates.get(to_cur.lower())
        return float(rate) if rate is not None else None
    except Exception as e:
        log.debug(f"FX primary fetch failed ({url}): {e}")
        return None


def _fetch_fallback(from_cur: str, to_cur: str, date_str: str, timeout: int) -> Optional[float]:
    """Cloudflare Pages fallback for the same dataset."""
    url = (
        f"https://currency-api.pages.dev/v1/currencies/{from_cur.lower()}.json"
        f"?date={date_str}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agentic-ai/fx-rate"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        rates = data.get(from_cur.lower(), {})
        rate  = rates.get(to_cur.lower())
        return float(rate) if rate is not None else None
    except Exception as e:
        log.debug(f"FX fallback fetch failed ({url}): {e}")
        return None


def get_rate_safe(
    from_currency: str,
    to_currency: str,
    date_str: str,
    fallback: float = 0.0,
) -> float:
    """
    Like get_rate() but returns `fallback` (default 0.0) instead of None on failure.
    Useful for non-critical pipelines where 0.0 signals 'update manually'.
    """
    rate = get_rate(from_currency, to_currency, date_str)
    return rate if rate is not None else fallback
