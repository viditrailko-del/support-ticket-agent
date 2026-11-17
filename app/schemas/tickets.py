"""API models for /tickets. GET returns the full trace; fields filled by later phases are null/empty until then."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.db.models import Action, AgentStep, Ticket


class TicketIn(BaseModel):
    from_email: EmailStr = Field(description="Sender address. The customer is matched from this, nothing else.")
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=20_000)
    received_at: datetime | None = Field(
        default=None,
        description="When the email arrived (default: now). Set it to replay labeled eval tickets, which are "
        "all received on the shop's fixed date.",
    )

    @field_validator("received_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return v.replace(tzinfo=UTC) if v is not None and v.tzinfo is None else v


class StepOut(BaseModel):
    seq: int
    kind: str
    model: str | None
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_result: dict[str, Any] | None
    raw_output: str | None
    error: str | None
    latency_ms: int | None
    created_at: datetime

    @classmethod
    def from_row(cls, s: AgentStep) -> "StepOut":
        return cls.model_validate(s, from_attributes=True)


class ActionOut(BaseModel):
    id: int
    type: str
    order_id: str | None
    amount_cents: int | None
    idempotency_key: str
    status: str
    created_at: datetime
    executed_at: datetime | None

    @classmethod
    def from_row(cls, a: Action) -> "ActionOut":
        return cls.model_validate(a, from_attributes=True)


class TicketOut(BaseModel):
    id: uuid.UUID
    status: str
    from_email: str
    subject: str
    body: str
    received_at: datetime
    customer_id: str | None  # None: the sender is not a customer
    category: str | None
    urgency: str | None
    prompt_version: str | None
    policy_version: str | None
    steps: list[StepOut]
    proposal: dict[str, Any] | None
    flags: list[dict[str, Any]]
    decision: str | None
    actions: list[ActionOut]
    created_at: datetime

    @classmethod
    def from_ticket(cls, t: Ticket) -> "TicketOut":
        return cls(
            id=t.id, status=t.status, from_email=t.from_email, subject=t.subject, body=t.body,
            received_at=t.received_at, customer_id=t.customer_id, category=t.category, urgency=t.urgency,
            prompt_version=t.prompt_version, policy_version=t.policy_version,
            steps=[StepOut.from_row(s) for s in t.steps], proposal=t.proposal, flags=t.flags or [],
            decision=t.decision, actions=[ActionOut.from_row(a) for a in t.actions], created_at=t.created_at,
        )
