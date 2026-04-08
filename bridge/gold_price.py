"""
bridge/gold_price.py — Fetch the IDR price per gram of gold.

Source: fawazahmed0 XAU/IDR currency API (the same free, no-key API used by
bridge/fx_rate.py).  Works for both current and historical dates.

  xau_idr = 1 troy ounce price in IDR
  price_per_gram = xau_idr / 31.1035  (grams per troy ounce)

NOTE: This returns the international spot price (LBMA/Comex).  Antam (Logam
Mulia) physical gold bars sell at a premium above spot — typically 5–15% higher
due to minting, certification, and dealer margin.  No Antam-specific public API
is available; if you need Antam-specific prices, supply them manually via
--prices or update the DB rows directly after seeding.
"""
from __future__ import annotations
from typing import Optional

TROY_OZ_TO_GRAMS: float = 31.1035  # 1 troy ounce = 31.1035 grams


def get_gold_price_idr_per_gram(date_str: str) -> Optional[float]:
    """
    Return the IDR price per gram of gold for the given YYYY-MM-DD date.

    Delegates to bridge.fx_rate.get_rate("xau", "idr", date_str) which uses
    the fawazahmed0 currency API (CDN-cached, free, no API key required).
    If the exact date is a weekend or holiday the API returns the nearest prior
    business day rate automatically.

    Returns None on network error or if the rate is unavailable.
    """
    from bridge.fx_rate import get_rate

    xau_idr = get_rate("xau", "idr", date_str)
    if xau_idr and xau_idr > 0:
        return xau_idr / TROY_OZ_TO_GRAMS
    return None


def get_gold_price_idr_per_bar(weight_grams: int, date_str: str) -> Optional[float]:
    """
    Return the IDR price for a single Antam gold bar of the given weight.

    Args:
        weight_grams: Bar weight in grams (e.g. 100, 50, 25).
        date_str:     YYYY-MM-DD snapshot date.

    Returns:
        IDR price for one bar of that weight, or None on failure.
    """
    per_gram = get_gold_price_idr_per_gram(date_str)
    if per_gram is None:
        return None
    return per_gram * weight_grams
