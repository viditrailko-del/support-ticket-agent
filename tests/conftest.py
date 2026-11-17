"""Test setup: a separate `support_test` database.

The fake shop and the policy rules are seeded once per session (read-only reference data, identical to the
dev seed). Ticket tables are wiped after every test. Tests never touch the dev database and never call
Gemini: `FakeLLM` replays scripted replies.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import json

from app.agent.client import ChatResponse, FunctionCall, LLMError, LLMResponse, ModelTurn, get_llm
from app.config import Settings, get_settings
from app.db.models import AgentStep, Action, Base, Ticket
from app.db.session import get_db
from app.main import app
from data.seed import seed

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://support:support@localhost:5434/support_test"
)

engine = create_engine(TEST_DB_URL)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


CLASSIFY_JSON = '{"category": "refund_request", "urgency": "normal", "reason": "Wants to return an item."}'


REPLY_ONLY = {
    "action": "reply_only", "order_id": None, "items": [], "include_shipping": False, "amount_cents": None,
    "reply_draft": "Hi, could you send us your order number?",
    "citations": [{"rule_id": "ACC-4", "quote": "we ask for the correct order number before acting"}],
    "rationale": "No order number.",
}


def turn(*calls: tuple[str, dict]) -> ChatResponse:
    """A scripted model turn making these function calls (name, args)."""
    fcs = [FunctionCall(name=n, args=a, id=f"call-{i}") for i, (n, a) in enumerate(calls)]
    raw = json.dumps({"parts": [{"function_call": {"name": c.name, "args": c.args}} for c in fcs]})
    return ChatResponse(text=raw, model="", latency_ms=5, input_tokens=2000, output_tokens=150, calls=fcs,
                        turn=ModelTurn({"fake_turn": raw}))


def submit(**proposal) -> ChatResponse:
    return turn(("submit_proposal", {**REPLY_ONLY, **proposal}))


class FakeLLM:
    """Stands in for Gemini.

    generate() (classification) pops `replies`: a str (the model's text) or an LLMError (raised); default
    CLASSIFY_JSON. chat() (the agent) pops `chat_replies`: a ChatResponse or an LLMError; default: submit a
    valid reply_only proposal."""

    def __init__(self) -> None:
        self.replies: list[str | LLMError] = []
        self.calls: list[dict] = []
        self.chat_replies: list[ChatResponse | LLMError] = []
        self.chat_calls: list[dict] = []

    def chat(self, *, system, messages, tools, model) -> ChatResponse:
        self.chat_calls.append({"system": system, "messages": list(messages), "tools": tools, "model": model})
        reply = self.chat_replies.pop(0) if self.chat_replies else submit()
        if isinstance(reply, LLMError):
            raise reply
        reply.model = model
        return reply

    def generate(self, *, system, user, schema, model) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "schema": schema, "model": model})
        reply = self.replies.pop(0) if self.replies else CLASSIFY_JSON
        if isinstance(reply, LLMError):
            raise reply
        return LLMResponse(text=reply, model=model, latency_ms=5, input_tokens=1000, output_tokens=100)


def test_settings(**overrides) -> Settings:
    base = dict(database_url=TEST_DB_URL, gemini_api_key="fake", llm_retry_backoff_s=0,
                classify_model="gemini-3.5-flash-lite", classify_fallback_model="gemini-3.5-flash")
    return Settings(**{**base, **overrides})


test_settings.__test__ = False  # a helper, not a test


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestSession() as s:
        seed(s)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    # Only what tests create. Shop/policy tables stay seeded; a test that must change them restores them.
    tables = ", ".join(m.__tablename__ for m in (Ticket, AgentStep, Action))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(fake_llm) -> Iterator[TestClient]:
    def _db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: test_settings()
    app.dependency_overrides[get_llm] = lambda: fake_llm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db() -> Iterator[Session]:
    with TestSession() as s:
        yield s
