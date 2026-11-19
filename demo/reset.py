"""Clean the DEV database (before recording, or after trying the demo page locally): delete every ticket (with
its steps, actions and reviews) and every refund a ticket executed, then re-seed the shop.

    docker compose run --rm -T api uv run --frozen python -m demo.reset

Only the dev DB: it refuses a database whose name is not `support` (the tests clean `support_test` themselves).
The public demo resets itself through POST /demo/reset (same code: app/demo/reset.py).
"""

from sqlalchemy import text

from app.db.session import SessionLocal
from app.demo.reset import reset_demo


def main() -> int:
    with SessionLocal() as db:
        name = db.execute(text("select current_database()")).scalar_one()
        if name != "support":
            raise SystemExit(f"refusing to reset database {name!r}: demo.reset only cleans the dev DB 'support'")
        r = reset_demo(db)
    print(f"dev DB reset: deleted {r['tickets_deleted']} ticket(s) and {r['refunds_deleted']} ticket-executed "
          "refund(s); shop re-seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
