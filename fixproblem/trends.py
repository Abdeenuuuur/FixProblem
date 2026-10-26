"""Emerging-issue detection.

For each issue we compare the number of tickets in the most recent window
(default 14 days) with what the preceding baseline (default 8 weeks) predicts.

    expected = baseline_tickets / baseline_weeks * recent_days / 7
    growth   = (recent + 1) / (expected + 1)
    p_value  = P(X >= recent) where X ~ Poisson(expected)

An issue is flagged as emerging when growth >= 2, recent >= 10 tickets and
p_value <= 0.01 / number_of_issues_tested (Bonferroni correction: testing ~30
issues at once at p <= 0.01 would otherwise raise a false alarm by chance about
a quarter of the time). The +1 smoothing stops brand-new issues dividing by zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from scipy.stats import poisson


@dataclass
class Trend:
    recent: int
    baseline_weekly: float
    expected: float
    growth: float
    p_value: float
    emerging: bool


def compute_trend(timestamps: list[datetime], as_of: datetime, recent_days: int = 14,
                  baseline_weeks: int = 8, min_recent: int = 10, min_growth: float = 2.0,
                  max_p: float = 0.01) -> Trend:
    recent_start = as_of - timedelta(days=recent_days)
    base_start = recent_start - timedelta(weeks=baseline_weeks)
    recent = sum(1 for t in timestamps if recent_start < t <= as_of)
    base = sum(1 for t in timestamps if base_start < t <= recent_start)
    weekly = base / baseline_weeks
    expected = weekly * recent_days / 7
    growth = (recent + 1) / (expected + 1)
    p = float(poisson.sf(recent - 1, expected)) if expected > 0 else (0.0 if recent > 0 else 1.0)
    emerging = recent >= min_recent and growth >= min_growth and p <= max_p
    return Trend(recent, round(weekly, 2), round(expected, 2), round(growth, 2), p, emerging)


def weekly_counts(timestamps: list[datetime], as_of: datetime, weeks: int = 12) -> list[int]:
    """Ticket counts per week, oldest first, for sparkline charts."""
    counts = [0] * weeks
    for t in timestamps:
        age = (as_of - t).days // 7
        if 0 <= age < weeks:
            counts[weeks - 1 - age] += 1
    return counts
