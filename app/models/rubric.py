"""Rubric versions.

State machine: ``draft -> active -> archived``. At most one row may be active
at a time; activating a draft archives the previous active version, while an
archived version can never be revived (a new import is required).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


class Rubric(Base):
    __tablename__ = "rubrics"
    __table_args__ = (
        # "at most one active" is a database invariant, not just a service
        # convention: two concurrent activations must not both win.
        Index(
            "uq_rubrics_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_DRAFT, server_default=STATUS_DRAFT
    )
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    weight_total: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
