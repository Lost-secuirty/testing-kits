"""
CLI Tool Test Harness (Harness 5 of 36)

Tests command-line tool behavior using subprocess. Pure stdlib, zero external dependencies.
Covers: argument parsing, exit codes, stdout/stderr validation, flag combinations,
help text, error messages, stdin piping, signal handling, and timeout handling.
"""

import json
import re
import signal
import subprocess
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import tempfile
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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
    args: list[str] = field(default_factory=list)
    stdin_data: str | None = None
    expected_exit_code: int = 0
    expected_stdout_contains: list[str] | None = None
    expected_stdout_exact: str | None = None
    expected_stdout_regex: str | None = None
    expected_stderr_contains: list[str] | None = None
    expected_stderr_regex: str | None = None
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
    failures: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0


@dataclass
class CliSuiteReport:
    """Aggregated report for a suite of CLI test cases."""

    results: list[CliTestResult] = field(default_factory=list)
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
            "CLI Test Suite Report",
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

    def contains_all(self, substrings: list[str]) -> tuple[bool, list[str]]:
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

    def is_in(self, codes: list[int]) -> bool:
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
        command_prefix: list[str] | None = None,
        default_timeout: float = 10.0,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
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

    def _build_command(self, args: list[str]) -> list[str]:
        return self.command_prefix + args

    def run_case(self, test_case: CliTestCase) -> CliTestResult:
        """Run a single CliTestCase and return a CliTestResult."""
        cmd = self._build_command(test_case.args)
        timeout = test_case.timeout_seconds or self.default_timeout

        start = time.monotonic()
        actual_exit_code = -1
        actual_stdout = ""
        actual_stderr = ""
        error_msg: str | None = None

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

        failures: list[str] = []

        if error_msg is None:
            # Exit code check
            if actual_exit_code != test_case.expected_exit_code:
                failures.append(
                    f"Exit code: expected {test_case.expected_exit_code}, got {actual_exit_code}"
                )

            stdout_v = OutputValidator(actual_stdout)
            stderr_v = OutputValidator(actual_stderr)

            # stdout checks
            if test_case.expected_stdout_exact is not None and not stdout_v.exact_match(
                test_case.expected_stdout_exact
            ):
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

            if test_case.expected_stdout_regex is not None and not stdout_v.regex_match(
                test_case.expected_stdout_regex
            ):
                failures.append(
                    f"Stdout did not match regex: {test_case.expected_stdout_regex!r}"
                )

            # stderr checks
            if test_case.expected_stderr_contains:
                ok, missing = stderr_v.contains_all(test_case.expected_stderr_contains)
                if not ok:
                    for m in missing:
                        failures.append(f"Stderr missing: {m!r}")

            if test_case.expected_stderr_regex is not None and not stderr_v.regex_match(
                test_case.expected_stderr_regex
            ):
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

    def run_suite(self, test_cases: list[CliTestCase]) -> CliSuiteReport:
        """Run a list of test cases and return a CliSuiteReport."""
        report = CliSuiteReport()
        for tc in test_cases:
            result = self.run_case(tc)
            report.add_result(result)
        return report

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def assert_exits_zero(self, args: list[str], **kwargs) -> CliTestResult:
        tc = CliTestCase(name="assert_exits_zero", args=args, expected_exit_code=0, **kwargs)
        return self.run_case(tc)

    def assert_exits_nonzero(self, args: list[str], **kwargs) -> CliTestResult:
        tc = CliTestCase(name="assert_exits_nonzero", args=args, expected_exit_code=1, **kwargs)
        return self.run_case(tc)

    def run_and_capture(
        self, args: list[str], stdin_data: str | None = None, timeout: float = 10.0
    ) -> tuple[int, str, str]:
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
        args: list[str],
        timeout_seconds: float,
        signal_on_timeout: int = signal.SIGTERM,
    ) -> tuple[int, str, str]:
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
        self, args: list[str], stdin_text: str, timeout: float = 10.0
    ) -> tuple[int, str, str]:
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
# TEETH: a PURE in-process CLI parse/dispatch oracle + planted buggy twins.
#
# The CliTestRunner above drives real subprocesses over a real CLI — fine for
# the legacy self-test and the paired unittest, but non-deterministic and
# side-effecting. The teeth, by contrast, exercise a PURE in-process model of
# the *parse -> dispatch -> exit-code* contract that a well-behaved CLI must
# honour, so the gate can verify "this harness catches a real CLI bug" with
# zero subprocess/clock/network/filesystem I/O and full determinism.
#
# An impl is a parse/dispatch FUNCTION: argv (a tuple of args) -> CliOutcome
# (exit_code + dispatched action + emitted text). The oracle is the correct
# parser; each Mutant models a genuine real-world CLI defect (wrong exit code on
# a usage error, accepting a mutually-exclusive flag combo, mis-dispatching a
# subcommand). prove() judges an impl against a FROZEN corpus of literal
# expected outcomes — never against the oracle object — so the check is
# non-circular.
# ---------------------------------------------------------------------------

