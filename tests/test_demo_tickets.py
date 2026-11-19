"""The demo tickets are verified labels, copied as POST /tickets bodies. If a label changes, the demo must too."""

import json
from pathlib import Path

import pytest

from app.schemas.tickets import TicketIn

ROOT = Path(__file__).resolve().parents[1]
LABELS = {t["id"]: t for t in json.loads((ROOT / "eval/tickets/tickets.json").read_text("utf-8"))["tickets"]}
DEMO = {"1_simple_refund": "t01", "2_injection": "t56", "3_needs_human": "t02"}


@pytest.mark.parametrize(("name", "label_id"), DEMO.items())
def test_demo_ticket_is_the_verified_label(name: str, label_id: str) -> None:
    body = json.loads((ROOT / f"demo/tickets/{name}.json").read_text("utf-8"))
    TicketIn.model_validate(body)  # a valid POST /tickets body
    label = LABELS[label_id]
    assert label.get("verified_hash"), f"{label_id} is not human-verified"
    assert (body["from_email"], body["subject"], body["body"]) == (label["from"], label["subject"], label["body"])
    assert body["received_at"].startswith(label["received_at"])
