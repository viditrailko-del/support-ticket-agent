"""Tables. Money is integer cents everywhere; status-like columns are plain strings (no enum to migrate).

Shop tables (customers, orders, order_items, refunds) and policy_rules are seeded from data/ by
`python -m data.seed`. Ticket tables hold everything that happens to a ticket, append-only where it matters
(agent_steps are never updated after the step is used: rule 5).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Base class of every table. Alembic autogenerate and the test DB (`create_all`) read its metadata."""


# ---------------------------------------------------------------- shop (seeded, read by the tools)

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)  # C0001
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lowercase: tickets match on it
    tier: Mapped[str] = mapped_column(String(20))  # standard | vip
    created_at: Mapped[date] = mapped_column(Date)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer", order_by="Order.placed_at")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # ORD-10001
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    placed_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))  # processing | shipped | delivered
    shipped_at: Mapped[date | None] = mapped_column(Date)
    delivered_at: Mapped[date | None] = mapped_column(Date)
    subtotal_cents: Mapped[int] = mapped_column(Integer)
    shipping_cents: Mapped[int] = mapped_column(Integer)
    total_paid_cents: Mapped[int] = mapped_column(Integer)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.line_no"
    )
    refunds: Mapped[list["Refund"]] = relationship(back_populates="order", order_by="Refund.id")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "line_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)  # position in the order, from 1
    sku: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    final_sale: Mapped[bool] = mapped_column(Boolean)

    order: Mapped[Order] = relationship(back_populates="items")


class Refund(Base):
    """Refunds already issued (seeded history; executed refunds will be added here too)."""

    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # RF-0001
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[date] = mapped_column(Date)

    order: Mapped[Order] = relationship(back_populates="refunds")


class PolicyRule(Base):
    """One customer-facing policy rule. The agent's prompt contains all of them (see app/policies.py)."""

    __tablename__ = "policy_rules"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # REF-1
    position: Mapped[int] = mapped_column(Integer)  # order in the prompt (page file order, then rule order)
    page: Mapped[str] = mapped_column(String(100))  # refunds
    page_title: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- tickets and their trace

class Ticket(Base):
    """One customer email. from/subject/body are stored exactly as received: untrusted data (rule 4)."""

    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    from_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # Attachment metadata as received (filename, content_type, size_bytes). Never the contents.
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, server_default=text("'[]'"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # "today" for every date check
    # Matched from the sender address only (rule 1), never from the body or the model. None = not a customer.
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), index=True)
    # received -> classified -> proposed -> auto_approved | needs_human | rejected (guardrails)
    # needs_human -> approved (human) | rejected (human); auto_approved | approved -> executed.
    # Any step before the guardrails can end in failed (see failure_reason).
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    category: Mapped[str | None] = mapped_column(String(40))  # Phase 2
    urgency: Mapped[str | None] = mapped_column(String(20))  # Phase 2
    # The current proposal: the agent's, or a reviewer's edit of it (the agent's original stays in agent_steps
    # and in the first review's proposal_before).
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    flags: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, server_default=text("'[]'"))  # guardrail flags
    # Guardrail decision on the current proposal: auto_approved | needs_human | rejected | failed
    decision: Mapped[str | None] = mapped_column(String(20))
    amount_cents: Mapped[int | None] = mapped_column(Integer)  # refund amount computed by code, never the model's
    # Why a ticket is `failed`: classification_failed | llm_unavailable | step_limit | timeout | invalid_proposal
    failure_reason: Mapped[str | None] = mapped_column(String(40))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    policy_version: Mapped[str | None] = mapped_column(String(16))  # hash of the rules the prompt contained
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="AgentStep.seq"
    )
    actions: Mapped[list["Action"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="Action.id"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="Review.id"
    )


class AgentStep(Base):
    """One model call or tool call, stored BEFORE its result is used (rule 5). kind=classify from Phase 2."""

    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("ticket_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # order within the ticket, from 1
    kind: Mapped[str] = mapped_column(String(20))  # classify | model | tool
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(40))  # e.g. classify_v1
    raw_output: Mapped[str | None] = mapped_column(Text)  # raw model response, before parsing
    tool_name: Mapped[str | None] = mapped_column(String(50))
    tool_args: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # exactly as the model wrote them
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    thinking_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))  # null = unknown price, never 0
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="steps")


class Action(Base):
    """An executed (simulated) action. One per ticket: the idempotency key is derived from the ticket, and
    its unique constraint makes a second execution of the same ticket a no-op, even when two run at once."""

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # refund | replace | reply_only | escalate
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | executed | cancelled
    items: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, server_default=text("'[]'"))
    reply_text: Mapped[str | None] = mapped_column(Text)  # the email "sent" to the customer
    approved_by: Mapped[str | None] = mapped_column(String(100))  # "auto" or the reviewer's name
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # what the simulated execution did
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[Ticket] = relationship(back_populates="actions")


class Review(Base):
    """Audit trail: one row per human approve / reject / edit. Append-only."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))  # approve | reject | edit
    note: Mapped[str | None] = mapped_column(Text)
    proposal_before: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    proposal_after: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # edit only
    decision_before: Mapped[str | None] = mapped_column(String(20))
    decision_after: Mapped[str | None] = mapped_column(String(20))  # edit: the checks' decision on the edit
    flags_after: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
    status_before: Mapped[str] = mapped_column(String(20))
    status_after: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="reviews")
