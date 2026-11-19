"""Live mode: a visitor's own email, sent as a demo customer, on real Gemini (the fake here), capped."""

import json
from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.demo import live
from app.demo.replay import scenarios
from app.main import app
from tests.conftest import test_settings

SHOP = json.loads(open("data/shop.json", encoding="utf-8").read())
LABELS = json.loads(open("eval/tickets/tickets.json", encoding="utf-8").read())["tickets"]
EMAIL = {"customer_id": "C0006", "subject": "Return water bottle", "body": "Hi, I'd like to return ORD-10436."}


@pytest.fixture(autouse=True)
def _fresh_quota() -> Iterator[None]:
    live.quota.reset()
    yield
    live.quota.reset()


def use(**overrides) -> None:
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_mode=True, **overrides)


@pytest.fixture
def demo(client):
    use()
    return client


def test_live_routes_are_hidden_unless_demo_mode(client):
    assert client.get("/demo/customers").status_code == 404
    assert client.get("/demo/status").status_code == 404
    assert client.post("/demo/live", json=EMAIL).status_code == 404


def test_shop_today_setting_is_the_seeded_shops_date():
    assert get_settings().shop_today.isoformat() == SHOP["shop_today"]


def test_live_customers_are_never_in_a_labeled_ticket_or_a_scenario():
    emails = {c["id"]: c["email"] for c in SHOP["customers"]}
    used = {t["from"] for t in LABELS} | {s.email["from_email"] for s in scenarios().values()}
    assert all(cid in emails for cid in live.LIVE_CUSTOMERS)
    assert not {emails[cid] for cid in live.LIVE_CUSTOMERS} & used


def test_lists_demo_customers_with_their_orders(demo):
    got = demo.get("/demo/customers").json()
    assert [c["customer_id"] for c in got] == list(live.LIVE_CUSTOMERS)
    farid = got[0]
    bottle = next(o for o in farid["orders"] if o["order_id"] == "ORD-10436")
    assert (bottle["status"], bottle["days_since_delivery"], bottle["total_paid_cents"]) == ("delivered", 21, 3598)
    assert bottle["items"][0]["name"] == "Insulated Water Bottle"


def test_live_ticket_is_sent_as_the_customer_on_the_shop_date_with_the_demo_model(demo, fake_llm):
    r = demo.post("/demo/live", json=EMAIL)
    assert r.status_code == 201
    t = r.json()
    assert t["from_email"] == "farid.quigley@example.com" and t["customer_id"] == "C0006"
    assert t["received_at"].startswith(SHOP["shop_today"])
    assert fake_llm.chat_calls[0]["model"] == get_settings().demo_live_model
    assert t["status"] in ("executed", "needs_human")


def test_only_demo_customers_can_be_used(demo):
    r = demo.post("/demo/live", json={**EMAIL, "customer_id": "C0001"})
    assert r.status_code == 404
    assert demo.get("/demo/status").json()["live_used_today"] == 0  # a refused request costs nothing


def test_sender_cannot_be_chosen(demo):
    assert demo.post("/demo/live", json={**EMAIL, "from_email": "x@example.com"}).status_code == 422


def test_body_is_capped_and_must_not_be_blank(demo):
    assert demo.post("/demo/live", json={**EMAIL, "body": "x" * 3001}).status_code == 422
    assert demo.post("/demo/live", json={**EMAIL, "body": "  \n "}).status_code == 422


def test_daily_cap(client):
    use(demo_live_daily_limit=2)
    assert [client.post("/demo/live", json=EMAIL).status_code for _ in range(3)] == [201, 201, 429]
    s = client.get("/demo/status").json()
    assert (s["live_used_today"], s["live_left_today"]) == (2, 0)
    assert "used up" in client.post("/demo/live", json=EMAIL).json()["detail"]


def test_replays_are_free_and_not_counted(client):
    use(demo_live_daily_limit=1)
    assert client.post("/demo/live", json=EMAIL).status_code == 201
    assert client.post("/demo/replay/simple_refund").status_code == 201  # cap reached, replay still works
    live.quota.reset()  # a restart re-reads the count from the DB: the replay isn't in it
    assert client.get("/demo/status").json()["live_used_today"] == 1


def test_cap_survives_a_restart(client):
    use(demo_live_daily_limit=2)
    client.post("/demo/live", json=EMAIL)
    client.post("/demo/live", json=EMAIL)
    live.quota.reset()
    assert client.post("/demo/live", json=EMAIL).status_code == 429


def test_per_visitor_hourly_cap(client):
    use(demo_live_per_ip_per_hour=1)
    assert client.post("/demo/live", json=EMAIL).status_code == 201
    r = client.post("/demo/live", json=EMAIL)
    assert r.status_code == 429 and "per hour" in r.json()["detail"]


def test_post_tickets_spends_the_same_quota_in_demo_mode(client, fake_llm):
    use(demo_live_daily_limit=1)
    body = {"from_email": "farid.quigley@example.com", "subject": "s", "body": "Return ORD-10436 please."}
    assert client.post("/tickets", json=body).status_code == 201
    assert fake_llm.chat_calls[0]["model"] == get_settings().demo_live_model
    assert client.post("/tickets", json=body).status_code == 429
    assert client.post("/demo/live", json=EMAIL).status_code == 429


def test_post_tickets_refuses_scenario_customers_in_demo_mode(demo, fake_llm):
    e = scenarios()["simple_refund"].email
    body = {"from_email": e["from_email"].upper(), "subject": e["subject"], "body": e["body"]}
    r = demo.post("/tickets", json=body)
    assert r.status_code == 409 and "/demo/replay" in r.json()["detail"]
    assert fake_llm.calls == []
    assert demo.get("/demo/status").json()["live_used_today"] == 0
    assert demo.post("/demo/replay/simple_refund").json()["status"] == "executed"


def test_post_tickets_is_not_capped_outside_demo_mode(client):
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_live_daily_limit=0)
    body = {"from_email": "farid.quigley@example.com", "subject": "s", "body": "Return ORD-10436 please."}
    assert client.post("/tickets", json=body).status_code == 201
