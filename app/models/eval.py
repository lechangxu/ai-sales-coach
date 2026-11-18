"""Eval-layer storage: run headers and per-transcript scores.

Physical isolation from the teaching core (W1 decision): nothing here is
reachable from ``reviews``. These tables never gain a foreign key to
``reviews`` and ``reviews`` never gains a graded column. ``eval_scores``
carries a plain integer ``transcript_id`` instead of a foreign key on purpose:
an eval run is a *snapshot* of a corpus, and re-seeding the demo data must not
erase the history that a drift check compares against.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

EVAL_DIMENSIONS = ("D1", "D2", "D3", "D4", "D5", "total")


class EvalRun(Base):
    """One header row per eval execution: what was scored, and how it went."""

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    scorer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    gold_label_source: Mapped[str] = mapped_column(String(32), nullable=False)
    qwk_total: Mapped[float] = mapped_column(Float, nullable=False)
    qwk_by_dimension: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confusion: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_stable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvalScore(Base):
    """One row per (run, transcript, dimension): gold vs predicted label."""

    __tablename__ = "eval_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eval_run_id: Mapped[int] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Deliberately not a ForeignKey — see module docstring (snapshot semantics).
    transcript_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension: Mapped[str] = mapped_column(String(8), nullable=False)
    gold: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted: Mapped[int] = mapped_column(Integer, nullable=False)
