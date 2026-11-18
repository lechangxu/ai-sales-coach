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

Drift monitoring rides on the same scoring path (never a second copy of it):

* ``--write-snapshot <path>`` — freeze this run as a deterministic baseline
  (per-transcript, per-dimension scores + report; no timestamp, no run id).
* ``--baseline <path>`` — re-run and compare. Any per-transcript difference
  (gold, a dimension, a total, a missing/extra transcript) or a report change
  prints ``drift=DRIFT`` and exits 1. Score distributions are printed for a
  human to read, and deliberately do **not** fail the run: a prompt change is
  supposed to move scores.
"""

from __future__ import annotations

import argparse
import hashlib
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


def transcript_key(title: str, turns: list) -> str:
    """Content-derived identity for a transcript.

    The database id is *not* a stable identity: ``make drift`` depends on
    ``seed-demo``, which truncates and re-inserts, so ids shift on every run and
    an id-keyed baseline would report drift on an unchanged corpus. The same
    corpus always regenerates the same title/turns, so hashing them gives a key
    that survives reseeds while still going red when the corpus itself changes.
    """
    blob = json.dumps(
        {"title": title or "", "turns": turns or []},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def predict_transcripts(session: Session) -> list[dict]:
    """Score every labelled synthetic transcript with the deterministic scorer."""
    rows: list[dict] = []
    transcripts = session.scalars(select(Transcript).order_by(Transcript.id)).all()
    occurrences: dict[str, int] = {}
    for transcript in transcripts:
        gold = gold_label(transcript.synthetic_profile)
        if gold is None:
            continue
        turns = list(transcript.turns or [])
        content_hash = transcript_key(transcript.title, turns)
        # The demo corpus contains identical (title, turns) pairs, so a bare
        # content hash would collapse them into one row. The occurrence counter
        # keeps every row addressable without falling back to the DB id.
        occurrences[content_hash] = occurrences.get(content_hash, 0) + 1
        rows.append(
            {
                "transcript_id": transcript.id,
                "transcript_key": f"{content_hash}-{occurrences[content_hash]}",
                "gold": gold,
                "scores": score_review(review_content(turns)),
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


# --- drift snapshot -------------------------------------------------------
#
# The drift baseline is a *deterministic* file: the same corpus with the same
# scorer must serialise to the same bytes. Two things are deliberately absent
# from it — a timestamp and the eval run id — because either would make the
# baseline drift on its own and turn every comparison red.


def build_snapshot(rows: list[dict], report: dict) -> dict:
    """Canonical snapshot of one eval pass: per-transcript scores + report."""
    rounded = _rounded(report)
    return {
        "scorer_version": SCORER_VERSION,
        "rows": [
            {
                "transcript_key": row["transcript_key"],
                "gold": row["gold"],
                "scores": {
                    dimension: row["scores"][dimension]
                    for dimension in REPORTED_DIMENSIONS
                },
            }
            for row in sorted(rows, key=lambda item: item["transcript_key"])
        ],
        "report": {
            "qwk_total": rounded["qwk_total"],
            "qwk_by_dimension": rounded["qwk_by_dimension"],
            "confusion": {"labels": list(EVAL_LABELS), "matrix": rounded["confusion"]},
        },
    }


def snapshot_text(snapshot: dict) -> str:
    """Stable serialisation: sorted keys, UTF-8, trailing newline."""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def write_snapshot(snapshot: dict, out_path: str) -> None:
    path = Path(out_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot_text(snapshot), encoding="utf-8")


def _round_floats(value, ndigits: int = 6):
    """Recursively round every float, so float jitter cannot fake a drift.

    Decision 6 of the card: report floats are compared at 6 decimal places.
    ``0.0`` and ``1e-9`` are the same quality score; without this they would
    be reported as drift and the monitor would cry wolf on its own arithmetic.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {key: _round_floats(item, ndigits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_floats(item, ndigits) for item in value]
    return value


def diff_snapshots(current: dict, baseline: dict) -> list[str]:
    """Every difference between two snapshots, one human-readable line each.

    Covers all five layers the card requires: the transcript id set, ``gold``,
    each per-dimension score, ``total`` (it is in ``REPORTED_DIMENSIONS``), and
    the aggregate report. An empty list means "no drift".
    """
    diffs: list[str] = []
    if current.get("scorer_version") != baseline.get("scorer_version"):
        diffs.append(
            f"scorer_version {baseline.get('scorer_version')} -> {current.get('scorer_version')}"
        )
    current_rows = {row["transcript_key"]: row for row in current.get("rows", [])}
    baseline_rows = {row["transcript_key"]: row for row in baseline.get("rows", [])}
    for key in sorted(set(baseline_rows) - set(current_rows)):
        diffs.append(f"missing transcript {key}")
    for key in sorted(set(current_rows) - set(baseline_rows)):
        diffs.append(f"extra transcript {key}")
    for key in sorted(set(current_rows) & set(baseline_rows)):
        new_row = current_rows[key]
        old_row = baseline_rows[key]
        if new_row.get("gold") != old_row.get("gold"):
            diffs.append(
                f"transcript {key} gold {old_row.get('gold')} -> {new_row.get('gold')}"
            )
        old_scores = old_row.get("scores") or {}
        for dimension in REPORTED_DIMENSIONS:
            new_value = new_row.get("scores", {}).get(dimension)
            old_value = old_scores.get(dimension)
            if new_value != old_value:
                diffs.append(
                    f"transcript {key} {dimension} {old_value} -> {new_value}"
                )
    current_report = _round_floats(current.get("report")) or {}
    baseline_report = _round_floats(baseline.get("report")) or {}
    if current_report != baseline_report:
        changed = sorted(
            key
            for key in set(current_report) | set(baseline_report)
            if current_report.get(key) != baseline_report.get(key)
        )
        detail = "; ".join(
            f"{key} {baseline_report.get(key)} -> {current_report.get(key)}"
            for key in changed
        )
        diffs.append(f"report changed: {detail}")
    return diffs


def score_distribution(rows: list[dict]) -> dict:
    """Label histogram per reported dimension — reported, never a red signal.

    A prompt change is *supposed* to move scores; treating a distribution shift
    as failure would forbid improving the product. The hard signal is the
    per-transcript comparison above.
    """
    distribution = {
        dimension: {str(label): 0 for label in EVAL_LABELS}
        for dimension in REPORTED_DIMENSIONS
    }
    for row in rows:
        for dimension in REPORTED_DIMENSIONS:
            label = row["scores"][dimension]
            bucket = str(label)
            if bucket not in distribution[dimension]:
                bucket = "other"
                distribution[dimension].setdefault(bucket, 0)
            distribution[dimension][bucket] += 1
    return distribution


def _load_baseline(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"drift=ERROR baseline not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"drift=ERROR baseline is not valid JSON: {path} ({exc})")


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
    parser.add_argument(
        "--write-snapshot",
        metavar="PATH",
        default=None,
        help="write a deterministic score snapshot (drift baseline) to PATH",
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        default=None,
        help="compare this run against a snapshot; exit 1 on drift",
    )
    args = parser.parse_args(argv)
    if args.write_snapshot and args.baseline:
        parser.error("--write-snapshot and --baseline are mutually exclusive")

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

        snapshot = build_snapshot(rows, report)
        drift_diffs: list[str] = []
        if args.write_snapshot:
            write_snapshot(snapshot, args.write_snapshot)
            print(f"snapshot={args.write_snapshot} rows={len(rows)}")
        if args.baseline:
            baseline = _load_baseline(args.baseline)
            drift_diffs = diff_snapshots(snapshot, baseline)
            current_distribution = score_distribution(rows)
            baseline_distribution = score_distribution(baseline.get("rows", []))
            print(
                f"distribution={json.dumps(current_distribution, sort_keys=True, ensure_ascii=False)}"
            )
            print(
                "distribution_changed="
                + ("yes" if current_distribution != baseline_distribution else "no")
            )
            current_by_key = {
                row["transcript_key"]: row for row in snapshot["rows"]
            }
            baseline_by_key = {
                row["transcript_key"]: row for row in baseline.get("rows", [])
            }
            matched = sum(
                1
                for key, row in current_by_key.items()
                if baseline_by_key.get(key) == row
            )
            print(
                f"drift={'DRIFT' if drift_diffs else 'OK'} rows={len(rows)} matched={matched}"
            )
            for line in drift_diffs[:5]:
                print(f"drift_diff={line}")
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
    if drift_diffs:
        print(
            f"EVAL FAILED: {len(drift_diffs)} drift difference(s) vs {args.baseline}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
