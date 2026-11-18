"""Review orchestration: prompt -> LLM -> schema check -> quote check -> store.

Failure policy is explicit: invalid output is retried exactly once, and a
second failure becomes ``status=error``. Nothing is silently backfilled.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import load_settings
from app.models.review import Review
from app.models.transcript import Transcript
from app.services.llm import SYSTEM_PROMPT, LLMClient
from app.services.projection import replace_projections
from app.services.quote_validation import classify_quote, validate_items

MAX_LLM_ATTEMPTS = 2
PLACEHOLDER_PRAISE = "本段暂未识别突出亮点"
# The whole payload contract, in one place: exactly these seven keys, no more.
REVIEW_FIELDS = (
    "praise",
    "needs",
    "emotions",
    "weaknesses",
    "next_actions",
    "script_demo",
    "encouragement",
)
EVIDENCE_FIELDS = ("needs", "emotions", "weaknesses", "next_actions")
TEXT_FIELDS = ("script_demo", "encouragement")
ITEM_FIELDS = ("text", "evidence_quote", "turn_index")


class InvalidLLMOutput(ValueError):
    """Raised when the model output is not a usable seven-block payload."""


def build_user_prompt(turns: list[dict]) -> str:
    lines = []
    for turn in turns:
        lines.append(
            f"[turn_index={int(turn['turn_index'])}] "
            f"{str(turn['speaker']).strip()}: {str(turn['text'])}"
        )
    body = "\n".join(lines)
    return (
        "<TRANSCRIPT>\n"
        f"{body}\n"
        "</TRANSCRIPT>\n\n"
        "请按下列结构输出一个 JSON 对象（七个顶层字段，缺一不可）：\n"
        '{"praise": 条目, "needs": [条目], "emotions": [条目], '
        '"weaknesses": [条目], "next_actions": [条目], '
        '"script_demo": "字符串", "encouragement": "字符串"}\n'
        "每个条目必须是 "
        '{"text": "结论", "evidence_quote": "通话原文中的连续片段", "turn_index": 整数}。\n'
        "evidence_quote 必须逐字来自对应 turn 的原文，不得改写；weaknesses 最多 2 条；"
        "praise 也是条目，没有可引用的亮点时 evidence_quote 留空。"
    )


def create_pending_review(
    session: Session, transcript_id: int, model_name: str | None = None
) -> Review:
    settings = load_settings()
    review = Review(
        transcript_id=transcript_id,
        status="pending",
        seed=settings.seed,
        temperature=settings.temperature,
        model=model_name or settings.model,
        prompt_version=settings.prompt_version,
        content={},
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def extract_json_object(raw: str) -> dict:
    """Parse the whole response as exactly one JSON object.

    Nothing is trimmed, unwrapped or repaired. Prose around the object and
    markdown fences are both rejected: the prompt asks for a bare JSON object,
    and every deviation from that is a contract violation the retry exists to
    catch — quietly cutting it out would hide a model that ignores instructions.
    """
    text = (raw or "").strip()
    if not text.startswith("{"):
        raise InvalidLLMOutput("model output is not a bare JSON object")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMOutput(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidLLMOutput("model output is not a JSON object")
    return parsed


def _validate_item(entry: object, field_name: str) -> dict:
    if not isinstance(entry, dict):
        raise InvalidLLMOutput(f"field {field_name} entries must be objects")
    extra = set(entry) - set(ITEM_FIELDS)
    if extra:
        raise InvalidLLMOutput(
            f"field {field_name} entry has unexpected keys: {sorted(extra)}"
        )
    text = entry.get("text")
    quote = entry.get("evidence_quote")
    turn_index = entry.get("turn_index")
    if not isinstance(text, str) or not isinstance(quote, str):
        raise InvalidLLMOutput(f"field {field_name} entry needs text/evidence_quote")
    if isinstance(turn_index, bool) or not isinstance(turn_index, int):
        raise InvalidLLMOutput(f"field {field_name} entry needs integer turn_index")
    return {"text": text, "evidence_quote": quote, "turn_index": turn_index}


def validate_payload(raw_object: dict) -> dict:
    """Strict seven-block check. Missing or extra fields are both fatal.

    ``praise`` is an evidence item too (decision 6): an unreferenced compliment
    is exactly the kind of conclusion this product refuses to state.
    """
    extra = set(raw_object) - set(REVIEW_FIELDS)
    if extra:
        raise InvalidLLMOutput(f"model output has unexpected fields: {sorted(extra)}")
    missing = set(REVIEW_FIELDS) - set(raw_object)
    if missing:
        raise InvalidLLMOutput(f"model output is missing fields: {sorted(missing)}")

    checked: dict = {"praise": _validate_item(raw_object["praise"], "praise")}

    for field_name in TEXT_FIELDS:
        value = raw_object.get(field_name)
        if not isinstance(value, str):
            raise InvalidLLMOutput(f"field {field_name} must be a string")
        checked[field_name] = value

    for field_name in EVIDENCE_FIELDS:
        value = raw_object.get(field_name)
        if not isinstance(value, list):
            raise InvalidLLMOutput(f"field {field_name} must be a list")
        checked[field_name] = [
            _validate_item(entry, field_name) for entry in value
        ]

    if len(checked["weaknesses"]) > 2:
        raise InvalidLLMOutput("weaknesses must contain at most 2 items")
    return checked


def _praise_text(praise: dict, turns_by_index: dict[int, str]) -> str:
    """Praise is kept only when its quote resolves; otherwise the placeholder.

    An unreferenced compliment is not counted as a dropped *item* (decision 6
    reserves that counter for evidence entries), it is replaced.
    """
    text = str(praise.get("text") or "").strip()
    quote = str(praise.get("evidence_quote") or "").strip()
    source = turns_by_index.get(int(praise.get("turn_index", -1)))
    if not text or not quote or source is None:
        return PLACEHOLDER_PRAISE
    if classify_quote(quote, source) is None:
        return PLACEHOLDER_PRAISE
    return text


def _filter_with_evidence(
    payload: dict, turns_by_index: dict[int, str]
) -> tuple[dict, int]:
    content: dict = {
        "praise": _praise_text(payload["praise"], turns_by_index),
        "script_demo": payload["script_demo"].strip(),
        "encouragement": payload["encouragement"].strip(),
    }
    dropped_total = 0
    for field_name in EVIDENCE_FIELDS:
        kept, dropped = validate_items(payload[field_name], turns_by_index)
        content[field_name] = kept
        dropped_total += dropped
    return content, dropped_total


def execute_review(
    session: Session, review_id: int, client: LLMClient
) -> Review | None:
    """Run or re-run a review row. Safe to call from a background task."""
    review = session.get(Review, review_id)
    if review is None:
        return None

    transcript = session.get(Transcript, review.transcript_id)
    if transcript is None:
        review.status = "error"
        review.error_message = "transcript missing"
        session.commit()
        return review

    turns = list(transcript.turns or [])
    turns_by_index = {int(turn["turn_index"]): str(turn["text"]) for turn in turns}
    user_prompt = build_user_prompt(turns)

    payload: dict | None = None
    attempts = 0
    last_error = ""
    while attempts < MAX_LLM_ATTEMPTS and payload is None:
        attempts += 1
        try:
            raw = client.complete(SYSTEM_PROMPT, user_prompt)
            payload = validate_payload(extract_json_object(raw))
        except Exception as exc:  # transport errors and schema errors share the retry path
            payload = None
            last_error = f"{type(exc).__name__}: {exc}"

    if payload is None:
        review.status = "error"
        review.retry_count = attempts
        review.confidence = None
        review.needs_spot_check = False
        review.dropped_evidence_count = 0
        review.content = {}
        review.error_message = last_error[:500]
        session.commit()
        session.refresh(review)
        return review

    content, dropped = _filter_with_evidence(payload, turns_by_index)
    review.content = content
    review.status = "done"
    review.confidence = "low" if dropped else "high"
    review.needs_spot_check = dropped > 0
    review.dropped_evidence_count = dropped
    review.retry_count = attempts - 1
    review.error_message = None
    session.flush()

    replace_projections(session, review.id, content, review.confidence, turns_by_index)
    session.commit()
    session.refresh(review)
    return review


def serialize_review(review: Review) -> dict:
    content = review.content or {}
    return {
        "review_id": review.id,
        "transcript_id": review.transcript_id,
        "status": review.status,
        "confidence": review.confidence,
        "dropped_evidence_count": review.dropped_evidence_count,
        "seed": review.seed,
        "temperature": review.temperature,
        "model": review.model,
        "prompt_version": review.prompt_version,
        "retry_count": review.retry_count,
        "needs_spot_check": review.needs_spot_check,
        "praise": content.get("praise", ""),
        "needs": content.get("needs", []),
        "emotions": content.get("emotions", []),
        "weaknesses": content.get("weaknesses", []),
        "next_actions": content.get("next_actions", []),
        "script_demo": content.get("script_demo", ""),
        "encouragement": content.get("encouragement", ""),
    }
