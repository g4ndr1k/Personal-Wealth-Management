"""Reconcile-from-PWM: match PWM source data (account_balances, holdings, liabilities)
to coretax_rows, applying learned mappings and recording unmatched rows.

Uses the same data sources as the FS report endpoint but reads them directly
via SQLite — not via HTTP call.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from finance.coretax.db import (
    _norm,
    _utcnow,
    ensure_coretax_tables,
    get_mappings,
    get_rows_for_year,
    increment_mapping_hit,
    insert_reconcile_run,
    insert_unmatched_pwm,
    update_row,
    upsert_mapping,
)
from finance.db import open_db


# ── Trace dataclass (preserve audit continuity with old CoretaxRowTrace) ──────

@dataclass
class CoretaxRowTrace:
    stable_key: str
    kode_harta: str
    keterangan: str
    status: str  # 'filled', 'locked_skipped', 'unmatched'
    pwm_source: str | None  # 'account_balance', 'holding', 'liability'
    pwm_value: float | None
    warnings: list[str]


# ── PWM data loaders ─────────────────────────────────────────────────────────

def _load_pwm_cash(conn, snapshot_date: str) -> list[dict]:
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


def _load_pwm_holdings(conn, snapshot_date: str) -> list[dict]:
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


def _load_pwm_liabilities(conn, snapshot_date: str) -> list[dict]:
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


# ── Month-end date helper ────────────────────────────────────────────────────

def _month_end_date(year_month: str) -> str:
    """Convert 'YYYY-MM' to last day of that month as 'YYYY-MM-DD'."""
    import calendar
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


# ── Main reconcile function ──────────────────────────────────────────────────

def run_reconcile(conn, tax_year: int, fs_start_month: str,
                  fs_end_month: str, snapshot_date: str | None = None) -> dict:
    """Run auto-reconcile from PWM data.

    For each PWM source row, builds match keys → looks up coretax_mappings.
    Hit → updates target coretax_row only if not locked.
    Miss → emits to unmatched list.

    Returns dict: { run_id, summary, trace, unmatched }.
    """
    ensure_coretax_tables(conn)

    if not snapshot_date:
        snapshot_date = _month_end_date(fs_end_month)

    # Load PWM source data
    pwm_cash = _load_pwm_cash(conn, snapshot_date)
    pwm_holdings = _load_pwm_holdings(conn, snapshot_date)
    pwm_liabilities = _load_pwm_liabilities(conn, snapshot_date)

    # Load current coretax rows and mappings
    coretax_rows = get_rows_for_year(conn, tax_year)
    row_by_key = {r["stable_key"]: r for r in coretax_rows}
    mappings = get_mappings(conn)
    mapping_lookup = {(m["match_kind"], m["match_value"]): m for m in mappings}

    traces: list[dict] = []
    unmatched: list[dict] = []
    filled_count = 0
    locked_skipped = 0
    used_pwm_ids: set[int] = set()

    # Build a kode-to-rows index for faster matching
    kode_index: dict[str, list[dict]] = {}
    for r in coretax_rows:
        kode = r.get("kode_harta") or ""
        kode_index.setdefault(kode, []).append(r)

    # ── Match cash (kode 012) ────────────────────────────────────────────
    # Cash has no meaningful market value — only writes current_amount_idr.
    cash_rows = kode_index.get("012", [])
    for pwm in pwm_cash:
        candidates = _build_match_candidates_cash(pwm)
        matched_row, matched_mapping = _find_mapping_target(
            candidates, mapping_lookup, row_by_key)

        if matched_row is None:
            # Try heuristic match by institution + account in keterangan
            matched_row = _heuristic_match_cash(cash_rows, pwm)

        if matched_row is None:
            payload = dict(pwm)
            payload["proposed_match_kind"] = candidates[0][0] if candidates else None
            payload["proposed_match_value"] = candidates[0][1] if candidates else None
            unmatched.append({
                "source_kind": "account_balance",
                "proposed_stable_key": f"pwm:account:{_norm(pwm['institution'])}:{_norm(pwm['account'])}",
                "payload": payload,
            })
            continue

        # Independent lock guards — cash skips market_value entirely.
        warnings: list[str] = []
        amount_applied = False
        if not matched_row["amount_locked"]:
            updates = {"current_amount_idr": pwm["value"],
                       "current_amount_source": "auto_reconciled"}
            if matched_mapping:
                updates["last_mapping_id"] = matched_mapping["id"]
            update_row(conn, matched_row["id"], **updates)
            amount_applied = True
        else:
            warnings.append("amount_locked")

        if matched_mapping and amount_applied:
            increment_mapping_hit(conn, matched_mapping["id"], tax_year)

        if amount_applied:
            filled_count += 1
            used_pwm_ids.add(pwm["source_id"])
            traces.append(_trace(matched_row, "filled", "account_balance", pwm["value"], warnings))
        else:
            locked_skipped += 1
            traces.append(_trace(matched_row, "locked_skipped", "account_balance",
                                  pwm["value"], warnings))

    # ── Match holdings (034=Obligasi, 036=Reksadana, 039=Saham) ──────────
    kode_to_asset_class = {"034": "bond", "036": "mutual_fund", "039": "stock"}
    for pwm in pwm_holdings:
        asset_class = pwm["asset_class"]
        # Determine kode from asset class
        kode = None
        for k, ac in kode_to_asset_class.items():
            if ac == asset_class:
                kode = k
                break
        if not kode:
            unmatched.append({
                "source_kind": "holding",
                "proposed_stable_key": f"pwm:holding:{_norm(asset_class)}:{_norm(pwm['institution'])}:{_norm(pwm.get('isin_or_code') or '')}:{_norm(pwm['owner'])}",
                "payload": pwm,
            })
            continue

        # Build match candidates (isin first, then asset_signature)
        candidates = _build_match_candidates_holding(pwm)
        matched_row, matched_mapping = _find_mapping_target(
            candidates, mapping_lookup, row_by_key)

        if matched_row is None:
            # Heuristic: match by kode + institution + owner
            investment_rows = kode_index.get(kode, [])
            matched_row = _heuristic_match_investment(investment_rows, pwm)

        if matched_row is None:
            payload = dict(pwm)
            payload["proposed_match_kind"] = candidates[0][0] if candidates else None
            payload["proposed_match_value"] = candidates[0][1] if candidates else None
            isin = pwm.get("isin_or_code", "")
            unmatched.append({
                "source_kind": "holding",
                "proposed_stable_key": f"pwm:holding:{_norm(asset_class)}:{_norm(pwm['institution'])}:{_norm(isin)}:{_norm(pwm['owner'])}",
                "payload": payload,
            })
            continue

        # Independent lock guards
        warnings: list[str] = []
        amount_applied = False
        mv_applied = False
        amount_updates: dict = {}
        mv_updates: dict = {}

        if not matched_row["amount_locked"]:
            amount_updates["current_amount_idr"] = pwm["cost_basis_idr"]
            amount_updates["current_amount_source"] = "auto_reconciled"
        else:
            warnings.append("amount_locked")

        if not matched_row["market_value_locked"]:
            mv_updates["market_value_idr"] = pwm["market_value_idr"]
            mv_updates["market_value_source"] = "auto_reconciled"
        else:
            warnings.append("market_value_locked")

        combined = {**amount_updates, **mv_updates}
        if combined:
            if matched_mapping:
                combined["last_mapping_id"] = matched_mapping["id"]
            update_row(conn, matched_row["id"], **combined)
            amount_applied = bool(amount_updates)
            mv_applied = bool(mv_updates)
            filled_count += 1
            used_pwm_ids.add(pwm["source_id"])
            if matched_mapping:
                increment_mapping_hit(conn, matched_mapping["id"], tax_year)
        else:
            locked_skipped += 1

        traces.append(_trace(matched_row,
                             "filled" if (amount_applied or mv_applied) else "locked_skipped",
                             "holding", pwm["market_value_idr"], warnings))

    # ── Match liabilities ────────────────────────────────────────────────
    liability_rows = [r for r in coretax_rows if r["kind"] == "liability"]
    for pwm in pwm_liabilities:
        candidates = _build_match_candidates_liability(pwm)
        matched_row, matched_mapping = _find_mapping_target(
            candidates, mapping_lookup, row_by_key)

        if matched_row is None:
            matched_row = _heuristic_match_liability(liability_rows, pwm)

        if matched_row is None:
            payload = dict(pwm)
            payload["proposed_match_kind"] = candidates[0][0] if candidates else None
            payload["proposed_match_value"] = candidates[0][1] if candidates else None
            unmatched.append({
                "source_kind": "liability",
                "proposed_stable_key": f"pwm:liability:{_norm(pwm['liability_type'])}:{_norm(pwm['liability_name'])}:{_norm(pwm['owner'])}",
                "payload": payload,
            })
            continue

        if matched_row["amount_locked"]:
            locked_skipped += 1
            traces.append(_trace(matched_row, "locked_skipped", "liability",
                                  pwm["balance_idr"], ["amount_locked"]))
            continue

        updates = {"current_amount_idr": pwm["balance_idr"],
                   "current_amount_source": "auto_reconciled"}
        if matched_mapping:
            updates["last_mapping_id"] = matched_mapping["id"]
        update_row(conn, matched_row["id"], **updates)
        filled_count += 1
        used_pwm_ids.add(pwm["source_id"])
        if matched_mapping:
            increment_mapping_hit(conn, matched_mapping["id"], tax_year)
        traces.append(_trace(matched_row, "filled", "liability", pwm["balance_idr"], []))

    # ── Persist reconcile run ────────────────────────────────────────────
    summary = {
        "filled": filled_count,
        "locked_skipped": locked_skipped,
        "unmatched": len(unmatched),
        "total_pwm_cash": len(pwm_cash),
        "total_pwm_holdings": len(pwm_holdings),
        "total_pwm_liabilities": len(pwm_liabilities),
    }

    run_id = insert_reconcile_run(conn, tax_year, fs_start_month, fs_end_month,
                                   snapshot_date, summary,
                                   [asdict(CoretaxRowTrace(**t)) if isinstance(t, CoretaxRowTrace) else t for t in traces])

    # Persist unmatched rows
    for um in unmatched:
        insert_unmatched_pwm(conn, run_id, tax_year, um["source_kind"],
                             um["payload"], um.get("proposed_stable_key"))

    conn.commit()

    return {
        "run_id": run_id,
        "summary": summary,
        "trace": traces,
        "unmatched": unmatched,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _trace(row: dict, status: str, pwm_source: str | None,
           pwm_value: float | None, warnings: list[str]) -> dict:
    return {
        "stable_key": row["stable_key"],
        "kode_harta": row.get("kode_harta", ""),
        "keterangan": row.get("keterangan", ""),
        "status": status,
        "pwm_source": pwm_source,
        "pwm_value": pwm_value,
        "warnings": warnings,
    }


def _find_mapping_target(candidates: list[tuple[str, str]],
                          mapping_lookup: dict,
                          row_by_key: dict) -> tuple[dict | None, dict | None]:
    """Look up a learned mapping and resolve the target coretax_row.

    Walks `candidates` (a prioritized list of (match_kind, match_value) tuples)
    and returns the first hit whose target row is found in `row_by_key`.

    Returns (matched_row, matched_mapping). Both are None if no mapping or
    the mapping points at a stable_key not present for this tax_year.
    """
    for mk in candidates:
        mapping = mapping_lookup.get(mk)
        if not mapping:
            continue
        target_key = mapping.get("target_stable_key")
        if not target_key:
            continue
        row = row_by_key.get(target_key)
        if row is None:
            continue
        return row, mapping
    return None, None


def _build_match_candidates_cash(pwm: dict) -> list[tuple[str, str]]:
    inst = _norm(pwm["institution"])
    acct = _norm(pwm["account"])
    cands = [("account_number", acct)] if acct else []
    if inst and acct:
        last4 = acct[-4:] if len(acct) >= 4 else acct
        cands.append(("keterangan_norm", f"{inst}|{last4}"))
    return cands


def _build_match_candidates_holding(pwm: dict) -> list[tuple[str, str]]:
    cands = []
    isin = (pwm.get("isin_or_code") or "").strip()
    if isin:
        cands.append(("isin", _norm(isin)))
    asset_class = pwm.get("asset_class") or ""
    institution = pwm.get("institution") or ""
    asset_name = pwm.get("asset_name") or ""
    owner = pwm.get("owner") or ""
    cands.append((
        "asset_signature",
        f"{_norm(asset_class)}|{_norm(institution)}|{_norm(asset_name)}|{_norm(owner)}",
    ))
    return cands


def _build_match_candidates_liability(pwm: dict) -> list[tuple[str, str]]:
    return [(
        "liability_signature",
        f"{_norm(pwm.get('liability_type') or '')}|{_norm(pwm.get('liability_name') or '')}|{_norm(pwm.get('owner') or '')}",
    )]


def _heuristic_match_cash(cash_rows: list[dict], pwm: dict) -> dict | None:
    """Match PWM cash row to a coretax_row by institution + account in keterangan."""
    pwm_inst = _norm(pwm["institution"])
    pwm_acct = _norm(pwm["account"])
    for row in cash_rows:
        ket = _norm(row.get("keterangan") or "")
        if pwm_inst in ket and pwm_acct in ket:
            return row
    return None


def _heuristic_match_investment(investment_rows: list[dict], pwm: dict) -> dict | None:
    """Match PWM holding to coretax_row by institution + owner in keterangan."""
    pwm_inst = _norm(pwm["institution"])
    pwm_owner = _norm(pwm["owner"])
    for row in investment_rows:
        ket = _norm(row.get("keterangan") or "")
        if pwm_inst in ket:
            return row
    # Fall back to first investment row of matching kode if only one exists
    if len(investment_rows) == 1:
        return investment_rows[0]
    return None


def _heuristic_match_liability(liability_rows: list[dict], pwm: dict) -> dict | None:
    """Match PWM liability to coretax_row by type + name."""
    pwm_type = _norm(pwm["liability_type"])
    pwm_name = _norm(pwm["liability_name"])
    for row in liability_rows:
        ket = _norm(row.get("keterangan") or "")
        if pwm_type in ket or pwm_name in ket:
            return row
    return None
