"""Human calibration sheet read-back: blank rows skip, bad values fail loud."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_human  # noqa: E402

HEADER = "transcript_id,title,human_score,note\n"


def write_sheet(tmp_path: pathlib.Path, rows: list[str]) -> str:
    path = tmp_path / "calib.csv"
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return str(path)


def test_blank_scores_are_skipped(tmp_path):
    path = write_sheet(
        tmp_path,
        [
            "1,滨江商务中心·good 通话,4,顺利\n",
            "2,临港物流园·mid 通话,,\n",
            "3,城东安置房·bad 通话,2,\n",
        ],
    )

    pairs = eval_human.read_scores(path)

    assert pairs == [(1, 4), (3, 2)]


def test_out_of_range_score_rejected(tmp_path):
    for bad in ("-1", "0", "6"):
        path = write_sheet(tmp_path, [f"1,通话,{bad},\n"])
        with pytest.raises(eval_human.ScoreSheetError):
            eval_human.read_scores(path)


def test_non_numeric_score_rejected(tmp_path):
    for bad in ("abc", "3.5"):
        path = write_sheet(tmp_path, [f"1,通话,{bad},\n"])
        with pytest.raises(eval_human.ScoreSheetError):
            eval_human.read_scores(path)


def test_no_scored_rows_errors(tmp_path):
    path = write_sheet(tmp_path, ["1,通话,,\n", "2,通话,,\n"])

    with pytest.raises(eval_human.NoScorableRows):
        eval_human.read_scores(path)


def test_qwk_reported():
    perfect = eval_human.summarize([1, 3, 5], [1, 3, 5])
    assert perfect == {"n": 3, "human_qwk": pytest.approx(1.0)}

    shifted = eval_human.summarize([1, 1, 5, 5], [3, 3, 3, 3])
    assert shifted["n"] == 4
    assert -1.0 <= shifted["human_qwk"] < 1.0

    with pytest.raises(eval_human.NoScorableRows):
        eval_human.summarize([], [])
