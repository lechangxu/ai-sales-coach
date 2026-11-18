"""LLM access layer.

Two backends share one interface:

* ``DeepSeekClient`` — real HTTP call to the DeepSeek chat completions API.
* ``StubLLMClient`` — deterministic, offline, no key. It reads the transcript
  that :mod:`app.services.review_service` embedded in the prompt and echoes
  verbatim slices, so the whole pipeline (including quote validation) can be
  exercised without any network access.

Tests never touch the network: they override :func:`provide_llm_client`.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from fastapi import HTTPException

from app.config import Settings, load_settings

SYSTEM_PROMPT = (
    "你是 B2B 建材销售团队的复盘教练。你只根据 <TRANSCRIPT> 内的通话原文工作："
    "任何结论都必须逐字引用原文，不许改写，不许推测原文之外的细节。"
    "只输出一个 JSON 对象，不要输出解释，不要输出 markdown 代码块。"
)

TRANSCRIPT_OPEN = "<TRANSCRIPT>"
TRANSCRIPT_CLOSE = "</TRANSCRIPT>"

_TURN_LINE = re.compile(r"^\[turn_index=(\d+)\] (.*)$")

STUB_MODEL_NAME = "stub-deterministic"


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class DeepSeekClient:
    """Direct DeepSeek client. One call per completion, no retry here."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        seed: int,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.model

    def complete(self, system: str, user: str) -> str:
        import httpx

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )
            response.raise_for_status()
            body = response.json()
        return body["choices"][0]["message"]["content"]


def parse_transcript_block(prompt: str) -> list[dict]:
    """Extract the ``[turn_index=N] speaker: text`` lines the prompt carries."""
    start = prompt.find(TRANSCRIPT_OPEN)
    end = prompt.find(TRANSCRIPT_CLOSE)
    if start < 0 or end < 0 or end <= start:
        return []
    body = prompt[start + len(TRANSCRIPT_OPEN) : end]
    turns: list[dict] = []
    for line in body.splitlines():
        match = _TURN_LINE.match(line.strip())
        if match is None:
            continue
        # Split on the first ": " only: a speaker name that itself contains a
        # colon (rare, but legal) must not make the whole turn vanish here.
        speaker, separator, text = match.group(2).partition(": ")
        if not separator:
            speaker, text = match.group(2), ""
        turns.append(
            {
                "turn_index": int(match.group(1)),
                "speaker": speaker.strip(),
                "text": text,
            }
        )
    return turns


def _quote_from(text: str, limit: int = 18) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped[:limit]


class StubLLMClient:
    """Deterministic offline backend producing a schema-valid seven-block JSON."""

    name = STUB_MODEL_NAME

    def complete(self, system: str, user: str) -> str:
        turns = parse_transcript_block(user)
        if not turns:
            # No transcript in the prompt means upstream data is broken. Return
            # something the schema check rejects, so the retry/error path takes
            # over instead of the stub inventing its own evidence.
            return "stub: empty transcript block"

        usable = [turn for turn in turns if str(turn["text"]).strip()]
        if not usable:
            # Same policy as an empty transcript block: never emit an item whose
            # evidence_quote is blank, let the retry/error path handle it.
            return "stub: no usable transcript turns"

        first = usable[0]
        second = usable[1] if len(usable) > 1 else usable[0]
        last = usable[-1]

        def item(turn: dict, text: str) -> dict:
            return {
                "text": text,
                "evidence_quote": _quote_from(str(turn["text"])),
                "turn_index": int(turn["turn_index"]),
            }

        payload = {
            "praise": item(first, "开场建立了基本信任：客户愿意说明进场时间"),
            "needs": [item(second, "客户的核心诉求需要被进一步量化确认")],
            "emotions": [item(second, "客户在议价环节表现出明显的谨慎情绪")],
            "weaknesses": [item(first, "开场提问过于宽泛，没有锁定具体项目节点")],
            "next_actions": [item(last, "下一次通话前准备好承兑贴息的具体测算")],
            "script_demo": "销售：这批 HRB400E 我可以按 12 天账期给您留货，您看 500 吨这个量能否先锁定？",
            "encouragement": "整体节奏稳，把需求量化再往前推一步即可。",
        }
        return json.dumps(payload, ensure_ascii=False)


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    resolved = settings or load_settings()
    if resolved.llm_backend == "stub":
        return StubLLMClient()
    if resolved.llm_backend == "deepseek":
        if not resolved.deepseek_api_key:
            raise RuntimeError("LLM_BACKEND=deepseek requires DEEPSEEK_API_KEY")
        return DeepSeekClient(
            api_key=resolved.deepseek_api_key,
            base_url=resolved.deepseek_base_url,
            model=resolved.model,
            temperature=resolved.temperature,
            seed=resolved.seed,
        )
    raise RuntimeError(f"unsupported LLM_BACKEND: {resolved.llm_backend}")


def provide_llm_client() -> LLMClient:
    """FastAPI dependency. Tests override this symbol to inject fakes.

    A misconfigured backend is a deployment problem, not a bug in this request:
    surface it as 503 with the reason, so the caller can tell "we forgot the
    key" apart from "the review code crashed".
    """
    try:
        return build_llm_client()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM backend is not usable: {exc}"
        ) from exc
