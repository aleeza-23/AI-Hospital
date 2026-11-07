"""Triage metrics (G1): exact accuracy, quadratic weighted kappa, under/over-triage.

Under-triage = predicted level is LESS urgent than the reference (higher number).
This is the dangerous error, reported overall and on high-acuity (level 1-2) cases.
"""
from __future__ import annotations

from collections.abc import Sequence

LEVELS = (1, 2, 3, 4, 5)


def quadratic_weighted_kappa(y_true: Sequence[int], y_pred: Sequence[int], levels=LEVELS) -> float:
    k, n = len(levels), len(y_true)
    if n == 0:
        return 0.0
    idx = {lv: i for i, lv in enumerate(levels)}
    obs = [[0.0] * k for _ in range(k)]
    for t, p in zip(y_true, y_pred):
        obs[idx[t]][idx[p]] += 1
    hist_t = [sum(row) for row in obs]
    hist_p = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / (k - 1) ** 2
            num += w * obs[i][j]
            den += w * hist_t[i] * hist_p[j] / n
    return 1.0 - num / den if den else 1.0


def triage_metrics(y_true: Sequence[int], y_pred: Sequence[int]) -> dict[str, float]:
    n = len(y_true)
    pairs = list(zip(y_true, y_pred))
    high = [(t, p) for t, p in pairs if t <= 2]
    rate = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0  # noqa: E731
    return {
        "n": n,
        "n_high_acuity": len(high),
        "accuracy": rate([t == p for t, p in pairs]),
        "qwk": round(quadratic_weighted_kappa(y_true, y_pred), 4),
        "under_triage": rate([p > t for t, p in pairs]),
        "over_triage": rate([p < t for t, p in pairs]),
        "under_triage_high_acuity": rate([p > t for t, p in high]),
        "within_one_level": rate([abs(p - t) <= 1 for t, p in pairs]),
    }
