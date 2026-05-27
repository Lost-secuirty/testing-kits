"""
Tests for cli_test_harness.py (Harness 5 of 36)

51 tests covering all major components:
- OutputValidator
- ExitCodeChecker
- CliTestCase / CliTestResult / CliSuiteReport
- CliTestRunner / SampleCliRunner
- End-to-end subprocess tests against sample CLI
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Make sure the harness module is importable even when run from a different cwd
sys.path.insert(0, str(Path(__file__).parent))

from harnesses.core.cli_test_harness import (
    CliSuiteReport,
    CliTestCase,
    CliTestResult,
    CliTestRunner,
    ExitCodeChecker,
    OutputValidator,
    SampleCliRunner,
    _get_sample_cli_path,
    run_self_test,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner() -> SampleCliRunner:
    return SampleCliRunner()


# ---------------------------------------------------------------------------
# OutputValidator tests (14 tests)
# ---------------------------------------------------------------------------

class TestOutputValidator(unittest.TestCase):

    def test_contains_true(self):
        v = OutputValidator("Hello, World!")
        self.assertTrue(v.contains("World"))

    def test_contains_false(self):
        v = OutputValidator("Hello, World!")
        self.assertFalse(v.contains("Python"))

    def test_contains_all_all_present(self):
        v = OutputValidator("foo bar baz")
        ok, missing = v.contains_all(["foo", "bar", "baz"])
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_contains_all_some_missing(self):
        v = OutputValidator("foo bar")
        ok, missing = v.contains_all(["foo", "qux"])
        self.assertFalse(ok)
        self.assertIn("qux", missing)

    def test_exact_match_true(self):
        v = OutputValidator("  hello  \n")
        self.assertTrue(v.exact_match("hello"))

    def test_exact_match_false(self):
        v = OutputValidator("hello world")
        self.assertFalse(v.exact_match("hello"))

    def test_regex_match_true(self):
        v = OutputValidator("error: file not found at line 42")
        self.assertTrue(v.regex_match(r"line \d+"))

    def test_regex_match_false(self):
        v = OutputValidator("all good")
        self.assertFalse(v.regex_match(r"error"))

    def test_is_valid_json_true(self):
        v = OutputValidator('{"key": "value"}')
        self.assertTrue(v.is_valid_json())

    def test_is_valid_json_false(self):
        v = OutputValidator("not json at all")
        self.assertFalse(v.is_valid_json())

    def test_parse_json(self):
        v = OutputValidator('{"x": 42}')
        data = v.parse_json()
        self.assertEqual(data["x"], 42)

    def test_line_count(self):
        v = OutputValidator("a\nb\nc")
        self.assertEqual(v.line_count(), 3)

    def test_is_empty_true(self):
        v = OutputValidator("   \n  ")
        self.assertTrue(v.is_empty())

    def test_is_empty_false(self):
        v = OutputValidator("something")
        self.assertFalse(v.is_empty())


# ---------------------------------------------------------------------------
# ExitCodeChecker tests (8 tests)
# ---------------------------------------------------------------------------

class TestExitCodeChecker(unittest.TestCase):

    def test_is_success_zero(self):
        self.assertTrue(ExitCodeChecker(0).is_success())

    def test_is_success_nonzero(self):
        self.assertFalse(ExitCodeChecker(1).is_success())

    def test_is_failure_nonzero(self):
        self.assertTrue(ExitCodeChecker(2).is_failure())

    def test_is_failure_zero(self):
        self.assertFalse(ExitCodeChecker(0).is_failure())

    def test_equals(self):
        self.assertTrue(ExitCodeChecker(42).equals(42))
        self.assertFalse(ExitCodeChecker(42).equals(0))

    def test_is_in(self):
        self.assertTrue(ExitCodeChecker(2).is_in([1, 2, 3]))
        self.assertFalse(ExitCodeChecker(5).is_in([1, 2, 3]))

    def test_describe_success(self):
        desc = ExitCodeChecker(0).describe()
        self.assertIn("0", desc)

    def test_describe_failure(self):
        desc = ExitCodeChecker(1).describe()
        self.assertIn("1", desc)


# ---------------------------------------------------------------------------
# CliTestCase / CliTestResult / CliSuiteReport tests (7 tests)
# ---------------------------------------------------------------------------

class TestDataClasses(unittest.TestCase):

    def test_cli_test_case_defaults(self):
        tc = CliTestCase(name="test")
        self.assertEqual(tc.args, [])
        self.assertEqual(tc.expected_exit_code, 0)
        self.assertIsNone(tc.stdin_data)

    def test_cli_test_result_passed(self):
        tc = CliTestCase(name="t")
        r = CliTestResult(test_case=tc, passed=True, actual_exit_code=0,
                          actual_stdout="", actual_stderr="")
        self.assertTrue(r.passed)

    def test_suite_report_add_result_pass(self):
        report = CliSuiteReport()
        tc = CliTestCase(name="t")
        r = CliTestResult(test_case=tc, passed=True, actual_exit_code=0,
                          actual_stdout="", actual_stderr="")
        report.add_result(r)
        self.assertEqual(report.total, 1)
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 0)

    def test_suite_report_add_result_fail(self):
        report = CliSuiteReport()
        tc = CliTestCase(name="t")
        r = CliTestResult(test_case=tc, passed=False, actual_exit_code=1,
                          actual_stdout="", actual_stderr="",
                          failures=["exit code mismatch"])
        report.add_result(r)
        self.assertEqual(report.failed, 1)

    def test_suite_report_add_result_error(self):
        report = CliSuiteReport()
        tc = CliTestCase(name="t")
        r = CliTestResult(test_case=tc, passed=False, actual_exit_code=-1,
                          actual_stdout="", actual_stderr="", error="timeout")
        report.add_result(r)
        self.assertEqual(report.errored, 1)

    def test_suite_report_success_property(self):
        report = CliSuiteReport()
        self.assertTrue(report.success)  # empty is success

    def test_suite_report_summary_contains_totals(self):
        report = CliSuiteReport()
        tc = CliTestCase(name="mytest")
        r = CliTestResult(test_case=tc, passed=True, actual_exit_code=0,
                          actual_stdout="", actual_stderr="")
        report.add_result(r)
        summary = report.summary()
        self.assertIn("mytest", summary)
        self.assertIn("1", summary)


# ---------------------------------------------------------------------------
# CliTestRunner unit tests (6 tests)
# ---------------------------------------------------------------------------

class TestCliTestRunner(unittest.TestCase):

    def test_build_command_prepends_prefix(self):
        runner = CliTestRunner(command_prefix=["echo"])
        cmd = runner._build_command(["hello"])
        self.assertEqual(cmd, ["echo", "hello"])

    def test_build_command_no_prefix(self):
        runner = CliTestRunner()
        cmd = runner._build_command(["ls"])
        self.assertEqual(cmd, ["ls"])

    def test_run_and_capture_exit_code(self):
        runner = CliTestRunner(command_prefix=[sys.executable, "-c"])
        code, out, err = runner.run_and_capture(["import sys; sys.exit(0)"])
        self.assertEqual(code, 0)

    def test_run_and_capture_stdout(self):
        runner = CliTestRunner(command_prefix=[sys.executable, "-c"])
        code, out, err = runner.run_and_capture(["print('hello')"])
        self.assertEqual(out.strip(), "hello")

    def test_run_and_capture_stderr(self):
        runner = CliTestRunner(command_prefix=[sys.executable, "-c"])
        code, out, err = runner.run_and_capture(
            ["import sys; sys.stderr.write('err\\n')"]
        )
        self.assertIn("err", err)

    def test_run_case_missing_command(self):
        runner = CliTestRunner(command_prefix=["nonexistent_binary_xyz"])
        tc = CliTestCase(name="t", args=[])
        result = runner.run_case(tc)
        self.assertIsNotNone(result.error)
        self.assertFalse(result.passed)


# ---------------------------------------------------------------------------
# End-to-end tests against sample CLI (16 tests)
# ---------------------------------------------------------------------------

class TestSampleCliEndToEnd(unittest.TestCase):

    def setUp(self):
        self.runner = _make_runner()

    # --- Argument parsing ---

    def test_no_args_exits_zero(self):
        tc = CliTestCase(name="no_args", args=[], expected_exit_code=0)
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    def test_name_arg_produces_greeting(self):
        tc = CliTestCase(
            name="name_greeting",
            args=["--name", "Alice"],
            expected_exit_code=0,
            expected_stdout_contains=["Hello, Alice!"],
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    def test_count_arg_repeats_output(self):
        tc = CliTestCase(
            name="count_repeat",
            args=["--name", "Bob", "--count", "4"],
            expected_exit_code=0,
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)
        self.assertEqual(r.actual_stdout.count("Hello, Bob!"), 4)

    def test_optional_arg_has_default(self):
        # Default format is text, not json
        tc = CliTestCase(name="default_format", args=["--name", "X"],
                         expected_exit_code=0)
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)
        self.assertNotIn('"greeting"', r.actual_stdout)

    def test_format_json_flag(self):
        tc = CliTestCase(
            name="json_flag",
            args=["--name", "Cat", "--format", "json"],
            expected_exit_code=0,
            expected_stdout_regex=r'"greeting".*"Hello, Cat!"',
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    # --- Exit codes ---

    def test_success_exit_zero(self):
        code, _, _ = self.runner.run_and_capture([])
        self.assertEqual(code, 0)

    def test_forced_exit_code_nonzero(self):
        tc = CliTestCase(name="exit42", args=["--exit-code", "42"],
                         expected_exit_code=42)
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    def test_invalid_flag_exit_2(self):
        code, _, _ = self.runner.run_and_capture(["--totally-invalid-flag"])
        self.assertEqual(code, 2)

    # --- stdout / stderr validation ---

    def test_stdout_exact_match(self):
        tc = CliTestCase(
            name="exact_stdout",
            args=["--name", "Z"],
            expected_exit_code=0,
            expected_stdout_exact="Hello, Z!",
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    def test_stderr_contains(self):
        tc = CliTestCase(
            name="stderr_contains",
            args=["--stderr-msg", "custom error"],
            expected_exit_code=0,
            expected_stderr_contains=["custom error"],
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    # --- Flag combinations ---

    def test_verbose_and_name_flags_combined(self):
        tc = CliTestCase(
            name="verbose_name",
            args=["--verbose", "--name", "Dave"],
            expected_exit_code=0,
            expected_stdout_contains=["Hello, Dave!"],
            expected_stderr_contains=["Verbose mode enabled"],
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    def test_json_count_combined(self):
        tc = CliTestCase(
            name="json_count",
            args=["--name", "E", "--format", "json", "--count", "2"],
            expected_exit_code=0,
        )
        r = self.runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)
        lines = [l for l in r.actual_stdout.strip().splitlines() if l]
        self.assertEqual(len(lines), 2)
        for line in lines:
            data = json.loads(line)
            self.assertIn("greeting", data)

    # --- Help text ---

    def test_help_flag_exits_zero(self):
        code, out, _ = self.runner.run_and_capture(["--help"])
        self.assertEqual(code, 0)

    def test_help_contains_usage(self):
        code, out, _ = self.runner.run_and_capture(["--help"])
        self.assertIn("usage", out.lower())

    def test_help_lists_name_flag(self):
        code, out, _ = self.runner.run_and_capture(["--help"])
        self.assertIn("--name", out)

    # --- stdin piping ---

    def test_stdin_piping(self):
        code, out, _ = self.runner.pipe_input(["--read-stdin"], "piped data\n")
        self.assertEqual(code, 0)
        self.assertIn("piped data", out)

    # --- Timeout handling ---

    def test_timeout_terminates_long_running_command(self):
        # --sleep 5 should be killed by 1s timeout
        code, out, err = self.runner.run_with_timeout(
            ["--sleep", "5"], timeout_seconds=1.0
        )
        # Should not have exited cleanly
        self.assertNotEqual(code, 0)

    # --- Version flag ---

    def test_version_flag_shows_version(self):
        code, out, err = self.runner.run_and_capture(["--version"])
        self.assertEqual(code, 0)
        combined = out + err  # argparse may write to either stream
        self.assertIn("1.2.3", combined)


# ---------------------------------------------------------------------------
# Self-test / integration tests (additional tests bringing total to 51)
# ---------------------------------------------------------------------------

class TestSelfTestAndIntegration(unittest.TestCase):

    def test_self_test_returns_zero(self):
        result = run_self_test(verbose=False)
        self.assertEqual(result, 0)

    def test_sample_cli_script_exists(self):
        path = _get_sample_cli_path()
        self.assertTrue(path.exists())

    def test_sample_cli_is_runnable(self):
        path = _get_sample_cli_path()
        proc = subprocess.run(
            [sys.executable, str(path), "--help"],
            capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0)

    def test_suite_report_multiple_passes(self):
        runner = _make_runner()
        cases = [
            CliTestCase(name=f"pass_{i}", args=[], expected_exit_code=0)
            for i in range(5)
        ]
        report = runner.run_suite(cases)
        self.assertEqual(report.passed, 5)
        self.assertTrue(report.success)

    def test_runner_run_case_passes(self):
        runner = _make_runner()
        tc = CliTestCase(
            name="basic",
            args=["--name", "Test"],
            expected_exit_code=0,
            expected_stdout_contains=["Hello, Test!"],
        )
        r = runner.run_case(tc)
        self.assertTrue(r.passed)
        self.assertEqual(r.actual_exit_code, 0)

    def test_runner_run_case_fails_wrong_exit_code(self):
        runner = _make_runner()
        tc = CliTestCase(
            name="wrong_exit",
            args=[],
            expected_exit_code=99,  # wrong expectation
        )
        r = runner.run_case(tc)
        self.assertFalse(r.passed)
        self.assertTrue(any("Exit code" in f for f in r.failures))

    def test_runner_run_case_fails_missing_stdout(self):
        runner = _make_runner()
        tc = CliTestCase(
            name="missing_stdout",
            args=[],
            expected_stdout_contains=["this string is not in the output"],
        )
        r = runner.run_case(tc)
        self.assertFalse(r.passed)

    def test_output_validator_multiline_regex(self):
        v = OutputValidator("line1\nline2\nline3")
        self.assertTrue(v.regex_match(r"line1.*line3"))

    def test_exit_code_checker_repr(self):
        r = repr(ExitCodeChecker(7))
        self.assertIn("7", r)

    def test_output_validator_repr(self):
        r = repr(OutputValidator("test data"))
        self.assertIn("test data", r)

    def test_cli_test_result_duration_recorded(self):
        runner = _make_runner()
        tc = CliTestCase(name="dur", args=[])
        result = runner.run_case(tc)
        self.assertGreater(result.duration_seconds, 0)

    def test_stderr_regex_validation(self):
        runner = _make_runner()
        tc = CliTestCase(
            name="stderr_regex",
            args=["--stderr-msg", "ERR-404-not-found"],
            expected_exit_code=0,
            expected_stderr_regex=r"ERR-\d+-\w+",
        )
        r = runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)

    def test_stdin_empty_string(self):
        runner = _make_runner()
        code, out, err = runner.pipe_input(["--read-stdin"], "")
        self.assertEqual(code, 0)

    def test_json_output_parseable(self):
        runner = _make_runner()
        code, out, err = runner.run_and_capture(
            ["--name", "ParseMe", "--format", "json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out.strip())
        self.assertIn("greeting", data)
        self.assertEqual(data["greeting"], "Hello, ParseMe!")

    def test_multiple_flags_no_interference(self):
        """verbose + json + name + count should all work together."""
        runner = _make_runner()
        tc = CliTestCase(
            name="all_flags",
            args=["--verbose", "--name", "Multi", "--format", "json", "--count", "1"],
            expected_exit_code=0,
            expected_stdout_regex=r'"greeting"',
            expected_stderr_contains=["Verbose mode enabled"],
        )
        r = runner.run_case(tc)
        self.assertTrue(r.passed, r.failures)


if __name__ == "__main__":
    unittest.main(verbosity=2)
