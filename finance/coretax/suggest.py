"""Suggestion engine for mapping-first reconciliation.

Pure (no DB writes) — returns ranked Suggestion records.  Called from the
Mapping tab's "Suggest" button and the "Suggest All" bulk action.

Rule catalog and scoring:
  isin_exact_unique        1.00   PWM ISIN appears in exactly one CoreTax row
  account_number_exact_unique  1.00   PWM account_number appears in exactly one kode-012 row
  keterangan_substring_unique  0.70   PWM institution+last4 appears in exactly one row
  single_row_in_kode_owner  0.50   Only one row of inferred kode for that owner
  single_row_in_kode      0.30   Only one row of inferred kode exists
  name_similarity          0.10–0.60  Fuzzy match on asset_name / liability_name
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Suggestion:
    target_stable_key: str
    confidence_score: float   # 0.0 - 1.0
    rule: str                 # e.g. "isin_exact_unique"
    reason: str               # human-readable, shown in tooltip
    match_kind: str           # fingerprint kind
    match_value: str          # fingerprint value
    pwm_label: str            # display label for the PWM item


def suggest_mappings_for_unmapped(conn, tax_year: int,
                                  unmapped: list[dict],
                                  rejected: set[tuple[str, str, str]] | None = None) -> list[dict]:
    """Return ranked suggestions per unmapped item.

    Each unmapped item gets a list of suggestions, sorted by confidence_score
    descending.  Ties broken by stable_key ascending.

    Args:
        conn: DB connection
        tax_year: current tax year
        unmapped: list of unmapped PWM items from compute_unmapped_pwm()
        rejected: set of (match_kind, match_value, rejected_target_stable_key)
    """
    if rejected is None:
        rejected = set()

    # Load coretax rows for this year
    from finance.coretax.db import get_rows_for_year
    rows = get_rows_for_year(conn, tax_year)
    row_by_key = {r["stable_key"]: r for r in rows}

    # Build indexes for fast lookup
    isin_index: dict[str, list[dict]] = {}
    account_index: dict[str, list[dict]] = {}
    kode_index: dict[str, list[dict]] = {}
    kode_owner_index: dict[tuple[str, str], list[dict]] = {}

    for r in rows:
        kode = r.get("kode_harta") or ""
        owner = (r.get("owner") or "").strip().lower()
        ket = (r.get("keterangan") or "").strip().lower()
        kode_index.setdefault(kode, []).append(r)
        kode_owner_index.setdefault((kode, owner), []).append(r)

        # Extract ISIN from keterangan if present
        # (CoreTax rows store ISIN in keterangan or notes)
        isin = _extract_isin_from_row(r)
        if isin:
            isin_index[isin].append(r)

        # Extract account number from keterangan
        acct = _extract_account_from_row(r)
        if acct:
            account_index[acct].append(r)

    all_suggestions = []
    for item in unmapped:
        mk = item.get("match_kind", "")
        mv = item.get("match_value", "")
        raw = item.get("fingerprint_raw", "")
        source_kind = item.get("source_kind", "")
        payload = item.get("payload", {})
        label = item.get("pwm_label", "")

        suggestions = []

        if mk == "isin":
            suggestions.extend(_suggest_isin(mv, isin_index, row_by_key))
        elif mk == "account_number_norm":
            suggestions.extend(_suggest_account(raw, account_index, row_by_key))
        elif mk in ("holding_signature", "liability_signature"):
            suggestions.extend(_suggest_signature(mk, mv, raw, source_kind, payload,
                                                   kode_index, kode_owner_index, row_by_key))

        # Filter out rejected suggestions
        suggestions = [
            s for s in suggestions
            if (mk, mv, s.target_stable_key) not in rejected
        ]

        # Sort by score descending, then stable_key ascending
        suggestions.sort(key=lambda s: (-s.confidence_score, s.target_stable_key))

        for s in suggestions:
            s.match_kind = mk
            s.match_value = mv
            s.pwm_label = label

        all_suggestions.extend(suggestions)

    return [asdict(s) for s in all_suggestions]


# ── Rule implementations ────────────────────────────────────────────────────


def _suggest_isin(isin: str, isin_index: dict, row_by_key: dict) -> list[Suggestion]:
    """Rule: isin_exact_unique — PWM ISIN appears in exactly one CoreTax row."""
    matches = isin_index.get(isin, [])
    if len(matches) == 1:
        row = matches[0]
        return [Suggestion(
            target_stable_key=row["stable_key"],
            confidence_score=1.0,
            rule="isin_exact_unique",
            reason=f"ISIN {isin} matches exactly one CoreTax row",
            match_kind="", match_value="", pwm_label="",
        )]
    return []


def _suggest_account(raw: str, account_index: dict, row_by_key: dict) -> list[Suggestion]:
    """Rule: account_number_exact_unique — PWM account_number appears in exactly
    one kode-012 row's metadata."""
    # raw format is "institution:account"
    parts = raw.split(":")
    if len(parts) < 2:
        return []
    account = parts[1]
    matches = account_index.get(account, [])
    if len(matches) == 1:
        row = matches[0]
        return [Suggestion(
            target_stable_key=row["stable_key"],
            confidence_score=1.0,
            rule="account_number_exact_unique",
            reason=f"Account {account} matches exactly one kode-012 row",
            match_kind="", match_value="", pwm_label="",
        )]
    return []


