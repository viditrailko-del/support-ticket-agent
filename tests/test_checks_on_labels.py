"""The checks against the 63 human-verified labels (no DB, no LLM).

A label is the right answer, so a proposal that matches it must not trip a policy check: if one does, the
check is wrong, not the label. And every injection ticket must be flagged while no ordinary email is.
Labels hold the amount, not the order lines, so every line combination adding up to it is tried
(eval/shop_facts.py, shared with the eval's scoring).
"""

import json

import pytest

from app.checks import Decision, Limits, injection_signals
from eval.shop_facts import POLICY_FLAGS, ROOT, clean, label_results, rules

TICKETS = json.loads((ROOT / "eval/tickets/tickets.json").read_text(encoding="utf-8"))["tickets"]
CATEGORY = {c["id"]: c["category"] for c in
            json.loads((ROOT / "eval/tickets/classification.json").read_text(encoding="utf-8"))["labels"]}
LIMITS = Limits(auto_limit_cents=5000)


@pytest.mark.parametrize("t", TICKETS, ids=[t["id"] for t in TICKETS])
def test_the_label_passes_every_policy_check(t):
    results = label_results(t, CATEGORY[t["id"]], LIMITS)
    assert results, "no combination of order lines adds up to the labeled amount"
    ok = clean(results)
    assert ok, sorted({fl.message for r in results for fl in r.flags if fl.code in POLICY_FLAGS})
    if t["injection"]:
        assert all(r.decision is Decision.needs_human for r in ok)


@pytest.mark.parametrize("t", TICKETS, ids=[t["id"] for t in TICKETS])
def test_injection_signals_match_the_labels(t):
    signals = injection_signals(t["subject"], t["body"], frozenset(rules()))
    assert bool(signals) is t["injection"], signals
