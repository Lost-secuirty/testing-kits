"""Test suite for hermeticity_test_harness."""

import os
import random
import time
import unittest

from harnesses.core.hermeticity_test_harness import (
    AuditConfig,
    AuditResult,
    _MockEnv,
    _run_self_test,
    audit,
    audit_suite,
    depends_on_env,
    depends_on_home,
    depends_on_time,
    hermetic_passes,
    list_scenarios,
)


class TestMockEnv(unittest.TestCase):
    def test_restores_time_after_exit(self):
        original = time.time
        with _MockEnv(True, False, False, False, seed=1):
            self.assertNotEqual(time.time, original)
        self.assertEqual(time.time, original)

    def test_restores_environ_after_exit(self):
        os.environ["UNIT_TEST_KEY"] = "preserved"
        try:
            with _MockEnv(False, False, True, False, seed=1):
                self.assertNotIn("UNIT_TEST_KEY", os.environ)
            self.assertEqual(os.environ.get("UNIT_TEST_KEY"), "preserved")
        finally:
            os.environ.pop("UNIT_TEST_KEY", None)

    def test_restores_random_state(self):
        random.seed(99)
        before = random.getstate()
        with _MockEnv(False, True, False, False, seed=1):
            random.random()
        self.assertEqual(random.getstate(), before)

    def test_mock_home_sets_HOME(self):
        with _MockEnv(False, False, False, True, seed=1):
            self.assertTrue(os.environ["HOME"].startswith("/tmp/hermetic-"))


class TestAudit(unittest.TestCase):
    def test_hermetic_function(self):
        result = audit(hermetic_passes, AuditConfig(iterations=3))
        self.assertTrue(result.deterministic)
        self.assertEqual(result.contaminating, [])

    def test_time_dependent_function(self):
        result = audit(depends_on_time, AuditConfig(iterations=5))
        self.assertFalse(result.deterministic)
        self.assertIn("time", result.contaminating)

    def test_env_dependent_function(self):
        result = audit(depends_on_env, AuditConfig(iterations=5))
        self.assertFalse(result.deterministic)
        self.assertIn("env", result.contaminating)

    def test_home_dependent_function(self):
        result = audit(depends_on_home, AuditConfig(iterations=5))
        self.assertFalse(result.deterministic)
        self.assertIn("home", result.contaminating)


class TestAuditSuite(unittest.TestCase):
    def test_runs_each_function(self):
        results = audit_suite([hermetic_passes, depends_on_time],
                              AuditConfig(iterations=3))
        names = [r.name for r in results]
        self.assertIn("hermetic_passes", names)
        self.assertIn("depends_on_time", names)


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertEqual(len(scenarios), 6)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(AuditConfig(iterations=3)), 0)


class TestAuditResult(unittest.TestCase):
    def test_fields(self):
        r = AuditResult(name="x", deterministic=True)
        self.assertTrue(r.deterministic)
        self.assertEqual(r.contaminating, [])


if __name__ == "__main__":
    unittest.main()
