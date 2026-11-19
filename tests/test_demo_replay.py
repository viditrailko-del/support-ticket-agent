"""Replay mode: every recorded scenario runs through the real pipeline and ends where the eval scored it.

If a check, the shop or a policy changes what a scenario does, these fail: re-run
`python -m demo.build_replay` and re-read the scenario's blurb.
"""

import copy
import json

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Refund, Ticket
from app.demo.replay import Scenario, replay, scenarios
from app.main import app
from tests.conftest import test_settings

LABELS = {t["id"]: t for t in json.loads(open("eval/tickets/tickets.json", encoding="utf-8").read())["tickets"]}


@pytest.fixture
def demo(client):
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_mode=True)
    return client


def test_demo_routes_are_hidden_unless_demo_mode(client):
    assert client.get("/demo/scenarios").status_code == 404
    assert client.post("/demo/replay/simple_refund").status_code == 404


def test_lists_scenarios_without_recordings(demo):
    listed = demo.get("/demo/scenarios").json()
    assert [s["name"] for s in listed] == list(scenarios())
    assert len(listed) >= 8
    assert all("recorded" not in s and "expected" not in s for s in listed)


def test_unknown_scenario_is_404(demo):
    assert demo.post("/demo/replay/nope").status_code == 404


@pytest.mark.parametrize("name", list(scenarios()))
def test_scenario_email_is_its_verified_label(name):
    sc = scenarios()[name]
    label = LABELS[sc.data["label_id"]]
    assert label.get("verified_hash")
    e = sc.email
    assert (e["from_email"], e["subject"], e["body"]) == (label["from"], label["subject"], label["body"])
    assert e["received_at"].startswith(label["received_at"])


@pytest.mark.parametrize("name", list(scenarios()))
def test_replay_reproduces_the_scored_decision(demo, fake_llm, name):
    exp = scenarios()[name].data["expected"]
    r = demo.post(f"/demo/replay/{name}")
    assert r.status_code == 201
    t = r.json()
    assert t["failure_reason"] is None, [s["error"] for s in t["steps"] if s.get("error")]
    assert t["decision"] == exp["decision"]
    assert sorted(f["code"] for f in t["flags"]) == sorted(exp["flags"])
    assert (t["proposal"]["action"], t["proposal"]["order_id"]) == (exp["action"], exp["order_id"])
    if exp["action"] == "refund":
        assert t["amount_cents"] == exp["amount_cents"]
    assert t["status"] == ("executed" if exp["decision"] == "auto_approved" else exp["decision"])
    model_steps = [s for s in t["steps"] if s["kind"] != "tool"]
    assert model_steps and all(s["model"].startswith("replay:") for s in model_steps)
    assert fake_llm.calls == [] and fake_llm.chat_calls == []  # no model was asked anything


def test_replaying_again_replaces_the_earlier_replay_and_its_refund(demo, db):
    first = demo.post("/demo/replay/simple_refund").json()
    second = demo.post("/demo/replay/simple_refund").json()
    assert first["id"] != second["id"]
    assert (second["status"], second["amount_cents"]) == ("executed", 2999)  # not "already refunded"
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert db.scalar(select(func.count()).select_from(Refund).where(Refund.id.like("RF-T%"))) == 1


def test_a_live_ticket_with_the_same_email_is_not_cleared(demo, db):
    e = scenarios()["needs_approval"].email
    live = demo.post("/tickets", json={k: e[k] for k in ("from_email", "subject", "body", "received_at")}).json()
    demo.post("/demo/replay/needs_approval")
    demo.post("/demo/replay/needs_approval")
    assert db.get(Ticket, live["id"]) is not None


def test_replayed_needs_human_ticket_can_be_approved(demo):
    t = demo.post("/demo/replay/needs_approval").json()
    done = demo.post(f"/tickets/{t['id']}/approve", json={"reviewer": "demo visitor"}).json()
    assert done["status"] == "executed"
    assert done["actions"][0]["amount_cents"] == 9499


def test_diverged_tool_result_fails_the_ticket_instead_of_improvising(db):
    sc = scenarios()["simple_refund"]
    data = copy.deepcopy(sc.data)
    data["recorded"]["tool_calls"][0]["result"]["name"] = "Somebody Else"
    t = replay(db, Scenario(name="diverged", data=data), test_settings())
    assert (t.status, t.failure_reason) == ("failed", "llm_unavailable")
    assert any("replay diverged" in (s.error or "") for s in t.steps)
