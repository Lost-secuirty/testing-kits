#!/usr/bin/env python3
"""
exceptional_conditions_test_harness.py — Mishandling of Exceptional Conditions.
===============================================================================

Pure-stdlib. Zero external dependencies.

A10 is new in OWASP Top 10:2025 and is a classic AI-generated-code smell
(models love ``try/except: pass``). This harness catches the code-level forms.
Maps to OWASP Top 10:2025 — A10:2025 Mishandling of Exceptional Conditions
(new category in 2025).

Hotspots / attacks exercised:
- Fail-open on exception: a security guard that ALLOWS access when it throws. (CWE-636)
- Swallowed exceptions: bare ``except:`` / ``except Exception: pass`` hiding a check. (CWE-390/391)
- Sensitive data in error output: stack traces, file paths, SQL, secrets, PII. (CWE-209)
- Error-path resource leaks: resource acquired but not released on exception. (CWE-404/772)

A checker never raises on hostile input; it returns (flagged: bool, reason: str) or findings.

TEETH: the harness's own exceptional-conditions auditor (oracle_exceptional_audit)
judged against a FROZEN corpus of (kind, should_flag) literals. Each planted Mutant
is a realistic defect (a fail-open tester that treats a raised exception as ALLOW, a
swallowed-exception scanner that misses the bare ``except:`` form, an error-leak
checker whose SQL pattern is anchored so embedded SQL slips through). prove() compares
each auditor to the frozen should_flag literal — never to the oracle — so it is
non-circular and deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/exceptional_conditions_test_harness.py --self-test
    python harnesses/security/exceptional_conditions_test_harness.py --json
    python harnesses/security/exceptional_conditions_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class ExcFinding:
    check_name: str
    severity: str
    description: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"Invalid severity: {self.severity}")


# ---------------------------------------------------------------------------
# FailOpenTester (CWE-636)
# ---------------------------------------------------------------------------

class FailOpenTester:
    """Drive a guard with input engineered to raise; a guard that returns
    allow on error fails OPEN (bad). A guard that denies or propagates the
    exception fails CLOSED (safe)."""

    def test(self, guard: Callable, raising_input) -> tuple[bool, str]:
        try:
            result = guard(raising_input)
        except Exception:  # noqa: BLE001 — a propagating guard fails closed, which is safe
            return False, "Guard propagated the exception (fails closed)"
        if result:
            return True, "Guard returned ALLOW on internal error (fail-open, CWE-636)"
        return False, "Guard returned DENY on error (fails closed)"


# ---------------------------------------------------------------------------
# SwallowedExceptionScanner (CWE-390/391)
# ---------------------------------------------------------------------------

_SECURITY_NAME_RE = re.compile(r"auth|verify|check|validat|permission|access|login|token",
                               re.IGNORECASE)


def _is_truthy_const(node) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value) is True


class SwallowedExceptionScanner:
    """AST scan for exception handlers that hide failures."""

    def scan(self, source_text: str) -> list[ExcFinding]:
        findings: list[ExcFinding] = []
        try:
            tree = ast.parse(source_text or "")
        except SyntaxError:
            return findings
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = node.body
            broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
            )
            only_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
            only_ellipsis = (
                len(body) == 1 and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis
            )
            returns_allow = any(
                isinstance(s, ast.Return) and _is_truthy_const(s.value) for s in body
            )
            line = getattr(node, "lineno", 0)
            if only_pass or only_ellipsis:
                sev = "HIGH" if broad else "MEDIUM"
                findings.append(ExcFinding(
                    "SwallowedExceptionScanner", sev,
                    "Exception swallowed (empty handler body)", f"line {line}"))
            elif broad and returns_allow:
                findings.append(ExcFinding(
                    "SwallowedExceptionScanner", "HIGH",
                    "Broad handler returns an allow/truthy value (fail-open)", f"line {line}"))
        return findings


# ---------------------------------------------------------------------------
# ErrorLeakChecker (CWE-209)
# ---------------------------------------------------------------------------

_LEAK_PATTERNS = [
    (re.compile(r"Traceback \(most recent call last\)"), "Stack trace in response"),
    (re.compile(r'File "[^"]+", line \d+'), "Source file path/line in response"),
    (re.compile(r"(?is)\bSELECT\b.+?\bFROM\b"), "SQL statement in error"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "Secret/key in error"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN-like PII in error"),
    (re.compile(r"(?i)\bat [\w./]+:\d+\)"), "Stack frame in error"),
]


class ErrorLeakChecker:
    """Detect sensitive content leaking through an error message/response body."""

    def check(self, error_response: str) -> tuple[bool, str]:
        text = error_response or ""
        for pattern, label in _LEAK_PATTERNS:
            if pattern.search(text):
                return True, f"{label} (CWE-209)"
        return False, "Error output appears sanitized"


# ---------------------------------------------------------------------------
# ResourceLeakTester (CWE-404/772)
# ---------------------------------------------------------------------------

class _TrackedResource:
    def __init__(self, tracker: dict) -> None:
        self.tracker = tracker
        tracker["open"] = tracker.get("open", 0) + 1

    def __enter__(self) -> _TrackedResource:
        return self

    def __exit__(self, *exc) -> bool:
        self.tracker["closed"] = self.tracker.get("closed", 0) + 1
        return False  # do not suppress


class ResourceLeakTester:
    """Run a body that may raise; report whether every acquired resource was released."""

    def make_resource(self, tracker: dict) -> _TrackedResource:
        return _TrackedResource(tracker)

    def leaks(self, body: Callable[[dict], None]) -> tuple[bool, str]:
        tracker: dict = {"open": 0, "closed": 0}
        # The body may raise; we measure cleanup, not the error itself.
        with contextlib.suppress(Exception):
            body(tracker)
        leaked = tracker["open"] > tracker["closed"]
        return leaked, f"open={tracker['open']} closed={tracker['closed']}"


# ---------------------------------------------------------------------------
# Scenario results (legacy --verbose view)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n      {self.detail}"
        return msg


# Shared checker instances (the harness's own guards).
_fail_open = FailOpenTester()
_swallow = SwallowedExceptionScanner()
_leak = ErrorLeakChecker()
_res = ResourceLeakTester()


def _raise(_):
    raise RuntimeError("lookup failed")


def _safe_guard(x):
    try:
        return _raise(x)
    except Exception:  # noqa: BLE001 — fixture guard that fails closed
        return False


def _bad_guard(x):
    try:
        return _raise(x)
    except Exception:  # noqa: BLE001 — fixture guard that fails OPEN (the defect under test)
        return True


_SAFE_SOURCE = (
    "try:\n    do_auth()\nexcept KeyError as e:\n    log.warning('auth failed: %s', e.args[0])\n    return False\n"
)
_BAD_SOURCE_SWALLOW = "try:\n    check_token()\nexcept Exception:\n    pass\n"
_BAD_SOURCE_FAILOPEN = "try:\n    authorize()\nexcept Exception:\n    return True\n"
_BAD_SOURCE_BARE = "try:\n    f()\nexcept:\n    pass\n"
_MALFORMED_SOURCE = "def ( this is not python"

_SAFE_ERROR = '{"error": "internal error", "id": "req-abc123"}'
_BAD_ERROR = 'Traceback (most recent call last):\n  File "/app/db.py", line 42\nSELECT * FROM users WHERE id=1'
_BAD_ERROR_SQL = "error running SELECT password FROM users WHERE id=1"


def _safe_body(tracker):
    with _res.make_resource(tracker):
        raise ValueError("boom")  # __exit__ still closes


def _bad_body(tracker):
    _res.make_resource(tracker)  # acquired without a context manager
    raise ValueError("boom")     # never closed


# ===========================================================================
# TEETH: the exceptional-conditions auditor judged against a frozen literal
# corpus. kind = auditor. The oracle dispatches each case to the correct
# checker and returns whether the condition should be FLAGGED. Each Mutant is a
# faithful planted defect. prove() compares to the frozen should_flag literal.
# ===========================================================================

@dataclass(frozen=True)
class ExcCase:
    """One frozen exceptional-conditions fixture. ``kind`` selects the checker;
    ``should_flag`` is the independently-pinned ground truth."""
    name: str
    kind: str  # "fail_open" | "swallow" | "leak" | "resource"
    payload: str  # a key the oracle resolves to the right fixture
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned),
# never read back from a checker. Includes the discriminators each mutant gets
# wrong (bare_except for the swallow mutant, embedded SQL for the leak mutant,
# the propagating + fail-open guards for the fail-open mutant).
EXC_CORPUS: tuple[ExcCase, ...] = (
    # fail-open (CWE-636)
    ExcCase("safe_guard_fails_closed", "fail_open", "safe", False),
    ExcCase("propagating_guard_fails_closed", "fail_open", "raise", False),
    ExcCase("bad_guard_fails_open", "fail_open", "bad", True),
    # swallowed exceptions (CWE-390/391)
    ExcCase("safe_handler_logged", "swallow", "safe", False),
    ExcCase("malformed_source_no_crash", "swallow", "malformed", False),
    ExcCase("bad_swallowed_exception", "swallow", "swallow", True),
    ExcCase("bad_failopen_in_except", "swallow", "failopen", True),
    ExcCase("bad_bare_except_pass", "swallow", "bare", True),
    # error leakage (CWE-209)
    ExcCase("safe_error_sanitized", "leak", "safe", False),
    ExcCase("bad_error_leaks_trace", "leak", "trace", True),
    ExcCase("bad_error_leaks_sql", "leak", "sql", True),
    # resource leaks (CWE-404/772)
    ExcCase("safe_resource_released", "resource", "safe", False),
    ExcCase("bad_resource_leaked", "resource", "bad", True),
)


def oracle_exceptional_audit(case: ExcCase) -> bool:
    """Correct verdict: does this case mishandle an exceptional condition (flag it)?

    Pure over its argument — dispatches to the harness's own checkers, no I/O.
    """
    if case.kind == "fail_open":
        guard = {"safe": _safe_guard, "bad": _bad_guard, "raise": _raise}[case.payload]
        return _fail_open.test(guard, "x")[0]
    if case.kind == "swallow":
        src = {
            "safe": _SAFE_SOURCE,
            "swallow": _BAD_SOURCE_SWALLOW,
            "failopen": _BAD_SOURCE_FAILOPEN,
            "bare": _BAD_SOURCE_BARE,
            "malformed": _MALFORMED_SOURCE,
        }[case.payload]
        return len(_swallow.scan(src)) > 0
    if case.kind == "leak":
        text = {"safe": _SAFE_ERROR, "trace": _BAD_ERROR, "sql": _BAD_ERROR_SQL}[case.payload]
        return _leak.check(text)[0]
    if case.kind == "resource":
        body = {"safe": _safe_body, "bad": _bad_body}[case.payload]
        return _res.leaks(body)[0]
    raise ValueError(f"unknown exceptional case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic exceptional-handling defect) ---

def mutant_fail_open_treats_raise_as_allow(case: ExcCase) -> bool:
    """BUG: the fail-open tester treats a guard that RAISES as a fail-open (flags
    it), when a propagating exception actually fails CLOSED. So a correctly
    fail-closed guard that re-raises is wrongly reported as a vulnerability — a
    real inverted error-semantics bug. Other checkers correct."""
    if case.kind == "fail_open":
        guard = {"safe": _safe_guard, "bad": _bad_guard, "raise": _raise}[case.payload]
        try:
            result = guard("x")
        except Exception:  # noqa: BLE001
            return True  # BUG: a raised exception is treated as fail-open
        return bool(result)
    return oracle_exceptional_audit(case)


def mutant_swallow_misses_bare_except(case: ExcCase) -> bool:
    """BUG: the swallowed-exception scanner only recognizes ``except Exception:``
    by name and ignores a bare ``except:`` (node.type is None), so the broadest,
    most dangerous swallow form slips through unflagged — a real handler-shape gap."""
    if case.kind == "swallow":
        src = {
            "safe": _SAFE_SOURCE,
            "swallow": _BAD_SOURCE_SWALLOW,
            "failopen": _BAD_SOURCE_FAILOPEN,
            "bare": _BAD_SOURCE_BARE,
            "malformed": _MALFORMED_SOURCE,
        }[case.payload]
        try:
            tree = ast.parse(src or "")
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            # BUG: requires a NAMED Exception handler; bare `except:` is skipped.
            if not (isinstance(node.type, ast.Name)
                    and node.type.id in ("Exception", "BaseException")):
                continue
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                return True
            if any(isinstance(s, ast.Return) and _is_truthy_const(s.value) for s in body):
                return True
        return False
    return oracle_exceptional_audit(case)


def mutant_leak_sql_anchored(case: ExcCase) -> bool:
    """BUG: the error-leak checker anchors the SQL pattern to the start of the
    string (``^SELECT``) instead of searching anywhere, so an error message with
    a leaked SQL statement EMBEDDED mid-text ('error running SELECT ...') is not
    caught — a real anchoring mistake that under-reports leaks."""
    if case.kind == "leak":
        text = {"safe": _SAFE_ERROR, "trace": _BAD_ERROR, "sql": _BAD_ERROR_SQL}[case.payload]
        patterns = [
            (re.compile(r"Traceback \(most recent call last\)"), "trace"),
            (re.compile(r'File "[^"]+", line \d+'), "file"),
            (re.compile(r"(?is)^\bSELECT\b.+?\bFROM\b"), "sql"),  # BUG: anchored to start
            (re.compile(r"AKIA[0-9A-Z]{16}"), "secret"),
            (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
        ]
        return any(p.search(text) for p, _ in patterns)
    return oracle_exceptional_audit(case)


def prove(audit: Callable[[ExcCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    ExcCase.should_flag constant, never against the oracle. An auditor that
    raises on a corpus case counts as caught.
    """
    for case in EXC_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_exceptional_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_exceptional_audit,
    mutants=(
        Mutant("fail_open_treats_raise_as_allow", mutant_fail_open_treats_raise_as_allow,
               "fail-open tester treats a propagating (fail-closed) guard as a fail-open vulnerability"),
        Mutant("swallow_misses_bare_except", mutant_swallow_misses_bare_except,
               "swallowed-exception scanner ignores bare `except:` (node.type is None), so it slips through"),
        Mutant("leak_sql_anchored", mutant_leak_sql_anchored,
               "error-leak checker anchors SQL to string start, so embedded leaked SQL is missed"),
    ),
    corpus_size=len(EXC_CORPUS),
    kind="auditor",
    notes="fail-open (allow-on-error), swallowed exceptions (empty/fail-open handlers incl. bare except), "
          "error leakage (substring search), resource release on exception",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = EXC_CORPUS


