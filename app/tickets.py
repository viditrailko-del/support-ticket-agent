"""Ticket intake: store the email exactly as received and match the sender to a customer.

The customer match uses the sender address only (rule 1). Nothing in the subject or body, and nothing the
model writes later, can change which customer's data the tools see.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Customer, Ticket
from app.logging_setup import log_event

log = logging.getLogger(__name__)


def match_customer(db: Session, from_email: str) -> str | None:
    return db.scalar(select(Customer.id).where(Customer.email == func.lower(from_email.strip())))


def create_ticket(
    db: Session, *, from_email: str, subject: str, body: str, received_at: datetime | None = None
) -> Ticket:
    ticket = Ticket(
        from_email=from_email, subject=subject, body=body,
        received_at=received_at or datetime.now(UTC),
        customer_id=match_customer(db, from_email),
        status="received",
    )
    db.add(ticket)
    db.commit()
    log_event(log, logging.INFO, "ticket_received", ticket_id=ticket.id, customer_matched=ticket.customer_id is not None)
    return get_ticket(db, ticket.id)  # type: ignore[return-value]


def get_ticket(db: Session, ticket_id: uuid.UUID) -> Ticket | None:
    return db.scalar(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.steps), selectinload(Ticket.actions))
        .execution_options(populate_existing=True)
    )
