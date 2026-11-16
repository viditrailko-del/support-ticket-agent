"""Test setup: a separate `support_test` database, wiped between tests.

Tests never touch the dev database and never call Gemini (a fake LLM arrives with Phase 2).
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://support:support@localhost:5434/support_test"
)

engine = create_engine(TEST_DB_URL)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_settings(**overrides) -> Settings:
    base = dict(database_url=TEST_DB_URL, gemini_api_key="fake")
    return Settings(**{**base, **overrides})


test_settings.__test__ = False  # a helper, not a test


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    if tables:  # no tables before Phase 1
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def client() -> Iterator[TestClient]:
    def _db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: test_settings()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db() -> Iterator[Session]:
    with TestSession() as s:
        yield s
