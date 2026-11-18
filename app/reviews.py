"""Human review: approve / reject / edit, each recorded as an append-only `reviews` row (who, when, what).

  approve  needs_human -> approved -> executed (or back to needs_human/rejected if the pre-execution
           re-check stops it). A ticket with a `reject` flag can't be approved: edit it first.
  reject   needs_human -> rejected. Nothing runs.
  edit     needs_human | rejected -> needs_human | rejected. The reviewer writes the whole proposal except
           the amount, which code computes; every check runs again. An edit never executes by itself: even
           a clean edit waits for an explicit approve.

No auth yet (scope: Phase 4), so `reviewer` is whatever name the caller gives.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.checks import Decision, compute_amount
from app.config import Settings
from app.db.models import Review, Ticket
from app.execution import execute_ticket
from app.execution.runner import lock_ticket
from app.guardrails import apply_result, build_facts, check_proposal
from app.logging_setup import log_event
from app.schemas.proposal import ActionType, Proposal

log = logging.getLogger(__name__)


@dataclass
class ReviewConflict(Exception):
    message: str


def _locked(db: Session, ticket_id: uuid.UUID) -> Ticket | None:
    t = db.get(Ticket, ticket_id)
    return None if t is None else lock_ticket(db, t)


def _review(ticket: Ticket, kind: str, reviewer: str, note: str | None, **after: Any) -> Review:
    return Review(ticket_id=ticket.id, kind=kind, reviewer=reviewer, note=note, proposal_before=ticket.proposal,
                  decision_before=ticket.decision, status_before=ticket.status, **after)


def approve(db: Session, ticket_id: uuid.UUID, *, reviewer: str, note: str | None, settings: Settings) -> Ticket | None:
    t = _locked(db, ticket_id)
    if t is None:
        return None
    if t.status != "needs_human":
        db.rollback()
        raise ReviewConflict(f"only a needs_human ticket can be approved (this one is {t.status})")
    db.add(_review(t, "approve", reviewer, note, status_after="approved"))
    t.status = "approved"
    db.commit()  # the approval is on record before anything runs
    log_event(log, logging.INFO, "ticket_approved", ticket_id=t.id, reviewer=reviewer)
    execute_ticket(db, t, settings, approved_by=reviewer)
    return t


def reject(db: Session, ticket_id: uuid.UUID, *, reviewer: str, note: str | None) -> Ticket | None:
    t = _locked(db, ticket_id)
    if t is None:
        return None
    if t.status != "needs_human":
        db.rollback()
        raise ReviewConflict(f"only a needs_human ticket can be rejected (this one is {t.status})")
    db.add(_review(t, "reject", reviewer, note, status_after="rejected"))
    t.status = "rejected"
    db.commit()
    log_event(log, logging.INFO, "ticket_rejected", ticket_id=t.id, reviewer=reviewer)
    return t


def edit(db: Session, ticket_id: uuid.UUID, *, reviewer: str, note: str | None, changes: dict[str, Any],
         settings: Settings) -> Ticket | None:
    """`changes` = the new proposal without amount_cents; missing citations / rationale keep the current
    ones. Raises pydantic.ValidationError when the edited proposal is invalid (nothing is stored)."""
    t = _locked(db, ticket_id)
    if t is None:
        return None
    if t.status not in ("needs_human", "rejected") or t.proposal is None:
        db.rollback()
        raise ReviewConflict(f"only a needs_human or rejected ticket can be edited (this one is {t.status})")
    data = {"citations": t.proposal["citations"], "rationale": t.proposal["rationale"],
            **{k: v for k, v in changes.items() if v is not None or k == "order_id"}}
    is_refund = data.get("action") == ActionType.refund.value
    try:
        p = Proposal.model_validate({**data, "amount_cents": 0 if is_refund else None})
    except Exception:
        db.rollback()
        raise
    if is_refund:  # the amount is computed, never typed
        order = build_facts(db, t, p).order
        p = p.model_copy(update={"amount_cents": compute_amount(p, order).payable_cents if order else 0})
    result = check_proposal(db, t, p, settings)
    status_after = "rejected" if result.decision is Decision.rejected else "needs_human"
    new = p.model_dump(mode="json")
    db.add(_review(t, "edit", reviewer, note, proposal_after=new, decision_after=result.decision.value,
                   flags_after=result.flags_json, status_after=status_after))
    t.proposal = new
    apply_result(t, result)
    t.status = status_after
    db.commit()
    log_event(log, logging.INFO, "ticket_edited", ticket_id=t.id, reviewer=reviewer, decision=t.decision,
              flags=[f.code for f in result.flags])
    return t
