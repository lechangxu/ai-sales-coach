"""Deterministic review-quality scorer for the W2 eval layer.

Pure stdlib, pure functions: the same seven-block review content always maps to
the same five dimension scores. No LLM takes part in scoring — an LLM grader
would make the eval unreproducible and would couple prompt drift to scorer
drift, which is exactly the variable the drift monitor needs held still.

Every constant below is the task-card contract
(``2026-11-18_ai-sales-coach-w2-eval``, decision 3). Changing a weight, a
threshold or the keyword tuple is a new card, not a tweak: a score is only
comparable to a baseline while the ruler that produced it is fixed.
"""

from __future__ import annotations

SCORER_VERSION = "eval-scorer-v1"

DIMENSIONS = ("D1", "D2", "D3", "D4", "D5")

WEIGHTS = {"D1": 0.3, "D2": 0.2, "D3": 0.2, "D4": 0.2, "D5": 0.1}

GOLD_MAP = {"good": 5, "mid": 3, "bad": 1}

# Key 2 stands for "two or more".
COUNT_TO_SCORE = {0: 1, 1: 3, 2: 5}

PLACEHOLDER_PRAISE = "本段暂未识别突出亮点"

ACTION_KEYWORDS = (
    "今天",
    "明天",
    "本周",
    "下周",
    "月底",
    "季度",
    "上午",
    "下午",
    "吨",
    "元",
    "万",
    "千",
    "%",
    "折",
    "个",
    "件",
    "采购",
    "老板",
    "经理",
    "技术",
    "财务",
)

CONTENT_FIELDS = (
    "praise",
    "needs",
    "emotions",
    "weaknesses",
    "next_actions",
    "script_demo",
    "encouragement",
)

ENTRY_FIELDS = ("praise", "needs", "emotions", "weaknesses", "next_actions")
LIST_FIELDS = ("needs", "emotions", "weaknesses", "next_actions")
TEXT_FIELDS = ("script_demo", "encouragement")


def _count_to_score(count: int) -> int:
    """Map an item count onto the 1/3/5 scale (``2`` means two or more)."""
    return COUNT_TO_SCORE[min(count, 2)]


def _ratio_to_score(ratio: float, total: int) -> int:
    """Band a density ratio: ``0`` -> 1, ``(0, 0.5]`` -> 3, ``> 0.5`` -> 5.

    A zero-entry review is the explicit empty branch: there is nothing to
    divide, and an empty review must score 1 on density rather than raise.
    """
    if total == 0 or ratio <= 0:
        return 1
    if ratio <= 0.5:
        return 3
    return 5


def _praise_entry(content: dict) -> tuple[str, bool] | None:
    """The praise item counts as a citation exactly when its quote survived.

    ``praise`` reaches this layer already filtered: ``review_service._praise_text``
    keeps the praise text only when its ``evidence_quote`` resolved against a
    real turn, and replaces it with ``PLACEHOLDER_PRAISE`` otherwise. So at the
    content layer "non-placeholder praise" *is* "praise carried a non-empty
    evidence_quote" — the card's D1 wording is evaluated upstream, not lost.
    An absent praise entry does not count toward the density denominator; a
    present one always counts, cited or not.
    """
    praise = str(content.get("praise") or "").strip()
    if not praise:
        return None
    return praise, praise != PLACEHOLDER_PRAISE


def _evidence_entries(content: dict) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    praise = _praise_entry(content)
    if praise is not None:
        entries.append(praise)
    for field_name in LIST_FIELDS:
        for item in content.get(field_name) or []:
            quote = str((item or {}).get("evidence_quote") or "").strip()
            entries.append((field_name, bool(quote)))
    return entries


def score_d1(content: dict) -> int:
    entries = _evidence_entries(content)
    cited = sum(1 for _, has_quote in entries if has_quote)
    total = len(entries)
    return _ratio_to_score(cited / total if total else 0.0, total)


def score_d2(content: dict) -> int:
    return _count_to_score(len(content.get("needs") or []))


def score_d3(content: dict) -> int:
    return _count_to_score(len(content.get("weaknesses") or []))


def score_d4(content: dict) -> int:
    actions = content.get("next_actions") or []
    total = len(actions)
    if total == 0:
        return _ratio_to_score(0.0, 0)
    actionable = 0
    for item in actions:
        text = str((item or {}).get("text") or "")
        if any(keyword in text for keyword in ACTION_KEYWORDS):
            actionable += 1
    return _ratio_to_score(actionable / total, total)


def score_d5(content: dict) -> int:
    """Structure completeness: seven fields, all non-empty, no placeholder praise."""
    if any(field_name not in content for field_name in CONTENT_FIELDS):
        return 1
    praise = str(content.get("praise") or "").strip()
    if not praise or praise == PLACEHOLDER_PRAISE:
        return 1
    for field_name in TEXT_FIELDS:
        if not str(content.get(field_name) or "").strip():
            return 1
    for field_name in LIST_FIELDS:
        if not (content.get(field_name) or []):
            return 1
    return 5


def score_review(content: dict) -> dict[str, int]:
    """Score one review's content. Returns D1-D5 plus the weighted total."""
    scores = {
        "D1": score_d1(content),
        "D2": score_d2(content),
        "D3": score_d3(content),
        "D4": score_d4(content),
        "D5": score_d5(content),
    }
    total = round(sum(WEIGHTS[name] * value for name, value in scores.items()))
    scores["total"] = max(1, min(5, int(total)))
    return scores


def gold_label(synthetic_profile: str | None) -> int | None:
    """Map a generator design tier to its ordinal gold label (``None`` if unknown)."""
    return GOLD_MAP.get(str(synthetic_profile or ""))
