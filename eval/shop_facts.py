"""Guardrail facts for labeled tickets, from data/shop.json + data/policies (no DB, no LLM).

Used by `eval.run score` (re-runs the current checks on frozen proposals, free) and by the tests. The shop
file is what the dev/test DBs are seeded from, so these are the same facts app/guardrails.py reads, as of
the shop's fixed date and before any ticket executed anything.
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.checks import CheckResult, Decision, Facts, LineFacts, Limits, OrderFacts, run_checks
from app.schemas.proposal import Proposal
from app.schemas.tools import OrderIdArgs
from data.policies import load_rules

ROOT = Path(__file__).parents[1]
SHOP_FILE = ROOT / "data" / "shop.json"

# Flags a policy-correct proposal never trips (everything except the "a human should look" ones that depend
# on the email or the amount: injection_signal, above_auto_limit, and the info flag amount_capped).
POLICY_FLAGS = frozenset({
    "order_not_found", "order_not_senders", "sender_not_customer", "order_not_in_email", "unknown_line", "qty_above_ordered",
    "nothing_left_to_refund", "zero_amount", "amount_mismatch", "in_transit", "replace_not_shipped",
    "outside_refund_window", "outside_replace_window", "shipping_not_refundable", "final_sale_item",
    "action_category_mismatch", "unknown_category", "unknown_rule", "quote_not_in_rule",
})


@lru_cache
def shop() -> dict[str, Any]:
    s = json.loads(SHOP_FILE.read_text(encoding="utf-8"))
    refunded: dict[str, int] = defaultdict(int)
    for r in s["refunds"]:
        refunded[r["order_id"]] += r["amount_cents"]
    return {
        "today": date.fromisoformat(s["shop_today"]),
        "customers": {c["email"]: c for c in s["customers"]},
        "orders": {o["id"]: o for o in s["orders"]},
        "refunded": dict(refunded),
    }


@lru_cache
def rules() -> dict[str, str]:
    return {rid: r.text for rid, r in load_rules().items()}


def normalize(order_id: str | None) -> str | None:
    if order_id is None:
        return None
    try:
        return OrderIdArgs(order_id=order_id).order_id
    except ValueError:
        return order_id


def facts_for(t: dict, order_id: str | None, category: str | None) -> Facts:
    s = shop()
    days = lambda iso: (s["today"] - date.fromisoformat(iso)).days if iso else None  # noqa: E731
    sender = s["customers"].get(t["from"].strip().lower())
    o = s["orders"].get(normalize(order_id) or "")
    order = None if o is None else OrderFacts(
        order_id=o["id"], customer_id=o["customer_id"], status=o["status"],
        days_since_shipped=days(o["shipped_at"]), days_since_delivered=days(o["delivered_at"]),
        lines=tuple(LineFacts(n, i["qty"], i["unit_price_cents"], i["category"], i["final_sale"])
                    for n, i in enumerate(o["items"], start=1)),
        shipping_cents=o["shipping_cents"], total_paid_cents=o["total_paid_cents"],
        refunded_cents=s["refunded"].get(o["id"], 0),
    )
    return Facts(sender_customer_id=sender["id"] if sender else None, sender_tier=sender["tier"] if sender else None,
                 category=category, subject=t["subject"], body=t["body"], order=order, rules=rules())


def label_results(t: dict, category: str, limits: Limits) -> list[CheckResult]:
    return [r for _, r in label_proposals(t, category, limits)]


def label_proposals(t: dict, category: str, limits: Limits) -> list[tuple[Proposal, CheckResult]]:
    """Every proposal that matches the label (same action, order, amount; each labeled rule quoted), with its
    check result. Labels hold the amount, not the order lines, so every line combination is tried."""
    exp = t["expected"]
    f = facts_for(t, exp["order_id"], category)
    base = dict(action=exp["action"], order_id=exp["order_id"], reply_draft="-", rationale="-",
                citations=[{"rule_id": r, "quote": rules()[r][:500]} for r in exp["rule_ids"]])
    if exp["action"] in ("reply_only", "escalate"):
        p = Proposal.model_validate(base)
        return [(p, run_checks(p, f, limits))]
    out = []
    lines = f.order.lines if f.order else ()
    for qtys in itertools.product(*[range(ln.qty + 1) for ln in lines]):
        items = [{"line_no": ln.line_no, "qty": q} for ln, q in zip(lines, qtys) if q]
        if not items:
            continue
        if exp["action"] == "replace":
            p = Proposal.model_validate({**base, "items": items})
            out.append((p, run_checks(p, f, limits)))
            continue
        for ship in (False, True):
            p = Proposal.model_validate({**base, "items": items, "include_shipping": ship,
                                         "amount_cents": exp["amount_cents"]})
            r = run_checks(p, f, limits)
            if r.amount_cents == exp["amount_cents"]:
                out.append((p, r))
    return out


def clean(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not {fl.code for fl in r.flags} & POLICY_FLAGS]


def label_would_auto_run(t: dict, category: str, limits: Limits) -> bool:
    """Would the RIGHT answer run without a human? If so, sending this ticket to a human is needless."""
    return any(r.decision is Decision.auto_approved for r in clean(label_results(t, category, limits)))
