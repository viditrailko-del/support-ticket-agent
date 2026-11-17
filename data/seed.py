"""Load the fake shop (data/shop.json) and the policy pages (data/policies) into the database.

    python -m data.seed            # dev DB (DATABASE_URL), after `alembic upgrade head`

Idempotent: rows are upserted by id, so re-running after a generator or policy change updates them in place
without breaking tickets that point at customers. Order items are replaced per order, and policy rules that
were deleted from the pages are removed (the prompt must match the pages). Shop rows are never deleted here:
tickets and executed actions may point at them.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Customer, Order, OrderItem, PolicyRule, Refund
from data.generate_shop import OUT as SHOP_JSON
from data.policies import load_rules


def _d(iso: str | None) -> date | None:
    return date.fromisoformat(iso) if iso else None


def _upsert(db: Session, model, rows: list[dict]) -> None:
    if not rows:
        return
    stmt = insert(model).values(rows)
    cols = {c: stmt.excluded[c] for c in rows[0] if c != "id"}
    db.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=cols))


def seed(db: Session, shop: dict | None = None) -> dict[str, int]:
    shop = shop or json.loads(SHOP_JSON.read_text(encoding="utf-8"))

    _upsert(db, Customer, [
        {"id": c["id"], "name": c["name"], "email": c["email"].lower(), "tier": c["tier"],
         "created_at": _d(c["created_at"])}
        for c in shop["customers"]
    ])
    _upsert(db, Order, [
        {"id": o["id"], "customer_id": o["customer_id"], "placed_at": _d(o["placed_at"]), "status": o["status"],
         "shipped_at": _d(o["shipped_at"]), "delivered_at": _d(o["delivered_at"]),
         "subtotal_cents": o["subtotal_cents"], "shipping_cents": o["shipping_cents"],
         "total_paid_cents": o["total_paid_cents"]}
        for o in shop["orders"]
    ])
    order_ids = [o["id"] for o in shop["orders"]]
    db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
    items = [
        {"order_id": o["id"], "line_no": n, "sku": i["sku"], "name": i["name"], "category": i["category"],
         "qty": i["qty"], "unit_price_cents": i["unit_price_cents"], "final_sale": i["final_sale"]}
        for o in shop["orders"] for n, i in enumerate(o["items"], start=1)
    ]
    db.execute(insert(OrderItem).values(items))
    _upsert(db, Refund, [
        {"id": r["id"], "order_id": r["order_id"], "amount_cents": r["amount_cents"], "reason": r["reason"],
         "created_at": _d(r["created_at"])}
        for r in shop["refunds"]
    ])

    rules = list(load_rules().values())
    _upsert(db, PolicyRule, [
        {"id": r.id, "position": n, "page": r.page, "page_title": r.page_title, "title": r.title, "text": r.text}
        for n, r in enumerate(rules, start=1)
    ])
    db.execute(delete(PolicyRule).where(PolicyRule.id.not_in([r.id for r in rules])))
    db.commit()

    return {
        "customers": len(shop["customers"]), "orders": len(shop["orders"]), "order_items": len(items),
        "refunds": len(shop["refunds"]), "policy_rules": len(rules),
    }


def main() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        counts = seed(db)
    print("seeded " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
