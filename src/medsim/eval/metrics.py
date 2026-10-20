"""Aggregate EpisodeResults into RunMetrics."""
from __future__ import annotations

import math
from collections.abc import Sequence

from medsim.contracts import EpisodeResult, EpisodeStatus, RunMetrics, Usage


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval; better than normal approx. for small n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def compute_metrics(run_name: str, results: Sequence[EpisodeResult]) -> RunMetrics:
    n = len(results)
    k = sum(r.correct for r in results)
    per_role: dict[str, Usage] = {}
    for r in results:
        for role, u in r.usage.items():
            per_role.setdefault(role, Usage()).add(u)
    total = Usage()
    for u in per_role.values():
        total.add(u)
    avg = lambda xs: round(sum(xs) / n, 3) if n else 0.0  # noqa: E731
    return RunMetrics(
        run_name=run_name,
        num_cases=n,
        accuracy=round(k / n, 4) if n else 0.0,
        accuracy_ci95=wilson_ci(k, n),
        num_correct=k,
        avg_turns=avg([r.num_turns for r in results]),
        avg_tests=avg([r.num_tests for r in results]),
        timeout_rate=avg([r.status == EpisodeStatus.TIMEOUT for r in results]),
        error_rate=avg([r.status == EpisodeStatus.ERROR for r in results]),
        total_prompt_tokens=total.prompt_tokens,
        total_completion_tokens=total.completion_tokens,
        total_latency_s=round(total.latency_s, 2),
        per_role_usage=per_role,
    )
