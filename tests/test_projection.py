"""CRM projection: whitelist, quote requirement, truncation, confirmation."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.main import app
from app.models.projection import Projection
from app.models.transcript import Transcript
from app.services.llm import StubLLMClient, provide_llm_client
from app.services.projection import (
    MAX_PROJECTION_CHARS,
    PROJECTION_FIELD_WHITELIST,
    build_projection_rows,
    confirm_projection,
)
from app.services.review_service import create_pending_review, execute_review
from tests.conftest import create_transcript, transcript_payload

FORBIDDEN_FIELDS = ("praise", "emotions", "weaknesses", "script_demo", "encouragement")

FABRICATED_PAYLOAD = {
    "praise": {"text": "开场清楚", "evidence_quote": "王总您好", "turn_index": 0},
    "needs": [
        {"text": "客户要签独家协议", "evidence_quote": "我们要签三年独家协议", "turn_index": 1}
    ],
    "emotions": [],
    "weaknesses": [],
    "next_actions": [
        {
            "text": "确认进场时间",
            "evidence_quote": "这批 HRB400E 什么时候进场？",
            "turn_index": 0,
        }
    ],
    "script_demo": "销售：我先确认排产。",
    "encouragement": "节奏稳。",
}


class StubFabricator:
    name = "test-fabricator"

    def complete(self, system: str, user: str) -> str:
        return json.dumps(FABRICATED_PAYLOAD, ensure_ascii=False)


def test_projection_only_uses_whitelisted_fields(client):
    transcript_id = create_transcript(client)
    review_id = client.post(f"/transcripts/{transcript_id}/review").json()["review_id"]

    response = client.get(f"/reviews/{review_id}/projections")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "review produced no projections"

    field_names = {item["field_name"] for item in items}
    assert field_names <= set(PROJECTION_FIELD_WHITELIST)
    for forbidden in FORBIDDEN_FIELDS:
        assert forbidden not in field_names


def test_items_without_quote_reference_are_not_projected():
    content = {
        "needs": [
            {"text": "有引用", "evidence_quote": "沙钢的货", "turn_index": 1},
            {"text": "无引用", "evidence_quote": "", "turn_index": 2},
        ],
        "next_actions": [],
        "emotions": [{"text": "情绪", "evidence_quote": "沙钢的货", "turn_index": 1}],
    }
    turns = {1: "沙钢的货我们有优势", 2: "价格再谈谈"}
    rows = build_projection_rows(
        review_id=1, content=content, confidence="high", turns_by_index=turns
    )

    assert len(rows) == 1
    assert rows[0].field_name == "needs"
    assert rows[0].item_text == "有引用"


def test_long_items_are_truncated_to_the_crm_limit():
    long_text = "很长的结论" * 60
    content = {
        "needs": [
            {"text": long_text, "evidence_quote": "沙钢的货", "turn_index": 1}
        ]
    }
    rows = build_projection_rows(
        review_id=1, content=content, confidence="high", turns_by_index={1: "沙钢的货"}
    )

    assert len(rows) == 1
    projected = rows[0].item_text
    assert len(projected) == MAX_PROJECTION_CHARS
    assert projected.endswith("…")


def test_low_confidence_projection_waits_for_confirmation(client):
    app.dependency_overrides[provide_llm_client] = lambda: StubFabricator()
    try:
        transcript_id = create_transcript(client)
        review_id = client.post(f"/transcripts/{transcript_id}/review").json()["review_id"]

        review = client.get(f"/reviews/{review_id}").json()
        assert review["confidence"] == "low"

        projections = client.get(f"/reviews/{review_id}/projections").json()["items"]
        assert projections
        assert all(item["status"] == "pending" for item in projections)

        projection_id = projections[0]["projection_id"]
        confirmed = client.post(
            f"/reviews/{review_id}/projections/{projection_id}/confirm"
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "applied"

        refreshed = client.get(f"/reviews/{review_id}/projections").json()["items"]
        by_id = {item["projection_id"]: item for item in refreshed}
        assert by_id[projection_id]["status"] == "applied"
    finally:
        app.dependency_overrides.pop(provide_llm_client, None)


def test_rerunning_a_review_keeps_a_confirmed_projection(db_session):
    transcript = Transcript(
        title="rerun",
        turns=transcript_payload()["turns"],
        is_synthetic=True,
        synthetic_profile="good",
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.refresh(transcript)
    review = create_pending_review(db_session, transcript.id, model_name="stub-deterministic")
    client = StubLLMClient()

    execute_review(db_session, review.id, client)
    rows = db_session.scalars(
        select(Projection).where(Projection.review_id == review.id)
    ).all()
    assert rows
    confirmed = rows[0]
    confirm_projection(db_session, confirmed)

    execute_review(db_session, review.id, client)

    refreshed = db_session.scalars(
        select(Projection).where(Projection.review_id == review.id)
    ).all()
    survivors = [row for row in refreshed if row.id == confirmed.id]
    assert len(survivors) == 1
    assert survivors[0].status == "applied"
