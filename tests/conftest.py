"""Test bootstrap: isolated database, no external network, no live API key.

The suite talks to a dedicated ``ai_sales_coach_test`` database created on the
compose Postgres instance, never to the demo database. Every test starts from
an emptied schema.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

ADMIN_DATABASE_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/ai_sales_coach",
)
TEST_DATABASE_NAME = "ai_sales_coach_test"


def _swap_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))


TEST_DATABASE_URL = _swap_database(ADMIN_DATABASE_URL, TEST_DATABASE_NAME)


def _ensure_test_database() -> None:
    engine = create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE_NAME},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
    finally:
        engine.dispose()


_ensure_test_database()

os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ["LLM_BACKEND"] = "stub"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.db import Base, get_engine, get_session_factory, reset_engine  # noqa: E402
import app.models  # noqa: E402,F401  (registers every mapper)
from app.main import app  # noqa: E402

TRUNCATE_SQL = (
    "TRUNCATE TABLE projections, scorecards, reviews, transcripts, rubrics "
    "RESTART IDENTITY CASCADE"
)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    reset_engine()
    engine = get_engine()
    # Drop first: the test database outlives a run, and create_all() alone
    # would leave a stale schema behind whenever a model gains an index or a
    # constraint (the suite would then test yesterday's shape).
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_create_connection = socket.create_connection

    def guarded(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in _LOCAL_HOSTS:
            raise RuntimeError(f"external network is disabled in tests: {host}")
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    session = get_session_factory()()
    try:
        session.execute(text(TRUNCATE_SQL))
        session.commit()
    finally:
        session.close()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def transcript_payload(turns: list[dict] | None = None, title: str = "demo call") -> dict:
    if turns is None:
        turns = [
            {"turn_index": 0, "speaker": "销售", "text": "王总您好，这批 HRB400E 什么时候进场？", "ts": "2026-11-10T09:00:00+00:00"},
            {"turn_index": 1, "speaker": "客户", "text": "下个月中旬，沙钢的货你们能保证吗？", "ts": "2026-11-10T09:02:00+00:00"},
            {"turn_index": 2, "speaker": "销售", "text": "承兑贴息我按 6 个月 3.2 个点给您算。", "ts": "2026-11-10T09:04:00+00:00"},
        ]
    return {"title": title, "turns": turns}


def create_transcript(client: TestClient, payload: dict | None = None) -> int:
    response = client.post("/transcripts", json=payload or transcript_payload())
    assert response.status_code == 201, response.text
    return int(response.json()["transcript_id"])
