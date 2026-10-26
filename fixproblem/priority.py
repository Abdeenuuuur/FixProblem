"""Priority score: which issue should the business fix first?

    raw = volume_28d x severity(category) x mean_tier_weight x (1 + log2(max(growth, 1)))

Scores are scaled so the top issue is 100. Severity and tier weights live in
config.py so the business can tune them without changing code.
"""
from __future__ import annotations

import math


def raw_priority(volume_28d: int, severity: float, mean_tier_weight: float, growth: float) -> float:
    if volume_28d < 0 or severity < 0 or mean_tier_weight < 0:
        raise ValueError("inputs must be non-negative")
    return volume_28d * severity * mean_tier_weight * (1 + math.log2(max(growth, 1.0)))


def scale(scores: list[float]) -> list[float]:
    top = max(scores, default=0)
    return [round(100 * s / top, 1) if top > 0 else 0.0 for s in scores]
