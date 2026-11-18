"""Transcript import and review triggering."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db, get_session_factory
from app.models.review import Review
from app.models.transcript import Transcript
from app.schemas import ReviewTriggered, TranscriptCreate, TranscriptCreated
from app.services.llm import LLMClient, provide_llm_client
from app.services.review_service import create_pending_review, execute_review

router = APIRouter(tags=["transcripts"])
logger = logging.getLogger(__name__)


def _mark_review_failed(review_id: int) -> None:
    """Leave a visible ``status=error`` behind instead of a forever-pending row.

    Uses its own session: the failed one may still hold an aborted transaction,
    and the whole point is to record the failure where the caller can see it.
    If even this write fails (DB still down) it is logged, never raised — the
    background context has nowhere to raise to.
    """
    try:
        session = get_session_factory()()
        try:
            review = session.get(Review, review_id)
            if review is not None and review.status == "pending":
                review.status = "error"
                review.error_message = "background job crashed before the review completed"
                session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("could not mark review as failed: review_id=%s", review_id)


def _run_review_job(review_id: int, client: LLMClient) -> None:
    """Background entry point: owns its own session and never raises upward.

    Starlette runs background tasks after the response, where an exception has
    nowhere to surface. ``execute_review`` already turns LLM failures into
    ``status=error``; anything else (DB outage, bug) is logged here and the row
    is flipped to ``status=error`` so "still running" and "died" stay
    distinguishable to the caller.
    """
    session = get_session_factory()()
    try:
        execute_review(session, review_id, client)
    except Exception:
        session.rollback()
        logger.exception("review job failed: review_id=%s", review_id)
        _mark_review_failed(review_id)
    finally:
        session.close()


@router.post("/transcripts", response_model=TranscriptCreated, status_code=201)
def create_transcript(
    payload: TranscriptCreate, session: Session = Depends(get_db)
) -> TranscriptCreated:
    transcript = Transcript(
        title=payload.title,
        turns=[turn.model_dump() for turn in payload.turns],
        is_synthetic=payload.is_synthetic,
        synthetic_profile=payload.synthetic_profile,
    )
    session.add(transcript)
    session.commit()
    session.refresh(transcript)
    return TranscriptCreated(transcript_id=transcript.id)


@router.get("/transcripts/{transcript_id}")
def get_transcript(
    transcript_id: int, session: Session = Depends(get_db)
) -> dict:
    transcript = session.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="transcript not found")
    return {
        "transcript_id": transcript.id,
        "title": transcript.title,
        "turns": transcript.turns,
        "is_synthetic": transcript.is_synthetic,
        "synthetic_profile": transcript.synthetic_profile,
    }


@router.post("/transcripts/{transcript_id}/review", response_model=ReviewTriggered)
def trigger_review(
    transcript_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
    client: LLMClient = Depends(provide_llm_client),
) -> ReviewTriggered:
    transcript = session.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="transcript not found")

    review = create_pending_review(session, transcript_id, model_name=client.name)
    background_tasks.add_task(_run_review_job, review.id, client)
    return ReviewTriggered(review_id=review.id, status="pending")
