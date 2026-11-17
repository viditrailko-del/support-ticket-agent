"""The label review loop: page facts come from the data, OKs are bound to the exact label they approved."""

import copy

import pytest

from eval.ticket_review import Shop, apply_review, data_hash, load, money, order_html, render_page, ticket_hash

DOC = load()
DATA = data_hash()
BY_ID = {t["id"]: t for t in DOC["tickets"]}


def _ticket(scenario: str) -> dict:
    return next(t for t in DOC["tickets"] if t["scenario"] == scenario)


def _review(doc: dict, marks: dict[str, tuple[str, str]]) -> dict:
    by_id = {t["id"]: t for t in doc["tickets"]}
    return {"data_hash": DATA, "reviews": {
        tid: {"verdict": v, "note": n, "hash": ticket_hash(by_id[tid], DATA)} for tid, (v, n) in marks.items()
    }}


def test_verified_labels_still_match_what_was_reviewed():
    # Fails when a verified label, its email, the shop data or a policy page changed after the review.
    for t in DOC["tickets"]:
        if t["verified_hash"]:
            assert t["verified_hash"] == ticket_hash(t, DATA), f"{t['id']} changed since review: re-review it"
            assert t["verified_by"] and t["verified_at"]


def test_hash_changes_with_the_label_but_not_with_verification():
    t = copy.deepcopy(BY_ID["t01"])
    h = ticket_hash(t, DATA)
    t["verified_by"], t["verified_hash"] = "someone", "x"
    assert ticket_hash(t, DATA) == h
    t["expected"]["amount_cents"] += 1
    assert ticket_hash(t, DATA) != h
    assert ticket_hash(BY_ID["t01"], DATA + "other-data") != h


@pytest.mark.parametrize("cents, text", [(0, "$0.00"), (5, "$0.05"), (3098, "$30.98"), (-599, "-$5.99"), (None, "—")])
def test_money(cents, text):
    assert money(cents) == text


def test_order_facts_come_from_shop_data():
    shop = Shop()
    t = _ticket("refund_cap")  # paid $40.98, $10 goodwill already refunded
    sender = shop.by_email[t["from"]]
    out = order_html(shop, t["expected"]["order_id"], sender)
    assert "belongs to the sender" in out
    assert "$40.98" in out and "$10.00" in out
    assert "shipping included):\n     $30.98." in out


def test_order_facts_flag_someone_elses_or_missing_order():
    shop = Shop()
    sender = shop.by_email["olga.kowal@example.com"]
    assert "NOT the sender" in order_html(shop, "ORD-10476", sender)
    assert "no such order" in order_html(shop, "ORD-99999", sender)


def test_page_has_every_ticket_and_escapes_the_email():
    doc = copy.deepcopy(DOC)
    doc["tickets"][0]["body"] = "<script>alert(1)</script>"
    page = render_page(doc)
    assert all(f'data-id="{t["id"]}"' in page for t in doc["tickets"])
    assert page.count('class="q"') == len(doc["tickets"])
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_apply_records_ok_and_clears_problems():
    doc = copy.deepcopy(DOC)
    doc["tickets"][1].update(verified_by="old", verified_at="2026-01-01", verified_hash="old")
    review = _review(doc, {"t01": ("ok", ""), "t02": ("problem", "amount wrong")})
    report = apply_review(doc, review, by="Vidit", today="2026-11-17")
    t1, t2 = doc["tickets"][0], doc["tickets"][1]
    assert (t1["verified_by"], t1["verified_at"]) == ("Vidit", "2026-11-17")
    assert t1["verified_hash"] == ticket_hash(t1, DATA)
    assert t2["verified_by"] is t2["verified_at"] is t2["verified_hash"] is None
    assert report[0].startswith("verified 1, problems 1, stale 0")
    assert any("t02: amount wrong" in line for line in report)


def test_apply_refuses_an_ok_for_a_label_that_changed_after_review():
    doc = copy.deepcopy(DOC)
    review = _review(doc, {"t01": ("ok", "")})
    doc["tickets"][0]["expected"]["amount_cents"] = 1  # label edited after the reviewer saw it
    report = apply_review(doc, review, by="Vidit", today="2026-11-17")
    assert doc["tickets"][0]["verified_by"] is None
    assert "stale" in report[0] and any("t01" in line for line in report if "stale (" in line)


def test_apply_is_idempotent():
    doc = copy.deepcopy(DOC)
    review = _review(doc, {"t01": ("ok", "")})
    apply_review(doc, review, by="Vidit", today="2026-11-17")
    once = copy.deepcopy(doc)
    apply_review(doc, review, by="Vidit", today="2026-11-17")
    assert doc == once
