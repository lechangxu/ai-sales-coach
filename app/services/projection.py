"""CRM projection: a hard whitelist, never a blacklist.

Only the two field names in :data:`PROJECTION_FIELD_WHITELIST` are ever read
from a review body. Everything else is untouched by construction, so a new
review field can never leak into the CRM by accident.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.projection import STATUS_APPLIED, STATUS_PENDING, Projection
from app.services.quote_validation import classify_quote

PROJECTION_FIELD_WHITELIST: tuple[str, ...] = ("needs", "next_actions")
MAX_PROJECTION_CHARS = 200
ELLIPSIS = "…"


def truncate_for_crm(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= MAX_PROJECTION_CHARS:
        return cleaned
    keep = MAX_PROJECTION_CHARS - len(ELLIPSIS)
    return cleaned[:keep] + ELLIPSIS


def build_projection_rows(
    review_id: int,
    content: dict,
    confidence: str | None,
    turns_by_index: dict[int, str],
) -> list[Projection]:
    """Build rows for whitelisted fields that carry a valid quote reference.

    ``turns_by_index`` is required on purpose: every projected item is
    re-verified against the cited turn here, so no caller can hand this layer
    an unverifiable quote by forgetting a filter upstream.
    """
    status = STATUS_PENDING if confidence == "low" else STATUS_APPLIED
    now = datetime.now(timezone.utc)
    rows: list[Projection] = []
    for field_name in PROJECTION_FIELD_WHITELIST:
        for item in content.get(field_name) or []:
            quote = str(item.get("evidence_quote") or "").strip()
            if not quote:
                continue
            turn_index = int(item.get("turn_index", -1))
            source = turns_by_index.get(turn_index)
            if source is None or classify_quote(quote, source) is None:
                continue
            rows.append(
                Projection(
                    review_id=review_id,
                    field_name=field_name,
                    item_text=truncate_for_crm(str(item.get("text") or "")),
                    evidence_quote=quote,
                    turn_index=turn_index,
                    status=status,
                    applied_at=now if status == STATUS_APPLIED else None,
                )
            )
    return rows


def replace_projections(
    session: Session,
    review_id: int,
    content: dict,
    confidence: str | None,
    turns_by_index: dict[int, str],
) -> list[Projection]:
    """Rebuild pending projections, but keep rows a human already confirmed."""
    already_applied = session.scalars(
        select(Projection).where(
            Projection.review_id == review_id, Projection.status == STATUS_APPLIED
        )
    ).all()
    session.execute(
        delete(Projection).where(
            Projection.review_id == review_id, Projection.status != STATUS_APPLIED
        )
    )
    # Identity includes the quote and turn: item_text alone is truncated to the
    # CRM limit, so two different conclusions can collide on their first 200
    # characters and a confirmed row would then shadow an unrelated one.
    kept_keys = {
        (row.field_name, row.item_text, row.evidence_quote, row.turn_index)
        for row in already_applied
    }
    rows = build_projection_rows(review_id, content, confidence, turns_by_index)
    for row in rows:
        if (row.field_name, row.item_text, row.evidence_quote, row.turn_index) in kept_keys:
            continue
        session.add(row)
    return rows


def confirm_projection(session: Session, projection: Projection) -> Projection:
    projection.status = STATUS_APPLIED
    projection.applied_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(projection)
    return projection
