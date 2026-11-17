"""The agent's final proposal: the single source of truth for the `submit_proposal` tool declaration (rule 7).

The model PROPOSES; nothing here executes. Phase 4 re-checks every field in code: the order must be the
sender's, the amount is recomputed from `items` + `include_shipping` (the model's `amount_cents` is only
compared, never used), and every citation quote must be an exact substring of its rule.

Structural rules (refund needs items and an amount, etc.) are enforced here, so an invalid proposal goes back
to the model once with the errors (rule: invalid output -> one retry).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionType(StrEnum):
    refund = "refund"
    replace = "replace"
    reply_only = "reply_only"
    escalate = "escalate"


class ProposedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_no: int = Field(ge=1, description="line_no of the order line, from get_order")
    qty: int = Field(ge=1, description="how many units of that line")


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(max_length=20, description="Policy rule id, e.g. REF-1")
    quote: str = Field(min_length=1, max_length=500, description="Exact words copied from that rule's text")


class Proposal(BaseModel):
    """Submit your final decision for this ticket. Call this exactly once, after looking up what you need."""

    model_config = ConfigDict(extra="forbid")

    action: ActionType
    order_id: str | None = Field(
        default=None, max_length=40, description="The order this is about (as returned by get_order), or null"
    )
    items: list[ProposedItem] = Field(
        default=[], description="refund/replace: the order lines refunded or replaced. Empty otherwise."
    )
    include_shipping: bool = Field(
        default=False, description="refund only: true only when a policy says the shipping fee is refunded too"
    )
    amount_cents: int | None = Field(
        default=None, ge=0, description="refund only: total refund in cents. Null for every other action."
    )
    reply_draft: str = Field(
        min_length=1, max_length=3000, description="The reply email to the customer (plain text, no internal details)"
    )
    citations: list[Citation] = Field(
        min_length=1, max_length=6, description="The policy rules that decide this outcome, each with an exact quote"
    )
    rationale: str = Field(max_length=600, description="For the human reviewer: why this action, in 1-3 sentences")

    @model_validator(mode="after")
    def _consistent(self) -> "Proposal":
        a = self.action
        if a is ActionType.refund:
            if self.order_id is None or not self.items or self.amount_cents is None:
                raise ValueError("refund needs order_id, items and amount_cents")
        elif a is ActionType.replace:
            if self.order_id is None or not self.items:
                raise ValueError("replace needs order_id and items")
        if a is not ActionType.refund and (self.amount_cents is not None or self.include_shipping):
            raise ValueError(f"{a.value} must have amount_cents null and include_shipping false")
        if a in (ActionType.reply_only, ActionType.escalate) and self.items:
            raise ValueError(f"{a.value} must have no items")
        if len({i.line_no for i in self.items}) != len(self.items):
            raise ValueError("each line_no may appear only once in items")
        return self
