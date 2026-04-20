"""Category list endpoint."""

from fastapi import APIRouter, Depends

from api.auth import require_auth
from api.db import get_db
from api.models import CategoryResponse

router = APIRouter(prefix="/api/household/categories", tags=["categories"])


@router.get("", response_model=list[CategoryResponse])
def list_categories(_auth: dict = Depends(require_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT code, label_id, sort_order FROM household_categories WHERE is_active = 1 ORDER BY sort_order"
        ).fetchall()
    return [dict(r) for r in rows]
