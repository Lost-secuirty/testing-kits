"""test_rate_limit_test_harness.py — unittest suite."""

import unittest

from harnesses.security.rate_limit_test_harness import (
    BusinessRuleChecker,
    LockoutPolicy,
    ReplayGuard,
    SlidingWindowLimiter,
    list_scenarios,
    run_all_scenarios,
)


class TestSlidingWindowLimiter(unittest.TestCase):
    def test_under_limit_allowed(self):
        lim = SlidingWindowLimiter(10, 60)
        self.assertTrue(all(lim.allow("ip", 1000 + i) for i in range(5)))

    def test_over_limit_denied(self):
        lim = SlidingWindowLimiter(3, 60)
        for i in range(3):
            lim.allow("ip", 1000 + i)
        self.assertFalse(lim.allow("ip", 1001))

    def test_window_resets(self):
        lim = SlidingWindowLimiter(1, 60)
        self.assertTrue(lim.allow("ip", 1000))
        self.assertFalse(lim.allow("ip", 1001))
        self.assertTrue(lim.allow("ip", 2000))

    def test_per_key_isolation(self):
        lim = SlidingWindowLimiter(1, 60)
        self.assertTrue(lim.allow("a", 1000))
        self.assertTrue(lim.allow("b", 1000))


class TestLockoutPolicy(unittest.TestCase):
    def test_locks_at_threshold(self):
        lp = LockoutPolicy(5, 300)
        for i in range(5):
            lp.record_failure("u", 1000 + i)
        self.assertTrue(lp.is_locked("u", 1004))

    def test_below_threshold_unlocked(self):
        lp = LockoutPolicy(5, 300)
        for i in range(3):
            lp.record_failure("u", 1000 + i)
        self.assertFalse(lp.is_locked("u", 1002))

    def test_cooldown_expires(self):
        lp = LockoutPolicy(5, 300)
        for i in range(5):
            lp.record_failure("u", 1000 + i)
        self.assertFalse(lp.is_locked("u", 9000))

    def test_reset_clears(self):
        lp = LockoutPolicy(5, 300)
        for i in range(5):
            lp.record_failure("u", 1000 + i)
        lp.reset("u")
        self.assertFalse(lp.is_locked("u", 1004))


class TestBusinessRuleChecker(unittest.TestCase):
    def setUp(self):
        self.c = BusinessRuleChecker()

    def test_valid_quantity(self):
        self.assertFalse(self.c.check_quantity(3)[0])

    def test_negative_quantity(self):
        self.assertTrue(self.c.check_quantity(-1)[0])

    def test_zero_quantity(self):
        self.assertTrue(self.c.check_quantity(0)[0])

    def test_overflow_quantity(self):
        self.assertTrue(self.c.check_quantity(10_000_000)[0])

    def test_boolean_rejected(self):
        self.assertTrue(self.c.check_quantity(True)[0])

    def test_matching_price(self):
        self.assertFalse(self.c.check_price(100, 100)[0])

    def test_price_tampering(self):
        self.assertTrue(self.c.check_price(100, 1)[0])


class TestReplayGuard(unittest.TestCase):
    def test_first_use_ok(self):
        self.assertFalse(ReplayGuard().seen("n1"))

    def test_replay_rejected(self):
        rg = ReplayGuard()
        rg.seen("n1")
        self.assertTrue(rg.seen("n1"))


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 14)


if __name__ == "__main__":
    unittest.main()