# Conventional CLI exit codes (BSD sysexits-flavoured, as argparse uses).
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2


@dataclass(frozen=True)
class CliOutcome:
    """The observable result of parsing+dispatching one argv.

    ``action`` is the subcommand actually dispatched (or a sentinel like
    "help"/"version"/"usage_error"); ``stream`` records which stream the
    primary text went to. Equality across all three fields is what the corpus
    freezes — so a wrong exit code, a mis-dispatch, or output on the wrong
    stream are all individually observable.
    """

    exit_code: int
    action: str
    stream: str = "stdout"  # "stdout" | "stderr" | "none"


# The CLI under model: a tiny tool with two subcommands (`add`, `list`), a
# global `--verbose` flag, and a `--format {text,json}` option on `list`. The
# rules the oracle enforces (and each mutant breaks one of):
#   * no subcommand            -> usage error, exit 2, stderr
#   * unknown subcommand       -> usage error, exit 2, stderr
#   * `--help` / `-h`          -> help, exit 0, stdout
#   * `--version`              -> version, exit 0, stdout
#   * `add` with < 2 operands  -> usage error, exit 2, stderr
#   * `list --format bogus`    -> usage error, exit 2 (invalid choice), stderr
#   * `list --json --format …` -> usage error (mutually exclusive), exit 2
#   * otherwise dispatch the subcommand -> exit 0, stdout

_SUBCOMMANDS = ("add", "list")
_FORMATS = ("text", "json")


def oracle_dispatch(argv: tuple[str, ...]) -> CliOutcome:
    """Correct parse + dispatch + exit-code logic — the contract a real CLI
    parser (argparse-style) must honour."""
    args = list(argv)

    # Global help / version take precedence and exit 0 on stdout.
    if "--help" in args or "-h" in args:
        return CliOutcome(EXIT_OK, "help", "stdout")
    if "--version" in args:
        return CliOutcome(EXIT_OK, "version", "stdout")

    if not args:
        return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")

    sub = args[0]
    rest = args[1:]

    if sub not in _SUBCOMMANDS:
        return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")

    if sub == "add":
        # `add` requires at least two positional operands.
        operands = [a for a in rest if not a.startswith("-")]
        if len(operands) < 2:
            return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")
        return CliOutcome(EXIT_OK, "add", "stdout")

    # sub == "list"
    fmt = "text"
    json_flag = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--json":
            json_flag = True
            i += 1
        elif tok == "--format":
            if i + 1 >= len(rest):
                return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")
            fmt = rest[i + 1]
            i += 2
        else:
            # Unknown option to `list`.
            return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")
    # --json and --format are mutually exclusive.
    if json_flag and "--format" in rest:
        return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")
    if fmt not in _FORMATS:
        return CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr")
    return CliOutcome(EXIT_OK, "list", "stdout")


# --- Planted buggy twins (each models a real, common CLI defect) -----------

def dispatch_usage_error_exits_zero(argv: tuple[str, ...]) -> CliOutcome:
    """BUG: a usage error exits 0 instead of 2.

    A pervasive real defect — a CLI that prints an error to stderr but forgets
    ``sys.exit(2)`` (or swallows argparse's SystemExit) so the shell, CI, or a
    calling script sees success and proceeds on a failed invocation.
    """
    out = oracle_dispatch(argv)
    if out.action == "usage_error":
        return CliOutcome(EXIT_OK, out.action, out.stream)
    return out


