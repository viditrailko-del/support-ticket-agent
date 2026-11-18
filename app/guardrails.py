"""Glue between the DB and the pure checks: gather the facts, run the checks, store flags and decision.

The facts are read here, fresh from the shop tables, never from what the agent saw or wrote: the order is
looked up by the id in the proposal (normalized like the tools do), WITHOUT the ownership filter, so the
check can tell someone else's order from a missing one.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.checks import CheckResult, Decision, Facts, LineFacts, Limits, OrderFacts, run_checks
from app.config import Settings
from app.db.models import Customer, Order, Ticket
from app.logging_setup import log_event
from app.policies import load_policies
from app.schemas.proposal import Proposal
from app.schemas.tools import OrderIdArgs

log = logging.getLogger(__name__)


def normalize_order_id(order_id: str | None) -> str | None:
    return None if order_id is None else OrderIdArgs(order_id=order_id).order_id


def load_order(db: Session, order_id: str | None, *, lock: bool = False) -> Order | None:
    oid = normalize_order_id(order_id)
    if oid is None:
        return None
    q = select(Order).where(Order.id == oid).options(selectinload(Order.items), selectinload(Order.refunds))
    if lock:  # serialize refunds on one order (two tickets about the same order)
        q = q.with_for_update(of=Order)
    return db.scalar(q.execution_options(populate_existing=True))


def build_facts(db: Session, ticket: Ticket, proposal: Proposal, *, lock_order: bool = False) -> Facts:
    today = ticket.received_at.date()
    customer = db.get(Customer, ticket.customer_id) if ticket.customer_id else None
    o = load_order(db, proposal.order_id, lock=lock_order)
    order = None if o is None else OrderFacts(
        order_id=o.id, customer_id=o.customer_id, status=o.status,
        days_since_shipped=(today - o.shipped_at).days if o.shipped_at else None,
        days_since_delivered=(today - o.delivered_at).days if o.delivered_at else None,
        lines=tuple(LineFacts(i.line_no, i.qty, i.unit_price_cents, i.category, i.final_sale) for i in o.items),
        shipping_cents=o.shipping_cents, total_paid_cents=o.total_paid_cents,
        refunded_cents=sum(r.amount_cents for r in o.refunds),
    )
    rules = {r.id: r.text for r in load_policies(db).rules}
    return Facts(sender_customer_id=ticket.customer_id, sender_tier=customer.tier if customer else None,
                 category=ticket.category, subject=ticket.subject, body=ticket.body, order=order, rules=rules)


def check_proposal(db: Session, ticket: Ticket, proposal: Proposal, settings: Settings,
                   *, lock_order: bool = False) -> CheckResult:
    facts = build_facts(db, ticket, proposal, lock_order=lock_order)
    return run_checks(proposal, facts, Limits(auto_limit_cents=settings.auto_limit_cents))


def apply_result(ticket: Ticket, result: CheckResult) -> None:
    ticket.flags = result.flags_json
    ticket.decision = result.decision.value
    ticket.amount_cents = result.amount_cents


def guard_ticket(db: Session, ticket: Ticket, settings: Settings) -> Ticket:
    """proposed -> auto_approved | needs_human | rejected. A failed ticket gets decision `failed`."""
    if ticket.status == "failed":
        ticket.decision = Decision.failed.value
        db.commit()
        return ticket
    if ticket.status != "proposed" or ticket.proposal is None:
        return ticket
    result = check_proposal(db, ticket, Proposal.model_validate(ticket.proposal), settings)
    apply_result(ticket, result)
    ticket.status = result.decision.value
    db.commit()
    log_event(log, logging.INFO, "ticket_checked", ticket_id=ticket.id, decision=ticket.decision,
              flags=[f.code for f in result.flags], amount_cents=result.amount_cents)
    return ticket
