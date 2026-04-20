"""Pydantic request/response models with validation bounds."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class LoginResponse(BaseModel):
    username: str
    display_name: str


# ── Transactions ──────────────────────────────────────────────────────

class TransactionCreate(BaseModel):
    client_txn_id: str = Field(min_length=1, max_length=64)
    txn_datetime: str = Field(min_length=1, max_length=30)  # ISO 8601 local
    amount: int = Field(gt=0, le=999_999_999)
    currency: str = Field(default="IDR", max_length=3)
    category_code: str = Field(min_length=1, max_length=50)
    merchant: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=500)
    payment_method: Literal["cash", "transfer", "ewallet"] = "cash"
    cash_pool_id: str | None = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=1000)


class TransactionUpdate(BaseModel):
    txn_datetime: str | None = Field(default=None, max_length=30)
    amount: int | None = Field(default=None, gt=0, le=999_999_999)
    currency: str | None = Field(default=None, max_length=3)
    category_code: str | None = Field(default=None, max_length=50)
    merchant: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    payment_method: Literal["cash", "transfer", "ewallet"] | None = None
    cash_pool_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


class TransactionResponse(BaseModel):
    id: int
    client_txn_id: str
    created_at: str
    updated_at: str
    txn_datetime: str
    amount: int
    currency: str
    category_code: str
    merchant: str
    description: str
    payment_method: str
    cash_pool_id: str | None
    recorded_by: str
    note: str
    reconcile_status: str
    matched_pwm_txn_id: str | None
    reconciled_at: str | None


# ── Categories ────────────────────────────────────────────────────────

class CategoryResponse(BaseModel):
    code: str
    label_id: str
    sort_order: int


# ── Cash pools ────────────────────────────────────────────────────────

class CashPoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    funded_amount: int = Field(gt=0, le=999_999_999)
    funded_at: str = Field(min_length=1, max_length=30)
    notes: str = Field(default="", max_length=500)


class CashPoolResponse(BaseModel):
    id: str
    name: str
    funded_amount: int
    funded_at: str
    remaining_amount: int
    status: str
    notes: str


# ── Reconciliation ────────────────────────────────────────────────────

class ReconcileItem(BaseModel):
    client_txn_id: str = Field(min_length=1, max_length=64)
    matched_pwm_txn_id: str = Field(min_length=1, max_length=64)


class ReconcileRequest(BaseModel):
    matches: list[ReconcileItem]
