"""QWK implementation against hand-computable cases."""

from __future__ import annotations

import pytest

from app.services.eval_metrics import (
    EVAL_LABELS,
    confusion_matrix,
    quadratic_weighted_kappa,
)


def test_perfect_agreement_is_one():
    actual = [1, 2, 3, 4, 5, 3, 3]
    assert quadratic_weighted_kappa(actual, list(actual)) == pytest.approx(1.0)


def test_complete_disagreement_is_negative():
    # Gold {1,1,5,5} predicted {4,4,2,2}: every call lands away from its own
    # gold, so agreement is below chance: kappa = -0.8 exactly.
    assert quadratic_weighted_kappa([1, 1, 5, 5], [4, 4, 2, 2]) == pytest.approx(-0.8)


def test_hand_computed_confusion_matches():
    actual = [1, 2, 2, 5]
    predicted = [1, 2, 5, 5]

    matrix = confusion_matrix(actual, predicted)

    assert matrix == [
        [1, 0, 0, 0, 0],
        [0, 1, 0, 0, 1],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 1],
    ]
    # Three exact hits out of four: kappa stays high but below 1.
    assert 0.5 < quadratic_weighted_kappa(actual, predicted) < 1.0


def test_degenerate_single_label_is_perfect():
    assert quadratic_weighted_kappa([3, 3, 3], [3, 3, 3]) == pytest.approx(1.0)


def test_labels_outside_range_rejected():
    with pytest.raises(ValueError):
        quadratic_weighted_kappa([1, 6], [1, 5], EVAL_LABELS)