def dispatch_accepts_mutually_exclusive(argv: tuple[str, ...]) -> CliOutcome:
    """BUG: accepts the mutually-exclusive `list --json --format …` combo.

    Models a CLI that declares two conflicting options but never enforces the
    exclusion, silently honouring one and ignoring the other instead of failing
    with a usage error.
    """
    args = list(argv)
    if args[:1] == ["list"] and "--json" in args and "--format" in args:
        return CliOutcome(EXIT_OK, "list", "stdout")
    return oracle_dispatch(argv)


def dispatch_misroutes_subcommand(argv: tuple[str, ...]) -> CliOutcome:
    """BUG: mis-dispatches `list` to the `add` handler.

    Models a subcommand routing table wired to the wrong handler (a copy-paste
    or fall-through bug) — the command 'succeeds' but runs the wrong action.
    """
    out = oracle_dispatch(argv)
    if out.action == "list":
        return CliOutcome(out.exit_code, "add", out.stream)
    return out


def dispatch_skips_required_operands(argv: tuple[str, ...]) -> CliOutcome:
    """BUG: `add` with too few operands runs anyway (exit 0) instead of erroring.

    Models missing required-argument validation — the handler dispatches with
    incomplete input rather than rejecting it with a usage error.
    """
    args = list(argv)
    if args[:1] == ["add"] and "--help" not in args and "-h" not in args:
        return CliOutcome(EXIT_OK, "add", "stdout")
    return oracle_dispatch(argv)


# --- Frozen corpus: argv -> expected outcome -------------------------------

@dataclass(frozen=True)
class CliOracleCase:
    name: str
    argv: tuple[str, ...]
    expected: CliOutcome
    note: str = ""


CLI_CORPUS: tuple[CliOracleCase, ...] = (
    CliOracleCase(
        "no_subcommand_usage_error",
        (),
        CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr"),
        note="no subcommand -> exit 2 on stderr (catches usage_error_exits_zero)",
    ),
    CliOracleCase(
        "unknown_subcommand_usage_error",
        ("frobnicate",),
        CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr"),
        note="unknown subcommand -> exit 2 (catches usage_error_exits_zero)",
    ),
    CliOracleCase(
        "help_exits_zero",
        ("--help",),
        CliOutcome(EXIT_OK, "help", "stdout"),
        note="--help -> exit 0 on stdout",
    ),
    CliOracleCase(
        "version_exits_zero",
        ("--version",),
        CliOutcome(EXIT_OK, "version", "stdout"),
        note="--version -> exit 0 on stdout",
    ),
    CliOracleCase(
        "add_two_operands_ok",
        ("add", "1", "2"),
        CliOutcome(EXIT_OK, "add", "stdout"),
        note="well-formed add dispatches and succeeds",
    ),
    CliOracleCase(
        "add_one_operand_usage_error",
        ("add", "1"),
        CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr"),
        note="add with < 2 operands -> exit 2 (catches skips_required_operands)",
    ),
    CliOracleCase(
        "list_plain_ok",
        ("list",),
        CliOutcome(EXIT_OK, "list", "stdout"),
        note="bare list dispatches to the list handler (catches misroutes_subcommand)",
    ),
    CliOracleCase(
        "list_format_json_ok",
        ("list", "--format", "json"),
        CliOutcome(EXIT_OK, "list", "stdout"),
        note="list --format json is valid",
    ),
    CliOracleCase(
        "list_bad_format_usage_error",
        ("list", "--format", "xml"),
        CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr"),
        note="invalid --format choice -> exit 2",
    ),
    CliOracleCase(
        "list_mutually_exclusive_usage_error",
        ("list", "--json", "--format", "json"),
        CliOutcome(EXIT_USAGE_ERROR, "usage_error", "stderr"),
        note="--json and --format conflict -> exit 2 (catches accepts_mutually_exclusive)",
    ),
)


