"""API routers."""

from app.api.reviews import router as reviews_router
from app.api.rubrics import router as rubrics_router
from app.api.scorecards import router as scorecards_router
from app.api.transcripts import router as transcripts_router

__all__ = [
    "reviews_router",
    "rubrics_router",
    "scorecards_router",
    "transcripts_router",
]
