"""The agent's tools: scoped to the ticket's sender, facts computed in code, never raising on model input."""

from datetime import date

import pytest

from app.schemas.tools import GetOrderArgs
from app.tools import TOOLS, ToolContext, run_tool
from app.tools.shop import NOT_A_CUSTOMER, NOT_ON_ACCOUNT, get_order

TODAY = date(2026, 11, 15)  # shop_today: every labeled ticket is received that day
AVA = "C0029"  # ava.whitlock: owns ORD-10473 (speaker $34.99 + $5.99 shipping, $10 goodwill refunded)
OLGA_KOWAL = "C0053"  # does NOT own ORD-10476 (that's C0112)


def ctx(db, customer_id, today=TODAY) -> ToolContext:
    return ToolContext(db=db, customer_id=customer_id, today=today)


def test_registry_lists_the_three_tools():
    assert set(TOOLS) == {"get_customer", "get_order", "list_refunds"}
    assert all(t.description for t in TOOLS.values())  # Phase 3 turns these into function declarations


# ---------------------------------------------------------------- get_customer

def test_get_customer_returns_the_sender_and_their_orders(db):
    r = run_tool(ctx(db, AVA), "get_customer", {})
    assert r["found"] and r["customer_id"] == AVA and r["tier"] == "standard"
    assert len(r["orders"]) == 6 and "ORD-10473" in [o["order_id"] for o in r["orders"]]
    placed = [o["placed_at"] for o in r["orders"]]
    assert placed == sorted(placed)  # oldest first


def test_get_customer_order_list_is_deterministic(db):
    # Olga has two orders placed the same day (ORD-10304, ORD-10308): the order id decides, on every database.
    r = run_tool(ctx(db, OLGA_KOWAL), "get_customer", {})
    keys = [(o["placed_at"], o["order_id"]) for o in r["orders"]]
    assert keys == sorted(keys)
    assert [o["order_id"] for o in r["orders"] if o["placed_at"] == "2026-08-29"] == ["ORD-10304", "ORD-10308"]


def test_get_customer_for_unknown_sender(db):
    r = run_tool(ctx(db, None), "get_customer", {})
    assert r["found"] is False and r["reason"] == NOT_A_CUSTOMER
    assert r["customer_id"] is None and r["orders"] == []


def test_get_customer_ignores_a_customer_id_the_model_invents(db):
    # The model can't widen its scope by passing another customer's id: unknown args are dropped.
    r = run_tool(ctx(db, AVA), "get_customer", {"customer_id": "C0001", "email": "someone@example.com"})
    assert r["customer_id"] == AVA


# ---------------------------------------------------------------- get_order

def test_get_order_computes_money_and_days_in_code(db):
    r = run_tool(ctx(db, AVA), "get_order", {"order_id": "ORD-10473"})
    assert r["found"] and r["status"] == "delivered"
    assert r["delivered_at"] == "2026-11-09" and r["days_since_delivered"] == 6
    assert [(i["sku"], i["line_total_cents"]) for i in r["items"]] == [("EL-101", 3499)]
    assert (r["subtotal_cents"], r["shipping_cents"], r["total_paid_cents"]) == (3499, 599, 4098)
    assert (r["refunded_cents"], r["refundable_left_cents"]) == (1000, 3098)


def test_days_are_relative_to_the_ticket_date(db):
    later = run_tool(ctx(db, AVA, today=date(2026, 12, 15)), "get_order", {"order_id": "ORD-10473"})
    assert later["days_since_delivered"] == 36


def test_processing_order_has_no_ship_or_delivery_days(db):
    r = run_tool(ctx(db, "C0091"), "get_order", {"order_id": "ORD-10496"})  # grace.varga, processing
    assert r["status"] == "processing" and r["days_since_shipped"] is None and r["days_since_delivered"] is None


@pytest.mark.parametrize("raw", ["ORD-10473", "ord-10473", " #ORD-10473 ", "#ord-10473"])
def test_order_id_format_is_forgiving(db, raw):
    assert run_tool(ctx(db, AVA), "get_order", {"order_id": raw})["order_id"] == "ORD-10473"


def test_someone_elses_order_looks_like_a_missing_one(db):
    others = run_tool(ctx(db, OLGA_KOWAL), "get_order", {"order_id": "ORD-10476"})
    missing = run_tool(ctx(db, OLGA_KOWAL), "get_order", {"order_id": "ORD-99999"})
    assert others == missing
    assert others["found"] is False and others["reason"] == NOT_ON_ACCOUNT
    assert others["items"] == [] and others["total_paid_cents"] is None  # nothing about the real order leaks


def test_unknown_sender_sees_no_orders(db):
    r = run_tool(ctx(db, None), "get_order", {"order_id": "ORD-10464"})
    assert r["found"] is False and r["reason"] == NOT_A_CUSTOMER


# ---------------------------------------------------------------- list_refunds

def test_list_refunds_on_own_order(db):
    r = run_tool(ctx(db, AVA), "list_refunds", {"order_id": "ORD-10473"})
    assert r["found"] and r["total_refunded_cents"] == 1000
    assert [(x["amount_cents"], x["reason"]) for x in r["refunds"]] == [(1000, "goodwill")]


def test_list_refunds_empty(db):
    r = run_tool(ctx(db, "C0054"), "list_refunds", {"order_id": "ORD-10466"})  # jonas.hartley, no refunds
    assert r["found"] and r["refunds"] == [] and r["total_refunded_cents"] == 0


def test_list_refunds_on_someone_elses_order(db):
    r = run_tool(ctx(db, AVA), "list_refunds", {"order_id": "ORD-10477"})  # sami.brandt's, fully refunded
    assert r["found"] is False and r["refunds"] == []


# ---------------------------------------------------------------- run_tool never raises on model input

@pytest.mark.parametrize("name, args, expect", [
    ("delete_order", {"order_id": "ORD-10473"}, "unknown tool"),
    ("get_order", {}, "order_id: Field required"),
    ("get_order", None, "order_id: Field required"),
    ("get_order", {"order_id": 10473}, "order_id"),
    ("get_order", {"order_id": "ORD-" + "9" * 50}, "at most 40 characters"),
])
def test_bad_model_calls_become_error_results(db, name, args, expect):
    r = run_tool(ctx(db, AVA), name, args)
    assert set(r) == {"error"} and expect in r["error"]


def test_tool_functions_are_plain_functions(db):
    # Usable without the registry (and without an LLM).
    assert get_order(ctx(db, AVA), GetOrderArgs(order_id="ORD-10473")).total_paid_cents == 4098
