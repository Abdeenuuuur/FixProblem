import unittest
from datetime import datetime, timedelta

from tests.helpers import SAMPLE  # noqa: F401
from fixproblem.priority import raw_priority, scale
from fixproblem.trends import compute_trend, weekly_counts

AS_OF = datetime(2026, 6, 1)


def stamps(per_week_baseline: int, recent: int):
    out = []
    for w in range(8):
        week_start = AS_OF - timedelta(days=14) - timedelta(weeks=w + 1)
        out += [week_start + timedelta(hours=1 + 10 * i) for i in range(per_week_baseline)]
    out += [AS_OF - timedelta(days=13) + timedelta(hours=5 * i) for i in range(recent)]
    return out


class TrendTests(unittest.TestCase):
    def test_expected_and_growth_calculation(self):
        tr = compute_trend(stamps(5, 30), AS_OF)
        self.assertEqual(tr.baseline_weekly, 5)
        self.assertEqual(tr.expected, 10)                     # 5/week x 2 weeks
        self.assertAlmostEqual(tr.growth, 31 / 11, places=2)  # (30+1)/(10+1)
        self.assertTrue(tr.emerging)

    def test_stable_volume_is_not_emerging(self):
        tr = compute_trend(stamps(5, 11), AS_OF)
        self.assertFalse(tr.emerging)

    def test_small_spike_below_minimum_count_is_ignored(self):
        tr = compute_trend(stamps(0, 6), AS_OF)   # big growth but only 6 tickets
        self.assertGreater(tr.growth, 2)
        self.assertFalse(tr.emerging)

    def test_brand_new_issue_does_not_divide_by_zero(self):
        tr = compute_trend(stamps(0, 25), AS_OF)
        self.assertEqual(tr.expected, 0)
        self.assertEqual(tr.growth, 26.0)
        self.assertTrue(tr.emerging)

    def test_weekly_counts_order(self):
        counts = weekly_counts([AS_OF - timedelta(days=1), AS_OF - timedelta(days=8)], AS_OF, weeks=3)
        self.assertEqual(counts, [0, 1, 1])


class PriorityTests(unittest.TestCase):
    def test_growth_increases_priority(self):
        self.assertGreater(raw_priority(10, 1.0, 1.0, 4.0), raw_priority(10, 1.0, 1.0, 1.0))

    def test_declining_issue_not_penalised_below_volume(self):
        self.assertEqual(raw_priority(10, 1.0, 1.0, 0.3), 10)

    def test_negative_inputs_rejected(self):
        with self.assertRaises(ValueError):
            raw_priority(-1, 1, 1, 1)

    def test_scale_top_is_100_and_handles_zero(self):
        self.assertEqual(scale([5, 10]), [50.0, 100.0])
        self.assertEqual(scale([0, 0]), [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
