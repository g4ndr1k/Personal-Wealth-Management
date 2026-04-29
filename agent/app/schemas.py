from pydantic import BaseModel, Field
from typing import Literal


Category = Literal[
    "urgent",
    "important",
    "reply_needed",
    "personal",
    "newsletter",
    "automated",
    "spam",
    "transaction_alert",
    "bill_statement",
    "bank_clarification",
    "payment_due",
    "security_alert",
    "financial_other",
    "not_financial",
]

Urgency = Literal["low", "medium", "high", "urgent"]
Action = Literal["alert", "draft", "label", "ignore", "pdf_route"]


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    summary: str = Field(max_length=200)
    requires_action: bool = False
    provider: str
    priority: int = Field(default=0, ge=0, le=10)
    action: Action = "ignore"
    filename: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=200)
