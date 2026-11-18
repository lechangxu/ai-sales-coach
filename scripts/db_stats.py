"""Print per-table row counts and self-assert the expected transcript count."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app.db import get_session_factory
from app.models import Projection, Review, Rubric, Scorecard, Transcript


def collect_counts() -> dict[str, int]:
    session = get_session_factory()()
    try:
        counts = {}
        for name, model in (
            ("transcripts", Transcript),
            ("reviews", Review),
            ("projections", Projection),
            ("rubrics", Rubric),
            ("scorecards", Scorecard),
        ):
            counts[name] = int(
                session.scalar(select(func.count()).select_from(model)) or 0
            )
        return counts
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-transcripts", type=int, default=None)
    args = parser.parse_args(argv)

    counts = collect_counts()
    print(" ".join(f"{name}={value}" for name, value in counts.items()))

    if args.expect_transcripts is not None and counts["transcripts"] != args.expect_transcripts:
        print(
            f"transcripts mismatch: expected {args.expect_transcripts}, got {counts['transcripts']}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
