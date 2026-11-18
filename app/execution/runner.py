"""The simulated, idempotent execution step (rule 3: the model never executes anything).

Runs only for a ticket whose status is `auto_approved` (checks) or `approved` (a human). Why idempotent:
a retry, a double click or two workers must never refund twice. The action's idempotency key is derived
from the ticket, it is unique in the DB, and the ticket row is locked while executing, so the second call
finds the first action and returns it instead of doing anything.

Just before paying, the checks run again on fresh shop data with the order row locked: another ticket may
have refunded the same order since. Any `reject` flag, or a refund amount that changed, stops the
execution and sends the ticket back to a human instead.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checks import Severity
from app.config import Settings
from app.db.models import Action, Refund, Ticket
from app.guardrails import apply_result, check_proposal, normalize_order_id
from app.logging_setup import log_event
from app.schemas.proposal import ActionType, Proposal

log = logging.getLogger(__name__)

EXECUTABLE = ("auto_approved", "approved")


class NotExecutable(Exception):
    pass


def idempotency_key(ticket: Ticket) -> str:
    return f"ticket:{ticket.id}"


def lock_ticket(db: Session, ticket: Ticket) -> Ticket:
    """Re-read the ticket with a row lock (released at commit/rollback)."""
    return db.scalar(select(Ticket).where(Ticket.id == ticket.id).with_for_update()
                     .execution_options(populate_existing=True))  # type: ignore[return-value]


def execute_ticket(db: Session, ticket: Ticket, settings: Settings, *, approved_by: str) -> Action | None:
    """Execute the ticket's current proposal once. Returns the action (the existing one on a repeat call),
    or None when the pre-execution re-check stopped it."""
    ticket = lock_ticket(db, ticket)
    existing = db.scalar(select(Action).where(Action.idempotency_key == idempotency_key(ticket)))
    if existing is not None:
        db.rollback()
        return existing
    if ticket.status not in EXECUTABLE or ticket.proposal is None:
        db.rollback()
        raise NotExecutable(f"ticket is {ticket.status}, not approved")

    p = Proposal.model_validate(ticket.proposal)
    result = check_proposal(db, ticket, p, settings, lock_order=True)
    rejected = any(f.severity is Severity.reject for f in result.flags)
    if rejected or result.amount_cents != ticket.amount_cents:
        apply_result(ticket, result)
        ticket.status = "rejected" if rejected else "needs_human"
        db.commit()
        log_event(log, logging.WARNING, "execution_stopped", ticket_id=ticket.id, status=ticket.status,
                  flags=[f.code for f in result.flags], amount_cents=result.amount_cents)
        return None

    now = datetime.now(UTC)
    action = Action(
        ticket_id=ticket.id, type=p.action.value, order_id=normalize_order_id(p.order_id),
        amount_cents=ticket.amount_cents, idempotency_key=idempotency_key(ticket), status="executed",
        items=[i.model_dump() for i in p.items], reply_text=p.reply_draft, approved_by=approved_by,
        executed_at=now,
    )
    if p.action is ActionType.refund:
        refund_id = f"RF-T{ticket.id.hex[:12].upper()}"
        db.add(Refund(id=refund_id, order_id=action.order_id, amount_cents=ticket.amount_cents,
                      reason=ticket.category or "support_ticket", created_at=ticket.received_at.date()))
        action.result = {"refund_id": refund_id, "amount_cents": ticket.amount_cents, "reply_sent": True}
    elif p.action is ActionType.replace:
        action.result = {"replacement_items": action.items, "reply_sent": True}
    else:  # reply_only, or an escalation a human took over: the reply is the action
        action.result = {"reply_sent": True}
    db.add(action)
    ticket.status = "executed"
    db.commit()
    log_event(log, logging.INFO, "ticket_executed", ticket_id=ticket.id, action=action.type,
              amount_cents=action.amount_cents, approved_by=approved_by)
    return action
