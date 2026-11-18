"""Request/response models shared by the API layer."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Turn(BaseModel):
    turn_index: int = Field(ge=0)
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)
    ts: str = ""


class TranscriptCreate(BaseModel):
    title: str = ""
    turns: list[Turn] = Field(min_length=1)
    is_synthetic: bool = False
    synthetic_profile: str | None = None

    @model_validator(mode="after")
    def _turns_must_be_unique_and_increasing(self) -> "TranscriptCreate":
        indexes = [turn.turn_index for turn in self.turns]
        if len(set(indexes)) != len(indexes):
            raise ValueError("turn_index must be unique")
        for previous, current in zip(indexes, indexes[1:]):
            if current <= previous:
                raise ValueError("turn_index must be strictly increasing")
        return self


class TranscriptCreated(BaseModel):
    transcript_id: int


class ReviewTriggered(BaseModel):
    review_id: int
    status: str = "pending"


class ScorecardCreate(BaseModel):
    review_id: int
    rubric_id: int
    reviewer: str = Field(min_length=1)
    dimension_scores: dict[str, float]


class ScorecardCreated(BaseModel):
    scorecard_id: int
    rubric_id: int
    total_score: float
