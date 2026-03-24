from pydantic import BaseModel, Field
from typing import Literal


Category = Literal[
    "transaction_alert",
    "bill_statement",
    "bank_clarification",
    "payment_due",
    "security_alert",
    "financial_other",
    "not_financial",
]

Urgency = Literal["low", "medium", "high"]


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    summary: str = Field(max_length=200)
    requires_action: bool = False
    provider: str
