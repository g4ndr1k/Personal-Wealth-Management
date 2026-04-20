"""Export + reconciliation endpoints for Mac Mini integration."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.auth import require_auth
from api.db import get_db
from api.models import ReconcileRequest, TransactionResponse

router = APIRouter(prefix="/api/household", tags=["export"])


@router.get("/export/unreconciled", response_model=list[TransactionResponse])
def export_unreconciled(_auth: dict = Depends(require_auth)):
    """Return all active pending transactions for agentic-ai import."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM household_transactions "
            "WHERE reconcile_status = 'pending' AND is_deleted = 0 "
            "ORDER BY txn_datetime ASC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/reconcile")
def reconcile(body: ReconcileRequest, _auth: dict = Depends(require_auth)):
    """Mark household transactions as reconciled with matched agentic-ai transaction hashes."""
    now = datetime.now(timezone.utc).isoformat()
    updated = 0

    with get_db() as conn:
        for item in body.matches:
            cur = conn.execute(
                "UPDATE household_transactions "
                "SET reconcile_status = 'reconciled', matched_pwm_txn_id = ?, "
                "reconciled_at = ?, updated_at = ? "
                "WHERE client_txn_id = ? AND reconcile_status = 'pending' AND is_deleted = 0",
                (item.matched_pwm_txn_id, now, now, item.client_txn_id),
            )
            updated += cur.rowcount

    return {"updated": updated, "total": len(body.matches)}
