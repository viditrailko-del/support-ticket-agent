"""The demo page at /: served only in demo mode, self-contained, never renders API text as HTML."""

import re

from app.config import get_settings
from app.main import DEMO_PAGE, app
from tests.conftest import test_settings

PAGE = DEMO_PAGE.read_text(encoding="utf-8")


def test_root_redirects_to_docs_outside_demo_mode(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_root_serves_the_demo_page_in_demo_mode(client):
    app.dependency_overrides[get_settings] = lambda: test_settings(demo_mode=True)
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "The AI proposes. Code decides." in r.text


def test_page_never_renders_strings_as_html():
    # Emails and model output are untrusted: the page builds every node with textContent.
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", PAGE)


def test_page_loads_nothing_from_other_hosts():
    assert not re.search(r"<(script|link)[^>]+(src|href)=\"https?://", PAGE)


def test_page_only_calls_existing_routes(client):
    paths = set(app.openapi()["paths"])
    called = set(re.findall(r'api\(`?"?(/[a-z/]+)', PAGE))
    assert {"/demo/scenarios", "/demo/customers", "/demo/status", "/demo/live"} <= called
    assert all(any(p.startswith(c.rstrip("/")) for p in paths) for c in called)
