"""What the checks are allowed to know: plain frozen data, built from the DB by app/guardrails.py.

The checks themselves never touch the DB or the network (rule 6), so every fact they use is in here and
tests can build any situation by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    reject = "reject"  # impossible action (someone else's order, more than is left to refund): never runs
    review = "review"  # a human must look (outside policy, unverifiable, suspicious): blocks auto-approval
    info = "info"  # worth showing the reviewer, changes nothing (e.g. amount capped at what is left)


@dataclass(frozen=True)
class Flag:
    code: str
    severity: Severity
    message: str  # for the human reviewer; internal, never shown to the customer

    def as_json(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity.value, "message": self.message}


@dataclass(frozen=True)
class LineFacts:
    line_no: int
    qty: int
    unit_price_cents: int
    category: str  # home | electronics | apparel | outdoor | clearance
    final_sale: bool


@dataclass(frozen=True)
class OrderFacts:
    """The order the proposal names, looked up WITHOUT the ownership filter so the check can tell 'someone
    else's' from 'missing'. The tools never reveal that difference to the model; the checks may."""

    order_id: str
    customer_id: str
    status: str  # processing | shipped | delivered
    days_since_shipped: int | None
    days_since_delivered: int | None
    lines: tuple[LineFacts, ...]
    shipping_cents: int
    total_paid_cents: int
    refunded_cents: int

    @property
    def refundable_left_cents(self) -> int:
        return self.total_paid_cents - self.refunded_cents

    def line(self, line_no: int) -> LineFacts | None:
        return next((ln for ln in self.lines if ln.line_no == line_no), None)


@dataclass(frozen=True)
class Facts:
    sender_customer_id: str | None  # from the sender address only (rule 1)
    sender_tier: str | None  # standard | vip
    category: str | None  # from classification
    subject: str
    body: str
    order: OrderFacts | None  # None: the proposal names no order, or the order does not exist
    rules: dict[str, str] = field(default_factory=dict)  # rule_id -> rule text (what the prompt contained)
    attachments: tuple[str, ...] = ()  # filenames the email carried (the agent never sees their contents)