def run_case(case: ExcCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_exceptional_audit(case)


# ---------------------------------------------------------------------------
# Legacy scenario view (kept for the paired unittest + --verbose)
# ---------------------------------------------------------------------------

def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        r = ScenarioResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)

    check("1. fail-closed guard accepted", _fail_open.test(_safe_guard, "x")[0] is False)
    check("2. fail-open guard flagged", _fail_open.test(_bad_guard, "x")[0] is True)
    check("3. logged handler not flagged", len(_swallow.scan(_SAFE_SOURCE)) == 0)
    check("4. except: pass flagged", len(_swallow.scan(_BAD_SOURCE_SWALLOW)) >= 1)
    check("5. fail-open in except flagged", len(_swallow.scan(_BAD_SOURCE_FAILOPEN)) >= 1)
    check("6. bare-except pass flagged", len(_swallow.scan(_BAD_SOURCE_BARE)) >= 1)
    check("7. sanitized error accepted", _leak.check(_SAFE_ERROR)[0] is False)
    check("8. traceback leak flagged", _leak.check(_BAD_ERROR)[0] is True)
    check("9. SQL-in-error flagged", _leak.check(_BAD_ERROR_SQL)[0] is True)
    check("10. resource released on exception accepted", _res.leaks(_safe_body)[0] is False)
    check("11. resource leak flagged", _res.leaks(_bad_body)[0] is True)
    check("12. malformed source handled (no crash)",
          isinstance(_swallow.scan(_MALFORMED_SOURCE), list))

    for case in EXC_CORPUS:
        check(f"proof:{case.name}", run_case(case) == case.should_flag,
              f"expected flag={case.should_flag}")

    return results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


# ---------------------------------------------------------------------------
# Report-based self-test — exercises the oracle by module-global name (so the
# vacuity gate's neuter is caught here) and asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    report = Report("security/exceptional_conditions")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_exceptional_audit by its module-global name is what the
    # vacuity gate's neuter breaks.
    for case in EXC_CORPUS:
        report.add(f"exc:{case.name}", case.should_flag,
                   oracle_exceptional_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="exceptional_conditions_test_harness",
        description="OWASP A10:2025 Mishandling of Exceptional Conditions harness (pure stdlib)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run all scenarios; exit 0 if all pass")
    p.add_argument("--json", action="store_true", help="Emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        for name in list_scenarios():
            print(name)
        return 0
    return _run_self_test(verbose=args.verbose, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
