#!/usr/bin/env python
"""Export a blind human-calibration sheet for the W2 eval.

Ten synthetic calls, human score left blank. Nothing that could anchor the
rater leaks into the file: no ``synthetic_profile`` tier, no AI score, no
ordering hint — the columns are exactly ``transcript_id,title,human_score,note``
so the person scoring hears the call, not our expectations of it.
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
        sys.path.insert(0, entry)

from sqlalchemy import select  # noqa: E402

from app.db import get_session_factory  # noqa: E402
from app.models import Transcript  # noqa: E402

HEADER = ("transcript_id", "title", "human_score", "note")
DEFAULT_COUNT = 10
# The generator writes the design tier into the title ("…·mid 通话"). Blind
# rating means the rater must not see it, so it is stripped on the way out.
TIER_TITLE_SUFFIXES = ("·good 通话", "·mid 通话", "·bad 通话")
TIER_WORDS = ("good", "mid", "bad")


def blind_title(title: str) -> str:
    cleaned = title
    for suffix in TIER_TITLE_SUFFIXES:
        cleaned = cleaned.replace(suffix, " 通话")
    return cleaned


def assert_blind(rows: list[dict]) -> None:
    """Fail loudly rather than ship a sheet that anchors the rater."""
    for row in rows:
        blob = " ".join(str(value) for value in row.values()).lower()
        leaked = [word for word in TIER_WORDS if word in blob]
        if leaked:
            raise SystemExit(
                f"calibration sheet would leak the design tier {leaked} "
                f"in row {row['transcript_id']}"
            )


def build_rows(count: int = DEFAULT_COUNT) -> list[dict]:
    """Pick labelled synthetic transcripts; output carries no label column."""
    session = get_session_factory()()
    try:
        transcripts = session.scalars(
            select(Transcript)
            .where(Transcript.synthetic_profile.is_not(None))
            .order_by(Transcript.id)
            .limit(count)
        ).all()
        return [
            {
                "transcript_id": transcript.id,
                "title": blind_title(transcript.title),
                "human_score": "",
                "note": "",
            }
            for transcript in transcripts
        ]
    finally:
        session.close()


def write_sheet(rows: list[dict], out_path: str) -> None:
    # Explicit "\n" line endings: the sheet is diffed and grepped by hand and by
    # the gate, and a stray "\r" makes the header fail an exact-line match.
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(HEADER), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.count < 1:
        print("--count must be >= 1", file=sys.stderr)
        return 2

    rows = build_rows(args.count)
    if len(rows) < args.count:
        print(
            f"only {len(rows)} labelled synthetic transcripts available, wanted {args.count}",
            file=sys.stderr,
        )
        return 2

    assert_blind(rows)
    write_sheet(rows, args.out)
    written = Path(args.out).read_text(encoding="utf-8").lower()
    leaked = [word for word in TIER_WORDS if word in written]
    if leaked:
        print(f"calibration sheet leaks the design tier {leaked}", file=sys.stderr)
        return 2
    print(f"calibration_rows={len(rows)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
