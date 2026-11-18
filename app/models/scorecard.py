"""Supervisor scorecards.

Deliberately a separate table with a separate read path: the teaching core
(reviews) never joins this table, so a graded row cannot leak into a review
response.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Scorecard(Base):
    __tablename__ = "scorecards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rubric_id: Mapped[int] = mapped_column(
        ForeignKey("rubrics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
