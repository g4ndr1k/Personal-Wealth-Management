"""Stable fingerprint derivation for PWM source rows.

Produces (match_kind, match_value, fingerprint_raw) tuples used by Mapping
and Reconcile.  This is the single source of truth for fingerprint derivation
— never call sha256() inline elsewhere.

Source row types (dicts from reconcile._load_pwm_*):
  - account_balance: { institution, account, owner, ... }
  - holding:         { asset_class, institution, owner, asset_name, isin_or_code, ... }
  - liability:       { liability_type, liability_name, owner, ... }
"""
from __future__ import annotations

import hashlib
from typing import Any, NamedTuple


class Fingerprint(NamedTuple):
    match_kind: str          # fingerprint kind identifier
    match_value: str         # authoritative key (full SHA-256 hex or normalized ISIN)
    fingerprint_raw: str     # pre-hash canonical form


def _norm(text: str) -> str:
    """Canonicalize a text value for fingerprinting."""
    return (text or "").strip().lower().replace(" ", "-")


def _sha256(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Public API ──────────────────────────────────────────────────────────────


def derive(row: dict) -> Fingerprint:
    """Derive a Fingerprint from a PWM source row.

    Dispatches based on ``row["source_kind"]``:
      - ``account_balance`` → ``account_number_norm``
      - ``holding`` with non-empty ISIN → ``isin``
      - ``holding`` without ISIN → ``holding_signature`` (volatile, LOW confidence)
      - ``liability`` → ``liability_signature`` (volatile, MEDIUM/LOW confidence)
    """
    kind = row.get("source_kind")
    if kind == "account_balance":
        return _derive_account_balance(row)
    if kind == "holding":
        return _derive_holding(row)
    if kind == "liability":
        return _derive_liability(row)
    raise ValueError(f"Unknown source_kind: {kind!r}")


def derive_raw_from_value(match_kind: str, match_value: str) -> str | None:
    """Re-derive fingerprint_raw from (match_kind, match_value).

    Used during lazy-fill when existing mappings have fingerprint_raw=NULL.
    Returns None if re-derivation is not possible (e.g. ISIN hashed value).
    """
    if match_kind == "isin":
        # ISIN match_value IS the normalized ISIN — raw is the same
        return match_value
    if match_kind == "account_number_norm":
        # Can't reverse SHA-256 — return None (caller should flag stale)
        return None
    if match_kind == "holding_signature":
        return None
    if match_kind == "liability_signature":
        return None
    return None


def is_volatile(match_kind: str) -> bool:
    """Return True if the fingerprint kind is volatile (derived from user-editable names)."""
    return match_kind in {"holding_signature", "liability_signature"}


def confidence_hint(match_kind: str) -> str:
    """Return the default confidence level for a freshly-derived fingerprint."""
    if match_kind == "isin":
        return "HIGH"
    if match_kind == "account_number_norm":
        return "HIGH"
    if match_kind == "liability_signature":
        return "MEDIUM"
    if match_kind == "holding_signature":
        return "LOW"
    return "LOW"


# ── Private helpers ─────────────────────────────────────────────────────────


def _derive_account_balance(row: dict) -> Fingerprint:
    inst = _norm(row.get("institution", ""))
    acct = _norm(row.get("account", ""))
    raw = f"{inst}:{acct}"
    return Fingerprint(
        match_kind="account_number_norm",
        match_value=_sha256(raw),
        fingerprint_raw=raw,
    )


def _derive_holding(row: dict) -> Fingerprint:
    isin = _norm(row.get("isin_or_code", ""))
    if isin:
        return Fingerprint(
            match_kind="isin",
            match_value=isin,
            fingerprint_raw=isin,
        )
    # Volatile: composed from user-editable fields
    asset_class = _norm(row.get("asset_class", ""))
    inst = _norm(row.get("institution", ""))
    asset_name = _norm(row.get("asset_name", ""))
    owner = _norm(row.get("owner", ""))
    raw = f"{asset_class}:{inst}:{asset_name}:{owner}"
    return Fingerprint(
        match_kind="holding_signature",
        match_value=_sha256(raw),
        fingerprint_raw=raw,
    )


def _derive_liability(row: dict) -> Fingerprint:
    liab_type = _norm(row.get("liability_type", ""))
    liab_name = _norm(row.get("liability_name", ""))
    owner = _norm(row.get("owner", ""))
    raw = f"{liab_type}:{liab_name}:{owner}"
    return Fingerprint(
        match_kind="liability_signature",
        match_value=_sha256(raw),
        fingerprint_raw=raw,
    )
