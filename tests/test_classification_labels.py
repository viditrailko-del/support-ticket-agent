"""Classification labels, their review loop, and the eval's (pure) scoring."""

import copy

from app.schemas.classification import Category, Urgency
from eval.classify_eval import score
from eval.ticket_review import (
    CLASSIFICATION,
    apply_classification_review,
    classification_hash,
    load,
    render_classification_page,
)

TICKETS = load()
LABELS = load(CLASSIFICATION)
TICKET_BY_ID = {t["id"]: t for t in TICKETS["tickets"]}


def test_one_valid_label_per_ticket():
    assert [lb["id"] for lb in LABELS["labels"]] == [t["id"] for t in TICKETS["tickets"]]
    for lb in LABELS["labels"]:
        assert lb["category"] in {c.value for c in Category}
        assert lb["urgency"] in {u.value for u in Urgency}


def test_verified_classification_labels_still_match():
    for lb in LABELS["labels"]:
        if lb["verified_hash"]:
            assert lb["verified_hash"] == classification_hash(lb, TICKET_BY_ID[lb["id"]]), f"{lb['id']}: re-review"


def test_hash_tracks_the_label_and_the_email_only():
    lb, t = copy.deepcopy(LABELS["labels"][0]), copy.deepcopy(TICKET_BY_ID["t01"])
    h = classification_hash(lb, t)
    t["expected"]["amount_cents"] = 1  # action label: irrelevant to classification
    assert classification_hash(lb, t) == h
    t["body"] += " Also, I'm suing you."
    assert classification_hash(lb, t) != h
    assert classification_hash({**lb, "urgency": "high"}, TICKET_BY_ID["t01"]) != h


def test_page_lists_every_label_and_names_its_set():
    page = render_classification_page(LABELS, TICKETS)
    assert page.count('class="q"') == len(LABELS["labels"])
    assert 'set: "classification"' in page and "review_classification.json" in page


def _unverified(labels: dict) -> dict:
    labels = copy.deepcopy(labels)
    for lb in labels["labels"]:
        lb["verified_by"] = lb["verified_at"] = lb["verified_hash"] = None
    return labels


def test_apply_classification_review():
    labels = _unverified(LABELS)
    t01, t03 = labels["labels"][0], labels["labels"][2]
    review = {"set": "classification", "reviews": {
        "t01": {"verdict": "ok", "hash": classification_hash(t01, TICKET_BY_ID["t01"])},
        "t02": {"verdict": "ok", "hash": "made-for-another-version"},
        "t03": {"verdict": "problem", "note": "urgency", "hash": classification_hash(t03, TICKET_BY_ID["t03"])},
    }}
    report = apply_classification_review(labels, TICKETS, review, by="Vidit", today="2026-11-17")
    assert t01["verified_by"] == "Vidit"
    assert labels["labels"][1]["verified_by"] is None  # stale mark: not verified
    assert t03["verified_by"] is None
    assert report[0].startswith("verified 1, problems 1, stale 1")


def test_stale_mark_leaves_a_still_valid_verification_alone():
    labels = copy.deepcopy(LABELS)
    before = copy.deepcopy(labels["labels"][1])
    review = {"set": "classification", "reviews": {"t02": {"verdict": "ok", "hash": "made-for-another-version"}}}
    apply_classification_review(labels, TICKETS, review, by="Someone", today="2030-01-01")
    assert labels["labels"][1] == before


# ---------------------------------------------------------------- scoring (no model calls)

def _attempt(raw, error=None, cost="0.000550", latency=100):
    return {"model": "lite", "prompt_version": "classify_v1", "raw_output": raw, "error": error,
            "latency_ms": latency, "input_tokens": 1000, "output_tokens": 100, "thinking_tokens": None,
            "cost_usd": cost}


def test_score_counts_accuracy_failures_and_cost():
    run = {"items": [
        {"id": "t01", "parsed": {"category": "refund_request", "urgency": "normal", "reason": "x"},
         "attempts": [_attempt("{}")]},
        {"id": "t51", "parsed": {"category": "damaged_or_defective", "urgency": "high", "reason": "x"},
         "attempts": [_attempt("bad", error="validation: x"), _attempt("{}", latency=300)]},
        {"id": "t56", "parsed": None, "attempts": [_attempt(None, error="503", cost=None, latency=50)]},
    ]}
    r = score(run, LABELS, TICKETS)
    assert r["tickets"] == 3 and r["failed"] == 1
    assert r["category_accuracy"] == round(1 / 3, 3)  # t01 right, t51 (other) wrong, t56 failed
    assert r["urgency_accuracy"] == round(2 / 3, 3)  # t01 normal, t51 high
    assert r["injection_category_accuracy"] == 0.0  # t56 is an injection ticket and failed
    assert r["calls_per_ticket"] == round(4 / 3, 2) and r["invalid_outputs"] == 1
    assert r["cost_usd_total"] == "0.001650" and r["latency_ms_max"] == 300
    assert r["confusions"] == {"other -> damaged_or_defective": 1, "refund_request -> FAILED": 1}
    assert [m["id"] for m in r["misses"]] == ["t51", "t56"]
