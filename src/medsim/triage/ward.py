"""G1: Ward Manager, an acuity-ordered patient queue (deterministic, seeded).

Simulates a ward of N patients arriving over time and one doctor seeing them.
Policies:
  * fifo    - arrival order (MedAgentSim: patients go straight to the doctor)
  * acuity  - lowest assigned KTAS level first, ties by arrival; patients the
              nurse flagged with red flags are escalated one level while waiting
The key outcome is time-to-doctor for truly high-acuity (expert level 1-2) patients
and the rate of KTAS target-time violations.
"""
from __future__ import annotations

import heapq
import random
from statistics import mean
from typing import Optional

# KTAS maximum time-to-physician targets (minutes) and assumed consultation lengths.
TARGET_MIN = {1: 0, 2: 10, 3: 30, 4: 60, 5: 120}
SERVICE_MIN = {1: 30, 2: 25, 3: 20, 4: 12, 5: 8}


def simulate_ward(
    true_levels: list[int],
    assigned_levels: list[Optional[int]],
    red_flags: Optional[list[bool]] = None,
    policy: str = "acuity",
    mean_interarrival_min: float = 10.0,
    doctors: int = 2,
    seed: int = 0,
) -> dict[str, float]:
    """Patients are given in arrival order; arrival gaps are exponential (seeded)."""
    n = len(true_levels)
    red_flags = red_flags or [False] * n
    rng = random.Random(seed)
    arrivals, t = [], 0.0
    for _ in range(n):
        t += rng.expovariate(1.0 / mean_interarrival_min)
        arrivals.append(t)
    # Unassigned (failed) triage falls back to level 3, as a human would default.
    prio = []
    for lv, flag in zip(assigned_levels, red_flags):
        lv = lv or 3
        prio.append(max(1, lv - 1) if (flag and policy == "acuity") else lv)

    waits = [0.0] * n
    free_at = [0.0] * doctors  # min-heap of times each doctor becomes free
    heapq.heapify(free_at)
    i, queue = 0, []
    while i < n or queue:
        clock = heapq.heappop(free_at)
        if not queue and i < n and arrivals[i] > clock:
            clock = arrivals[i]
        while i < n and arrivals[i] <= clock:
            key = (prio[i], arrivals[i]) if policy == "acuity" else (arrivals[i],)
            heapq.heappush(queue, (key, i))
            i += 1
        _, j = heapq.heappop(queue)
        waits[j] = clock - arrivals[j]
        heapq.heappush(free_at, clock + SERVICE_MIN[true_levels[j]])

    high = [w for w, lv in zip(waits, true_levels) if lv <= 2]
    viol = [w > TARGET_MIN[lv] for w, lv in zip(waits, true_levels)]
    viol_high = [w > TARGET_MIN[lv] for w, lv in zip(waits, true_levels) if lv <= 2]
    return {
        "mean_wait_all_min": round(mean(waits), 2),
        "mean_wait_high_acuity_min": round(mean(high), 2) if high else 0.0,
        "max_wait_high_acuity_min": round(max(high), 2) if high else 0.0,
        "target_violation_rate": round(sum(viol) / n, 4),
        "target_violation_rate_high_acuity": round(sum(viol_high) / len(viol_high), 4) if viol_high else 0.0,
    }
