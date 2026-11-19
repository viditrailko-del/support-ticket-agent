"""Live mode for the public demo: a visitor writes their own email, as one of a few shop customers, and it runs
on real Gemini. Everything after intake is the normal pipeline; this module only decides WHO sends it, WHEN it
is received, which model answers, and whether there is quota left.

Why a customer picker instead of a free "From" field: the sender is matched by address only (rule 1), so a
visitor's own address is "not a customer" and every ticket would end `reply_to_non_customer`. Picking one of
these customers gives the agent real orders to look up. The server sets the address; the visitor can't.

The customers are chosen so that none of them appears in a labeled ticket, so none is a replay scenario's
customer either: a live refund on one of them can never make a replay diverge. Between them they cover the
interesting cases (small refund that auto-runs, refund over the limit, outside the window, VIP window,
final-sale items, an order still processing or shipped, an expensive electronics order).

Quota: live tickets spend the free tier (~3-6 calls each), so there is a daily cap for the whole demo and an
hourly cap per visitor IP. The counter lives in memory (the demo runs as one process) and is re-read from the
DB at the first request of each UTC day, so a restart doesn't hand out a fresh day. Internal numbers stay in
code/config (rule 13).
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.db.models import AgentStep, Customer, Order, Ticket
from app.demo.replay import REPLAY_PREFIX

LIVE_CUSTOMERS = ("C0006", "C0094", "C0164", "C0194", "C0065", "C0135", "C0187")


class QuotaExceeded(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LiveQuota:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: date | None = None
        self._used = 0
        self._by_ip: dict[str, deque[datetime]] = {}

    def reset(self) -> None:
        with self._lock:
            self._day, self._used, self._by_ip = None, 0, {}

    def used_today(self, db: Session, now: datetime) -> int:
        with self._lock:
            self._roll(db, now)
            return self._used

    def take(self, db: Session, ip: str, settings: Settings, now: datetime) -> None:
        """Reserve one live ticket for this IP, or raise QuotaExceeded."""
        with self._lock:
            self._roll(db, now)
            if self._used >= settings.demo_live_daily_limit:
                raise QuotaExceeded("Today's live demo quota is used up (it runs on a free Gemini tier). "
                                    "The recorded scenarios still work; live mode is back tomorrow (UTC).")
            recent = self._by_ip.setdefault(ip, deque())
            while recent and now - recent[0] >= timedelta(hours=1):
                recent.popleft()
            if len(recent) >= settings.demo_live_per_ip_per_hour:
                raise QuotaExceeded("You've sent the most live tickets allowed per hour. Try the recorded "
                                    "scenarios, or come back in a little while.")
            recent.append(now)
            self._used += 1

    def _roll(self, db: Session, now: datetime) -> None:
        if self._day != now.date():
            self._day, self._by_ip = now.date(), {}
            self._used = count_live_today(db, now)


def count_live_today(db: Session, now: datetime) -> int:
    """Tickets created today (UTC) whose classification went to a real model, not a replay."""
    start = datetime.combine(now.date(), time(), UTC)
    return db.scalar(
        select(func.count(distinct(AgentStep.ticket_id)))
        .join(Ticket, Ticket.id == AgentStep.ticket_id)
        .where(Ticket.created_at >= start, AgentStep.kind == "classify",
               AgentStep.model.is_not(None), AgentStep.model.not_like(f"{REPLAY_PREFIX}%"))
    ) or 0


quota = LiveQuota()


def live_settings(settings: Settings) -> Settings:
    """Live tickets: the agent on the demo model (flash-lite), so a day of visitors fits the free tier."""
    return settings.model_copy(update={"agent_model": settings.demo_live_model})


def received_at(settings: Settings, now: datetime) -> datetime:
    """Today's time of day, on the shop's date: every window and day count matches the seeded shop."""
    return datetime.combine(settings.shop_today, now.time(), UTC)


def demo_customer(db: Session, customer_id: str) -> Customer | None:
    return db.get(Customer, customer_id) if customer_id in LIVE_CUSTOMERS else None


def list_customers(db: Session, settings: Settings) -> list[dict[str, Any]]:
    """The demo customers and their orders, as the visitor needs them to write a realistic email."""
    rows = db.scalars(
        select(Customer).where(Customer.id.in_(LIVE_CUSTOMERS))
        .options(selectinload(Customer.orders).selectinload(Order.items),
                 selectinload(Customer.orders).selectinload(Order.refunds))
    ).all()
    by_id = {c.id: c for c in rows}
    return [_customer(by_id[cid], settings.shop_today) for cid in LIVE_CUSTOMERS if cid in by_id]


def _customer(c: Customer, today: date) -> dict[str, Any]:
    return {
        "customer_id": c.id, "name": c.name, "email": c.email, "tier": c.tier,
        "orders": [{
            "order_id": o.id, "status": o.status, "placed_at": o.placed_at.isoformat(),
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
            "days_since_delivery": (today - o.delivered_at).days if o.delivered_at else None,
            "items": [{"line_no": i.line_no, "name": i.name, "qty": i.qty, "unit_price_cents": i.unit_price_cents,
                       "final_sale": i.final_sale} for i in o.items],
            "shipping_cents": o.shipping_cents, "total_paid_cents": o.total_paid_cents,
            "refunded_cents": sum(r.amount_cents for r in o.refunds),
        } for o in sorted(c.orders, key=lambda o: o.placed_at, reverse=True)],
    }
