"""Metric anomaly detectors: EWMA-based z-score (agent) and a static threshold rule (baseline)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev

# Minimum sigma per unit so flat series (e.g. restarts=0) do not produce infinite z-scores.
SIGMA_FLOOR = {"ratio": 0.002, "count": 0.25, "ms": 3.0, "MiB": 3.0}


@dataclass
class Detection:
    index: int
    baseline: float
    observed: float
    score: float
    threshold: float


def ewma_zscore(values: list[float], unit: str = "", alpha: float = 0.2, threshold: float = 4.0,
                warmup: int = 15, consecutive: int = 2, direction: str = "up") -> Detection | None:
    """Flag the first point where |x - ewma_mean| / ewma_std exceeds `threshold` for `consecutive` points.

    The baseline is frozen while points look anomalous so a fault does not poison its own baseline.
    """
    if len(values) <= warmup + consecutive:
        return None
    mu = mean(values[:warmup])
    var = pstdev(values[:warmup]) ** 2
    floor = SIGMA_FLOOR.get(unit, 0.0)
    run_start: int | None = None
    run_len = 0
    for i in range(warmup, len(values)):
        x = values[i]
        sigma = max(math.sqrt(var), floor, abs(mu) * 0.02, 1e-9)
        z = (x - mu) / sigma
        if direction == "down":
            z = -z
        elif direction == "both":
            z = abs(z)
        if z >= threshold:
            if run_start is None:
                run_start = i
            run_len += 1
            if run_len >= consecutive:
                return Detection(index=run_start, baseline=round(mu, 4), observed=round(values[run_start], 4),
                                 score=round(z, 2), threshold=threshold)
            continue
        run_start, run_len = None, 0
        diff = x - mu
        mu += alpha * diff
        var = (1 - alpha) * (var + alpha * diff * diff)
    return None


def static_threshold(values: list[float], threshold: float, for_points: int) -> int | None:
    """Prometheus-style `expr > threshold for: N` rule. Returns the index at which the alert fires."""
    run = 0
    for i, v in enumerate(values):
        run = run + 1 if v > threshold else 0
        if run >= for_points:
            return i
    return None
