"""
Error-Path / Negative Coverage Test Harness (Harness 26 of 36)
Pure stdlib, zero external dependencies.
Mock HTTP server on dynamic port (default 19120).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from typing import Any

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BranchResult:
    """Result for a single labelled branch."""
    label: str
    hit: bool
    call_count: int


@dataclass
class NegativeCaseResult:
    """Result for a single negative test case."""
    input: Any
    expected_behavior: str
    actual_behavior: str
    passed: bool


@dataclass
class ErrorPathReport:
    """Aggregate report for an error-path test run."""
    branch_results: list[BranchResult] = field(default_factory=list)
    negative_results: list[NegativeCaseResult] = field(default_factory=list)
    exception_results: list[dict[str, Any]] = field(default_factory=list)
    null_results: list[dict[str, Any]] = field(default_factory=list)
    boundary_results: list[dict[str, Any]] = field(default_factory=list)
    timeout_results: list[dict[str, Any]] = field(default_factory=list)
    cleanup_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_tests(self) -> int:
        return (len(self.branch_results) + len(self.negative_results) +
                len(self.exception_results) + len(self.null_results) +
                len(self.boundary_results) + len(self.timeout_results) +
                len(self.cleanup_results))

    @property
    def passed_tests(self) -> int:
        count = 0
        for r in self.branch_results:
            if r.hit:
                count += 1
        for r in self.negative_results:
            if r.passed:
                count += 1
        for r in self.exception_results:
            if r.get("passed"):
                count += 1
        for r in self.null_results:
            if r.get("passed"):
                count += 1
        for r in self.boundary_results:
            if r.get("passed"):
                count += 1
        for r in self.timeout_results:
            if r.get("passed"):
                count += 1
        for r in self.cleanup_results:
            if r.get("passed"):
                count += 1
        return count


# ---------------------------------------------------------------------------
# CoverageProbe
# ---------------------------------------------------------------------------

class CoverageProbe:
    """Records which labelled branches execute."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._registered: set = set()

    def register(self, labels: list[str]) -> None:
        """Pre-register expected labels so never_hit() can report them."""
        for label in labels:
            self._registered.add(label)
            if label not in self._counts:
                self._counts[label] = 0

    def probe(self, label: str) -> None:
        """Record that a labelled branch was executed."""
        self._counts[label] = self._counts.get(label, 0) + 1
        self._registered.add(label)

    def hit(self, label: str) -> bool:
        """Return True if the label was ever probed."""
        return self._counts.get(label, 0) > 0

    def never_hit(self) -> list[str]:
        """Return labels that were registered but never executed."""
        return [label for label in self._registered
                if self._counts.get(label, 0) == 0]

    def call_count(self, label: str) -> int:
        """Return number of times a label was probed."""
        return self._counts.get(label, 0)

    def all_labels(self) -> list[str]:
        """Return all known labels."""
        return list(self._registered)

    def get_branch_results(self) -> list[BranchResult]:
        """Return BranchResult list for all registered labels."""
        results = []
        for label in sorted(self._registered):
            count = self._counts.get(label, 0)
            results.append(BranchResult(
                label=label,
                hit=count > 0,
                call_count=count
            ))
        return results

    def reset(self) -> None:
        """Clear all probed data but keep registered labels."""
        for label in self._counts:
            self._counts[label] = 0

    def reset_all(self) -> None:
        """Clear everything including registrations."""
        self._counts.clear()
        self._registered.clear()


# ---------------------------------------------------------------------------
# ExceptionPathTester
# ---------------------------------------------------------------------------