def _suggest_signature(match_kind: str, match_value: str, raw: str,
                       source_kind: str, payload: dict,
                       kode_index: dict, kode_owner_index: dict,
                       row_by_key: dict) -> list[Suggestion]:
    """Rules for signature-based fingerprints (holding_signature, liability_signature)."""
    suggestions = []

    # Infer kode from source_kind + asset_class
    kode = _infer_kode(source_kind, payload)
    owner = (payload.get("owner") or "").strip().lower()

    # Rule: keterangan_substring_unique (score 0.70)
    inst = (payload.get("institution") or "").strip().lower()
    if inst:
        for key, rows in kode_index.items():
            if key != kode:
                continue
            matches = [r for r in rows if inst in (r.get("keterangan") or "").lower()]
            if len(matches) == 1:
                suggestions.append(Suggestion(
                    target_stable_key=matches[0]["stable_key"],
                    confidence_score=0.70,
                    rule="keterangan_substring_unique",
                    reason=f"Institution '{inst}' appears in exactly one kode-{kode} row",
                    match_kind="", match_value="", pwm_label="",
                ))
                break

    # Rule: single_row_in_kode_owner (score 0.50)
    ko_rows = kode_owner_index.get((kode, owner), [])
    if len(ko_rows) == 1:
        suggestions.append(Suggestion(
            target_stable_key=ko_rows[0]["stable_key"],
            confidence_score=0.50,
            rule="single_row_in_kode_owner",
            reason=f"Only one kode-{kode} row exists for owner '{owner}'",
            match_kind="", match_value="", pwm_label="",
        ))

    # Rule: single_row_in_kode (score 0.30)
    k_rows = kode_index.get(kode, [])
    if len(k_rows) == 1:
        # Don't duplicate if already matched by single_row_in_kode_owner
        already = {s.target_stable_key for s in suggestions}
        if k_rows[0]["stable_key"] not in already:
            suggestions.append(Suggestion(
                target_stable_key=k_rows[0]["stable_key"],
                confidence_score=0.30,
                rule="single_row_in_kode",
                reason=f"Only one row of kode-{kode} exists",
                match_kind="", match_value="", pwm_label="",
            ))

    return suggestions


# ── Helpers ─────────────────────────────────────────────────────────────────


def _extract_isin_from_row(row: dict) -> str | None:
    """Try to extract an ISIN from a coretax_row's keterangan or notes."""
    import re
    for field in ("keterangan", "notes_internal"):
        text = row.get(field) or ""
        # ISIN format: 2 letters + 10 alphanumeric chars
        m = re.search(r"\b([A-Z]{2}[A-Z0-9]{10})\b", text)
        if m:
            return m.group(1)
    return None


def _extract_account_from_row(row: dict) -> str | None:
    """Try to extract an account number from a coretax_row's keterangan."""
    import re
    ket = row.get("keterangan") or ""
    # Look for "rek <number>" pattern
    m = re.search(r"rek\s+(\S+)", ket.lower())
    if m:
        return m.group(1)
    return None


def _infer_kode(source_kind: str, payload: dict) -> str:
    """Infer kode_harta from source_kind and asset_class."""
    if source_kind == "account_balance":
        return "012"
    if source_kind == "liability":
        return "liability"
    if source_kind == "holding":
        ac = payload.get("asset_class", "")
        return {"bond": "034", "mutual_fund": "036", "stock": "039"}.get(ac, "")
    return ""
