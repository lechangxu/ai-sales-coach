"""Deterministic scorer contract: five dimension bands and the weighted total."""

from __future__ import annotations

from app.services.eval_scoring import (
    PLACEHOLDER_PRAISE,
    WEIGHTS,
    score_d1,
    score_d2,
    score_d3,
    score_d4,
    score_d5,
    score_review,
)


def item(text: str = "条目", quote: str = "原文引用", turn_index: int = 0) -> dict:
    return {"text": text, "evidence_quote": quote, "turn_index": turn_index}


def content(**overrides) -> dict:
    payload = {
        "praise": "开场建立了基本信任",
        "needs": [item("需求需要量化")],
        "emotions": [item("客户在议价环节谨慎")],
        "weaknesses": [item("开场提问过于宽泛")],
        "next_actions": [item("明天把测算发给客户")],
        "script_demo": "销售：我按 12 天账期给您留货。",
        "encouragement": "节奏稳，把需求量化再往前推一步。",
    }
    payload.update(overrides)
    return payload


def test_d1_empty_review_scores_one():
    empty = {
        "praise": "",
        "needs": [],
        "emotions": [],
        "weaknesses": [],
        "next_actions": [],
        "script_demo": "",
        "encouragement": "",
    }

    assert score_d1(empty) == 1
    assert score_review(empty)["D1"] == 1


def test_d1_density_bands():
    # 0 cited of 2 entries -> 1
    no_evidence = content(
        praise=PLACEHOLDER_PRAISE,
        needs=[item(quote="")],
        emotions=[],
        weaknesses=[],
        next_actions=[],
    )
    assert score_d1(no_evidence) == 1

    # 2 cited of 4 entries (0.5) -> 3
    half = content(
        praise="开场建立了基本信任",
        needs=[item(quote="引用一")],
        emotions=[item(quote="")],
        weaknesses=[item(quote="")],
        next_actions=[],
    )
    assert score_d1(half) == 3

    # 3 cited of 4 entries (0.75) -> 5
    mostly = content(
        praise="开场建立了基本信任",
        needs=[item(quote="引用一"), item(quote="引用二")],
        emotions=[item(quote="")],
        weaknesses=[],
        next_actions=[],
    )
    assert score_d1(mostly) == 5


def test_d2_count_to_score_bands():
    assert score_d2(content(needs=[])) == 1
    assert score_d2(content(needs=[item()])) == 3
    assert score_d2(content(needs=[item(), item()])) == 5
    assert score_d2(content(needs=[item(), item(), item()])) == 5


def test_d3_count_to_score_bands():
    assert score_d3(content(weaknesses=[])) == 1
    assert score_d3(content(weaknesses=[item()])) == 3
    assert score_d3(content(weaknesses=[item(), item()])) == 5


def test_d4_ratio_bands():
    assert score_d4(content(next_actions=[])) == 1
    assert score_d4(content(next_actions=[item(text="下周把条款过一遍")])) == 5
    assert score_d4(content(next_actions=[item(text="回头再聊")])) == 1
    # 1 of 2 actionable -> exactly 0.5 -> 3
    assert (
        score_d4(
            content(
                next_actions=[
                    item(text="明天发出合同草案"),
                    item(text="回头再聊"),
                ]
            )
        )
        == 3
    )
    # 2 of 3 above 0.5 -> 5
    assert (
        score_d4(
            content(
                next_actions=[
                    item(text="明天发出合同草案"),
                    item(text="本周确认 500 吨"),
                    item(text="回头再聊"),
                ]
            )
        )
        == 5
    )


def test_d5_missing_field_and_placeholder():
    assert score_d5(content()) == 5

    missing = content()
    missing.pop("script_demo")
    assert score_d5(missing) == 1

    assert score_d5(content(praise=PLACEHOLDER_PRAISE)) == 1
    assert score_d5(content(praise="")) == 1
    assert score_d5(content(needs=[])) == 1
    assert score_d5(content(encouragement="")) == 1


def test_total_is_weighted_round_within_1_5():
    full = content(
        needs=[item(), item()],
        weaknesses=[item(), item()],
        next_actions=[item(text="明天发合同")],
    )
    scores = score_review(full)
    expected = round(sum(WEIGHTS[name] * scores[name] for name in WEIGHTS))
    assert scores["total"] == expected == 5

    # D1=5 D2=3 D3=3 D4=1 D5=5 -> 0.3*5 + 0.2*3 + 0.2*3 + 0.2*1 + 0.1*5 = 3.4
    mid = score_review(
        content(next_actions=[item(text="回头再聊")])
    )
    assert (mid["D1"], mid["D2"], mid["D3"], mid["D4"], mid["D5"]) == (5, 3, 3, 1, 5)
    assert mid["total"] == round(
        sum(WEIGHTS[name] * mid[name] for name in WEIGHTS)
    ) == 3

    empty = score_review(
        {
            "praise": "",
            "needs": [],
            "emotions": [],
            "weaknesses": [],
            "next_actions": [],
            "script_demo": "",
            "encouragement": "",
        }
    )
    assert empty["total"] == 1
    assert all(1 <= empty[name] <= 5 for name in WEIGHTS)


def test_same_input_same_output():
    sample = content(needs=[item(), item()], next_actions=[item(text="月底锁量")])
    first = score_review(sample)
    second = score_review(dict(sample))

    assert first == second
    assert score_review(sample) == first


def test_praise_citation_follows_upstream_filter():
    """D1's "non-empty evidence_quote" test for praise is enforced upstream.

    ``review_service._praise_text`` keeps the praise text only when its quote
    resolves against a real turn and substitutes the placeholder otherwise, so
    the scorer reads the placeholder as "this compliment had no citation".
    If that upstream rule changes, this test fails before the eval silently
    starts crediting unreferenced praise.
    """
    from app.services.review_service import _filter_with_evidence

    turns = {0: "客户：我们二期厂房下周进场，预算两百万元。"}
    payload = {
        "praise": {
            "text": "开场建立了基本信任",
            "evidence_quote": "下周进场",
            "turn_index": 0,
        },
        "needs": [],
        "emotions": [],
        "weaknesses": [],
        "next_actions": [],
        "script_demo": "销售：我按 12 天账期给您留货。",
        "encouragement": "节奏稳。",
    }

    kept, _ = _filter_with_evidence(payload, turns)
    assert kept["praise"] != PLACEHOLDER_PRAISE
    assert score_d1(kept) == 5  # one entry, cited -> density 1.0

    payload["praise"]["evidence_quote"] = "这句话不在通话里"
    dropped, _ = _filter_with_evidence(payload, turns)
    assert dropped["praise"] == PLACEHOLDER_PRAISE
    assert score_d1(dropped) == 1  # one entry, nothing cited
