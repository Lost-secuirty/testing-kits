"""
CLI Tool Test Harness (Harness 5 of 36)

Tests command-line tool behavior using subprocess. Pure stdlib, zero external dependencies.
Covers: argument parsing, exit codes, stdout/stderr validation, flag combinations,
help text, error messages, stdin piping, signal handling, and timeout handling.
"""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Tuple, Union


# ---------------------------------------------------------------------------
# Sample CLI script written to a temp file for testing
# ---------------------------------------------------------------------------

SAMPLE_CLI_SCRIPT = textwrap.dedent("""
import argparse
import json
import sys
import time

__version__ = "1.2.3"

def main():
    parser = argparse.ArgumentParser(
        prog="sample_cli",
        description="A sample CLI tool for testing purposes.",
    )
    parser.add_argument("--name", type=str, help="Name to greet")
    parser.add_argument("--count", type=int, default=1, help="Number of times to repeat")
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--sleep", type=float, default=0, help="Sleep N seconds (for timeout tests)")
    parser.add_argument("--exit-code", type=int, default=0, help="Force a specific exit code")
    parser.add_argument("--stderr-msg", type=str, default="", help="Print a message to stderr")
    parser.add_argument("--read-stdin", action="store_true", help="Read and echo stdin")

    args = parser.parse_args()

    if args.verbose:
        print("Verbose mode enabled", file=sys.stderr)

    if args.stderr_msg:
        print(args.stderr_msg, file=sys.stderr)

    if args.read_stdin:
        data = sys.stdin.read()
        print(f"stdin: {data.strip()}")

    if args.sleep > 0:
        time.sleep(args.sleep)

    if args.name:
        greeting = f"Hello, {args.name}!"
        if args.format == "json":
            output = {"greeting": greeting, "count": args.count}
            for _ in range(args.count):
                print(json.dumps(output))
        else:
            for _ in range(args.count):
                print(greeting)
    else:
        if args.format == "json":
            print(json.dumps({"status": "ok"}))
        else:
            print("No name provided.")

    sys.exit(args.exit_code)

if __name__ == "__main__":
    main()
""").strip()


def _get_sample_cli_path() -> Path:
    """Return the path to the sample CLI script, creating it if needed."""
    tmp = Path(tempfile.gettempdir()) / "sample_cli_harness5.py"
    if not tmp.exists():
        tmp.write_text(SAMPLE_CLI_SCRIPT)
    return tmp


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CliTestCase:
    """Describes a single CLI test scenario."""

    name: str
    args: List[str] = field(default_factory=list)
    stdin_data: Optional[str] = None
    expected_exit_code: int = 0
    expected_stdout_contains: Optional[List[str]] = None
    expected_stdout_exact: Optional[str] = None
    expected_stdout_regex: Optional[str] = None
    expected_stderr_contains: Optional[List[str]] = None
    expected_stderr_regex: Optional[str] = None
    timeout_seconds: float = 10.0
    description: str = ""


@dataclass
class CliTestResult:
    """Result of running a single CliTestCase."""

    test_case: CliTestCase
    passed: bool
    actual_exit_code: int
    actual_stdout: str
    actual_stderr: str
    failures: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_seconds: float = 0.0


