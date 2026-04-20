"""Cash pool endpoints (backend API, hidden from PWA UI in v1)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_auth
from api.db import get_db
from api.models import CashPoolCreate, CashPoolResponse

router = APIRouter(prefix="/api/household/cash-pools", tags=["cash-pools"])


@router.get("", response_model=list[CashPoolResponse])
def list_cash_pools(_auth: dict = Depends(require_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, funded_amount, funded_at, remaining_amount, status, notes "
            "FROM cash_pools ORDER BY funded_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("", response_model=CashPoolResponse, status_code=status.HTTP_201_CREATED)
def create_cash_pool(body: CashPoolCreate, _auth: dict = Depends(require_auth)):
    pool_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT INTO cash_pools (id, name, funded_amount, funded_at, remaining_amount, status, notes) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (pool_id, body.name, body.funded_amount, body.funded_at, body.funded_amount, body.notes),
        )
        row = conn.execute("SELECT * FROM cash_pools WHERE id = ?", (pool_id,)).fetchone()

    return dict(row)
