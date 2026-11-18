"""Synthetic demo corpus: size, steel vocabulary, tier balance, labelling."""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_demo_data  # noqa: E402


def test_demo_dataset_shape():
    records = generate_demo_data.build_dataset(50, 20261118)

    assert len(records) == 50

    tiers = Counter(record["synthetic_profile"] for record in records)
    assert tiers["good"] == 10
    assert tiers["mid"] == 25
    assert tiers["bad"] == 15

    corpus = " ".join(
        turn["text"] for record in records for turn in record["turns"]
    )
    for term in ("HRB400E", "沙钢", "承兑贴息"):
        assert term in corpus, term

    assert all(record["is_synthetic"] is True for record in records)
    assert all(record["turns"] for record in records)
    assert all(
        [turn["turn_index"] for turn in record["turns"]]
        == list(range(len(record["turns"])))
        for record in records
    )


def test_demo_dataset_is_deterministic():
    first = generate_demo_data.build_dataset(12, 7)
    second = generate_demo_data.build_dataset(12, 7)
    assert first == second
