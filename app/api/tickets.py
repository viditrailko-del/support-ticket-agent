"""POST /tickets and GET /tickets/{id}. Thin: the work is in app/tickets.py (and later app/agent)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.tickets import TicketIn, TicketOut
from app.tickets import create_ticket, get_ticket

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def post_ticket(body: TicketIn, db: Annotated[Session, Depends(get_db)]) -> TicketOut:
    """Store a customer email as received. The sender is matched to a customer by address only."""
    t = create_ticket(db, from_email=str(body.from_email), subject=body.subject, body=body.body,
                      received_at=body.received_at)
    return TicketOut.from_ticket(t)


@router.get("/{ticket_id}", response_model=TicketOut)
def read_ticket(ticket_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]) -> TicketOut:
    """The ticket with its full trace: classification, agent steps, proposal, flags, decision, actions."""
    t = get_ticket(db, ticket_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return TicketOut.from_ticket(t)
