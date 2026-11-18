"""Transcript import validation."""

from __future__ import annotations

from tests.conftest import create_transcript, transcript_payload


def test_transcript_import_and_read_back(client):
    transcript_id = create_transcript(client)

    fetched = client.get(f"/transcripts/{transcript_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["transcript_id"] == transcript_id
    assert len(body["turns"]) == 3
    assert body["turns"][0]["turn_index"] == 0
    assert body["turns"][-1]["turn_index"] == 2


def test_empty_turns_are_rejected(client):
    response = client.post("/transcripts", json={"turns": []})
    assert response.status_code == 422


def test_duplicate_turn_index_is_rejected(client):
    payload = transcript_payload(
        turns=[
            {"turn_index": 0, "speaker": "销售", "text": "第一句"},
            {"turn_index": 0, "speaker": "客户", "text": "第二句"},
        ]
    )
    response = client.post("/transcripts", json=payload)
    assert response.status_code == 422


def test_non_increasing_turn_index_is_rejected(client):
    payload = transcript_payload(
        turns=[
            {"turn_index": 2, "speaker": "销售", "text": "第一句"},
            {"turn_index": 1, "speaker": "客户", "text": "第二句"},
        ]
    )
    response = client.post("/transcripts", json=payload)
    assert response.status_code == 422


def test_review_trigger_unknown_transcript_is_404(client):
    response = client.post("/transcripts/9999/review")
    assert response.status_code == 404


def test_missing_deepseek_key_is_a_503_not_a_500(client, monkeypatch):
    """Misconfiguration must be visible as such, not as an unhandled crash."""
    transcript_id = create_transcript(client)
    monkeypatch.setenv("LLM_BACKEND", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    response = client.post(f"/transcripts/{transcript_id}/review")

    assert response.status_code == 503
    assert "DEEPSEEK_API_KEY" in response.json()["detail"]


def test_background_job_failure_marks_the_review_as_error(client, monkeypatch):
    """A crash outside execute_review's own guards must not leave the row pending."""
    from app.api import transcripts as transcripts_api
    from app.db import get_session_factory
    from app.models.review import Review

    transcript_id = create_transcript(client)

    def crash(session, review_id, llm_client):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(transcripts_api, "execute_review", crash)
    review_id = client.post(f"/transcripts/{transcript_id}/review").json()["review_id"]

    session = get_session_factory()()
    try:
        review = session.get(Review, review_id)
        assert review is not None
        session.refresh(review)
        assert review.status == "error"
        assert review.error_message
    finally:
        session.close()