@dataclass
class CliSuiteReport:
    """Aggregated report for a suite of CLI test cases."""

    results: List[CliTestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0

    def add_result(self, result: CliTestResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.error:
            self.errored += 1
        elif result.passed:
            self.passed += 1
        else:
            self.failed += 1

    @property
    def success(self) -> bool:
        return self.failed == 0 and self.errored == 0

    def summary(self) -> str:
        lines = [
            f"CLI Test Suite Report",
            f"  Total : {self.total}",
            f"  Passed: {self.passed}",
            f"  Failed: {self.failed}",
            f"  Errors: {self.errored}",
        ]
        for r in self.results:
            status = "PASS" if r.passed and not r.error else ("ERROR" if r.error else "FAIL")
            lines.append(f"  [{status}] {r.test_case.name}")
            if r.error:
                lines.append(f"         Error: {r.error}")
            for f_msg in r.failures:
                lines.append(f"         - {f_msg}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------

class OutputValidator:
    """Validates CLI stdout/stderr output against various criteria."""

    def __init__(self, output: str):
        self._output = output

    @property
    def text(self) -> str:
        return self._output

    def contains(self, substring: str) -> bool:
        return substring in self._output

    def contains_all(self, substrings: List[str]) -> Tuple[bool, List[str]]:
        missing = [s for s in substrings if s not in self._output]
        return (len(missing) == 0, missing)

    def exact_match(self, expected: str) -> bool:
        return self._output.strip() == expected.strip()

    def regex_match(self, pattern: str) -> bool:
        return bool(re.search(pattern, self._output, re.MULTILINE | re.DOTALL))

    def is_valid_json(self) -> bool:
        try:
            json.loads(self._output)
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    def parse_json(self) -> Any:
        return json.loads(self._output)

    def line_count(self) -> int:
        return len(self._output.splitlines())

    def starts_with(self, prefix: str) -> bool:
        return self._output.lstrip().startswith(prefix)

    def ends_with(self, suffix: str) -> bool:
        return self._output.rstrip().endswith(suffix)

    def is_empty(self) -> bool:
        return self._output.strip() == ""

    def __repr__(self) -> str:
        preview = self._output[:80].replace("\n", "\\n")
        return f"OutputValidator({preview!r})"


# ---------------------------------------------------------------------------
# ExitCodeChecker
# ---------------------------------------------------------------------------

class ExitCodeChecker:
    """Validates process exit codes."""

    SUCCESS_CODE = 0

    def __init__(self, actual: int):
        self._actual = actual

    @property
    def code(self) -> int:
        return self._actual

    def is_success(self) -> bool:
        return self._actual == self.SUCCESS_CODE

    def is_failure(self) -> bool:
        return self._actual != self.SUCCESS_CODE

    def equals(self, expected: int) -> bool:
        return self._actual == expected

    def is_in(self, codes: List[int]) -> bool:
        return self._actual in codes

    def describe(self) -> str:
        if self._actual == 0:
            return "success (0)"
        if self._actual < 0:
            # Negative codes indicate signal termination on Unix
            try:
                sig = signal.Signals(-self._actual)
                return f"killed by signal {sig.name} ({self._actual})"
            except ValueError:
                return f"killed by signal {self._actual}"
        return f"failure ({self._actual})"

    def __repr__(self) -> str:
        return f"ExitCodeChecker({self._actual})"


# ---------------------------------------------------------------------------
# CliTestRunner
# ---------------------------------------------------------------------------

class CliTestRunner:
    """
    Runs CLI commands in subprocesses and validates results against CliTestCase specs.
    """

    def __init__(
        self,
        command_prefix: Optional[List[str]] = None,
        default_timeout: float = 10.0,
        cwd: Optional[Union[str, Path]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        """
        Args:
            command_prefix: Base command to prepend to each test's args.
                            E.g. ["python3", "/path/to/cli.py"]
            default_timeout: Default timeout in seconds for each subprocess.
            cwd: Working directory for subprocesses.
            env: Environment variables for subprocesses. None means inherit.
        """
        self.command_prefix = command_prefix or []
        self.default_timeout = default_timeout
        self.cwd = str(cwd) if cwd else None
        self.env = env

    def _build_command(self, args: List[str]) -> List[str]:
        return self.command_prefix + args

    def run_case(self, test_case: CliTestCase) -> CliTestResult:
        """Run a single CliTestCase and return a CliTestResult."""
        cmd = self._build_command(test_case.args)
        timeout = test_case.timeout_seconds or self.default_timeout

        start = time.monotonic()
        actual_exit_code = -1
        actual_stdout = ""
        actual_stderr = ""
        error_msg: Optional[str] = None

        try:
            proc = subprocess.run(
                cmd,
                input=test_case.stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cwd,
                env=self.env,
            )
            actual_exit_code = proc.returncode
            actual_stdout = proc.stdout
            actual_stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            error_msg = f"Command timed out after {timeout}s"
            actual_stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            actual_stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            actual_exit_code = -1
        except FileNotFoundError:
            error_msg = f"Command not found: {cmd[0]!r}"
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Unexpected error: {exc}"

        duration = time.monotonic() - start

        failures: List[str] = []

        if error_msg is None:
            # Exit code check
            if actual_exit_code != test_case.expected_exit_code:
                failures.append(
                    f"Exit code: expected {test_case.expected_exit_code}, got {actual_exit_code}"
                )

            stdout_v = OutputValidator(actual_stdout)
            stderr_v = OutputValidator(actual_stderr)

            # stdout checks
            if test_case.expected_stdout_exact is not None:
                if not stdout_v.exact_match(test_case.expected_stdout_exact):
                    failures.append(
                        f"Stdout exact mismatch.\n"
                        f"  Expected: {test_case.expected_stdout_exact!r}\n"
                        f"  Actual  : {actual_stdout!r}"
                    )

            if test_case.expected_stdout_contains:
                ok, missing = stdout_v.contains_all(test_case.expected_stdout_contains)
                if not ok:
                    for m in missing:
                        failures.append(f"Stdout missing: {m!r}")

            if test_case.expected_stdout_regex is not None:
                if not stdout_v.regex_match(test_case.expected_stdout_regex):
                    failures.append(
                        f"Stdout did not match regex: {test_case.expected_stdout_regex!r}"
                    )

            # stderr checks
            if test_case.expected_stderr_contains:
                ok, missing = stderr_v.contains_all(test_case.expected_stderr_contains)
                if not ok:
                    for m in missing:
                        failures.append(f"Stderr missing: {m!r}")

            if test_case.expected_stderr_regex is not None:
                if not stderr_v.regex_match(test_case.expected_stderr_regex):
                    failures.append(
                        f"Stderr did not match regex: {test_case.expected_stderr_regex!r}"
                    )

        passed = (error_msg is None) and (len(failures) == 0)

        return CliTestResult(
            test_case=test_case,
            passed=passed,
            actual_exit_code=actual_exit_code,
            actual_stdout=actual_stdout,
            actual_stderr=actual_stderr,
            failures=failures,
            error=error_msg,
            duration_seconds=duration,
        )

    def run_suite(self, test_cases: List[CliTestCase]) -> CliSuiteReport:
        """Run a list of test cases and return a CliSuiteReport."""
        report = CliSuiteReport()
        for tc in test_cases:
            result = self.run_case(tc)
            report.add_result(result)
        return report

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def assert_exits_zero(self, args: List[str], **kwargs) -> CliTestResult:
        tc = CliTestCase(name="assert_exits_zero", args=args, expected_exit_code=0, **kwargs)
        return self.run_case(tc)

    def assert_exits_nonzero(self, args: List[str], **kwargs) -> CliTestResult:
        tc = CliTestCase(name="assert_exits_nonzero", args=args, expected_exit_code=1, **kwargs)
        return self.run_case(tc)

    def run_and_capture(
        self, args: List[str], stdin_data: Optional[str] = None, timeout: float = 10.0
    ) -> Tuple[int, str, str]:
        """Run command and return (exit_code, stdout, stderr)."""
        cmd = self._build_command(args)
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cwd,
                env=self.env,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def run_with_timeout(
        self,
        args: List[str],
        timeout_seconds: float,
        signal_on_timeout: int = signal.SIGTERM,
    ) -> Tuple[int, str, str]:
        """
        Run a command, sending signal_on_timeout if it exceeds timeout_seconds.
        Returns (exit_code, stdout, stderr).
        """
        cmd = self._build_command(args)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.cwd,
                env=self.env,
            )
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            proc.send_signal(signal_on_timeout)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            return proc.returncode, stdout, stderr

    def pipe_input(
        self, args: List[str], stdin_text: str, timeout: float = 10.0
    ) -> Tuple[int, str, str]:
        """Pipe stdin_text into the command and capture output."""
        return self.run_and_capture(args, stdin_data=stdin_text, timeout=timeout)


# ---------------------------------------------------------------------------
# High-level test helper that uses the sample CLI
# ---------------------------------------------------------------------------

class SampleCliRunner(CliTestRunner):
    """Convenience subclass pre-configured to run the sample CLI."""

    def __init__(self):
        path = _get_sample_cli_path()
        super().__init__(
            command_prefix=[sys.executable, str(path)],
            default_timeout=10.0,
        )


# ---------------------------------------------------------------------------
# Built-in self-test suite
# ---------------------------------------------------------------------------

def _build_self_test_suite(runner: SampleCliRunner) -> List[CliTestCase]:
    return [
        CliTestCase(
            name="no_args_exits_zero",
            args=[],
            expected_exit_code=0,
            expected_stdout_contains=["No name provided."],
            description="Running with no args should succeed",
        ),
        CliTestCase(
            name="name_arg_greets",
            args=["--name", "World"],
            expected_exit_code=0,
            expected_stdout_contains=["Hello, World!"],
            description="--name produces greeting",
        ),
        CliTestCase(
            name="count_repeats",
            args=["--name", "Bob", "--count", "3"],
            expected_exit_code=0,
            expected_stdout_regex=r"(Hello, Bob!\n){3}",
            description="--count repeats greeting",
        ),
        CliTestCase(
            name="json_format",
            args=["--name", "Alice", "--format", "json"],
            expected_exit_code=0,
            expected_stdout_regex=r'"greeting"',
            description="--format json produces JSON output",
        ),
        CliTestCase(
            name="verbose_flag",
            args=["--verbose"],
            expected_exit_code=0,
            expected_stderr_contains=["Verbose mode enabled"],
            description="--verbose writes to stderr",
        ),
        CliTestCase(
            name="version_flag",
            args=["--version"],
            expected_exit_code=0,
            expected_stdout_regex=r"1\.2\.3",
            description="--version shows version string",
        ),
        CliTestCase(
            name="help_flag",
            args=["--help"],
            expected_exit_code=0,
            expected_stdout_contains=["usage", "--name"],
            description="--help shows usage information",
        ),
        CliTestCase(
            name="invalid_flag_nonzero",
            args=["--nonexistent-flag"],
            expected_exit_code=2,
            description="Unknown flag produces exit code 2",
        ),
        CliTestCase(
            name="forced_exit_code_1",
            args=["--exit-code", "1"],
            expected_exit_code=1,
            description="--exit-code 1 produces exit 1",
        ),
        CliTestCase(
            name="forced_exit_code_42",
            args=["--exit-code", "42"],
            expected_exit_code=42,
            description="--exit-code 42 produces exit 42",
        ),
        CliTestCase(
            name="stdin_pipe",
            args=["--read-stdin"],
            stdin_data="hello from pipe\n",
            expected_exit_code=0,
            expected_stdout_contains=["hello from pipe"],
            description="stdin piping works",
        ),
        CliTestCase(
            name="stderr_message",
            args=["--stderr-msg", "error detail here"],
            expected_exit_code=0,
            expected_stderr_contains=["error detail here"],
            description="--stderr-msg writes to stderr",
        ),
        CliTestCase(
            name="json_no_name",
            args=["--format", "json"],
            expected_exit_code=0,
            expected_stdout_contains=['"status"', '"ok"'],
            description="JSON format without name shows status ok",
        ),
    ]


def run_self_test(verbose: bool = False) -> int:
    """Run built-in self-tests. Returns 0 on all pass, 1 on any failure."""
    runner = SampleCliRunner()
    cases = _build_self_test_suite(runner)
    report = runner.run_suite(cases)
    print(report.summary())
    return 0 if report.success else 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> Any:
    import argparse

    p = argparse.ArgumentParser(
        prog="cli_test_harness",
        description="CLI Tool Test Harness (harness 5 of 36)",
    )
    p.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in self-test suite against sample CLI",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.self_test:
        return run_self_test(verbose=args.verbose)
    print("CLI Test Harness ready. Use --self-test to run the built-in suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
