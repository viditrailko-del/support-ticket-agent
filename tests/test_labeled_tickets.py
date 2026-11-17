"""Labeled tickets are consistent with the shop data and the policy pages.

These checks don't decide the labels (a human does); they catch labels that can't be right: an order that
isn't the sender's, a refund above what is left to refund, a window claim that the dates contradict.
"""

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from data.generate_shop import generate
from data.policies import load_rules

TICKETS = json.loads(
    (Path(__file__).parents[1] / "eval" / "tickets" / "tickets.json").read_text(encoding="utf-8")
)["tickets"]
SHOP = generate()
TODAY = date.fromisoformat(SHOP["shop_today"])
CUSTOMERS_BY_EMAIL = {c["email"]: c for c in SHOP["customers"]}
ORDERS = {o["id"]: o for o in SHOP["orders"]}
RULES = load_rules()
REFUNDED: dict[str, int] = defaultdict(int)
for r in SHOP["refunds"]:
    REFUNDED[r["order_id"]] += r["amount_cents"]

ACTIONS = {"refund", "replace", "reply_only", "escalate"}


def _days(iso: str | None) -> int | None:
    return (TODAY - date.fromisoformat(iso)).days if iso else None


def test_ticket_set_size_and_mix():
    assert 55 <= len(TICKETS) <= 70
    assert len({t["id"] for t in TICKETS}) == len(TICKETS)
    assert {t["expected"]["action"] for t in TICKETS} == ACTIONS
    assert sum(t["injection"] for t in TICKETS) >= 6


@pytest.mark.parametrize("t", TICKETS, ids=[t["id"] for t in TICKETS])
def test_label_is_consistent(t):
    exp = t["expected"]
    assert exp["action"] in ACTIONS
    assert t["received_at"] == SHOP["shop_today"]
    assert exp["rule_ids"] and all(r in RULES for r in exp["rule_ids"]), exp["rule_ids"]
    if t["injection"]:
        assert "ESC-4" in exp["rule_ids"]

    # amount only on refunds, and always positive there
    if exp["action"] == "refund":
        assert isinstance(exp["amount_cents"], int) and exp["amount_cents"] > 0
    else:
        assert exp["amount_cents"] is None

    # refund / replace need an order that belongs to the sender
    if exp["action"] in ("refund", "replace"):
        assert exp["order_id"] is not None

    if exp["order_id"] is None:
        return
    sender = CUSTOMERS_BY_EMAIL.get(t["from"])
    order = ORDERS.get(exp["order_id"])
    assert sender and order, "a labeled order must exist and the sender must be a customer"
    assert order["customer_id"] == sender["id"], "label points at someone else's order"
    assert exp["order_id"] in t["body"] or exp["order_id"] in t["subject"]

    if exp["action"] == "refund":
        left = order["total_paid_cents"] - REFUNDED[order["id"]]
        assert exp["amount_cents"] <= left, "refund above amount paid minus already refunded (REF-3)"

    delivered = _days(order["delivered_at"])
    window = 60 if sender["tier"] == "vip" else 30
    if exp["action"] == "refund" and ("REF-1" in exp["rule_ids"] or "VIP-1" in exp["rule_ids"]):
        assert delivered is not None and delivered <= window
    if "REF-5" in exp["rule_ids"] and exp["action"] == "reply_only":
        assert delivered is not None and delivered > window
    if "DMG-2" in exp["rule_ids"]:
        assert delivered is not None and delivered <= 14
    if "SHP-3" in exp["rule_ids"]:
        assert order["status"] == "shipped" and _days(order["shipped_at"]) >= 14
    if "SHP-4" in exp["rule_ids"]:
        assert order["status"] == "shipped" and _days(order["shipped_at"]) < 14
    if "CAN-1" in exp["rule_ids"]:
        assert order["status"] == "processing"
        assert exp["amount_cents"] == order["total_paid_cents"]
    if "WAR-2" in exp["rule_ids"]:
        assert any(i["category"] == "electronics" for i in order["items"])
        assert delivered is not None and delivered <= 365


@pytest.mark.parametrize(
    "t", [t for t in TICKETS if "ACC-2" in t["expected"]["rule_ids"]], ids=lambda t: t["id"]
)
def test_someone_elses_order_really_is(t):
    sender = CUSTOMERS_BY_EMAIL.get(t["from"])
    mentioned = [o for o in ORDERS if o in t["body"] or o in t["subject"]]
    assert mentioned
    assert all(sender is None or ORDERS[o]["customer_id"] != sender["id"] for o in mentioned)
