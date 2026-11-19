"""POST /demo/reset (nightly on the public demo) and the deploy-time settings."""

import json

import pytest
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db.models import Refund, Ticket
from app.demo import live
from app.main import app
from tests.conftest import test_settings

SHOP = json.loads(open("data/shop.json", encoding="utf-8").read())
TOKEN = "t0ken-for-tests"
LIVE = {"customer_id": "C0006", "subject": "Return", "body": "Hi, I'd like to return ORD-10436."}


@pytest.fixture
def demo(client):
    live.quota.reset()
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_mode=True, demo_reset_token=TOKEN)
    yield client
    live.quota.reset()


def test_reset_is_off_without_a_token(client):
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_mode=True)
    assert client.post("/demo/reset", headers={"X-Reset-Token": ""}).status_code == 404


def test_reset_is_off_outside_demo_mode(client):
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_reset_token=TOKEN)
    assert client.post("/demo/reset", headers={"X-Reset-Token": TOKEN}).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Reset-Token": "wrong"}, {"X-Reset-Token": TOKEN + "x"}])
def test_reset_needs_the_token(demo, headers):
    demo.post("/demo/replay/simple_refund")
    assert demo.post("/demo/reset", headers=headers).status_code == 403
    assert demo.get("/demo/status").status_code == 200


def test_reset_clears_tickets_and_executed_refunds_and_keeps_the_shop(demo, db):
    demo.post("/demo/replay/simple_refund")  # executes a refund
    demo.post("/demo/live", json=LIVE)
    assert demo.get("/demo/status").json()["live_used_today"] == 1
    r = demo.post("/demo/reset", headers={"X-Reset-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert (body["tickets_deleted"], body["refunds_deleted"]) == (2, 1)
    assert db.scalar(select(func.count()).select_from(Ticket)) == 0
    assert db.scalar(select(func.count()).select_from(Refund)) == len(SHOP["refunds"])
    assert demo.get("/demo/status").json()["live_used_today"] == 0
    assert demo.post("/demo/replay/simple_refund").json()["status"] == "executed"  # scenarios work again


@pytest.mark.parametrize("url", [
    "postgres://u:p@ep-x.neon.tech/neondb?sslmode=require",
    "postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require",
    "postgresql+psycopg://u:p@ep-x.neon.tech/neondb?sslmode=require",
])
def test_hosted_database_urls_use_our_driver(url):
    assert Settings(database_url=url).database_url == "postgresql+psycopg://u:p@ep-x.neon.tech/neondb?sslmode=require"
