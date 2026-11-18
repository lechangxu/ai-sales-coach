"""Live smoke: seed -> trigger review -> read back the seven blocks.

Runs against the compose database through the real application object. The LLM
backend defaults to the deterministic stub, so no API key is needed.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("LLM_BACKEND", "stub")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import get_session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models.transcript import Transcript  # noqa: E402

SEVEN_BLOCKS = (
    "praise",
    "needs",
    "emotions",
    "weaknesses",
    "next_actions",
    "script_demo",
    "encouragement",
)
POLL_ATTEMPTS = 60
POLL_INTERVAL_SECONDS = 0.2


def first_transcript_id() -> int | None:
    session = get_session_factory()()
    try:
        return session.scalar(select(Transcript.id).order_by(Transcript.id).limit(1))
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    transcript_id = first_transcript_id()
    if transcript_id is None:
        print("no transcript found; run `make seed-demo` first", file=sys.stderr)
        return 1

    with TestClient(app) as client:
        triggered = client.post(f"/transcripts/{transcript_id}/review")
        if triggered.status_code != 200:
            print(f"trigger failed: {triggered.status_code} {triggered.text}", file=sys.stderr)
            return 1
        body = triggered.json()
        review_id = body["review_id"]

        detail = client.get(f"/reviews/{review_id}").json()
        for _ in range(POLL_ATTEMPTS):
            if detail.get("status") != "pending":
                break
            time.sleep(POLL_INTERVAL_SECONDS)
            detail = client.get(f"/reviews/{review_id}").json()

        missing = [field for field in SEVEN_BLOCKS if field not in detail]
        if detail.get("status") != "done" or missing:
            print(
                f"seven_blocks=FAIL status={detail.get('status')} missing={missing}",
                file=sys.stderr,
            )
            return 1

        print(f"review_id={review_id}")
        print(
            "confidence={} dropped={} model={}".format(
                detail.get("confidence"),
                detail.get("dropped_evidence_count"),
                detail.get("model"),
            )
        )
        print("seven_blocks=OK")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
