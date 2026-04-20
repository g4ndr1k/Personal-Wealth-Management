"""Transaction CRUD endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import require_auth
from api.db import get_db
from api.models import TransactionCreate, TransactionResponse, TransactionUpdate

router = APIRouter(prefix="/api/household/transactions", tags=["transactions"])


@router.get("", response_model=list[TransactionResponse])
def list_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    from_date: str | None = None,
    to_date: str | None = None,
    category: str | None = None,
    reconcile_status: str | None = None,
    _auth: dict = Depends(require_auth),
):
    clauses = ["is_deleted = 0"]
    params: list = []

    if from_date:
        clauses.append("txn_datetime >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("txn_datetime <= ?")
        params.append(to_date)
    if category:
        clauses.append("category_code = ?")
        params.append(category)
    if reconcile_status:
        clauses.append("reconcile_status = ?")
        params.append(reconcile_status)

    where = " AND ".join(clauses)
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM household_transactions WHERE {where} ORDER BY txn_datetime DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()

    return [dict(r) for r in rows]


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(body: TransactionCreate, _auth: dict = Depends(require_auth)):
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # Verify category exists
        cat = conn.execute("SELECT 1 FROM household_categories WHERE code = ?", (body.category_code,)).fetchone()
        if not cat:
            raise HTTPException(status_code=400, detail=f"Unknown category: {body.category_code}")

        # client_txn_id dedup
        existing = conn.execute(
            "SELECT id FROM household_transactions WHERE client_txn_id = ?", (body.client_txn_id,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Duplicate client_txn_id")

        cur = conn.execute(
            "INSERT INTO household_transactions "
            "(client_txn_id, created_at, updated_at, txn_datetime, amount, currency, "
            "category_code, merchant, description, payment_method, cash_pool_id, "
            "recorded_by, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                body.client_txn_id, now, now, body.txn_datetime, body.amount, body.currency,
                body.category_code, body.merchant, body.description, body.payment_method,
                body.cash_pool_id, _auth["username"], body.note,
            ),
        )
        row = conn.execute("SELECT * FROM household_transactions WHERE id = ?", (cur.lastrowid,)).fetchone()

    return dict(row)


@router.put("/{txn_id}", response_model=TransactionResponse)
def update_transaction(txn_id: int, body: TransactionUpdate, _auth: dict = Depends(require_auth)):
    now = datetime.now(timezone.utc).isoformat()

    updates = []
    params: list = []
    for field in ("txn_datetime", "amount", "currency", "category_code", "merchant",
                  "description", "payment_method", "cash_pool_id", "note"):
        val = getattr(body, field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = ?")
    params.append(now)
    params.append(txn_id)

    with get_db() as conn:
        # Verify category if changing
        if body.category_code is not None:
            cat = conn.execute("SELECT 1 FROM household_categories WHERE code = ?", (body.category_code,)).fetchone()
            if not cat:
                raise HTTPException(status_code=400, detail=f"Unknown category: {body.category_code}")

        conn.execute(
            f"UPDATE household_transactions SET {', '.join(updates)} WHERE id = ? AND is_deleted = 0",
            params,
        )
        row = conn.execute("SELECT * FROM household_transactions WHERE id = ?", (txn_id,)).fetchone()

    if not row or row["is_deleted"]:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return dict(row)


@router.delete("/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(txn_id: int, _auth: dict = Depends(require_auth)):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE household_transactions SET is_deleted = 1, updated_at = ? WHERE id = ? AND is_deleted = 0",
            (now, txn_id),
        )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
