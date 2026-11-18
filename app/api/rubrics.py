"""Rubric import and activation."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.rubric import Rubric
from app.services.rubric_service import (
    RubricConflictError,
    RubricNotFoundError,
    RubricValidationError,
    activate_rubric,
    get_active_rubric,
    import_rubric,
)

router = APIRouter(tags=["rubrics"])


def _extract_yaml(raw: bytes, content_type: str) -> str:
    """Accept either a raw YAML document or a ``{"yaml": "..."}`` JSON envelope.

    The envelope is recognised by content too: a client that sends JSON without
    a Content-Type header still means the envelope. A YAML flow mapping also
    starts with ``{``, so a failed JSON parse without a JSON Content-Type falls
    back to treating the body as raw YAML instead of erroring.
    """
    text = raw.decode("utf-8", errors="strict")
    stripped = text.strip()
    declared_json = "json" in content_type
    if declared_json or stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if declared_json:
                raise HTTPException(
                    status_code=422, detail=f"invalid JSON body: {exc}"
                ) from exc
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("yaml"), str):
            return parsed["yaml"]
        if declared_json:
            raise HTTPException(status_code=422, detail='JSON body must be {"yaml": "..."}')
    if not stripped:
        raise HTTPException(status_code=422, detail="empty rubric body")
    return text


def _serialize(rubric: Rubric) -> dict:
    return {
        "rubric_id": rubric.id,
        "name": rubric.name,
        "version": rubric.version,
        "status": rubric.status,
        "weight_total": rubric.weight_total,
        "dimensions": (rubric.definition or {}).get("dimensions", []),
    }


@router.post("/rubrics/import", status_code=201)
async def import_rubric_endpoint(
    request: Request, session: Session = Depends(get_db)
) -> dict:
    raw = await request.body()
    yaml_text = _extract_yaml(raw, request.headers.get("content-type", ""))
    try:
        rubric = import_rubric(session, yaml_text)
    except RubricValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize(rubric)


@router.post("/rubrics/{rubric_id}/activate")
def activate_rubric_endpoint(
    rubric_id: int, session: Session = Depends(get_db)
) -> dict:
    try:
        rubric = activate_rubric(session, rubric_id)
    except RubricNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RubricConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(rubric)


@router.get("/rubrics")
def list_rubrics(session: Session = Depends(get_db)) -> dict:
    rows = session.scalars(select(Rubric).order_by(Rubric.id)).all()
    active = get_active_rubric(session)
    return {
        "items": [_serialize(row) for row in rows],
        "active_rubric_id": active.id if active is not None else None,
    }
