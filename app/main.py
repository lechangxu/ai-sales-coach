"""FastAPI application factory and route registration."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.reviews import router as reviews_router
from app.api.rubrics import router as rubrics_router
from app.api.scorecards import router as scorecards_router
from app.api.transcripts import router as transcripts_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Sales Coach",
        version="0.1.0",
        description=(
            "B2B call transcript -> evidence-bound review -> whitelisted CRM projection. "
            "Reviews carry no graded field; supervisor scoring is a separate table."
        ),
    )
    app.include_router(transcripts_router)
    app.include_router(reviews_router)
    app.include_router(rubrics_router)
    app.include_router(scorecards_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
