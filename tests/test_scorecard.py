"""Supervisor scorecards: separate write path, no leak into the review read path."""

from __future__ import annotations

from tests.conftest import create_transcript

RUBRIC_YAML = """
name: scorecard-rubric
dimensions:
  - key: discovery
    weight: 40
  - key: closing
    weight: 60
"""

YAML_HEADERS = {"content-type": "application/yaml"}


def _activate_rubric(client) -> int:
    imported = client.post("/rubrics/import", content=RUBRIC_YAML, headers=YAML_HEADERS)
    assert imported.status_code == 201, imported.text
    rubric_id = imported.json()["rubric_id"]
    activated = client.post(f"/rubrics/{rubric_id}/activate")
    assert activated.status_code == 200, activated.text
    return int(rubric_id)


def _finished_review(client) -> int:
    transcript_id = create_transcript(client)
    triggered = client.post(f"/transcripts/{transcript_id}/review")
    assert triggered.status_code == 200, triggered.text
    return int(triggered.json()["review_id"])


def test_scorecard_is_written_on_its_own_path(client):
    rubric_id = _activate_rubric(client)
    review_id = _finished_review(client)

    created = client.post(
        "/scorecards",
        json={
            "review_id": review_id,
            "rubric_id": rubric_id,
            "reviewer": "主管A",
            "dimension_scores": {"discovery": 8.0, "closing": 6.0},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["total_score"] == 6.8

    listed = client.get(f"/scorecards?review_id={review_id}").json()["items"]
    assert len(listed) == 1
    assert listed[0]["reviewer"] == "主管A"

    review = client.get(f"/reviews/{review_id}").json()
    assert "score" not in " ".join(review.keys()).lower()
    assert "total_score" not in review


def test_scoring_an_archived_rubric_is_rejected(client):
    first_rubric = _activate_rubric(client)
    second_import = client.post(
        "/rubrics/import",
        content=RUBRIC_YAML,
        headers=YAML_HEADERS,
    )
    second_rubric = second_import.json()["rubric_id"]
    client.post(f"/rubrics/{second_rubric}/activate")

    review_id = _finished_review(client)
    response = client.post(
        "/scorecards",
        json={
            "review_id": review_id,
            "rubric_id": first_rubric,
            "reviewer": "主管B",
            "dimension_scores": {"discovery": 5.0, "closing": 5.0},
        },
    )
    assert response.status_code == 409


def test_dimension_keys_must_match_the_active_rubric(client):
    rubric_id = _activate_rubric(client)
    review_id = _finished_review(client)

    response = client.post(
        "/scorecards",
        json={
            "review_id": review_id,
            "rubric_id": rubric_id,
            "reviewer": "主管C",
            "dimension_scores": {"discovery": 8.0},
        },
    )
    assert response.status_code == 422
