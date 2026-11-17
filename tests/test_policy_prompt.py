"""Policy text for the prompt: built from the seeded policy_rules, versioned by hash."""

import json
from pathlib import Path

from app.db.models import PolicyRule
from app.policies import load_policies, render, version_of
from data.policies import load_rules


def test_prompt_contains_every_rule_from_the_pages(db):
    ps = load_policies(db)
    pages = load_rules()
    assert [r.id for r in ps.rules] == list(pages)  # same order as the pages
    for rule in pages.values():
        assert f"[{rule.id}] {rule.page_title} > {rule.title}: {rule.text}" in ps.text


def test_every_labeled_rule_is_in_the_prompt(db):
    text = load_policies(db).text
    tickets = json.loads((Path(__file__).parents[1] / "eval/tickets/tickets.json").read_text(encoding="utf-8"))
    for t in tickets["tickets"]:
        for rid in t["expected"]["rule_ids"]:
            assert f"[{rid}]" in text


def test_version_is_stable_and_tracks_the_text(db):
    a, b = load_policies(db), load_policies(db)
    assert a.version == b.version and len(a.version) == 16
    changed = render([PolicyRule(id="REF-1", position=1, page="p", page_title="P", title="T", text="other")])
    assert version_of(changed) != a.version


def test_lookup_by_id(db):
    ps = load_policies(db)
    assert ps.rule("VIP-1").page == "vip"
    assert ps.rule("REF-9") is None


def test_internal_thresholds_never_reach_the_prompt(db):
    # Rule 13: the $50 auto-approval limit and other internals live in code, not in policy pages.
    text = load_policies(db).text.lower()
    for internal in ("auto-approv", "auto approv", "$50.00", "flag", "injection", "threshold"):
        assert internal not in text, internal
