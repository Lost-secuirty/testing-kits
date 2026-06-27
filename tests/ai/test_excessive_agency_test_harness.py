"""test_excessive_agency_test_harness.py — unittest suite."""

import unittest

from harnesses.ai.excessive_agency_test_harness import (
    BlastRadiusLimiter,
    DestructiveActionGuard,
    ToolAllowlist,
    list_scenarios,
    run_all_scenarios,
)

ALLOWED = ["search", "read_file"]


class TestToolAllowlist(unittest.TestCase):
    def setUp(self):
        self.c = ToolAllowlist()

    def test_permitted(self):
        self.assertFalse(self.c.check("search", ALLOWED)[0])

    def test_unlisted_flagged(self):
        self.assertTrue(self.c.check("exec_shell", ALLOWED)[0])

    def test_empty_allowlist_blocks(self):
        self.assertTrue(self.c.check("search", [])[0])


class TestDestructiveActionGuard(unittest.TestCase):
    def setUp(self):
        self.c = DestructiveActionGuard()

    def test_confirmed_ok(self):
        self.assertFalse(self.c.check("delete_user", confirmed=True)[0])

    def test_unconfirmed_flagged(self):
        self.assertTrue(self.c.check("delete_user")[0])

    def test_transfer_flagged(self):
        self.assertTrue(self.c.check("transfer_funds")[0])

    def test_nondestructive_ok(self):
        self.assertFalse(self.c.check("read_file")[0])


class TestBlastRadiusLimiter(unittest.TestCase):
    def setUp(self):
        self.c = BlastRadiusLimiter()

    def test_within(self):
        self.assertFalse(self.c.check("update", 5, 100)[0])

    def test_over(self):
        self.assertTrue(self.c.check("delete", 5000, 100)[0])

    def test_boundary(self):
        self.assertFalse(self.c.check("update", 100, 100)[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 10)


if __name__ == "__main__":
    unittest.main()
