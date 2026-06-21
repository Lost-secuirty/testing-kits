"""test_sensitive_disclosure_test_harness.py — unittest suite."""

import unittest

from harnesses.ai.sensitive_disclosure_test_harness import (
    OutputSecretScanner,
    PIIDisclosureChecker,
    SystemPromptLeakDetector,
    list_scenarios,
    run_all_scenarios,
)


class TestOutputSecretScanner(unittest.TestCase):
    def setUp(self):
        self.c = OutputSecretScanner()

    def test_clean(self):
        self.assertFalse(self.c.check("Paris is the capital.")[0])

    def test_aws_key(self):
        self.assertTrue(self.c.check("AKIAIOSFODNN7EXAMPLE")[0])

    def test_bearer(self):
        self.assertTrue(self.c.check("Bearer abcdefghijklmnopqrstuvwxyz")[0])

    def test_sk_key(self):
        self.assertTrue(self.c.check("sk-ABCDEFGHIJKLMNOPQRSTUVWX")[0])


class TestPIIDisclosureChecker(unittest.TestCase):
    def setUp(self):
        self.c = PIIDisclosureChecker()

    def test_clean(self):
        self.assertFalse(self.c.check("ships tomorrow")[0])

    def test_ssn(self):
        self.assertTrue(self.c.check("123-45-6789")[0])

    def test_email(self):
        self.assertTrue(self.c.check("a@b.com")[0])

    def test_card(self):
        self.assertTrue(self.c.check("4111 1111 1111 1111")[0])


class TestSystemPromptLeakDetector(unittest.TestCase):
    def setUp(self):
        self.c = SystemPromptLeakDetector()

    def test_clean(self):
        self.assertFalse(self.c.check("a recipe", "CANARY-X")[0])

    def test_canary(self):
        self.assertTrue(self.c.check("text CANARY-X here", "CANARY-X")[0])

    def test_phrase(self):
        self.assertTrue(self.c.check("You are a helpful assistant")[0])

    def test_system_prefix(self):
        self.assertTrue(self.c.check("SYSTEM: do this")[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 12)


if __name__ == "__main__":
    unittest.main()
