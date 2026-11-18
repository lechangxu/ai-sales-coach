"""Rubric import and the draft -> active -> archived state machine."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import yaml
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.rubric import STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_DRAFT, Rubric

WEIGHT_TOTAL = 100.0
WEIGHT_TOLERANCE = 0.01
# Arbitrary but fixed: every activation takes the same transaction-scoped lock,
# so two concurrent activations queue up instead of interleaving.
ACTIVATION_LOCK_KEY = 772311


class RubricValidationError(ValueError):
    """The submitted rubric document is malformed (HTTP 422)."""


class RubricConflictError(RuntimeError):
    """The requested transition is not allowed (HTTP 409)."""


class RubricNotFoundError(LookupError):
    """No rubric with that id (HTTP 404)."""


def parse_rubric_document(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RubricValidationError(f"YAML parse failed: {exc}") from exc

    if not isinstance(data, dict):
        raise RubricValidationError("rubric document must be a YAML mapping")

    name = str(data.get("name") or "").strip()
    if not name:
        raise RubricValidationError("rubric.name is required")

    dimensions = data.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise RubricValidationError("rubric.dimensions must be a non-empty list")

    total = 0.0
    seen: set[str] = set()
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise RubricValidationError("each dimension must be a mapping")
        key = str(dimension.get("key") or "").strip()
        if not key:
            raise RubricValidationError("dimension.key is required")
        if key in seen:
            raise RubricValidationError(f"duplicate dimension key: {key}")
        weight = dimension.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise RubricValidationError(f"dimension {key} needs a numeric weight")
        numeric = float(weight)
        if not math.isfinite(numeric):
            # NaN/inf: NaN compares False against every tolerance test below, so
            # a `.nan` weight would otherwise slip through the sum check.
            raise RubricValidationError(f"dimension {key} weight must be finite")
        seen.add(key)
        total += numeric

    if abs(total - WEIGHT_TOTAL) > WEIGHT_TOLERANCE:
        raise RubricValidationError(
            f"weights sum to {total}, expected {WEIGHT_TOTAL} ± {WEIGHT_TOLERANCE}"
        )

    return {"name": name, "dimensions": dimensions, "weight_total": total}


def import_rubric(session: Session, text: str) -> Rubric:
    document = parse_rubric_document(text)
    highest = session.scalar(
        select(func.max(Rubric.version)).where(Rubric.name == document["name"])
    )
    rubric = Rubric(
        name=document["name"],
        version=int(highest or 0) + 1,
        status=STATUS_DRAFT,
        definition={"dimensions": document["dimensions"]},
        weight_total=document["weight_total"],
    )
    session.add(rubric)
    session.commit()
    session.refresh(rubric)
    return rubric


def activate_rubric(session: Session, rubric_id: int) -> Rubric:
    # Serialise the whole read-archive-activate sequence. The partial unique
    # index on rubrics(status) WHERE status='active' is the backstop; this lock
    # is what keeps the ordinary path from failing on it.
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": ACTIVATION_LOCK_KEY}
    )
    rubric = session.get(Rubric, rubric_id)
    if rubric is None:
        raise RubricNotFoundError(f"rubric {rubric_id} not found")
    if rubric.status == STATUS_ARCHIVED:
        raise RubricConflictError(
            "archived rubric versions cannot be reactivated; import a new version"
        )
    if rubric.status == STATUS_ACTIVE:
        return rubric

    now = datetime.now(timezone.utc)
    actives = session.scalars(
        select(Rubric).where(Rubric.status == STATUS_ACTIVE)
    ).all()
    for current in actives:
        current.status = STATUS_ARCHIVED
        current.archived_at = now
    session.flush()

    rubric.status = STATUS_ACTIVE
    rubric.activated_at = now
    rubric.archived_at = None
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise RubricConflictError(
            "another rubric version became active concurrently; retry"
        ) from exc
    session.refresh(rubric)
    return rubric


def get_active_rubric(session: Session) -> Rubric | None:
    return session.scalars(
        select(Rubric).where(Rubric.status == STATUS_ACTIVE).limit(1)
    ).first()


def dimension_keys(rubric: Rubric) -> list[str]:
    dimensions = (rubric.definition or {}).get("dimensions") or []
    return [str(dimension["key"]) for dimension in dimensions]


def dimension_weights(rubric: Rubric) -> dict[str, float]:
    dimensions = (rubric.definition or {}).get("dimensions") or []
    return {str(d["key"]): float(d["weight"]) for d in dimensions}
