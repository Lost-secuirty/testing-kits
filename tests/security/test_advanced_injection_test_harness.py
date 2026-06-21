"""test_advanced_injection_test_harness.py — unittest suite."""

import unittest

from harnesses.security.advanced_injection_test_harness import (
    LDAPInjectionChecker,
    NoSQLInjectionChecker,
    SSTIChecker,
    list_scenarios,
    run_all_scenarios,
)


class TestSSTI(unittest.TestCase):
    def setUp(self):
        self.c = SSTIChecker()

    def test_plain_clean(self):
        self.assertFalse(self.c.check("Hello Alice")[0])

    def test_jinja_flagged(self):
        self.assertTrue(self.c.check("{{7*7}}")[0])

    def test_el_flagged(self):
        self.assertTrue(self.c.check("${x}")[0])

    def test_erb_flagged(self):
        self.assertTrue(self.c.check("<%= x %>")[0])


class TestNoSQL(unittest.TestCase):
    def setUp(self):
        self.c = NoSQLInjectionChecker()

    def test_plain_clean(self):
        self.assertFalse(self.c.check({"user": "alice"})[0])

    def test_operator_flagged(self):
        self.assertTrue(self.c.check({"$ne": None})[0])

    def test_where_string_flagged(self):
        self.assertTrue(self.c.check("$where: 1")[0])


class TestLDAP(unittest.TestCase):
    def setUp(self):
        self.c = LDAPInjectionChecker()

    def test_plain_clean(self):
        self.assertFalse(self.c.check("john.doe")[0])

    def test_metachar_flagged(self):
        self.assertTrue(self.c.check("*)(uid=*")[0])

    def test_escape_neutralizes(self):
        self.assertFalse(self.c.check(self.c.escape("*)(uid=*"))[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 10)


if __name__ == "__main__":
    unittest.main()
