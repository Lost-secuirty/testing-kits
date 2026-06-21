"""test_session_test_harness.py — unittest suite."""

import unittest

from harnesses.security.session_test_harness import (
    CSRFTokenValidator,
    SessionFixationChecker,
    SessionIdEntropyChecker,
    SessionTimeoutChecker,
    list_scenarios,
    run_all_scenarios,
)


class TestFixation(unittest.TestCase):
    def setUp(self):
        self.c = SessionFixationChecker()

    def test_rotated_ok(self):
        self.assertFalse(self.c.check("old", "new", True)[0])

    def test_fixation_flagged(self):
        self.assertTrue(self.c.check("same", "same", True)[0])

    def test_no_priv_change_ok(self):
        self.assertFalse(self.c.check("same", "same", False)[0])


class TestCSRF(unittest.TestCase):
    def setUp(self):
        self.c = CSRFTokenValidator()

    def test_valid(self):
        self.assertFalse(self.c.validate("t", "t")[0])

    def test_missing(self):
        self.assertTrue(self.c.validate("", "t")[0])

    def test_mismatch(self):
        self.assertTrue(self.c.validate("x", "t")[0])

    def test_no_bound_token(self):
        self.assertTrue(self.c.validate("t", "")[0])


class TestEntropy(unittest.TestCase):
    def setUp(self):
        self.c = SessionIdEntropyChecker()

    def test_strong(self):
        self.assertFalse(self.c.check("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")[0])

    def test_short(self):
        self.assertTrue(self.c.check("abc")[0])

    def test_constant(self):
        self.assertTrue(self.c.check("a" * 40)[0])


class TestTimeout(unittest.TestCase):
    def setUp(self):
        self.c = SessionTimeoutChecker()

    def test_within(self):
        self.assertFalse(self.c.check(1000, 1500, 3600)[0])

    def test_expired(self):
        self.assertTrue(self.c.check(1000, 99999, 3600)[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 11)


if __name__ == "__main__":
    unittest.main()
