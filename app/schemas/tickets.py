"""API models for /tickets. GET returns the full trace; fields filled by later phases are null/empty until then."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import Action, AgentStep, Review, Ticket
from app.schemas.proposal import ActionType, Citation, ProposedItem


class AttachmentIn(BaseModel):
    """What the email said it carried. Only metadata: the agent can't read files, so their contents are never
    accepted, and a ticket with any attachment always goes to a human (the evidence may be in the file)."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)


class TicketIn(BaseModel):
    # Unknown fields are an error, not silently dropped: a caller must not believe something reached us.
    model_config = ConfigDict(extra="forbid")

    from_email: EmailStr = Field(description="Sender address. The customer is matched from this, nothing else.")
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=20_000, description="Must contain some text (not only whitespace)")
    attachments: list[AttachmentIn] = Field(default=[], max_length=50)
    received_at: datetime | None = Field(
        default=None,
        description="When the email arrived (default: now). Set it to replay labeled eval tickets, which are "
        "all received on the shop's fixed date.",
    )

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # Checked, not stripped: the body is stored exactly as received (rule 4).
        if not v.strip():
            raise ValueError("body is empty (only whitespace)")
        return v

    @field_validator("received_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return v.replace(tzinfo=UTC) if v is not None and v.tzinfo is None else v


class StepOut(BaseModel):
    seq: int
    kind: str
    model: str | None
    prompt_version: str | None
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_result: dict[str, Any] | None
    raw_output: str | None
    error: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    cost_usd: Decimal | None
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
    items: list[dict[str, Any]]
    reply_text: str | None
    approved_by: str | None
    result: dict[str, Any] | None
    created_at: datetime
    executed_at: datetime | None

    @classmethod
    def from_row(cls, a: Action) -> "ActionOut":
        return cls.model_validate(a, from_attributes=True)


class ReviewOut(BaseModel):
    id: int
    reviewer: str
    kind: str
    note: str | None
    proposal_before: dict[str, Any] | None
    proposal_after: dict[str, Any] | None
    decision_before: str | None
    decision_after: str | None
    flags_after: list[dict[str, Any]] | None
    status_before: str
    status_after: str
    created_at: datetime

    @classmethod
    def from_row(cls, r: Review) -> "ReviewOut":
        return cls.model_validate(r, from_attributes=True)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=100, description="Who is reviewing (no auth yet)")
    note: str | None = Field(default=None, max_length=2000)


class EditIn(ReviewIn):
    """The new proposal. There is no amount field: a refund amount is always computed from the items."""

    action: ActionType
    order_id: str | None = Field(default=None, max_length=40)
    items: list[ProposedItem] = []
    include_shipping: bool = False
    reply_draft: str = Field(min_length=1, max_length=3000)
    citations: list[Citation] | None = Field(default=None, description="Default: keep the current citations")
    rationale: str | None = Field(default=None, max_length=600, description="Default: keep the current one")


class TicketOut(BaseModel):
    id: uuid.UUID
    status: str
    failure_reason: str | None
    from_email: str
    subject: str
    body: str
    attachments: list[dict[str, Any]]
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
    amount_cents: int | None  # refund amount computed by code
    actions: list[ActionOut]
    reviews: list[ReviewOut]
    created_at: datetime

    @classmethod
    def from_ticket(cls, t: Ticket) -> "TicketOut":
        return cls(
            id=t.id, status=t.status, failure_reason=t.failure_reason, from_email=t.from_email, subject=t.subject, body=t.body,
            attachments=t.attachments or [],
            received_at=t.received_at, customer_id=t.customer_id, category=t.category, urgency=t.urgency,
            prompt_version=t.prompt_version, policy_version=t.policy_version,
            steps=[StepOut.from_row(s) for s in t.steps], proposal=t.proposal, flags=t.flags or [],
            decision=t.decision, amount_cents=t.amount_cents, actions=[ActionOut.from_row(a) for a in t.actions],
            reviews=[ReviewOut.from_row(r) for r in t.reviews], created_at=t.created_at,
        )
