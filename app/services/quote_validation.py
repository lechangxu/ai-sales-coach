"""Evidence validation: an item survives only if its quote exists in the call.

Order is exact substring match first, then fuzzy match (>= 0.9) against the
sentence of that turn, otherwise the item is dropped and counted.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")


def sentence_segments(source: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(source) if part.strip()]
    if not parts:
        return [source.strip()] if source.strip() else []
    segments = list(parts)
    for index in range(len(parts) - 1):
        segments.append(parts[index] + parts[index + 1])
    return segments


def classify_quote(quote: str, source: str) -> str | None:
    """Return ``"exact"``, ``"fuzzy"`` or ``None`` (no defensible match)."""
    cleaned = (quote or "").strip()
    if not cleaned or not source:
        return None
    if cleaned in source:
        return "exact"
    best = 0.0
    for segment in sentence_segments(source):
        ratio = SequenceMatcher(None, cleaned, segment).ratio()
        if ratio > best:
            best = ratio
    if best >= FUZZY_THRESHOLD:
        return "fuzzy"
    return None


def validate_items(
    items: list[dict], turns_by_index: dict[int, str]
) -> tuple[list[dict], int]:
    """Keep only items whose quote resolves inside the cited turn."""
    kept: list[dict] = []
    dropped = 0
    for item in items:
        turn_index = int(item["turn_index"])
        source = turns_by_index.get(turn_index)
        if source is None:
            dropped += 1
            continue
        match_type = classify_quote(str(item["evidence_quote"]), source)
        if match_type is None:
            dropped += 1
            continue
        kept.append(
            {
                "text": str(item["text"]),
                "evidence_quote": str(item["evidence_quote"]),
                "turn_index": turn_index,
                "match_type": match_type,
            }
        )
    return kept, dropped
