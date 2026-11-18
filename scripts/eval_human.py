#!/usr/bin/env python
"""Read a filled human-calibration sheet back and report its QWK.

The sheet is blind (see ``make_calibration_sheet.py``), so the AI side is
recomputed here from the transcript rather than read from the CSV: same stub
review -> deterministic scorer path the eval uses.

Row contract, fixed by the task card:

* blank ``human_score`` -> the row is skipped (a half-filled sheet is the
  normal state while calibration is in progress);
* non-blank but not an integer in 1..5 -> exit 1, naming the row;
* no scorable rows at all -> exit 2 (nothing to compare, not a clean result).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, str(entry))

from app.db import get_session_factory  # noqa: E402
from app.services.eval_metrics import quadratic_weighted_kappa  # noqa: E402

from eval_qwk import predict_totals  # noqa: E402

HEADER = ("transcript_id", "title", "human_score", "note")
SCORE_LABELS = (1, 2, 3, 4, 5)


class ScoreSheetError(ValueError):
    """The sheet is structurally wrong (bad header, bad score value)."""


class NoScorableRows(ValueError):
    """Every row's human_score is blank."""


def read_scores(path: str) -> list[tuple[int, int]]:
    """Parse ``(transcript_id, human_score)`` pairs, skipping blank scores."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        if fieldnames != HEADER:
            raise ScoreSheetError(f"unexpected header {fieldnames}, want {HEADER}")
        pairs: list[tuple[int, int]] = []
        for line_number, row in enumerate(reader, start=2):
            raw = str(row.get("human_score") or "").strip()
            if not raw:
                continue
            try:
                score = int(raw)
            except ValueError:
                raise ScoreSheetError(
                    f"row {line_number}: human_score is not an integer: {raw!r}"
                ) from None
            if score not in SCORE_LABELS:
                raise ScoreSheetError(
                    f"row {line_number}: human_score out of range 1-5: {score}"
                )
            try:
                transcript_id = int(str(row.get("transcript_id") or "").strip())
            except ValueError:
                raise ScoreSheetError(
                    f"row {line_number}: transcript_id is not an integer"
                ) from None
            pairs.append((transcript_id, score))
    if not pairs:
        raise NoScorableRows("no scorable rows: every human_score is blank")
    return pairs


def summarize(human: list[int], predicted: list[int]) -> dict:
    if len(human) != len(predicted):
        raise ScoreSheetError(
            f"length mismatch: human={len(human)} predicted={len(predicted)}"
        )
    if not human:
        raise NoScorableRows("no scorable rows")
    return {"n": len(human), "human_qwk": quadratic_weighted_kappa(human, predicted)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", required=True)
    args = parser.parse_args(argv)

    try:
        pairs = read_scores(args.scores)
    except NoScorableRows as exc:
        print(f"NO SCORABLE ROWS: {exc}", file=sys.stderr)
        return 2
    except ScoreSheetError as exc:
        print(f"INVALID SHEET: {exc}", file=sys.stderr)
        return 1

    session = get_session_factory()()
    try:
        predictions = predict_totals(session, [transcript_id for transcript_id, _ in pairs])
    finally:
        session.close()

    missing = [transcript_id for transcript_id, _ in pairs if transcript_id not in predictions]
    if missing:
        print(f"INVALID SHEET: transcripts not scorable: {missing}", file=sys.stderr)
        return 1

    report = summarize(
        [score for _, score in pairs],
        [predictions[transcript_id] for transcript_id, _ in pairs],
    )
    print(f"human_n={report['n']}")
    print(f"human_qwk={report['human_qwk']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
