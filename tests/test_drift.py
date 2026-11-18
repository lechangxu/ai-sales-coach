"""Drift monitor: the snapshot is deterministic and every difference layer is red.

These tests are deliberately database-free — they exercise the pure snapshot
functions, so a red here always means the comparison logic is wrong, never that
postgres was down.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_qwk  # noqa: E402

DIMENSIONS = eval_qwk.REPORTED_DIMENSIONS


def make_rows(scores: list[dict] | None = None) -> list[dict]:
    """Three fake transcripts with distinct scores per dimension."""
    if scores is None:
        scores = [
            {"D1": 5, "D2": 3, "D3": 1, "D4": 3, "D5": 5, "total": 3},
            {"D1": 3, "D2": 5, "D3": 3, "D4": 5, "D5": 5, "total": 5},
            {"D1": 1, "D2": 1, "D3": 5, "D4": 1, "D5": 3, "total": 1},
        ]
    return [
        {
            "transcript_id": index + 1,
            "transcript_key": f"key-{index + 1:04d}",
            "gold": 5 - 2 * index if index < 3 else 3,
            "scores": item,
        }
        for index, item in enumerate(scores)
    ]


def make_report(rows: list[dict]) -> dict:
    totals = [row["scores"]["total"] for row in rows]
    return {
        "qwk_total": 0.0,
        "qwk_by_dimension": {dimension: 0.0 for dimension in eval_qwk.DIMENSIONS},
        "confusion": [
            [sum(1 for total in totals if total == label)] * 5 for label in eval_qwk.EVAL_LABELS
        ],
    }


def snapshot(rows: list[dict] | None = None) -> dict:
    rows = make_rows() if rows is None else rows
    return eval_qwk.build_snapshot(rows, make_report(rows))


def test_snapshot_roundtrip_is_deterministic(tmp_path: pathlib.Path):
    """Same rows -> byte-identical file, twice, in different directories."""
    rows = make_rows()
    first = tmp_path / "a.json"
    second = tmp_path / "nested" / "b.json"
    eval_qwk.write_snapshot(eval_qwk.build_snapshot(rows, make_report(rows)), str(first))
    eval_qwk.write_snapshot(eval_qwk.build_snapshot(rows, make_report(rows)), str(second))

    assert first.read_bytes() == second.read_bytes()
    assert first.read_text(encoding="utf-8").endswith("\n")


def test_snapshot_has_no_volatile_fields(tmp_path: pathlib.Path):
    """A timestamp or run id would make the baseline drift on its own."""
    rows = make_rows()
    path = tmp_path / "snap.json"
    eval_qwk.write_snapshot(eval_qwk.build_snapshot(rows, make_report(rows)), str(path))
    blob = path.read_text(encoding="utf-8")

    for volatile in ("timestamp", "created_at", "eval_run_id", "run_id"):
        assert volatile not in blob
    payload = json.loads(blob)
    assert payload["scorer_version"] == eval_qwk.SCORER_VERSION
    keys = [row["transcript_key"] for row in payload["rows"]]
    assert keys == sorted(keys)


def test_tampered_score_is_drift():
    """The anti-decoration check: a changed total must be reported."""
    baseline = snapshot()
    tampered = json.loads(json.dumps(baseline))
    tampered["rows"][0]["scores"]["total"] = 1 if tampered["rows"][0]["scores"]["total"] != 1 else 5

    diffs = eval_qwk.diff_snapshots(tampered, baseline)

    assert diffs
    assert any("total" in line for line in diffs)


def test_missing_row_is_drift():
    baseline = snapshot()
    current = json.loads(json.dumps(baseline))
    current["rows"] = current["rows"][:-1]

    diffs = eval_qwk.diff_snapshots(current, baseline)

    assert any(line.startswith("missing transcript") for line in diffs)


def test_extra_row_is_drift():
    baseline = snapshot()
    current = json.loads(json.dumps(baseline))
    extra = json.loads(json.dumps(current["rows"][0]))
    extra["transcript_key"] = "key-9999"
    current["rows"] = current["rows"] + [extra]

    diffs = eval_qwk.diff_snapshots(current, baseline)

    assert any(line.startswith("extra transcript") for line in diffs)


def test_distribution_counts_by_label():
    """Histograms are exact per dimension and never drive the exit code."""
    rows = make_rows()
    distribution = eval_qwk.score_distribution(rows)

    assert distribution["D1"] == {"1": 1, "2": 0, "3": 1, "4": 0, "5": 1}
    assert distribution["total"] == {"1": 1, "2": 0, "3": 1, "4": 0, "5": 1}
    assert sum(distribution["D2"].values()) == len(rows)
    # Identical rows must not register as a distribution change.
    assert distribution == eval_qwk.score_distribution(json.loads(json.dumps(rows)))


def test_tampered_gold_is_drift():
    baseline = snapshot()
    current = json.loads(json.dumps(baseline))
    current["rows"][0]["gold"] = 1 if current["rows"][0]["gold"] != 1 else 5

    diffs = eval_qwk.diff_snapshots(current, baseline)

    assert any("gold" in line for line in diffs)


def test_tampered_dimension_is_drift():
    baseline = snapshot()
    current = json.loads(json.dumps(baseline))
    current["rows"][1]["scores"]["D3"] = 1 if current["rows"][1]["scores"]["D3"] != 1 else 5

    diffs = eval_qwk.diff_snapshots(current, baseline)

    assert any("D3" in line for line in diffs)


def test_report_float_jitter_is_not_drift():
    """Decision 6: report floats compare at 6 decimals, so jitter is not drift."""
    baseline = snapshot()
    current = json.loads(json.dumps(baseline))
    current["report"]["qwk_total"] = baseline["report"]["qwk_total"] + 1e-9
    current["report"]["qwk_by_dimension"]["D2"] = -1e-9

    assert eval_qwk.diff_snapshots(current, baseline) == []


def test_tampered_report_is_drift():
    baseline = snapshot()
    current = json.loads(json.dumps(baseline))
    current["report"]["qwk_total"] = 0.5

    diffs = eval_qwk.diff_snapshots(current, baseline)

    assert any(line.startswith("report ") for line in diffs)
