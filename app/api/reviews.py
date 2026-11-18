"""Review reads and CRM projection confirmation.

There is deliberately no endpoint in this router that returns a graded value:
supervisor scoring lives in :mod:`app.api.scorecards`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.projection import Projection
from app.models.review import Review
from app.services.projection import confirm_projection
from app.services.review_service import serialize_review

router = APIRouter(tags=["reviews"])


@router.get("/reviews")
def list_reviews(
    spot_check: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> dict:
    statement = select(Review)
    if spot_check is not None:
        statement = statement.where(Review.needs_spot_check.is_(spot_check))
    statement = statement.order_by(Review.id.desc()).limit(limit)
    rows = session.scalars(statement).all()
    return {"items": [serialize_review(row) for row in rows]}


@router.get("/reviews/{review_id}")
def get_review(review_id: int, session: Session = Depends(get_db)) -> dict:
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return serialize_review(review)


@router.get("/reviews/{review_id}/projections")
def list_projections(review_id: int, session: Session = Depends(get_db)) -> dict:
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    rows = session.scalars(
        select(Projection).where(Projection.review_id == review_id).order_by(Projection.id)
    ).all()
    return {
        "items": [
            {
                "projection_id": row.id,
                "field_name": row.field_name,
                "item_text": row.item_text,
                "evidence_quote": row.evidence_quote,
                "turn_index": row.turn_index,
                "status": row.status,
            }
            for row in rows
        ]
    }


@router.post("/reviews/{review_id}/projections/{projection_id}/confirm")
def confirm_projection_endpoint(
    review_id: int, projection_id: int, session: Session = Depends(get_db)
) -> dict:
    projection = session.get(Projection, projection_id)
    if projection is None or projection.review_id != review_id:
        raise HTTPException(status_code=404, detail="projection not found")
    confirm_projection(session, projection)
    return {"projection_id": projection.id, "status": projection.status}
