"""Runtime configuration read from environment variables.

Every value here is a deterministic knob: the same transcript plus the same
knobs must produce a comparable review row, so the values are persisted with
each review instead of being read back at render time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/ai_sales_coach"
)
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
PROMPT_VERSION = "review-v1"
# Decision 4 (任务卡): every review call runs at temperature=0 with a fixed
# seed, and both are stored on the row. They are constants on purpose — an env
# knob would let a deployment silently break the comparability the field is
# there to guarantee.
FIXED_SEED = 42
FIXED_TEMPERATURE = 0.0


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_backend: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    model: str
    prompt_version: str
    seed: int
    temperature: float


def _resolve_backend() -> str:
    backend = os.environ.get("LLM_BACKEND", "").strip().lower()
    if backend:
        return backend
    return "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "stub"


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        llm_backend=_resolve_backend(),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        prompt_version=PROMPT_VERSION,
        seed=FIXED_SEED,
        temperature=FIXED_TEMPERATURE,
    )
