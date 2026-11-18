"""End-to-end smoke: import -> trigger review -> read the seven blocks."""

from __future__ import annotations

from tests.conftest import create_transcript

SEVEN_BLOCKS = (
    "praise",
    "needs",
    "emotions",
    "weaknesses",
    "next_actions",
    "script_demo",
    "encouragement",
)
REVIEW_METADATA = (
    "status",
    "confidence",
    "dropped_evidence_count",
    "seed",
    "temperature",
    "model",
    "prompt_version",
)


def _keys_recursively(payload) -> list[str]:
    keys: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.append(str(key))
            keys.extend(_keys_recursively(value))
    elif isinstance(payload, list):
        for entry in payload:
            keys.extend(_keys_recursively(entry))
    return keys


def test_review_endpoint_returns_seven_blocks_and_no_grade(client):
    transcript_id = create_transcript(client)

    triggered = client.post(f"/transcripts/{transcript_id}/review")
    assert triggered.status_code == 200
    triggered_body = triggered.json()
    assert triggered_body["status"] == "pending"
    review_id = triggered_body["review_id"]

    response = client.get(f"/reviews/{review_id}")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "done"
    assert body["confidence"] in {"high", "low"}
    for field in SEVEN_BLOCKS:
        assert field in body, field
    for field in REVIEW_METADATA:
        assert field in body, field
    assert isinstance(body["dropped_evidence_count"], int)
    assert body["temperature"] == 0.0
    assert body["seed"] == 42
    assert body["prompt_version"] == "review-v1"

    leaked = [
        key
        for key in _keys_recursively(body)
        if "score" in key.lower() or "grade" in key.lower() or "rating" in key.lower()
    ]
    assert leaked == [], leaked
