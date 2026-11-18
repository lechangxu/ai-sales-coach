"""Evidence chain: exact match, fuzzy match, fabrication, retry, hard failure."""

from __future__ import annotations

import json

from app.models.review import Review
from app.models.transcript import Transcript
from app.services.review_service import (
    PLACEHOLDER_PRAISE,
    create_pending_review,
    execute_review,
)

TURNS = [
    {
        "turn_index": 0,
        "speaker": "销售",
        "text": "王总您好，这批 HRB400E 什么时候进场？",
        "ts": "2026-11-10T09:00:00+00:00",
    },
    {
        "turn_index": 1,
        "speaker": "客户",
        "text": "下个月中旬，沙钢的货你们能保证吗？",
        "ts": "2026-11-10T09:02:00+00:00",
    },
    {
        "turn_index": 2,
        "speaker": "销售",
        "text": "这批货的价格可以再谈一谈吧。",
        "ts": "2026-11-10T09:04:00+00:00",
    },
]


class ScriptedClient:
    """Returns queued responses; exception instances are raised."""

    name = "scripted"

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        item = self.responses[index]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return json.dumps(item, ensure_ascii=False)
        return str(item)


def seed_review(session) -> int:
    transcript = Transcript(
        title="chain call", turns=TURNS, is_synthetic=True, synthetic_profile="good"
    )
    session.add(transcript)
    session.commit()
    session.refresh(transcript)
    review = create_pending_review(session, transcript.id, model_name="scripted")
    return review.id


def build_payload(quote: str, turn_index: int) -> dict:
    return {
        "praise": {
            "text": "开场清楚",
            "evidence_quote": "王总您好",
            "turn_index": 0,
        },
        "needs": [
            {"text": "客户关心供货保障", "evidence_quote": quote, "turn_index": turn_index}
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
        "script_demo": "销售：我先把排产给您确认一下。",
        "encouragement": "节奏稳。",
    }


def test_exact_quote_is_kept_with_reference(db_session):
    review_id = seed_review(db_session)
    client = ScriptedClient([build_payload("沙钢的货你们能保证吗？", 1)])

    review = execute_review(db_session, review_id, client)

    assert review.status == "done"
    assert review.dropped_evidence_count == 0
    assert review.confidence == "high"
    needs = review.content["needs"]
    assert len(needs) == 1
    assert needs[0]["match_type"] == "exact"
    assert needs[0]["turn_index"] == 1


def test_fuzzy_quote_above_threshold_is_kept(db_session):
    review_id = seed_review(db_session)
    client = ScriptedClient([build_payload("这批货价格可以再谈一谈", 2)])

    review = execute_review(db_session, review_id, client)

    assert review.status == "done"
    needs = review.content["needs"]
    assert len(needs) == 1
    assert needs[0]["match_type"] == "fuzzy"
    assert review.dropped_evidence_count == 0
    assert review.confidence == "high"


def test_fabricated_quote_is_dropped_and_lowered(db_session):
    from sqlalchemy import select

    from app.models.projection import Projection

    review_id = seed_review(db_session)
    client = ScriptedClient([build_payload("我们要签三年独家协议", 1)])

    review = execute_review(db_session, review_id, client)

    assert review.status == "done"
    assert review.dropped_evidence_count == 1
    assert review.confidence == "low"
    assert review.needs_spot_check is True
    assert review.content["needs"] == []

    rows = db_session.scalars(
        select(Projection).where(Projection.review_id == review_id)
    ).all()
    assert rows
    assert all(row.status == "pending" for row in rows)


def test_unreferenced_praise_falls_back_to_placeholder(db_session):
    payload = build_payload("沙钢的货你们能保证吗？", 1)
    payload["praise"] = {
        "text": "客户非常满意",
        "evidence_quote": "客户非常满意这次合作",
        "turn_index": 0,
    }
    review_id = seed_review(db_session)
    client = ScriptedClient([payload])

    review = execute_review(db_session, review_id, client)

    assert review.status == "done"
    assert review.content["praise"] == PLACEHOLDER_PRAISE


def test_invalid_json_is_retried_once_and_then_succeeds(db_session):
    review_id = seed_review(db_session)
    client = ScriptedClient(
        ["这不是 JSON", build_payload("沙钢的货你们能保证吗？", 1)]
    )

    review = execute_review(db_session, review_id, client)

    assert client.calls == 2
    assert review.status == "done"
    assert review.retry_count == 1


def test_json_wrapped_in_prose_is_rejected(db_session):
    payload = json.dumps(build_payload("沙钢的货你们能保证吗？", 1), ensure_ascii=False)
    review_id = seed_review(db_session)
    client = ScriptedClient([f"好的，结果如下：{payload}", payload])

    review = execute_review(db_session, review_id, client)

    assert client.calls == 2
    assert review.status == "done"
    assert review.retry_count == 1


def test_markdown_fenced_output_is_rejected(db_session):
    payload = json.dumps(build_payload("沙钢的货你们能保证吗？", 1), ensure_ascii=False)
    review_id = seed_review(db_session)
    client = ScriptedClient([f"```json\n{payload}\n```", payload])

    review = execute_review(db_session, review_id, client)

    assert client.calls == 2
    assert review.status == "done"
    assert review.retry_count == 1


def test_extra_top_level_field_is_rejected(db_session):
    """The seven-field contract is exact: a smuggle-in field is a failure."""
    bad = build_payload("沙钢的货你们能保证吗？", 1)
    bad["score"] = 5
    review_id = seed_review(db_session)
    client = ScriptedClient([bad, build_payload("沙钢的货你们能保证吗？", 1)])

    review = execute_review(db_session, review_id, client)

    assert client.calls == 2
    assert review.status == "done"
    assert review.retry_count == 1
    assert "score" not in review.content


def test_temperature_and_seed_are_fixed_regardless_of_env(db_session, monkeypatch):
    """Decision 4: reproducibility knobs are constants, not deployment settings."""
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    monkeypatch.setenv("LLM_SEED", "123")
    review_id = seed_review(db_session)

    review = db_session.get(Review, review_id)
    assert review is not None
    assert review.temperature == 0.0
    assert review.seed == 42


def test_two_invalid_outputs_end_in_error_state(db_session):
    review_id = seed_review(db_session)
    client = ScriptedClient(["{\"praise\": \"只有一段\"}", "还是不行"])

    review = execute_review(db_session, review_id, client)

    assert client.calls == 2
    assert review.status == "error"
    assert review.content == {}
    assert review.error_message
    assert review.confidence is None
