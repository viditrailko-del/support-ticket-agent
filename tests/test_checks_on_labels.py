"""The checks against the 63 human-verified labels (no DB, no LLM).

A label is the right answer, so a proposal that matches it must not trip a policy check: if one does, the
check is wrong, not the label. And every injection ticket must be flagged while no ordinary email is.

Labels hold the amount, not the order lines, so the lines are recovered: every combination of lines (and
shipping) that adds up to the labeled amount is tried, and at least one must pass.
"""

import itertools
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from app.checks import Decision, Facts, LineFacts, Limits, OrderFacts, injection_signals, run_checks
from app.schemas.proposal import Proposal
from data.generate_shop import generate
from data.policies import load_rules

ROOT = Path(__file__).parents[1]
TICKETS = json.loads((ROOT / "eval/tickets/tickets.json").read_text(encoding="utf-8"))["tickets"]
CATEGORY = {c["id"]: c["category"] for c in
            json.loads((ROOT / "eval/tickets/classification.json").read_text(encoding="utf-8"))["labels"]}
SHOP = generate()
TODAY = date.fromisoformat(SHOP["shop_today"])
CUSTOMERS = {c["email"]: c for c in SHOP["customers"]}
ORDERS = {o["id"]: o for o in SHOP["orders"]}
REFUNDED: dict[str, int] = defaultdict(int)
for r in SHOP["refunds"]:
    REFUNDED[r["order_id"]] += r["amount_cents"]
RULES = {rid: r.text for rid, r in load_rules().items()}
LIMITS = Limits(auto_limit_cents=5000)
# A label is policy-correct, so none of these may fire on it.
POLICY_FLAGS = {"order_not_found", "order_not_senders", "sender_not_customer", "unknown_line", "qty_above_ordered",
                "nothing_left_to_refund", "zero_amount", "amount_mismatch", "in_transit", "replace_not_shipped",
                "outside_refund_window", "outside_replace_window", "shipping_not_refundable", "final_sale_item",
                "action_category_mismatch", "unknown_category", "unknown_rule", "quote_not_in_rule"}


def _days(iso: str | None) -> int | None:
    return (TODAY - date.fromisoformat(iso)).days if iso else None


def facts_for(t: dict, order_id: str | None) -> Facts:
    sender = CUSTOMERS.get(t["from"])
    o = ORDERS.get(order_id) if order_id else None
    order = None if o is None else OrderFacts(
        order_id=o["id"], customer_id=o["customer_id"], status=o["status"],
        days_since_shipped=_days(o["shipped_at"]), days_since_delivered=_days(o["delivered_at"]),
        lines=tuple(LineFacts(n, i["qty"], i["unit_price_cents"], i["category"], i["final_sale"])
                    for n, i in enumerate(o["items"], start=1)),
        shipping_cents=o["shipping_cents"], total_paid_cents=o["total_paid_cents"], refunded_cents=REFUNDED[o["id"]],
    )
    return Facts(sender_customer_id=sender["id"] if sender else None, sender_tier=sender["tier"] if sender else None,
                 category=CATEGORY[t["id"]], subject=t["subject"], body=t["body"], order=order, rules=RULES)


def candidates(t: dict, f: Facts) -> list[Proposal]:
    """Proposals that match the label: same action, order and amount, quoting each labeled rule in full."""
    exp = t["expected"]
    base = dict(action=exp["action"], order_id=exp["order_id"], reply_draft="-", rationale="-",
                citations=[{"rule_id": r, "quote": RULES[r][:500]} for r in exp["rule_ids"]])
    if exp["action"] in ("reply_only", "escalate"):
        return [Proposal.model_validate(base)]
    lines = f.order.lines
    out = []
    for qtys in itertools.product(*[range(ln.qty + 1) for ln in lines]):
        items = [{"line_no": ln.line_no, "qty": q} for ln, q in zip(lines, qtys) if q]
        if not items:
            continue
        if exp["action"] == "replace":
            out.append(Proposal.model_validate({**base, "items": items}))
            continue
        for ship in (False, True):
            p = Proposal.model_validate({**base, "items": items, "include_shipping": ship,
                                         "amount_cents": exp["amount_cents"]})
            if run_checks(p, f, LIMITS).amount_cents == exp["amount_cents"]:
                out.append(p)
    return out


@pytest.mark.parametrize("t", TICKETS, ids=[t["id"] for t in TICKETS])
def test_the_label_passes_every_policy_check(t):
    f = facts_for(t, t["expected"]["order_id"])
    results = [run_checks(p, f, LIMITS) for p in candidates(t, f)]
    assert results, "no combination of order lines adds up to the labeled amount"
    clean = [r for r in results if not {fl.code for fl in r.flags} & POLICY_FLAGS]
    assert clean, sorted({fl.message for r in results for fl in r.flags if fl.code in POLICY_FLAGS})
    if t["injection"]:
        assert all(r.decision is Decision.needs_human for r in clean)


@pytest.mark.parametrize("t", TICKETS, ids=[t["id"] for t in TICKETS])
def test_injection_signals_match_the_labels(t):
    signals = injection_signals(t["subject"], t["body"], frozenset(RULES))
    assert bool(signals) is t["injection"], signals