class ExceptionPathTester:
    """Forces exception paths and verifies correct behavior."""

    def __init__(self) -> None:
        self._results: list[dict[str, Any]] = []

    def test(
        self,
        func: Callable,
        bad_input: Any,
        expected_exc_type: type[Exception],
        expected_msg_fragment: str = "",
        state_obj: Any = None,
        state_snapshot_fn: Callable | None = None,
    ) -> dict[str, Any]:
        """
        Call func(bad_input), verifying:
        (a) correct exception type raised
        (b) error message contains expected text
        (c) object state unchanged after failure (if state_snapshot_fn provided)
        """
        # Capture pre-call state
        pre_state = None
        if state_obj is not None and state_snapshot_fn is not None:
            pre_state = state_snapshot_fn(state_obj)

        result = {
            "input": bad_input,
            "expected_exc_type": expected_exc_type.__name__,
            "expected_msg_fragment": expected_msg_fragment,
            "passed": False,
            "actual_exc_type": None,
            "actual_msg": None,
            "state_unchanged": None,
            "error": None,
        }

        try:
            if isinstance(bad_input, tuple):
                func(*bad_input)
            else:
                func(bad_input)
            result["error"] = "No exception raised"
        except Exception as exc:
            result["actual_exc_type"] = type(exc).__name__
            result["actual_msg"] = str(exc)

            type_ok = isinstance(exc, expected_exc_type)
            msg_ok = (not expected_msg_fragment or
                      expected_msg_fragment.lower() in str(exc).lower())

            state_ok = True
            if state_obj is not None and state_snapshot_fn is not None:
                post_state = state_snapshot_fn(state_obj)
                state_ok = (pre_state == post_state)
            result["state_unchanged"] = state_ok

            result["passed"] = type_ok and msg_ok and state_ok

        self._results.append(result)
        return result

    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    def all_passed(self) -> bool:
        return all(r["passed"] for r in self._results)

    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self._results if not r["passed"]]


# ---------------------------------------------------------------------------
# NullHandlingTester
# ---------------------------------------------------------------------------

