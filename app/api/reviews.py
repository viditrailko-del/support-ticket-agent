"""Human review endpoints. Thin: the rules are in app/reviews.py."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Ticket
from app.db.session import get_db
from app.reviews import ReviewConflict, approve, edit, reject
from app.schemas.tickets import EditIn, ReviewIn, TicketOut
from app.tickets import get_ticket

router = APIRouter(prefix="/tickets/{ticket_id}", tags=["review"])

Db = Annotated[Session, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings)]


def _out(db: Session, t: Ticket | None) -> TicketOut:
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return TicketOut.from_ticket(get_ticket(db, t.id))  # type: ignore[arg-type]


@router.post("/approve", response_model=TicketOut, responses={409: {"description": "Not awaiting review"}})
def approve_ticket(ticket_id: uuid.UUID, body: ReviewIn, db: Db, settings: Cfg) -> TicketOut:
    """Approve a `needs_human` ticket and execute it (once: execution is idempotent)."""
    try:
        return _out(db, approve(db, ticket_id, reviewer=body.reviewer, note=body.note, settings=settings))
    except ReviewConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.message) from None


@router.post("/reject", response_model=TicketOut, responses={409: {"description": "Not awaiting review"}})
def reject_ticket(ticket_id: uuid.UUID, body: ReviewIn, db: Db) -> TicketOut:
    """Reject a `needs_human` ticket. Nothing is executed."""
    try:
        return _out(db, reject(db, ticket_id, reviewer=body.reviewer, note=body.note))
    except ReviewConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.message) from None


@router.post("/edit", response_model=TicketOut, responses={409: {"description": "Not editable"}})
def edit_ticket(ticket_id: uuid.UUID, body: EditIn, db: Db, settings: Cfg) -> TicketOut:
    """Replace the proposal (the refund amount is computed, not given). All checks run again; the ticket
    then waits for an approve, even when the edit is clean."""
    changes = body.model_dump(exclude={"reviewer", "note"}, mode="json")
    try:
        return _out(db, edit(db, ticket_id, reviewer=body.reviewer, note=body.note, changes=changes,
                             settings=settings))
    except ReviewConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, e.message) from None
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()]) from None
