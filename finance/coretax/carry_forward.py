"""Carry-forward logic: applies rule defaults + user overrides from staging
to create coretax_rows records for the target tax year.

Semantics:
  - prior_amount_idr = parsed F from prior template (= value-at-end-of-(T-1))
  - current_amount_idr:
      - If carry_forward: copy prior_amount_idr, source='carried_forward'
      - If NOT carry_forward: NULL, source='unset'
  - market_value_idr = parsed_market_value_idr from staging
  - No zero pre-materialization for refreshable codes
"""
from __future__ import annotations

from finance.coretax.db import (
    _utcnow,
    ensure_coretax_tables,
    get_asset_codes,
    get_staging_batch,
)


def commit_staging_batch(conn, batch_id: str, target_tax_year: int) -> dict:
    """Commit a staging batch into coretax_rows.

    For each staging row:
      1. Determine carry_forward (user override > rule default)
      2. Insert into coretax_rows with proper source tracking
      3. Delete staging rows for this batch

    Returns dict with counts: committed, skipped, errors.
    """
    ensure_coretax_tables(conn)

    staging_rows = get_staging_batch(conn, batch_id)
    if not staging_rows:
        return {"committed": 0, "skipped": 0, "errors": []}

    asset_codes = {ac["kode"]: ac for ac in get_asset_codes(conn)}

    committed = 0
    skipped = 0
    errors: list[str] = []

    for srow in staging_rows:
        try:
            _commit_single_staging_row(conn, srow, target_tax_year, asset_codes)
            committed += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"Row {srow['source_row_no']}: {exc}")

    # Clean up staging for this batch
    conn.execute(
        "DELETE FROM coretax_import_staging WHERE staging_batch_id = ?",
        (batch_id,),
    )
    conn.commit()

    return {"committed": committed, "skipped": skipped, "errors": errors}


def _commit_single_staging_row(conn, srow: dict, target_tax_year: int,
                                asset_codes: dict) -> None:
    """Convert a single staging row into a coretax_rows record."""
    kode = srow["parsed_kode_harta"]
    carry_forward = _resolve_carry_forward(srow, asset_codes)

    # prior_amount = column F of prior template (= value at end of prior year = E of new)
    prior_amount = srow["parsed_carry_amount_idr"]

    # current_amount: carry forward or unset
    if carry_forward:
        current_amount = prior_amount
        current_source = "carried_forward"
    else:
        current_amount = None
        current_source = "unset"

    # prior_amount_source: always 'imported' since this came from the template
    prior_source = "imported"

    market_value = srow["parsed_market_value_idr"]
    market_source = "imported" if market_value is not None else "unset"

    stable_key = srow["proposed_stable_key"]
    if not stable_key:
        from finance.coretax.db import make_stable_key_manual
        stable_key = make_stable_key_manual(
            kode or "",
            srow["parsed_keterangan"] or "",
            srow["parsed_acquisition_year"],
        )

    # Check for existing row with same (tax_year, stable_key) — should not happen
    # on first import but guard against re-import
    existing = conn.execute(
        "SELECT id FROM coretax_rows WHERE tax_year = ? AND stable_key = ?",
        (target_tax_year, stable_key),
    ).fetchone()

    now = _utcnow()

    if existing:
        # Update existing row with imported values (only if not locked)
        conn.execute(
            """UPDATE coretax_rows SET
               kode_harta = ?, asset_type_label = ?, keterangan = ?,
               acquisition_year = ?,
               prior_amount_idr = ?, prior_amount_source = ?,
               current_amount_idr = COALESCE(current_amount_idr, ?),
               current_amount_source = CASE WHEN current_amount_source IS NULL OR current_amount_source = 'unset' THEN ? ELSE current_amount_source END,
               market_value_idr = COALESCE(market_value_idr, ?),
               market_value_source = CASE WHEN market_value_source IS NULL OR market_value_source = 'unset' THEN ? ELSE market_value_source END,
               updated_at = ?
               WHERE id = ?""",
            (kode, _label_for_kode(kode, asset_codes), srow["parsed_keterangan"],
             srow["parsed_acquisition_year"],
             prior_amount, prior_source,
             current_amount, current_source,
             market_value, market_source,
             now, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO coretax_rows
               (tax_year, kind, stable_key, kode_harta, asset_type_label,
                keterangan, acquisition_year,
                prior_amount_idr, prior_amount_source,
                current_amount_idr, current_amount_source,
                market_value_idr, market_value_source,
                amount_locked, market_value_locked,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            (target_tax_year, srow["parsed_kind"] or "asset", stable_key,
             kode, _label_for_kode(kode, asset_codes),
             srow["parsed_keterangan"], srow["parsed_acquisition_year"],
             prior_amount, prior_source,
             current_amount, current_source,
             market_value, market_source,
             now, now),
        )


def _resolve_carry_forward(srow: dict, asset_codes: dict) -> bool:
    """Determine if a row should carry forward.

    Priority: user_override > rule default > False (refreshable).
    """
    # User override takes precedence
    override = srow.get("user_override_carry_forward")
    if override is not None:
        return bool(override)

    # Rule default from asset codes
    kode = srow.get("parsed_kode_harta")
    if kode and kode in asset_codes:
        return bool(asset_codes[kode]["default_carry_forward"])

    # Default: don't carry forward (refreshable)
    return False


def _label_for_kode(kode: str | None, asset_codes: dict) -> str:
    if kode and kode in asset_codes:
        return asset_codes[kode]["label"]
    return ""


def reset_from_rules(conn, tax_year: int, kind: str | None = None,
                     kode_harta: str | None = None) -> dict:
    """Re-apply carry-forward rule defaults to UNLOCKED rows only.

    Resets current_amount_idr to prior_amount_idr for rows where
    current_amount_source = 'carried_forward' AND amount_locked = 0.
    Useful after editing rules in the codes table.
    """
    asset_codes = {ac["kode"]: ac for ac in get_asset_codes(conn)}

    query = """SELECT id, kode_harta, prior_amount_idr, current_amount_source, amount_locked
               FROM coretax_rows
               WHERE tax_year = ? AND amount_locked = 0"""
    params: list = [tax_year]

    if kind:
        query += " AND kind = ?"
        params.append(kind)
    if kode_harta:
        query += " AND kode_harta = ?"
        params.append(kode_harta)

    rows = conn.execute(query, params).fetchall()

    reset_count = 0
    now = _utcnow()
    for row in rows:
        kode = row["kode_harta"]
        if kode and kode in asset_codes:
            should_carry = bool(asset_codes[kode]["default_carry_forward"])
        else:
            continue

        if should_carry:
            conn.execute(
                """UPDATE coretax_rows SET
                   current_amount_idr = prior_amount_idr,
                   current_amount_source = 'carried_forward',
                   updated_at = ?
                   WHERE id = ?""",
                (now, row["id"]),
            )
        else:
            conn.execute(
                """UPDATE coretax_rows SET
                   current_amount_idr = NULL,
                   current_amount_source = 'unset',
                   updated_at = ?
                   WHERE id = ?""",
                (now, row["id"]),
            )
        reset_count += 1

    conn.commit()
    return {"reset_count": reset_count}
