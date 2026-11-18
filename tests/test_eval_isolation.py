"""Isolation regression: the eval layer never re-opens the teaching core."""

from __future__ import annotations

from sqlalchemy import inspect

from app.db import get_engine
from app.models import EvalRun, EvalScore, Review


def test_eval_models_exist():
    assert EvalRun.__tablename__ == "eval_runs"
    assert EvalScore.__tablename__ == "eval_scores"

    inspector = inspect(get_engine())
    tables = set(inspector.get_table_names())
    assert {"eval_runs", "eval_scores"} <= tables

    run_columns = {column["name"] for column in inspector.get_columns("eval_runs")}
    score_columns = {column["name"] for column in inspector.get_columns("eval_scores")}
    assert {"qwk_total", "qwk_by_dimension", "confusion"} <= run_columns
    assert {"eval_run_id", "transcript_id", "dimension", "gold", "predicted"} <= score_columns


def test_reviews_table_has_no_score_columns():
    columns = [column["name"] for column in inspect(get_engine()).get_columns("reviews")]
    graded = [name for name in columns if "score" in name.lower() or "grade" in name.lower()]

    assert graded == []
    # W1 contract: reviews is the model output, not a grade book.
    assert Review.__tablename__ == "reviews"


def test_eval_tables_have_no_fk_to_reviews():
    inspector = inspect(get_engine())
    for table in ("eval_runs", "eval_scores"):
        for foreign_key in inspector.get_foreign_keys(table):
            assert foreign_key.get("referred_table") != "reviews", (table, foreign_key)

    # Snapshot semantics: transcript_id is a plain integer so re-seeding demo
    # data cannot delete the baselines a drift report compares against.
    score_fks = inspect(get_engine()).get_foreign_keys("eval_scores")
    referred = {fk.get("referred_table") for fk in score_fks}
    assert referred == {"eval_runs"}
