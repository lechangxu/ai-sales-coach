"""Supervisor scorecards: the only graded write path in the product.

Kept in its own router, its own table and its own response shape so that the
review read path can never join a graded value.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.review import Review
from app.models.rubric import STATUS_ACTIVE, Rubric
from app.models.scorecard import Scorecard
from app.schemas import ScorecardCreate, ScorecardCreated
from app.services.rubric_service import dimension_keys, dimension_weights

router = APIRouter(tags=["scorecards"])

MIN_POINTS = 0.0
MAX_POINTS = 10.0


@router.post("/scorecards", response_model=ScorecardCreated, status_code=201)
def create_scorecard(
    payload: ScorecardCreate, session: Session = Depends(get_db)
) -> ScorecardCreated:
    review = session.get(Review, payload.review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    rubric = session.get(Rubric, payload.rubric_id)
    if rubric is None:
        raise HTTPException(status_code=404, detail="rubric not found")
    if rubric.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"rubric {rubric.id} is {rubric.status}; only an active rubric accepts scoring",
        )

    expected = set(dimension_keys(rubric))
    submitted = set(payload.dimension_scores)
    if submitted != expected:
        raise HTTPException(
            status_code=422,
            detail=f"dimension_scores keys {sorted(submitted)} != rubric keys {sorted(expected)}",
        )

    for key, value in payload.dimension_scores.items():
        if not MIN_POINTS <= float(value) <= MAX_POINTS:
            raise HTTPException(
                status_code=422,
                detail=f"dimension {key} must be between {MIN_POINTS} and {MAX_POINTS}",
            )

    weights = dimension_weights(rubric)
    weighted = sum(float(payload.dimension_scores[key]) * weights[key] for key in expected)
    total = round(weighted / 100.0, 4)

    scorecard = Scorecard(
        review_id=payload.review_id,
        rubric_id=payload.rubric_id,
        reviewer=payload.reviewer,
        dimension_scores={key: float(value) for key, value in payload.dimension_scores.items()},
        total_score=total,
    )
    session.add(scorecard)
    session.commit()
    session.refresh(scorecard)
    return ScorecardCreated(
        scorecard_id=scorecard.id,
        rubric_id=scorecard.rubric_id,
        total_score=scorecard.total_score,
    )


@router.get("/scorecards")
def list_scorecards(
    review_id: int | None = None, session: Session = Depends(get_db)
) -> dict:
    statement = select(Scorecard).order_by(Scorecard.id)
    if review_id is not None:
        statement = statement.where(Scorecard.review_id == review_id)
    rows = session.scalars(statement).all()
    return {
        "items": [
            {
                "scorecard_id": row.id,
                "review_id": row.review_id,
                "rubric_id": row.rubric_id,
                "reviewer": row.reviewer,
                "dimension_scores": row.dimension_scores,
                "total_score": row.total_score,
            }
            for row in rows
        ]
    }
