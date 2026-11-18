#!/usr/bin/env python
"""Run the W2 review-quality eval (QWK) over the synthetic corpus.

Pipeline: transcript -> stub review content (W1 seven-block contract, nothing
is written to ``reviews``) -> deterministic scorer -> quadratic weighted kappa
against the generator's design tier (good=5 / mid=3 / bad=1).

Two things this command deliberately proves instead of asserting in prose:

* ``eval_isolation=OK`` — ``reviews`` gained no graded column and no eval table
  points at it (the teaching-core isolation W1 established).
* ``--expect-stable-rerun`` — two full passes in this process agree exactly on
  QWK, the per-dimension QWK and the confusion matrix. A drift monitor that
  compares against a baseline is worthless if the baseline itself wobbles.

Reading the number honestly: with the offline stub backend the review content
is transcript-independent (same five items for every call), so predicted labels
are constant and QWK is 0 by construction — it is a plumbing/determinism
baseline, not a quality verdict. Point the pipeline at a real LLM backend and
the same command becomes the quality measurement it looks like.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from sqlalchemy import func, inspect, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import PROMPT_VERSION  # noqa: E402
from app.db import get_engine, get_session_factory  # noqa: E402
from app.models import EvalRun, EvalScore, Transcript  # noqa: E402
from app.services.eval_metrics import (  # noqa: E402
    EVAL_LABELS,
    confusion_matrix,
    quadratic_weighted_kappa,
)
from app.services.eval_scoring import (  # noqa: E402
    DIMENSIONS,
    SCORER_VERSION,
    gold_label,
    score_review,
)
from app.services.llm import SYSTEM_PROMPT, StubLLMClient  # noqa: E402
from app.services.review_service import (  # noqa: E402
    _filter_with_evidence,
    build_user_prompt,
    extract_json_object,
    validate_payload,
)

EVAL_TABLES = ("eval_runs", "eval_scores")
GOLD_LABEL_SOURCE = "generator_profile"
REPORTED_DIMENSIONS = DIMENSIONS + ("total",)


def ensure_schema() -> None:
    """Make the eval entry runnable from a clean checkout (README quickstart)."""
    names = set(inspect(get_engine()).get_table_names())
    missing = [table for table in EVAL_TABLES if table not in names]
    if not missing:
        return
    print(f"eval_setup=migrate missing={','.join(missing)}")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stdout, file=sys.stderr)
        print(completed.stderr, file=sys.stderr)
        raise SystemExit("eval schema migration failed")
    names = set(inspect(get_engine()).get_table_names())
    still_missing = [table for table in EVAL_TABLES if table not in names]
    if still_missing:
        raise SystemExit(f"eval tables missing after migrate: {still_missing}")


def ensure_corpus(session: Session) -> None:
    """Seed the demo corpus when the database is empty; never re-seed over data."""
    count = session.scalar(select(func.count()).select_from(Transcript)) or 0
    if count:
        return
    import generate_demo_data as demo

    records = demo.build_dataset(demo.DEFAULT_COUNT, demo.DEFAULT_SEED)
    demo.write_dataset(records)
    print(f"eval_setup=seeded transcripts={len(records)}")


def review_content(turns: list[dict]) -> dict:
    """Produce W1 review content in memory — no ``reviews`` row is written."""
    turns_by_index = {int(turn["turn_index"]): str(turn["text"]) for turn in turns}
    client = StubLLMClient()
    raw = client.complete(SYSTEM_PROMPT, build_user_prompt(turns))
    payload = validate_payload(extract_json_object(raw))
    content, _dropped = _filter_with_evidence(payload, turns_by_index)
    return content


def predict_transcripts(session: Session) -> list[dict]:
    """Score every labelled synthetic transcript with the deterministic scorer."""
    rows: list[dict] = []
    transcripts = session.scalars(select(Transcript).order_by(Transcript.id)).all()
    for transcript in transcripts:
        gold = gold_label(transcript.synthetic_profile)
        if gold is None:
            continue
        rows.append(
            {
                "transcript_id": transcript.id,
                "gold": gold,
                "scores": score_review(review_content(list(transcript.turns or []))),
            }
        )
    return rows


def predict_totals(session: Session, transcript_ids: list[int]) -> dict[int, int]:
    """Predicted total for a hand-picked set of transcripts (human calibration)."""
    wanted = set(transcript_ids)
    predictions: dict[int, int] = {}
    for row in predict_transcripts(session):
        if row["transcript_id"] in wanted:
            predictions[row["transcript_id"]] = row["scores"]["total"]
    return predictions


def compute_report(rows: list[dict]) -> dict:
    if not rows:
        raise SystemExit("eval_report=empty no labelled synthetic transcripts to score")
    golds = [row["gold"] for row in rows]
    by_dimension = {
        dimension: quadratic_weighted_kappa(
            golds, [row["scores"][dimension] for row in rows]
        )
        for dimension in DIMENSIONS
    }
    totals = [row["scores"]["total"] for row in rows]
    return {
        "qwk_total": quadratic_weighted_kappa(golds, totals),
        "qwk_by_dimension": by_dimension,
        "confusion": confusion_matrix(golds, totals),
    }


def _score_rows(rows: list[dict]) -> list[tuple]:
    """Every per-transcript, per-dimension score — the raw material of a run."""
    return [
        (
            row["transcript_id"],
            row["gold"],
            tuple(row["scores"][dimension] for dimension in REPORTED_DIMENSIONS),
        )
        for row in rows
    ]


def _rounded(report: dict) -> dict:
    return {
        "qwk_total": round(report["qwk_total"], 6),
        "qwk_by_dimension": {
            key: round(value, 6) for key, value in report["qwk_by_dimension"].items()
        },
        "confusion": [list(row) for row in report["confusion"]],
    }


def persist_run(session: Session, rows: list[dict], report: dict, model_name: str) -> EvalRun:
    run = EvalRun(
        prompt_version=PROMPT_VERSION,
        model=model_name,
        scorer_version=SCORER_VERSION,
        gold_label_source=GOLD_LABEL_SOURCE,
        qwk_total=report["qwk_total"],
        qwk_by_dimension={
            key: round(value, 6) for key, value in report["qwk_by_dimension"].items()
        },
        confusion={
            "labels": list(EVAL_LABELS),
            "matrix": [list(row) for row in report["confusion"]],
        },
        is_stable=False,
    )
    session.add(run)
    session.flush()
    for row in rows:
        for dimension in REPORTED_DIMENSIONS:
            session.add(
                EvalScore(
                    eval_run_id=run.id,
                    transcript_id=row["transcript_id"],
                    dimension=dimension,
                    gold=row["gold"],
                    predicted=row["scores"][dimension],
                )
            )
    session.commit()
    return run


def check_isolation() -> str:
    """Assert the W1 teaching-core isolation still holds around the eval layer."""
    inspector = inspect(get_engine())
    review_columns = [column["name"] for column in inspector.get_columns("reviews")]
    graded = sorted(
        name for name in review_columns if "score" in name.lower() or "grade" in name.lower()
    )
    if graded:
        raise SystemExit(f"eval_isolation=BROKEN reviews gained graded columns {graded}")
    for table in EVAL_TABLES:
        for foreign_key in inspector.get_foreign_keys(table):
            if foreign_key.get("referred_table") == "reviews":
                raise SystemExit(f"eval_isolation=BROKEN {table} references reviews")
    return "eval_isolation=OK"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-min-qwk", type=float, default=None)
    parser.add_argument("--expect-stable-rerun", action="store_true")
    parser.add_argument("--expect-scored-min", type=int, default=None)
    args = parser.parse_args(argv)

    ensure_schema()
    session = get_session_factory()()
    try:
        ensure_corpus(session)
        rows = predict_transcripts(session)
        if not rows:
            print("eval_report=empty no labelled synthetic transcripts", file=sys.stderr)
            return 2

        report = compute_report(rows)
        # Isolation is asserted before anything is persisted: a broken boundary
        # must not leave a run behind that later reads as a valid measurement.
        isolation = check_isolation()
        first = persist_run(session, rows, report, StubLLMClient.name)

        stable = None
        second_id = None
        if args.expect_stable_rerun:
            # Re-read and re-score from the database rather than reusing the
            # in-memory rows: the assertion is about the whole pipeline, not
            # about this process's variables. Compared unrounded — rounding is
            # for printing only, the assertion is bit-for-bit.
            rerun_rows = predict_transcripts(session)
            rerun_report = compute_report(rerun_rows)
            # Both layers must be identical: the aggregate report and every
            # per-transcript, per-dimension score that fed it. An aggregate can
            # match while rows drift past each other.
            stable = rerun_report == report and _score_rows(rerun_rows) == _score_rows(rows)
            second = persist_run(session, rerun_rows, rerun_report, StubLLMClient.name)
            second_id = second.id
            for run_id in (first.id, second_id):
                run = session.get(EvalRun, run_id)
                run.is_stable = stable
            session.commit()
        else:
            first.is_stable = False
            session.commit()

        print(f"qwk={_rounded(report)['qwk_total']:.6f}")
        print(f"qwk_by_dimension={json.dumps(_rounded(report)['qwk_by_dimension'], ensure_ascii=False)}")
        print(f"confusion={json.dumps({'labels': list(EVAL_LABELS), 'matrix': _rounded(report)['confusion']}, ensure_ascii=False)}")
        print(f"eval_run_id={first.id}")
        if second_id is not None:
            print(f"eval_run_id_rerun={second_id}")
        print(f"transcripts_scored={len(rows)}")
        print(isolation)
    finally:
        session.close()

    if args.expect_scored_min is not None and len(rows) < args.expect_scored_min:
        print(
            f"EVAL FAILED: scored {len(rows)} transcripts, expected >= {args.expect_scored_min}",
            file=sys.stderr,
        )
        return 1
    if args.expect_min_qwk is not None and report["qwk_total"] < args.expect_min_qwk:
        print(
            f"EVAL FAILED: qwk {report['qwk_total']:.6f} < {args.expect_min_qwk}",
            file=sys.stderr,
        )
        return 1
    if stable is False:
        print("EVAL FAILED: rerun produced a different report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
