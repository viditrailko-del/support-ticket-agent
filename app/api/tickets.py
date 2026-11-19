"""POST /tickets and GET /tickets/{id}. Thin: the work is in app/tickets.py and app/agent."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.client import LLMClient, get_llm
from app.api.demo import admit_live
from app.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.tickets import TicketIn, TicketOut
from app.tickets import get_ticket, receive_ticket

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def post_ticket(
    body: TicketIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TicketOut:
    """Store a customer email as received (sender matched to a customer by address only), then classify
    it. An LLM outage gives status `failed` (HTTP 201), never a 500. On the public demo this spends the
    live-mode quota (HTTP 429 when it is used up)."""
    if settings.demo_mode:
        settings = admit_live(db, request, settings)
    t = receive_ticket(db, from_email=str(body.from_email), subject=body.subject, body=body.body,
                       received_at=body.received_at, llm=llm, settings=settings,
                       attachments=[a.model_dump() for a in body.attachments])
    return TicketOut.from_ticket(t)


@router.get("/{ticket_id}", response_model=TicketOut)
def read_ticket(ticket_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]) -> TicketOut:
    """The ticket with its full trace: classification, agent steps, proposal, flags, decision, actions."""
    t = get_ticket(db, ticket_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return TicketOut.from_ticket(t)
