"""test_unbounded_consumption_test_harness.py — unittest suite."""

import unittest

from harnesses.ai.unbounded_consumption_test_harness import (
    CostCeiling,
    LoopGuard,
    TokenBudget,
    list_scenarios,
    run_all_scenarios,
)


class TestTokenBudget(unittest.TestCase):
    def setUp(self):
        self.b = TokenBudget()

    def test_request_within_cap(self):
        self.assertFalse(self.b.check_request(500, 4000)[0])

    def test_oversized_request_flagged(self):
        self.assertTrue(self.b.check_request(50000, 4000)[0])

    def test_request_at_cap_ok(self):
        self.assertFalse(self.b.check_request(4000, 4000)[0])

    def test_window_under_cap(self):
        for i in range(2):
            self.b.record(1000 + i, 1000)
        self.assertFalse(self.b.over_window(1001, 60, 10000)[0])

    def test_window_flood_flagged(self):
        for i in range(5):
            self.b.record(1000 + i, 3000)
        self.assertTrue(self.b.over_window(1004, 60, 10000)[0])


class TestLoopGuard(unittest.TestCase):
    def setUp(self):
        self.g = LoopGuard()

    def test_no_loop(self):
        self.assertFalse(self.g.repeated_output(["a", "b", "c"])[0])

    def test_repeated_output_flagged(self):
        self.assertTrue(self.g.repeated_output(["x", "x", "x"])[0])

    def test_shallow_depth_ok(self):
        self.assertFalse(self.g.check_depth(3, 10)[0])

    def test_deep_recursion_flagged(self):
        self.assertTrue(self.g.check_depth(50, 10)[0])


class TestCostCeiling(unittest.TestCase):
    def setUp(self):
        self.c = CostCeiling()

    def test_within_ceiling(self):
        self.assertFalse(self.c.check(1.5, 10.0)[0])

    def test_overrun_flagged(self):
        self.assertTrue(self.c.check(100.0, 10.0)[0])

    def test_at_ceiling_ok(self):
        self.assertFalse(self.c.check(10.0, 10.0)[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 10)


if __name__ == "__main__":
    unittest.main()
