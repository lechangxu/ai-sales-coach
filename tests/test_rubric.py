"""Rubric import validation and the draft/active/archived state machine."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.rubric import Rubric

VALID_V1 = """
name: test-rubric
dimensions:
  - key: discovery
    weight: 40
  - key: closing
    weight: 60
"""

VALID_V2 = """
name: test-rubric
dimensions:
  - key: discovery
    weight: 50
  - key: closing
    weight: 50
"""

BAD_WEIGHTS = """
name: test-rubric
dimensions:
  - key: discovery
    weight: 40
  - key: closing
    weight: 50
"""

NAN_WEIGHT = """
name: test-rubric
dimensions:
  - key: discovery
    weight: .nan
  - key: closing
    weight: 100
"""

YAML_HEADERS = {"content-type": "application/yaml"}


def import_rubric(client, document: str) -> dict:
    response = client.post("/rubrics/import", content=document, headers=YAML_HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def test_weights_not_summing_to_100_are_rejected(client):
    response = client.post("/rubrics/import", content=BAD_WEIGHTS, headers=YAML_HEADERS)
    assert response.status_code == 422
    assert "weights sum" in response.json()["detail"]


def test_non_finite_weight_is_rejected(client):
    """`.nan` makes every comparison False — it must not slip past the sum check."""
    response = client.post("/rubrics/import", content=NAN_WEIGHT, headers=YAML_HEADERS)
    assert response.status_code == 422
    assert "finite" in response.json()["detail"]


def test_import_creates_a_draft(client):
    rubric = import_rubric(client, VALID_V1)
    assert rubric["status"] == "draft"
    assert rubric["version"] == 1
    assert rubric["weight_total"] == 100.0


def test_json_envelope_without_a_content_type_header_is_accepted(client):
    import json

    response = client.post("/rubrics/import", content=json.dumps({"yaml": VALID_V1}))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "draft"


def test_activating_a_draft_archives_the_previous_active(client):
    first = import_rubric(client, VALID_V1)
    first_active = client.post(f"/rubrics/{first['rubric_id']}/activate")
    assert first_active.status_code == 200
    assert first_active.json()["status"] == "active"

    second = import_rubric(client, VALID_V2)
    second_active = client.post(f"/rubrics/{second['rubric_id']}/activate")
    assert second_active.status_code == 200
    assert second_active.json()["status"] == "active"

    listing = client.get("/rubrics").json()
    statuses = {item["rubric_id"]: item["status"] for item in listing["items"]}
    assert statuses[first["rubric_id"]] == "archived"
    assert statuses[second["rubric_id"]] == "active"
    active_ids = [
        item["rubric_id"] for item in listing["items"] if item["status"] == "active"
    ]
    assert active_ids == [second["rubric_id"]]
    assert listing["active_rubric_id"] == second["rubric_id"]


def test_database_rejects_a_second_active_version(db_session):
    db_session.add(
        Rubric(
            name="db-guard",
            version=1,
            status="active",
            definition={"dimensions": []},
            weight_total=100.0,
        )
    )
    db_session.commit()
    db_session.add(
        Rubric(
            name="db-guard",
            version=2,
            status="active",
            definition={"dimensions": []},
            weight_total=100.0,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_activating_an_archived_version_is_rejected(client):
    first = import_rubric(client, VALID_V1)
    client.post(f"/rubrics/{first['rubric_id']}/activate")
    second = import_rubric(client, VALID_V2)
    client.post(f"/rubrics/{second['rubric_id']}/activate")

    response = client.post(f"/rubrics/{first['rubric_id']}/activate")
    assert response.status_code == 409
