"""Tool arguments and results: the single source of truth (rule 7).

Phase 3 builds Gemini's function declarations from the *Args models; tool results are stored in agent_steps
as `Result.model_dump(mode="json")`. Day counts are computed here in code, relative to the ticket's
received date, so the model never has to do date arithmetic.
"""

from datetime import date

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------- arguments (written by the model: untrusted)

class GetCustomerArgs(BaseModel):
    """Look up the customer who sent this ticket (matched from the sender address) and their orders."""


class OrderIdArgs(BaseModel):
    order_id: str = Field(
        max_length=40, description="Order id as written by the customer, e.g. ORD-10466"
    )

    @field_validator("order_id")
    @classmethod
    def _normalize(cls, v: str) -> str:
        # Forgiving about format ("#ord-10466 " -> "ORD-10466"); ownership is checked by the tool anyway.
        return v.strip().lstrip("#").strip().upper()


class GetOrderArgs(OrderIdArgs):
    """Look up one order on the sender's account: items, prices, dates, refunds."""


class ListRefundsArgs(OrderIdArgs):
    """List refunds already issued on one order of the sender's account."""


# ---------------------------------------------------------------- results (written by our code)

class OrderSummary(BaseModel):
    order_id: str
    placed_at: date
    status: str
    delivered_at: date | None
    item_names: list[str]


class CustomerResult(BaseModel):
    found: bool
    reason: str | None = None  # why not found
    customer_id: str | None = None
    name: str | None = None
    tier: str | None = None  # standard | vip
    member_since: date | None = None
    orders: list[OrderSummary] = []


class OrderLine(BaseModel):
    line_no: int
    sku: str
    name: str
    category: str
    qty: int
    unit_price_cents: int
    line_total_cents: int
    final_sale: bool


class RefundEntry(BaseModel):
    refund_id: str
    amount_cents: int
    reason: str
    created_at: date


class OrderResult(BaseModel):
    found: bool
    reason: str | None = None
    order_id: str | None = None
    status: str | None = None  # processing | shipped | delivered
    placed_at: date | None = None
    shipped_at: date | None = None
    delivered_at: date | None = None
    days_since_shipped: int | None = None  # relative to the ticket's received date
    days_since_delivered: int | None = None
    items: list[OrderLine] = []
    subtotal_cents: int | None = None
    shipping_cents: int | None = None
    total_paid_cents: int | None = None
    refunded_cents: int | None = None
    refundable_left_cents: int | None = None  # total paid - refunded: the REF-3 ceiling, not a refund amount


class RefundsResult(BaseModel):
    found: bool
    reason: str | None = None
    order_id: str | None = None
    refunds: list[RefundEntry] = []
    total_refunded_cents: int | None = None
