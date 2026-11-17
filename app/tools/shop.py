"""get_customer, get_order, list_refunds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Customer, Order
from app.schemas.tools import (
    CustomerResult,
    GetCustomerArgs,
    GetOrderArgs,
    ListRefundsArgs,
    OrderLine,
    OrderResult,
    OrderSummary,
    RefundEntry,
    RefundsResult,
)

if TYPE_CHECKING:
    from app.tools.registry import ToolContext

NOT_A_CUSTOMER = "the sender's email address does not belong to a customer account"
NOT_ON_ACCOUNT = "no order with this id on the sender's account"


def _own_order(db: Session, customer_id: str | None, order_id: str) -> Order | None:
    """The order, only if it belongs to the sender. Someone else's order and a missing one look the same."""
    if customer_id is None:
        return None
    return db.scalar(
        select(Order)
        .where(Order.id == order_id, Order.customer_id == customer_id)
        .options(selectinload(Order.items), selectinload(Order.refunds))
    )


def get_customer(ctx: ToolContext, args: GetCustomerArgs) -> CustomerResult:
    if ctx.customer_id is None:
        return CustomerResult(found=False, reason=NOT_A_CUSTOMER)
    c = ctx.db.scalar(
        select(Customer)
        .where(Customer.id == ctx.customer_id)
        .options(selectinload(Customer.orders).selectinload(Order.items))
    )
    if c is None:  # ticket points at a customer that no longer exists
        return CustomerResult(found=False, reason=NOT_A_CUSTOMER)
    return CustomerResult(
        found=True, customer_id=c.id, name=c.name, tier=c.tier, member_since=c.created_at,
        orders=[
            OrderSummary(order_id=o.id, placed_at=o.placed_at, status=o.status, delivered_at=o.delivered_at,
                         item_names=[i.name for i in o.items])
            for o in c.orders
        ],
    )


def get_order(ctx: ToolContext, args: GetOrderArgs) -> OrderResult:
    if ctx.customer_id is None:
        return OrderResult(found=False, reason=NOT_A_CUSTOMER)
    o = _own_order(ctx.db, ctx.customer_id, args.order_id)
    if o is None:
        return OrderResult(found=False, reason=NOT_ON_ACCOUNT)
    refunded = sum(r.amount_cents for r in o.refunds)
    return OrderResult(
        found=True, order_id=o.id, status=o.status, placed_at=o.placed_at, shipped_at=o.shipped_at,
        delivered_at=o.delivered_at,
        days_since_shipped=(ctx.today - o.shipped_at).days if o.shipped_at else None,
        days_since_delivered=(ctx.today - o.delivered_at).days if o.delivered_at else None,
        items=[
            OrderLine(line_no=i.line_no, sku=i.sku, name=i.name, category=i.category, qty=i.qty,
                      unit_price_cents=i.unit_price_cents, line_total_cents=i.qty * i.unit_price_cents,
                      final_sale=i.final_sale)
            for i in o.items
        ],
        subtotal_cents=o.subtotal_cents, shipping_cents=o.shipping_cents, total_paid_cents=o.total_paid_cents,
        refunded_cents=refunded, refundable_left_cents=o.total_paid_cents - refunded,
    )


def list_refunds(ctx: ToolContext, args: ListRefundsArgs) -> RefundsResult:
    if ctx.customer_id is None:
        return RefundsResult(found=False, reason=NOT_A_CUSTOMER)
    o = _own_order(ctx.db, ctx.customer_id, args.order_id)
    if o is None:
        return RefundsResult(found=False, reason=NOT_ON_ACCOUNT)
    return RefundsResult(
        found=True, order_id=o.id,
        refunds=[RefundEntry(refund_id=r.id, amount_cents=r.amount_cents, reason=r.reason, created_at=r.created_at)
                 for r in o.refunds],
        total_refunded_cents=sum(r.amount_cents for r in o.refunds),
    )