class NullHandlingTester:
    """Injects None into every parameter position of a function."""

    def __init__(self, allowed_exc_types: list[type[Exception]] | None = None) -> None:
        # These exception types are acceptable when None is passed
        self._allowed = set(allowed_exc_types or [TypeError, ValueError])
        # Crashes with unexpected exceptions are failures
        self._crash_types = {AttributeError}
        self._results: list[dict[str, Any]] = []

    def test_function(
        self,
        func: Callable,
        baseline_args: list[Any],
        allowed_exc_types: list[type[Exception]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        For each positional argument in baseline_args, replace it with None
        and call func. Record whether it handles gracefully (returns a value
        or raises an allowed exception) or crashes badly.
        """
        allowed = set(allowed_exc_types or []) | self._allowed
        run_results = []

        for i, _ in enumerate(baseline_args):
            args = list(baseline_args)
            args[i] = None
            result = {
                "param_index": i,
                "args": args,
                "passed": False,
                "outcome": None,
                "exc_type": None,
                "exc_msg": None,
            }
            try:
                ret = func(*args)
                result["outcome"] = f"returned {ret!r}"
                result["passed"] = True
            except Exception as exc:
                exc_type = type(exc)
                result["exc_type"] = exc_type.__name__
                result["exc_msg"] = str(exc)
                if exc_type in allowed or any(issubclass(exc_type, a) for a in allowed):
                    result["outcome"] = f"raised allowed {exc_type.__name__}"
                    result["passed"] = True
                else:
                    result["outcome"] = f"crashed with {exc_type.__name__}: {exc}"
                    result["passed"] = False
            run_results.append(result)
            self._results.append(result)

        return run_results

    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    def all_passed(self) -> bool:
        return all(r["passed"] for r in self._results)


# ---------------------------------------------------------------------------
# BoundaryTester
# ---------------------------------------------------------------------------

class BoundaryTester:
    """Tests guard clauses with boundary inputs."""

    # Default boundary values to try
    BOUNDARY_INPUTS = [
        ("empty_string", ""),
        ("empty_list", []),
        ("empty_dict", {}),
        ("zero", 0),
        ("negative", -1),
        ("negative_large", -1000000),
        ("none", None),
        ("oversized_string", "x" * 100001),
        ("oversized_list", list(range(100001))),
    ]

    def __init__(self) -> None:
        self._results: list[dict[str, Any]] = []

    def test(
        self,
        func: Callable,
        boundary_input: Any,
        label: str = "",
        expect_raises: bool = True,
        allowed_exc_types: list[type[Exception]] | None = None,
        expect_return_value: Any = None,
        check_return_fn: Callable | None = None,
    ) -> dict[str, Any]:
        """
        Call func(boundary_input). By default expects an exception to be raised.
        If expect_raises=False, checks the return value or calls check_return_fn.
        """
        allowed = set(allowed_exc_types or [ValueError, TypeError, OverflowError,
                                            IndexError, KeyError])
        result = {
            "label": label or repr(boundary_input)[:40],
            "input": boundary_input,
            "passed": False,
            "outcome": None,
            "exc_type": None,
            "exc_msg": None,
        }

        try:
            ret = func(boundary_input)
            if expect_raises:
                result["outcome"] = f"no exception raised, returned {ret!r}"
                result["passed"] = False
            else:
                if check_return_fn is not None:
                    ok = check_return_fn(ret)
                    result["outcome"] = f"returned {ret!r}, check={'ok' if ok else 'fail'}"
                    result["passed"] = bool(ok)
                elif expect_return_value is not None:
                    result["passed"] = (ret == expect_return_value)
                    result["outcome"] = f"returned {ret!r}"
                else:
                    result["passed"] = True
                    result["outcome"] = f"returned {ret!r}"
        except Exception as exc:
            exc_type = type(exc)
            result["exc_type"] = exc_type.__name__
            result["exc_msg"] = str(exc)
            if expect_raises:
                if not allowed or exc_type in allowed or any(
                        issubclass(exc_type, a) for a in allowed):
                    result["passed"] = True
                    result["outcome"] = f"raised {exc_type.__name__}"
                else:
                    result["outcome"] = f"raised unexpected {exc_type.__name__}"
                    result["passed"] = False
            else:
                result["outcome"] = f"unexpected exception {exc_type.__name__}: {exc}"
                result["passed"] = False

        self._results.append(result)
        return result

    def test_all_defaults(
        self,
        func: Callable,
        allowed_exc_types: list[type[Exception]] | None = None,
    ) -> list[dict[str, Any]]:
        """Run all default boundary inputs against func."""
        results = []
        for label, val in self.BOUNDARY_INPUTS:
            results.append(self.test(func, val, label=label,
                                     allowed_exc_types=allowed_exc_types))
        return results

    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    def all_passed(self) -> bool:
        return all(r["passed"] for r in self._results)


# ---------------------------------------------------------------------------
# TimeoutTester
# ---------------------------------------------------------------------------

class TimeoutTester:
    """Calls a slow function with a timeout; verifies clean abort."""

    def __init__(self) -> None:
        self._results: list[dict[str, Any]] = []

    def test(
        self,
        func: Callable,
        args: tuple = (),
        kwargs: dict | None = None,
        timeout_seconds: float = 1.0,
        check_no_partial_data: Callable | None = None,
    ) -> dict[str, Any]:
        """
        Run func(*args, **kwargs) in a thread. If it doesn't finish within
        timeout_seconds, consider it timed out. Optionally verify no partial
        data was committed via check_no_partial_data().
        """
        kwargs = kwargs or {}
        result = {
            "func": getattr(func, "__name__", repr(func)),
            "timeout": timeout_seconds,
            "timed_out": False,
            "no_partial_data": None,
            "passed": False,
            "exc_type": None,
            "exc_msg": None,
            "return_value": None,
        }

        container = {"exc": None, "return_value": None, "done": False}

        def runner():
            try:
                container["return_value"] = func(*args, **kwargs)
            except Exception as exc:
                container["exc"] = exc
            finally:
                container["done"] = True

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=timeout_seconds)

        if t.is_alive():
            result["timed_out"] = True
            result["passed"] = True  # expected timeout
        else:
            result["timed_out"] = False
            if container["exc"] is not None:
                exc = container["exc"]
                result["exc_type"] = type(exc).__name__
                result["exc_msg"] = str(exc)
            result["return_value"] = container["return_value"]
            # If the function returned quickly, it's NOT a timeout case - still pass
            result["passed"] = True

        if check_no_partial_data is not None:
            no_partial = bool(check_no_partial_data())
            result["no_partial_data"] = no_partial
            if result["timed_out"]:
                result["passed"] = result["passed"] and no_partial

        self._results.append(result)
        return result

    def test_expects_timeout(
        self,
        func: Callable,
        args: tuple = (),
        kwargs: dict | None = None,
        timeout_seconds: float = 1.0,
        check_no_partial_data: Callable | None = None,
    ) -> dict[str, Any]:
        """Like test() but fails if the function does NOT time out."""
        kwargs = kwargs or {}
        result = {
            "func": getattr(func, "__name__", repr(func)),
            "timeout": timeout_seconds,
            "timed_out": False,
            "no_partial_data": None,
            "passed": False,
            "exc_type": None,
            "exc_msg": None,
            "return_value": None,
        }

        container = {"exc": None, "return_value": None, "done": False}

        def runner():
            try:
                container["return_value"] = func(*args, **kwargs)
            except Exception as exc:
                container["exc"] = exc
            finally:
                container["done"] = True

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=timeout_seconds)

        if t.is_alive():
            result["timed_out"] = True
            result["passed"] = True
        else:
            result["timed_out"] = False
            result["passed"] = False  # Expected timeout but function returned
            if container["exc"] is not None:
                exc = container["exc"]
                result["exc_type"] = type(exc).__name__
                result["exc_msg"] = str(exc)
            result["return_value"] = container["return_value"]

        if check_no_partial_data is not None:
            no_partial = bool(check_no_partial_data())
            result["no_partial_data"] = no_partial
            if result["timed_out"]:
                result["passed"] = result["passed"] and no_partial

        self._results.append(result)
        return result

    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    def all_passed(self) -> bool:
        return all(r["passed"] for r in self._results)


# ---------------------------------------------------------------------------
# ResourceCleanupTester
# ---------------------------------------------------------------------------

class ResourceCleanupTester:
    """Verifies try/finally resource release via acquire/release counters."""

    def __init__(self) -> None:
        self._results: list[dict[str, Any]] = []

    def test(
        self,
        func: Callable,
        acquire_fn: Callable,
        release_fn: Callable,
        args: tuple = (),
        kwargs: dict | None = None,
        expect_exception: bool = False,
    ) -> dict[str, Any]:
        """
        Run func(*args, **kwargs). Track acquire/release counts via the
        provided callables. A proper implementation always calls release,
        even if func raises. A leaking implementation (no finally) is flagged.
        """
        kwargs = kwargs or {}
        result = {
            "func": getattr(func, "__name__", repr(func)),
            "acquired": 0,
            "released": 0,
            "leaked": False,
            "passed": False,
            "exc_type": None,
            "exc_msg": None,
        }

        acquire_count = [0]
        release_count = [0]

        orig_acquire = acquire_fn
        orig_release = release_fn

        def counting_acquire(*a, **kw):
            acquire_count[0] += 1
            return orig_acquire(*a, **kw)

        def counting_release(*a, **kw):
            release_count[0] += 1
            return orig_release(*a, **kw)

        try:
            func(*args, acquire_fn=counting_acquire, release_fn=counting_release,
                 **kwargs)
        except Exception as exc:
            result["exc_type"] = type(exc).__name__
            result["exc_msg"] = str(exc)

        result["acquired"] = acquire_count[0]
        result["released"] = release_count[0]
        result["leaked"] = acquire_count[0] != release_count[0]
        result["passed"] = not result["leaked"]

        self._results.append(result)
        return result

    def test_with_counters(
        self,
        func: Callable,
        args: tuple = (),
        kwargs: dict | None = None,
    ) -> dict[str, Any]:
        """
        Simpler API: func receives a counter dict and should increment
        counter['acquired'] and counter['released']. We check for balance.
        """
        kwargs = kwargs or {}
        counters = {"acquired": 0, "released": 0}
        result = {
            "func": getattr(func, "__name__", repr(func)),
            "acquired": 0,
            "released": 0,
            "leaked": False,
            "passed": False,
            "exc_type": None,
            "exc_msg": None,
        }

        try:
            func(*args, counters=counters, **kwargs)
        except Exception as exc:
            result["exc_type"] = type(exc).__name__
            result["exc_msg"] = str(exc)

        result["acquired"] = counters["acquired"]
        result["released"] = counters["released"]
        result["leaked"] = counters["acquired"] != counters["released"]
        result["passed"] = not result["leaked"]

        self._results.append(result)
        return result

    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    def all_passed(self) -> bool:
        return all(r["passed"] for r in self._results)


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (acquired, released) resource-event counts -> the
# exact leak verdict a CORRECT try/finally cleanup auditor MUST return.
#
# This harness's whole reason to exist is catching resources that are acquired
# but not released. ResourceCleanupTester reduces every run to a pair of
# counters (acquired, released); the leak verdict is the pure function under
# test. The ONE correct rule is a balance check:
#
#     leaked  iff  acquired != released
#
# i.e. a clean run releases exactly what it acquired. Under-release (a missing
# ``finally``) AND over-release (a double-``close``/double-``release``, the
# use-after-free / double-free class) are both real leaks, so both must be
# flagged. ``acquired == released == 0`` (nothing acquired) is clean.
#
# An impl is a callable ``leaked(acquired, released) -> bool``. prove() judges
# each impl against the corpus's FROZEN LITERAL ``expected`` verdicts — every
# one hand-written here as a constant, NEVER read back from the oracle at
# runtime — so the check is non-circular: flip any single ``expected`` literal
# and prove(oracle) flips to True. prove(impl) is True iff any verdict diverges
# from its frozen literal — i.e. the planted leak-detector bug is caught.
#
# Pure + deterministic: integer comparison only, no clock/sleep, no RNG, no
# threads, no network, no filesystem. The mock HTTP server and the
# thread-based TimeoutTester are deliberately OUT of the teeth path (real
# sockets/threads are non-deterministic) — the teeth ride the pure counter
# verdict, exactly the in-process behavior the gate wants.
#
# The two planted mutants model genuine real-world cleanup-auditor defects:
#
#   * ignores_balance — the auditor only flags ``released < acquired``, so an
#     over-release / double-free where ``released > acquired`` slips past
#     unnoticed: a real "we only checked for under-release" bug (the common
#     mistake this harness's own ResourceCleanupTester used to make);
#   * off_by_one — a fencepost slip (``acquired - released > 1`` instead of
#     ``!= 0``) that "tolerates one unreleased handle", letting a genuine
#     single-handle leak go unreported.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakCase:
    """One frozen resource-cleanup case with a literal, hand-written verdict."""
    name: str
    acquired: int
    released: int
    expected: bool   # the EXACT leak verdict a correct balance check yields
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND each planted
# mutant now gets caught by at least TWO discriminating cases (no single-point-
# of-failure tooth — flipping or removing any one case still leaves the mutant
# caught). ``ignores_balance`` is caught by BOTH over-release cases
# (``double_release`` 1->2 and ``over_release_from_zero`` 0->1, where it wrongly
# reports clean). ``off_by_one`` is caught by BOTH single-handle leaks
# (``single_leak`` 2->1 and ``over_release_from_zero`` 0->1: there
# ``(0 - 1) > 1`` is False while the oracle flags it True). Every ``expected``
# is hand-written from the rule ``leaked iff acquired != released`` — a
# constant, never derived at runtime. ``balanced`` and ``no_resource`` are
# decoys neither mutant can distinguish (both agree with the oracle there), so
# the teeth come from the real leak/over-release cases, not coincidence.
LEAK_CORPUS: tuple[LeakCase, ...] = (
    # Acquired exactly as many as released: a clean try/finally. No leak.
    LeakCase("balanced", 2, 2, False,
             "acquired == released: a correct try/finally releases everything"),
    # Acquired 3, released only 1: a missing ``finally`` leaks 2 handles. Both
    # the oracle and both mutants agree this is a leak (under-release).
    LeakCase("under_release", 3, 1, True,
             "released < acquired: the classic missing-finally resource leak"),
    # Acquired 1, released 2: a double-``release``/double-free. The oracle flags
    # it; ``ignores_balance`` (released < acquired only) wrongly calls it clean.
    LeakCase("double_release", 1, 2, True,
             "released > acquired: over-release/double-free — ignores_balance "
             "misses this"),
    # Released 1 having acquired 0: a spurious/over-release (release without a
    # matching acquire). The oracle flags it (0 != 1); ``ignores_balance``
    # (released < acquired only) wrongly calls it clean — a 2nd over-release case
    # so ignores_balance is caught even if ``double_release`` is ever changed.
    LeakCase("over_release_from_zero", 0, 1, True,
             "released without acquiring: spurious/over-release -> "
             "ignores_balance misses it"),
    # Acquired 2, released 1: a single leaked handle. The oracle flags it;
    # ``off_by_one`` (tolerates a gap of one) wrongly calls it clean.
    LeakCase("single_leak", 2, 1, True,
             "off-by-one leak of exactly one handle — off_by_one misses this"),
    # Nothing acquired, nothing released: not a leak. A decoy — every impl
    # agrees, so it cannot be the source of the teeth.
    LeakCase("no_resource", 0, 0, False,
             "no resource acquired: nothing to leak"),
)


# --- ORACLE: the correct leak verdict (acquired != released) -----------------

def oracle_leaked(acquired: int, released: int) -> bool:
    """Correct cleanup verdict: a run leaks iff it did not release exactly what
    it acquired. Catches both under-release (missing ``finally``) and
    over-release (double-free)."""
    return acquired != released


# --- Planted buggy twins (each models a real cleanup-auditor defect) ---------

def ignores_balance(acquired: int, released: int) -> bool:
    """BUG: only flags UNDER-release (``released < acquired``), ignoring the
    over-release / double-free case where ``released > acquired``.

    It models the very common "we only ever checked for a missing release,
    never for releasing twice" auditor bug, so a double-free run is silently
    reported clean. (This harness's own ResourceCleanupTester used to ship this
    exact bug; it now uses the correct ``acquired != released`` balance rule.)
    """
    return acquired > 0 and released < acquired


def off_by_one(acquired: int, released: int) -> bool:
    """BUG: a fencepost slip that tolerates one unreleased handle.

    Models the "allow a gap of one" defect (e.g. a developer who wrote
    ``acquired - released > 1`` thinking one straggler is acceptable): a genuine
    single-handle leak (acquired 2, released 1) is wrongly reported clean.
    """
    return (acquired - released) > 1


def prove(impl: Callable[[int, int], bool]) -> bool:
    """True iff ``impl`` returns the WRONG leak verdict for any frozen corpus
    case (i.e. the cleanup-auditor bug is caught): its verdict diverges from
    the hand-written literal, or it raises.

    Non-circular + deterministic: every expectation is a literal baked into
    LEAK_CORPUS, never read from the oracle; integer comparison only, no
    RNG/clock/threads/network/filesystem. An impl that raises on a corpus case
    counts as caught.
    """
    for case in LEAK_CORPUS:
        try:
            verdict = impl(case.acquired, case.released)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if bool(verdict) != case.expected:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_leaked"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_leaked,
    mutants=(
        Mutant("ignores_balance", ignores_balance,
               "only flags released < acquired, so an over-release/double-free "
               "(released > acquired) is silently reported clean"),
        Mutant("off_by_one", off_by_one,
               "tolerates a gap of one (acquired - released > 1), so a genuine "
               "single-handle leak goes unreported"),
    ),
    corpus_size=len(LEAK_CORPUS),
    kind="oracle_swap",
    notes="resource cleanup leaks iff acquired != released; both under-release "
          "(missing finally) and over-release (double-free) are real leaks",
)


def list_teeth_scenarios() -> list[str]:
    """Names of the frozen resource-cleanup corpus cases (the teeth scenarios)."""
    return [c.name for c in LEAK_CORPUS]


def _run_self_test(as_json: bool = False) -> int:
    """Assert the teeth: the correct leak oracle reproduces every frozen verdict
    literal, and the universal swap-check passes (oracle clean, every planted
    cleanup-auditor mutant caught). Pure + deterministic — the thread-based
    TimeoutTester and the mock HTTP server are intentionally excluded."""
    report = Report("core/errorpath")

    # 1. The correct oracle reproduces every frozen leak-verdict literal exactly.
    for case in LEAK_CORPUS:
        verdict = oracle_leaked(case.acquired, case.released)
        report.add(f"oracle_leak:{case.name}", case.expected, verdict,
                   detail=case.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Error-path / negative-coverage harness self-test")
    parser.add_argument("--self-test", action="store_true",
                        help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_teeth_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


# ---------------------------------------------------------------------------
# MockErrorPathHandler  (HTTP server)
# ---------------------------------------------------------------------------

class MockErrorPathHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler that simulates various error paths:
    - GET /ok -> 200 with JSON body
    - GET /error -> 500 with error JSON
    - GET /notfound -> 404
    - GET /timeout -> delays 10s (simulates slow endpoint)
    - GET /badjson -> 200 with invalid JSON body
    - GET /empty -> 200 with empty body
    - POST /validate -> validates JSON body, returns 400 on bad input
    - GET /probe/<label> -> records coverage probe hit
    """

    # Class-level probe storage shared across all handler instances
    _probe_hits: dict[str, int] = {}
    _probe_lock = threading.Lock()

    def log_message(self, format, *args):
        # Suppress default HTTP server logs
        pass

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/ok":
            self._send_json(200, {"status": "ok", "data": "success"})

        elif path == "/error":
            self._send_json(500, {"status": "error", "message": "internal server error"})

        elif path == "/notfound":
            self._send_json(404, {"status": "error", "message": "not found"})

        elif path == "/timeout":
            time.sleep(10)  # simulate slow endpoint
            self._send_json(200, {"status": "ok"})

        elif path == "/badjson":
            body = b"not valid json {"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/empty":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        elif path.startswith("/probe/"):
            label = path[len("/probe/"):]
            with MockErrorPathHandler._probe_lock:
                MockErrorPathHandler._probe_hits[label] = (
                    MockErrorPathHandler._probe_hits.get(label, 0) + 1
                )
            self._send_json(200, {"label": label, "recorded": True})

        else:
            self._send_json(404, {"status": "error", "message": "unknown path"})

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/validate":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON"})
                return

            # Validate: must have 'value' key with positive integer
            if "value" not in data:
                self._send_json(400, {"error": "missing 'value' field"})
            elif not isinstance(data["value"], int):
                self._send_json(400, {"error": "'value' must be integer"})
            elif data["value"] <= 0:
                self._send_json(400, {"error": "'value' must be positive"})
            else:
                self._send_json(200, {"result": data["value"] * 2})

        else:
            self._send_json(404, {"status": "error", "message": "unknown path"})

    @classmethod
    def get_probe_hits(cls) -> dict[str, int]:
        with cls._probe_lock:
            return dict(cls._probe_hits)

    @classmethod
    def reset_probes(cls) -> None:
        with cls._probe_lock:
            cls._probe_hits.clear()


class QuietHTTPServer(HTTPServer):
    """Suppress expected client-abort tracebacks from negative-path tests."""

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def find_free_port(preferred: int = 19120) -> int:
    """Find a free port, preferring the given one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", preferred))
            return preferred
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


class ErrorPathServer:
    """Manages the mock HTTP server lifecycle."""

    def __init__(self, port: int = 0) -> None:
        self.port = find_free_port(port if port else 19120)
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> ErrorPathServer:
        self._server = QuietHTTPServer(("127.0.0.1", self.port), MockErrorPathHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> ErrorPathServer:
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()


if __name__ == "__main__":
    sys.exit(main())
