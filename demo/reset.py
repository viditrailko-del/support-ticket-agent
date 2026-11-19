"""Clean the DEV database before recording the demo: delete every ticket (with its steps, actions and reviews)
and every refund a ticket executed, then re-seed the shop. The seeded shop and policies are kept.

    docker compose run --rm -T api uv run --frozen python -m demo.reset

Only the dev DB: it refuses a database whose name is not `support` (the tests clean `support_test` themselves).
"""

from sqlalchemy import func, select, text

from app.db.models import Action, AgentStep, Refund, Review, Ticket
from app.db.session import SessionLocal
from data.seed import seed


def main() -> int:
    with SessionLocal() as db:
        name = db.execute(text("select current_database()")).scalar_one()
        if name != "support":
            raise SystemExit(f"refusing to reset database {name!r}: demo.reset only cleans the dev DB 'support'")
        tickets = db.scalar(select(func.count()).select_from(Ticket))
        executed = db.scalar(select(func.count()).select_from(Refund).where(Refund.id.like("RF-T%")))
        tables = ", ".join(m.__tablename__ for m in (Ticket, AgentStep, Action, Review))
        db.execute(text(f"TRUNCATE {tables} CASCADE"))
        db.execute(text(f"DELETE FROM {Refund.__tablename__} WHERE id LIKE 'RF-T%'"))
        db.commit()
        seed(db)
    print(f"dev DB reset: deleted {tickets} ticket(s) and {executed} ticket-executed refund(s); shop re-seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
