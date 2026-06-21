"""test_insecure_output_handling_test_harness.py — unittest suite."""

import unittest

from harnesses.ai.insecure_output_handling_test_harness import (
    HtmlOutputChecker,
    OutputSinkPolicy,
    StructuredOutputValidator,
    list_scenarios,
    run_all_scenarios,
)


class TestOutputSinkPolicy(unittest.TestCase):
    def setUp(self):
        self.c = OutputSinkPolicy()

    def test_sanitized_accepted(self):
        self.assertFalse(self.c.check("eval", sanitized=True)[0])

    def test_unsanitized_flagged(self):
        self.assertTrue(self.c.check("eval", sanitized=False)[0])

    def test_benign_sink(self):
        self.assertFalse(self.c.check("logger.info")[0])

    def test_messy_sink_normalized(self):
        self.assertTrue(self.c.check("  EVAL  ", sanitized=False)[0])


class TestHtmlOutputChecker(unittest.TestCase):
    def setUp(self):
        self.c = HtmlOutputChecker()

    def test_plain_clean(self):
        self.assertFalse(self.c.check("<p>hi</p>")[0])

    def test_script_flagged(self):
        self.assertTrue(self.c.check("<script>x</script>")[0])

    def test_handler_flagged(self):
        self.assertTrue(self.c.check("<img onerror=alert(1)>")[0])

    def test_js_uri_flagged(self):
        self.assertTrue(self.c.check('<a href="javascript:x">y</a>')[0])


class TestStructuredOutputValidator(unittest.TestCase):
    def setUp(self):
        self.c = StructuredOutputValidator()

    def test_valid_accepted(self):
        self.assertFalse(self.c.validate('{"a": 1}', ("a",))[0])

    def test_invalid_flagged(self):
        self.assertTrue(self.c.validate("not json", ("a",))[0])

    def test_missing_key_flagged(self):
        self.assertTrue(self.c.validate('{"a": 1}', ("b",))[0])

    def test_non_object_flagged(self):
        self.assertTrue(self.c.validate("[1,2]")[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 13)


if __name__ == "__main__":
    unittest.main()
