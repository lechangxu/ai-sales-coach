"""Metrics for the eval layer: confusion matrix and quadratic weighted kappa.

Stdlib only (no sklearn/numpy/pandas): the whole point of this layer is that
the number it prints today is the number it prints next month. The kappa here
follows the standard definition — observed and expected quadratic disagreement
over the same label set — with weights ``1 - (i - j) ** 2 / (K - 1) ** 2``.
"""

from __future__ import annotations

EVAL_LABELS = (1, 2, 3, 4, 5)


def _validate(actual: list[int], predicted: list[int], labels: tuple[int, ...]) -> None:
    if len(actual) != len(predicted):
        raise ValueError(
            f"length mismatch: actual={len(actual)} predicted={len(predicted)}"
        )
    if not actual:
        raise ValueError("cannot score an empty label list")
    allowed = set(labels)
    for name, values in (("actual", actual), ("predicted", predicted)):
        bad = [value for value in values if value not in allowed]
        if bad:
            raise ValueError(f"{name} labels outside {sorted(allowed)}: {sorted(set(bad))}")


def confusion_matrix(
    actual: list[int],
    predicted: list[int],
    labels: tuple[int, ...] = EVAL_LABELS,
) -> list[list[int]]:
    """Rows = gold label, columns = predicted label, ordered by ``labels``."""
    _validate(actual, predicted, labels)
    index = {label: position for position, label in enumerate(labels)}
    matrix = [[0 for _ in labels] for _ in labels]
    for gold, guess in zip(actual, predicted):
        matrix[index[gold]][index[guess]] += 1
    return matrix


def quadratic_weighted_kappa(
    actual: list[int],
    predicted: list[int],
    labels: tuple[int, ...] = EVAL_LABELS,
) -> float:
    """Quadratic weighted kappa between two ordinal label lists.

    ``1.0`` = perfect agreement, ``0.0`` = chance, negative = worse than chance.
    A degenerate case where both lists are a single identical label is perfect
    agreement by construction (the expected disagreement is zero and observed is
    zero with it), so it returns ``1.0`` rather than dividing by zero.
    """
    _validate(actual, predicted, labels)
    index = {label: position for position, label in enumerate(labels)}
    size = len(labels)
    denominator = (size - 1) ** 2

    observed = [[0.0 for _ in labels] for _ in labels]
    actual_counts = [0.0 for _ in labels]
    predicted_counts = [0.0 for _ in labels]
    for gold, guess in zip(actual, predicted):
        observed[index[gold]][index[guess]] += 1.0
        actual_counts[index[gold]] += 1.0
        predicted_counts[index[guess]] += 1.0

    total = float(len(actual))
    observed_agreement = 0.0
    expected_agreement = 0.0
    for i in range(size):
        for j in range(size):
            weight = 1.0 - ((i - j) ** 2) / denominator
            observed_agreement += weight * observed[i][j]
            expected_agreement += weight * (
                actual_counts[i] * predicted_counts[j] / total
            )

    # kappa = (Po - Pe) / (1 - Pe), with both agreements weighted by
    # ``1 - (i - j) ** 2 / (K - 1) ** 2``. Equal weights cancel, so the
    # unnormalised sums are used directly.
    spread = total - expected_agreement
    if spread == 0.0:
        return 1.0 if observed_agreement == total else 0.0
    return (observed_agreement - expected_agreement) / spread
