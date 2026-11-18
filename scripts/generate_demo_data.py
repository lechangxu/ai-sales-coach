"""Generate the synthetic demo corpus (fictional B2B steel-trade calls).

Deterministic: the same ``--count`` and ``--seed`` always yield the same rows.
Every row is labelled ``is_synthetic=True`` so a demo call can never be
mistaken for a real customer conversation.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

TIER_WEIGHTS = {"good": 2, "mid": 5, "bad": 3}
DEFAULT_COUNT = 50
DEFAULT_SEED = 20261118

MEMBERS = ["小陈", "小李", "小周"]
CUSTOMERS = ["王总", "刘经理", "赵工", "孙总"]
PROJECTS = ["滨江商务中心", "临港物流园", "城东安置房", "高铁站前广场", "经开厂房二期"]

_BASE_TIME = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

DIALOGUES: dict[str, list[tuple[str, str]]] = {
    "good": [
        ("销售", "王总您好，我是{member}，上次您提到的{project}项目，HRB400E 的进场节点排到哪一周了？"),
        ("客户", "下个月中旬要首批，沙钢的货你们能保证吗？"),
        ("销售", "沙钢这周排产正常，{volume} 吨以内我可以给您留仓。您项目总用量大概多少？"),
        ("客户", "整个项目大概 1200 吨，分批提。承兑贴息你们怎么算？"),
        ("销售", "承兑贴息按 6 个月 3.2 个点算，比市场价低 0.4 个点，首批含税价我们再单谈。"),
        ("客户", "账期能不能做到 60 天？"),
        ("销售", "60 天我可以申请，前提是首批 {volume} 吨先锁量，我明天下午把合同草案发您。"),
        ("客户", "可以，你先发过来，我看完这周给你答复。"),
        ("销售", "好，明天上午我把草案和承兑贴息测算一起发您，周四我再跟您确认锁量数量。"),
    ],
    "mid": [
        ("销售", "王总您好，我是{member}，{project}这边 HRB400E 还有需求吗？"),
        ("客户", "有一点，沙钢的货你们什么价？"),
        ("销售", "含税 3800 左右，看量。"),
        ("客户", "承兑贴息能不能再让一点？"),
        ("销售", "可以让半个点，具体我再问问。"),
        ("客户", "那你问完给我说一声。"),
        ("销售", "好的。"),
        ("客户", "先这样吧。"),
        ("销售", "行，有消息我联系您。"),
    ],
    "bad": [
        ("销售", "王总，HRB400E 现在 3780，要不要？"),
        ("客户", "沙钢的还是别家的？"),
        ("销售", "都有，价格差不多。"),
        ("客户", "承兑贴息呢？"),
        ("销售", "老价格。"),
        ("客户", "行我知道了。"),
        ("销售", "有需要随时联系我。"),
        ("客户", "嗯。"),
        ("销售", "那我先挂了。"),
    ],
}


def tier_plan(count: int) -> dict[str, int]:
    total_weight = sum(TIER_WEIGHTS.values())
    plan = {
        tier: (count * weight) // total_weight for tier, weight in TIER_WEIGHTS.items()
    }
    remainder = count - sum(plan.values())
    order = list(TIER_WEIGHTS)
    index = 0
    while remainder > 0:
        plan[order[index % len(order)]] += 1
        remainder -= 1
        index += 1
    return plan


def build_turns(tier: str, rng: random.Random) -> list[dict]:
    project = rng.choice(PROJECTS)
    member = rng.choice(MEMBERS)
    customer = rng.choice(CUSTOMERS)
    volume = rng.choice([200, 300, 500, 800])
    turns: list[dict] = []
    for index, (speaker, template) in enumerate(DIALOGUES[tier]):
        text = template.format(project=project, member=member, customer=customer, volume=volume)
        turns.append(
            {
                "turn_index": index,
                "speaker": speaker,
                "text": text,
                "ts": (_BASE_TIME + timedelta(minutes=2 * index)).isoformat(),
            }
        )
    return turns


def build_dataset(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict]:
    rng = random.Random(seed)
    plan = tier_plan(count)
    records: list[dict] = []
    for tier in TIER_WEIGHTS:
        for _ in range(plan[tier]):
            records.append(
                {
                    "title": f"{rng.choice(PROJECTS)}·{tier} 通话",
                    "turns": build_turns(tier, rng),
                    "is_synthetic": True,
                    "synthetic_profile": tier,
                }
            )
    rng.shuffle(records)
    return records


def write_dataset(records: list[dict]) -> int:
    from sqlalchemy import delete

    from app.db import get_session_factory
    from app.models import Projection, Review, Scorecard, Transcript

    session = get_session_factory()()
    try:
        session.execute(delete(Scorecard))
        session.execute(delete(Projection))
        session.execute(delete(Review))
        session.execute(delete(Transcript))
        session.flush()
        for record in records:
            session.add(
                Transcript(
                    title=record["title"],
                    turns=record["turns"],
                    is_synthetic=record["is_synthetic"],
                    synthetic_profile=record["synthetic_profile"],
                )
            )
        session.commit()
    finally:
        session.close()
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--dry-run", action="store_true", help="build only, do not touch the database")
    parser.add_argument("--count-only", action="store_true", help="print the row count and exit")
    args = parser.parse_args(argv)

    if args.count < 1:
        print("--count must be >= 1", file=sys.stderr)
        return 2

    records = build_dataset(args.count, args.seed)
    plan = tier_plan(args.count)
    summary = " ".join(f"{tier}={plan[tier]}" for tier in TIER_WEIGHTS)

    if args.count_only:
        print(f"transcripts={len(records)} {summary}")
        return 0

    if args.dry_run:
        print(f"dry-run transcripts={len(records)} {summary}")
        print(f"sample_title={records[0]['title']}")
        return 0

    written = write_dataset(records)
    print(f"transcripts={written} {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
