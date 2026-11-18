"""eval tables

Revision ID: 0003_eval_tables
Revises: 0001_init
Create Date: 2026-11-18

The eval layer is physically isolated from the teaching core: neither table
below references ``reviews``, and ``eval_scores.transcript_id`` is a plain
integer (snapshot semantics — re-seeding demo transcripts must not delete the
baselines a drift report compares against).

Numbering note: this file keeps the name the task card declares
(``0003_eval_tables``). It chains straight onto ``0001_init``; no ``0002``
revision exists in this repo.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_eval_tables"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("scorer_version", sa.String(length=32), nullable=False),
        sa.Column("gold_label_source", sa.String(length=32), nullable=False),
        sa.Column("qwk_total", sa.Float(), nullable=False),
        sa.Column(
            "qwk_by_dimension",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("confusion", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "is_stable", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "eval_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "eval_run_id",
            sa.Integer(),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transcript_id", sa.Integer(), nullable=False),
        sa.Column("dimension", sa.String(length=8), nullable=False),
        sa.Column("gold", sa.Integer(), nullable=False),
        sa.Column("predicted", sa.Integer(), nullable=False),
    )
    op.create_index("ix_eval_scores_eval_run_id", "eval_scores", ["eval_run_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_scores_eval_run_id", table_name="eval_scores")
    op.drop_table("eval_scores")
    op.drop_table("eval_runs")
