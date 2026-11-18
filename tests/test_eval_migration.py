"""The eval tables must exist via alembic, not only via create_all()."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  (registers every mapper on Base.metadata)
from app.db import Base

ADMIN_DATABASE_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/ai_sales_coach",
)
MIGRATION_DATABASE = "ai_sales_coach_migration_test"

EVAL_TABLES = ("eval_runs", "eval_scores")


def _swap_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))


@pytest.fixture(scope="module")
def migrated_engine():
    """Build a throwaway database with ``alembic upgrade head`` and inspect it.

    The test database itself is built by ``create_all()`` in conftest, which is
    exactly how W1 shipped a model/migration drift unnoticed — so the migration
    gets its own empty database and has to stand on its own.
    """
    admin = create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DATABASE}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{MIGRATION_DATABASE}"'))

    url = _swap_database(ADMIN_DATABASE_URL, MIGRATION_DATABASE)
    environment = {**os.environ, "DATABASE_URL": url}
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(
                text(f'DROP DATABASE IF EXISTS "{MIGRATION_DATABASE}" WITH (FORCE)')
            )
        admin.dispose()


def test_migration_columns_match_models(migrated_engine):
    inspector = inspect(migrated_engine)

    for table in EVAL_TABLES:
        migrated = {column["name"] for column in inspector.get_columns(table)}
        modelled = set(Base.metadata.tables[table].columns.keys())
        assert migrated == modelled, (table, sorted(migrated ^ modelled))

    run_columns = {column["name"] for column in inspector.get_columns("eval_runs")}
    assert {
        "prompt_version",
        "model",
        "scorer_version",
        "gold_label_source",
        "qwk_total",
        "qwk_by_dimension",
        "confusion",
        "is_stable",
        "created_at",
    } <= run_columns


def test_migration_has_no_fk_to_reviews(migrated_engine):
    inspector = inspect(migrated_engine)

    assert set(EVAL_TABLES) <= set(inspector.get_table_names())
    for table in EVAL_TABLES:
        for foreign_key in inspector.get_foreign_keys(table):
            assert foreign_key.get("referred_table") != "reviews", (table, foreign_key)

    # demo re-seeding deletes transcripts; eval history must survive it.
    score_fks = inspector.get_foreign_keys("eval_scores")
    assert {fk.get("referred_table") for fk in score_fks} == {"eval_runs"}
