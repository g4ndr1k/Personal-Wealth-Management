"""Reconcile-from-PWM: match PWM source data (account_balances, holdings, liabilities)
to coretax_rows using the two-tier execution engine.

Tier 1 — Mappings (deterministic, persistent):
  Look up source_fingerprint in coretax_mappings → if hit: match.

Tier 2 — Safe heuristics (deterministic, HIGH-confidence only):
  a. ISIN exact match → exactly one CoreTax row with that ISIN → match (HIGH)
  b. account_number exact match → exactly one kode-012 row → match (HIGH)
  Auto-persist guard: Tier-2 hit becomes a learned mapping ONLY IF:
    - confidence=HIGH
    - no mapping for that fingerprint already exists
    - no mapping for that fingerprint points at a DIFFERENT target

Legacy heuristics (deprecated, gated behind CORETAX_LEGACY_HEURISTICS=True):
  Will be removed in Phase 5 after one full reconcile cycle.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from finance.coretax.confidence import (
    RunUsed,
    RunUnused,
    apply as apply_confidence,
    derive_level,
)
from finance.coretax.db import (
    _norm,
    _utcnow,
    ensure_coretax_tables,
    get_mappings,
    get_rows_for_year,
    increment_mapping_hit,
    insert_reconcile_run,
    insert_unmatched_pwm,
    replace_row_components_for_targets,
    update_mapping_confidence,
    update_row,
)
from finance.coretax.fingerprint import derive as fp_derive, confidence_hint, is_volatile
from finance.coretax.pwm_universe import snapshot as pwm_snapshot
from finance.db import open_db


# ── Feature flag ────────────────────────────────────────────────────────────

LEGACY_HEURISTICS = os.environ.get("CORETAX_LEGACY_HEURISTICS", "true").lower() == "true"


# ── Trace dataclass ─────────────────────────────────────────────────────────

@dataclass
class CoretaxRowTrace:
    stable_key: str
    kode_harta: str
    keterangan: str
    status: str  # 'filled', 'locked_skipped', 'unmatched'
    pwm_source: str | None
    pwm_value: float | None
    warnings: list[str]
    tier: str = ""           # 'tier1_mapping', 'tier2_safe', 'legacy_heuristic'
    confidence_level: str = ""  # 'HIGH', 'MEDIUM', 'LOW'


# ── Tier-2 auto-persist rules ───────────────────────────────────────────────

TIER2_AUTO_PERSIST_RULES = frozenset({"isin_exact_unique", "account_number_exact_unique"})


def _tier2_should_auto_persist(rule: str, confidence: str,
                               existing_mapping: dict | None,
                               conflicting_target: str | None) -> bool:
    """Guard function for Tier-2 auto-persist.  Returns True only if safe."""
    if rule not in TIER2_AUTO_PERSIST_RULES:
        return False
    if confidence != "HIGH":
        return False
    if existing_mapping is not None:
        return False  # never overwrite an existing mapping
    if conflicting_target is not None:
        return False  # conflict → surface as ambiguity
    return True


# ── Month-end date helper ────────────────────────────────────────────────────

def _month_end_date(year_month: str) -> str:
    import calendar
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


# ── Main reconcile function ──────────────────────────────────────────────────

def run_reconcile(conn, tax_year: int, fs_start_month: str,
                  fs_end_month: str, snapshot_date: str | None = None) -> dict:
    """Run auto-reconcile from PWM data using two-tier execution.

    Returns dict: { run_id, summary, trace, unmatched }.
    """
    ensure_coretax_tables(conn)

    if not snapshot_date:
        snapshot_date = _month_end_date(fs_end_month)

    # Load PWM source data
    pwm_items = pwm_snapshot(conn, tax_year, snapshot_date)

    # Load current coretax rows and mappings
    coretax_rows = get_rows_for_year(conn, tax_year)
    row_by_key = {r["stable_key"]: r for r in coretax_rows}
    mappings = get_mappings(conn)
    mapping_lookup = {(m["match_kind"], m["match_value"]): m for m in mappings}

    # Build indexes for Tier-2 safe heuristics
    isin_index: dict[str, list[dict]] = {}
    account_index: dict[str, list[dict]] = {}
    kode_index: dict[str, list[dict]] = {}
    for r in coretax_rows:
        kode = r.get("kode_harta") or ""
        kode_index.setdefault(kode, []).append(r)
        # Extract ISIN from keterangan
        isin = _extract_isin(r)
        if isin:
            isin_index.setdefault(isin, []).append(r)
        # Extract account number from keterangan (kode 012 only)
        if kode == "012":
            acct = _extract_account(r)
            if acct:
                account_index.setdefault(acct, []).append(r)

    # Build stable_key → set of (match_kind, match_value) that already map to it
    # (for conflict detection in auto-persist)
    target_to_fingerprints: dict[str, set[tuple[str, str]]] = {}
    for m in mappings:
        tsk = m.get("target_stable_key")
        if tsk:
            target_to_fingerprints.setdefault(tsk, set()).add((m["match_kind"], m["match_value"]))

    traces: list[dict] = []
    unmatched: list[dict] = []
    components: list[dict] = []
    filled_count = 0
    locked_skipped = 0
    tier1_count = 0
    tier2_count = 0
    legacy_count = 0
    low_confidence_count = 0

    for pwm in pwm_items:
        fp = fp_derive(pwm)
        source_kind = pwm["source_kind"]

        # ── Tier 1: Mapping lookup ──────────────────────────────────────
        mapping = mapping_lookup.get((fp.match_kind, fp.match_value))
        matched_row = None
        tier = ""

        if mapping:
            target_key = mapping.get("target_stable_key")
            if target_key:
                matched_row = row_by_key.get(target_key)
                if matched_row:
                    # Fingerprint-raw verification (sanity check)
                    stored_raw = mapping.get("fingerprint_raw")
                    if stored_raw is not None and stored_raw != fp.fingerprint_raw:
                        # Mismatch — mapping may be stale, skip this match
                        matched_row = None
                        mapping = None
                    else:
                        tier = "tier1_mapping"
                        tier1_count += 1

        # ── Tier 2: Safe heuristics ─────────────────────────────────────
        if matched_row is None and not LEGACY_HEURISTICS:
            matched_row, rule = _tier2_match(
                pwm, fp, source_kind, isin_index, account_index, kode_index, row_by_key)
            if matched_row:
                tier = "tier2_safe"
                tier2_count += 1
                # Auto-persist guard
                existing = mapping_lookup.get((fp.match_kind, fp.match_value))
                conflict = _check_conflict(fp, matched_row["stable_key"],
                                           target_to_fingerprints)
                if _tier2_should_auto_persist(rule, "HIGH", existing, conflict):
                    from finance.coretax.db import assign_mapping
                    assign_mapping(
                        conn, fp.match_kind, fp.match_value,
                        matched_row.get("kode_harta", ""),
                        matched_row.get("kind", "asset"),
                        matched_row["stable_key"],
                        source="auto_safe",
                        confidence_score=1.0,
                        confidence_level="HIGH",
                        fingerprint_raw=fp.fingerprint_raw,
                        created_from_tax_year=tax_year,
                    )
                    # Refresh mapping lookup
                    mappings = get_mappings(conn)
                    mapping_lookup = {(m["match_kind"], m["match_value"]): m for m in mappings}

        # ── Legacy heuristics (deprecated) ──────────────────────────────
        if matched_row is None and LEGACY_HEURISTICS:
            matched_row = _legacy_match(pwm, source_kind, kode_index,
                                        coretax_rows)
            if matched_row:
                tier = "legacy_heuristic"
                legacy_count += 1

        # ── No match → unmatched ────────────────────────────────────────
        if matched_row is None:
            proposed_mk = fp.match_kind
            proposed_mv = fp.match_value
            unmatched.append({
                "source_kind": source_kind,
                "proposed_stable_key": _make_proposed_key(pwm),
                "payload": {**pwm, "proposed_match_kind": proposed_mk,
                            "proposed_match_value": proposed_mv},
            })
            continue

        # ── Apply match ─────────────────────────────────────────────────
        conf_level = _get_confidence_level(mapping, tier)
        if conf_level == "LOW":
            low_confidence_count += 1

        warnings, amount_applied, mv_applied = _apply_match(
            conn, matched_row, pwm, source_kind, mapping, tax_year)

        # Build component for many-to-one breakdown
        comp = _build_component(
            tax_year, matched_row, pwm, source_kind, fp, conf_level)
        components.append(comp)

        # Determine status
        if amount_applied or mv_applied:
            filled_count += 1
            status = "filled"
        else:
            locked_skipped += 1
            status = "locked_skipped"

        traces.append(_trace(matched_row, status, source_kind,
                             _pwm_value(pwm, source_kind), warnings,
                             tier=tier, confidence_level=conf_level))

    # ── Persist reconcile run ────────────────────────────────────────────
    summary = {
        "filled": filled_count,
        "locked_skipped": locked_skipped,
        "unmatched": len(unmatched),
        "tier1_matches": tier1_count,
        "tier2_matches": tier2_count,
        "legacy_matches": legacy_count,
        "low_confidence_matches": low_confidence_count,
        "total_pwm_items": len(pwm_items),
        "legacy_heuristics_enabled": LEGACY_HEURISTICS,
    }

    trace_dicts = []
    for t in traces:
        if isinstance(t, CoretaxRowTrace):
            trace_dicts.append(asdict(t))
        elif isinstance(t, dict):
            trace_dicts.append(t)
        else:
            trace_dicts.append(t)

    run_id = insert_reconcile_run(conn, tax_year, fs_start_month, fs_end_month,
                                   snapshot_date, summary, trace_dicts)

    # Persist unmatched rows
    for um in unmatched:
        insert_unmatched_pwm(conn, run_id, tax_year, um["source_kind"],
                             um["payload"], um.get("proposed_stable_key"))

    # Persist component breakdowns
    if components:
        affected_keys = list({c["target_stable_key"] for c in components})
        replace_row_components_for_targets(conn, run_id, tax_year,
                                           affected_keys, components)

    # ── Confidence dynamics: update mapping scores ───────────────────────
    _update_mapping_confidence_dynamics(conn, mappings, traces, tax_year)

    conn.commit()

    return {
        "run_id": run_id,
        "summary": summary,
        "trace": trace_dicts,
        "unmatched": unmatched,
    }


# ── Tier-2 safe heuristic matching ──────────────────────────────────────────

def _tier2_match(pwm: dict, fp, source_kind: str,
                 isin_index: dict, account_index: dict,
                 kode_index: dict, row_by_key: dict) -> tuple[dict | None, str]:
    """Apply only provable 1:1 safe heuristics.

    Returns (matched_row, rule_name) or (None, "").
    """
    if source_kind == "holding":
        isin = (pwm.get("isin_or_code") or "").strip()
        if isin:
            matches = isin_index.get(isin, [])
            if len(matches) == 1:
                return matches[0], "isin_exact_unique"

    if source_kind == "account_balance":
        acct = (pwm.get("account") or "").strip()
        if acct:
            matches = account_index.get(acct, [])
            if len(matches) == 1:
                return matches[0], "account_number_exact_unique"

    return None, ""


# ── Legacy heuristic matching (deprecated) ──────────────────────────────────

def _legacy_match(pwm: dict, source_kind: str, kode_index: dict,
                  coretax_rows: list[dict]) -> dict | None:
    """Legacy heuristic matching — kept during migration, will be removed in Phase 5."""
    if source_kind == "account_balance":
        pwm_inst = _norm(pwm["institution"])
        pwm_acct = _norm(pwm["account"])
        for row in kode_index.get("012", []):
            ket = _norm(row.get("keterangan") or "")
            if pwm_inst in ket and pwm_acct in ket:
                return row

    if source_kind == "holding":
        asset_class = pwm.get("asset_class", "")
        kode = {"bond": "034", "mutual_fund": "036", "stock": "039"}.get(asset_class)
        if not kode:
            return None
        rows = kode_index.get(kode, [])
        pwm_inst = _norm(pwm["institution"])
        for row in rows:
            ket = _norm(row.get("keterangan") or "")
            if pwm_inst in ket:
                return row
        if len(rows) == 1:
            return rows[0]

    if source_kind == "liability":
        liability_rows = [r for r in coretax_rows if r["kind"] == "liability"]
        pwm_type = _norm(pwm["liability_type"])
        pwm_name = _norm(pwm["liability_name"])
        for row in liability_rows:
            ket = _norm(row.get("keterangan") or "")
            if pwm_type in ket or pwm_name in ket:
                return row

    return None


# ── Match application helpers ────────────────────────────────────────────────

def _apply_match(conn, row: dict, pwm: dict, source_kind: str,
                 mapping: dict | None, tax_year: int) -> tuple[list[str], bool, bool]:
    """Apply a match to the coretax_row. Returns (warnings, amount_applied, mv_applied)."""
    warnings = []
    amount_applied = False
    mv_applied = False

    if source_kind == "account_balance":
        if not row["amount_locked"]:
            updates = {"current_amount_idr": pwm["value"],
                       "current_amount_source": "auto_reconciled"}
            if mapping:
                updates["last_mapping_id"] = mapping["id"]
            update_row(conn, row["id"], **updates)
            amount_applied = True
        else:
            warnings.append("amount_locked")
        if mapping and amount_applied:
            increment_mapping_hit(conn, mapping["id"], tax_year)

    elif source_kind == "holding":
        amount_updates = {}
        mv_updates = {}
        if not row["amount_locked"]:
            amount_updates["current_amount_idr"] = pwm["cost_basis_idr"]
            amount_updates["current_amount_source"] = "auto_reconciled"
        else:
            warnings.append("amount_locked")
        if not row["market_value_locked"]:
            mv_updates["market_value_idr"] = pwm["market_value_idr"]
            mv_updates["market_value_source"] = "auto_reconciled"
        else:
            warnings.append("market_value_locked")
        combined = {**amount_updates, **mv_updates}
        if combined:
            if mapping:
                combined["last_mapping_id"] = mapping["id"]
            update_row(conn, row["id"], **combined)
            amount_applied = bool(amount_updates)
            mv_applied = bool(mv_updates)
            if mapping:
                increment_mapping_hit(conn, mapping["id"], tax_year)

    elif source_kind == "liability":
        if not row["amount_locked"]:
            updates = {"current_amount_idr": pwm["balance_idr"],
                       "current_amount_source": "auto_reconciled"}
            if mapping:
                updates["last_mapping_id"] = mapping["id"]
            update_row(conn, row["id"], **updates)
            amount_applied = True
        else:
            warnings.append("amount_locked")
        if mapping and amount_applied:
            increment_mapping_hit(conn, mapping["id"], tax_year)

    return warnings, amount_applied, mv_applied


# ── Component builder ────────────────────────────────────────────────────────

def _build_component(tax_year: int, row: dict, pwm: dict, source_kind: str,
                     fp, confidence_level: str) -> dict:
    """Build a component dict for coretax_row_components."""
    return {
        "target_stable_key": row["stable_key"],
        "source_kind": source_kind,
        "match_kind": fp.match_kind,
        "match_value": fp.match_value,
        "component_amount_idr": _pwm_amount(pwm, source_kind),
        "component_market_value_idr": _pwm_mv(pwm, source_kind),
        "pwm_label": _pwm_label(pwm, source_kind),
        "confidence_level": confidence_level,
    }


# ── Confidence helpers ───────────────────────────────────────────────────────

def _get_confidence_level(mapping: dict | None, tier: str) -> str:
    """Get the confidence level for a match."""
    if mapping:
        return mapping.get("confidence_level", "HIGH")
    if tier == "tier2_safe":
        return "HIGH"
    if tier == "legacy_heuristic":
        return "MEDIUM"
    return "LOW"


def _update_mapping_confidence_dynamics(conn, mappings: list[dict],
                                         traces: list, tax_year: int) -> None:
    """Update confidence scores for all mappings based on this reconcile run."""
    # Determine which mappings were used
    used_mapping_ids = set()
    for t in traces:
        if isinstance(t, CoretaxRowTrace):
            if t.status in ("filled", "locked_skipped") and t.tier == "tier1_mapping":
                # Find the mapping that produced this match
                pass  # We track via increment_mapping_hit already

    # For now, mark all mappings that weren't used as RunUnused
    # (more precise tracking would require knowing which mappings were evaluated)
    # This is a conservative approach — only penalize if fingerprint is still present
    from finance.coretax.pwm_universe import snapshot
    pwm_items = snapshot(conn, tax_year)
    pwm_fingerprints = set()
    for item in pwm_items:
        fp = fp_derive(item)
        pwm_fingerprints.add((fp.match_kind, fp.match_value))

    for m in mappings:
        mk = m.get("match_kind", "")
        mv = m.get("match_value", "")
        if m.get("hits", 0) > 0:
            # Was used — apply RunUsed
            updates = apply_confidence(RunUsed(tax_year), m)
            update_mapping_confidence(conn, m["id"], **updates)
        else:
            # Was not used — apply RunUnused only if fingerprint still present
            still_present = (mk, mv) in pwm_fingerprints
            updates = apply_confidence(
                RunUnused(tax_year, fingerprint_still_present=still_present), m)
            update_mapping_confidence(conn, m["id"], **updates)


# ── Conflict detection ───────────────────────────────────────────────────────

def _check_conflict(fp, target_stable_key: str,
                    target_to_fingerprints: dict) -> str | None:
    """Check if this fingerprint already maps to a different target."""
    for tsk, fps in target_to_fingerprints.items():
        if tsk != target_stable_key and (fp.match_kind, fp.match_value) in fps:
            return tsk
    return None


# ── PWM value helpers ────────────────────────────────────────────────────────

def _pwm_value(pwm: dict, source_kind: str) -> float | None:
    if source_kind == "account_balance":
        return pwm.get("value")
    if source_kind == "holding":
        return pwm.get("market_value_idr")
    if source_kind == "liability":
        return pwm.get("balance_idr")
    return None


def _pwm_amount(pwm: dict, source_kind: str) -> float | None:
    if source_kind == "account_balance":
        return pwm.get("value")
    if source_kind == "holding":
        return pwm.get("cost_basis_idr")
    if source_kind == "liability":
        return pwm.get("balance_idr")
    return None


def _pwm_mv(pwm: dict, source_kind: str) -> float | None:
    if source_kind == "holding":
        return pwm.get("market_value_idr")
    return None


def _pwm_label(pwm: dict, source_kind: str) -> str:
    if source_kind == "account_balance":
        return f"{pwm.get('institution', '')} / {pwm.get('account', '')}"
    if source_kind == "holding":
        return f"{pwm.get('institution', '')} / {pwm.get('asset_name', '')}"
    if source_kind == "liability":
        return f"{pwm.get('liability_type', '')} / {pwm.get('liability_name', '')}"
    return str(pwm.get("source_id", ""))


def _make_proposed_key(pwm: dict) -> str:
    kind = pwm.get("source_kind", "")
    if kind == "account_balance":
        return f"pwm:account:{_norm(pwm.get('institution', ''))}:{_norm(pwm.get('account', ''))}"
    if kind == "holding":
        return f"pwm:holding:{_norm(pwm.get('asset_class', ''))}:{_norm(pwm.get('institution', ''))}:{_norm(pwm.get('isin_or_code', ''))}:{_norm(pwm.get('owner', ''))}"
    if kind == "liability":
        return f"pwm:liability:{_norm(pwm.get('liability_type', ''))}:{_norm(pwm.get('liability_name', ''))}:{_norm(pwm.get('owner', ''))}"
    return ""


# ── Row extraction helpers ───────────────────────────────────────────────────

def _extract_isin(row: dict) -> str | None:
    import re
    for field in ("keterangan", "notes_internal"):
        text = row.get(field) or ""
        m = re.search(r"\b([A-Z]{2}[A-Z0-9]{10})\b", text)
        if m:
            return m.group(1)
    return None


def _extract_account(row: dict) -> str | None:
    import re
    ket = row.get("keterangan") or ""
    m = re.search(r"rek\s+(\S+)", ket.lower())
    if m:
        return m.group(1)
    return None


# ── Trace builder ────────────────────────────────────────────────────────────

def _trace(row: dict, status: str, pwm_source: str | None,
           pwm_value: float | None, warnings: list[str],
           tier: str = "", confidence_level: str = "") -> dict:
    return {
        "stable_key": row["stable_key"],
        "kode_harta": row.get("kode_harta", ""),
        "keterangan": row.get("keterangan", ""),
        "status": status,
        "pwm_source": pwm_source,
        "pwm_value": pwm_value,
        "warnings": warnings,
        "tier": tier,
        "confidence_level": confidence_level,
    }
