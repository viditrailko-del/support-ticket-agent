"""Put the demo database back to the seeded shop: no tickets, no ticket-executed refunds, shop and policies
re-seeded. Used by the nightly POST /demo/reset on the public demo and by `python -m demo.reset` locally.

Only ticket data is deleted (tickets cascade to steps, actions and reviews) plus the refunds tickets executed
(`RF-T...`); the seed upsert then restores every shop row a refund may have touched.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db.models import Action, AgentStep, Refund, Review, Ticket
from data.seed import seed


def reset_demo(db: Session) -> dict[str, int]:
    tickets = db.scalar(select(func.count()).select_from(Ticket)) or 0
    executed = db.scalar(select(func.count()).select_from(Refund).where(Refund.id.like("RF-T%"))) or 0
    tables = ", ".join(m.__tablename__ for m in (Ticket, AgentStep, Action, Review))
    db.execute(text(f"TRUNCATE {tables} CASCADE"))
    db.execute(delete(Refund).where(Refund.id.like("RF-T%")))
    db.commit()
    seeded = seed(db)
    return {"tickets_deleted": tickets, "refunds_deleted": executed, **seeded}