def prove(impl: Callable[[tuple[str, ...]], CliOutcome]) -> bool:
    """True iff parse/dispatch ``impl`` MISHANDLES any frozen corpus case.

    Non-circular and deterministic: each impl outcome is compared to the case's
    frozen expected ``CliOutcome`` (literal constant), never to the oracle
    object. No subprocess, clock, network, or filesystem I/O; no RNG. An impl
    that raises on a corpus case counts as caught.
    """
    for case in CLI_CORPUS:
        try:
            outcome = impl(case.argv)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if outcome != case.expected:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_dispatch,
    mutants=(
        Mutant("usage_error_exits_zero", dispatch_usage_error_exits_zero,
               "usage error exits 0 instead of 2 — callers see success on a failed invocation"),
        Mutant("accepts_mutually_exclusive", dispatch_accepts_mutually_exclusive,
               "accepts the mutually-exclusive --json/--format combo instead of erroring"),
        Mutant("misroutes_subcommand", dispatch_misroutes_subcommand,
               "dispatches `list` to the `add` handler — runs the wrong action"),
        Mutant("skips_required_operands", dispatch_skips_required_operands,
               "`add` runs with too few operands instead of a usage error"),
    ),
    corpus_size=len(CLI_CORPUS),
    kind="oracle_swap",
    notes="a usage error must exit 2, conflicting flags must be rejected, and each "
          "subcommand must dispatch to its own handler",
)


def list_oracle_cases() -> list[str]:
    return [c.name for c in CLI_CORPUS]


# ---------------------------------------------------------------------------
# Built-in self-test suite
# ---------------------------------------------------------------------------

def _build_self_test_suite(runner: SampleCliRunner) -> list[CliTestCase]:
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
    """Legacy subprocess-driven self-test: spawns the sample CLI and validates
    its behaviour. Returns 0 on all pass, 1 on any failure.

    Retained as a real end-to-end smoke test, but it is NOT the teeth: it uses
    subprocesses (non-deterministic, side-effecting), so the campaign self-test
    below gates on the pure in-process oracle/teeth instead and only runs this
    when subprocess execution is requested.
    """
    runner = SampleCliRunner()
    cases = _build_self_test_suite(runner)
    report = runner.run_suite(cases)
    print(report.summary())
    return 0 if report.success else 1


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False, *, subprocess_smoke: bool = False) -> int:
    """Campaign self-test.

    1. The correct oracle parser agrees with every frozen corpus expectation.
    2. Teeth: the oracle is clean and every planted mutant IS caught.
    3. (optional) The legacy subprocess smoke test of the sample CLI — skipped
       by default so the teeth/oracle checks stay pure, deterministic, and
       side-effect-free.
    """
    report = Report("core/cli")

    # 1. The correct oracle dispatch agrees with every frozen expectation.
    for case in CLI_CORPUS:
        report.add(f"oracle_case:{case.name}", case.expected,
                   oracle_dispatch(case.argv), detail=case.note)

    # 2. Teeth: oracle is not flagged and every planted mutant IS flagged.
    report.assert_teeth(TEETH)

    # 3. Optional live subprocess smoke test of the sample CLI.
    if subprocess_smoke:
        report.record("subprocess_smoke", run_self_test() == 0,
                      detail="sample CLI driven over real subprocesses")

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> Any:
    import argparse

    p = argparse.ArgumentParser(
        prog="cli_test_harness",
        description="CLI Tool Test Harness (harness 5 of 36)",
    )
    p.add_argument(
        "--self-test",
        action="store_true",
        help="Run the campaign self-test (oracle + teeth)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable findings (implies --self-test)",
    )
    p.add_argument(
        "--list-scenarios",
        action="store_true",
        help="list the frozen oracle corpus case names",
    )
    p.add_argument(
        "--subprocess-smoke",
        action="store_true",
        help="also run the legacy subprocess-driven sample-CLI smoke test",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.list_scenarios:
        print("\n".join(list_oracle_cases()))
        return 0
    # Default action (and --self-test/--json) is the campaign self-test.
    return _run_self_test(as_json=args.json, subprocess_smoke=args.subprocess_smoke)


if __name__ == "__main__":
    sys.exit(main())
