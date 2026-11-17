"""Ticket classification: the model's output schema, and the source of truth for Gemini's response schema
(rule 7). Classification sees only the email: no order data, no dates. Dates decide which rule applies
(damaged vs warranty, refund window...) later, in code.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    refund_request = "refund_request"  # changed mind, return for money (incl. partial returns)
    damaged_or_defective = "damaged_or_defective"  # arrived broken, stopped working, warranty defect
    wrong_item = "wrong_item"  # received something other than what was ordered
    delivery_issue = "delivery_issue"  # late, lost, where is my order
    cancellation = "cancellation"  # cancel an order
    account = "account"  # change email / address / password / payment details
    other = "other"  # compensation, complaint, unclear, anything else


class Urgency(StrEnum):
    high = "high"  # legal action, chargeback, payment dispute
    normal = "normal"  # default: a problem to fix
    low = "low"  # general question, nothing to fix


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category = Field(description="The customer's main request, judged from what they actually ask for")
    urgency: Urgency
    reason: str = Field(max_length=300, description="One short sentence: why this category and urgency")
