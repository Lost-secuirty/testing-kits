#!/usr/bin/env python3
"""
error_path_leak_test_harness.py — Resource leaks on the error path.
====================================================================

Pure-stdlib. Zero external dependencies.

"Allocation of Resources Without Limits or Throttling" was added to the 2025
CWE Top 25. A 1% leak rate under load takes services down in hours
(StackInsight 2025 empirical study). The typical AI-coded bug:

    conn = pool.acquire()
    do_work(conn)        # raises — release() never runs
    pool.release(conn)

This harness instruments an acquire/release pair, drives the wrapped
operation N times with injected error_rate, and asserts that the live
resource count returns to baseline. Detects DB-connection-pool leaks, file
descriptor leaks, lock leaks, subprocess-pipe leaks, and arbitrary
acquire/release pair violations.

Usage:
  python harnesses/core/error_path_leak_test_harness.py --self-test
  python harnesses/core/error_path_leak_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import random
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class ResourceTracker:
    """Counts live acquisitions. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live = 0
        self._high_water = 0
        self._total_acquired = 0
        self._total_released = 0

    def on_acquire(self) -> None:
        with self._lock:
            self._live += 1
            self._total_acquired += 1
            if self._live > self._high_water:
                self._high_water = self._live

    def on_release(self) -> None:
        with self._lock:
            self._live -= 1
            self._total_released += 1

    @property
    def live(self) -> int:
        with self._lock:
            return self._live

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "live": self._live,
                "high_water": self._high_water,
                "total_acquired": self._total_acquired,
                "total_released": self._total_released,
            }


# ---------------------------------------------------------------------------
# Config + result
# ---------------------------------------------------------------------------


@dataclass
class LeakProbeConfig:
    iterations: int = 1000
    error_rate: float = 0.3
    seed: int = 42
    tolerance: int = 0  # acceptable leftover live resources at the end


@dataclass
class LeakProbeResult:
    name: str
    iterations: int
    errors_injected: int
    final_live: int
    high_water: int
    leaked: bool
    detail: str = ""


@dataclass
class TargetSpec:
    """A pair (acquire, release) plus an operation that uses the resource."""

    name: str
    acquire: Callable[[ResourceTracker], Any]
    release: Callable[[ResourceTracker, Any], None]
    operation: Callable[[Any], None]


# ---------------------------------------------------------------------------
# Runner — wraps the op in a fault injector and tracks balance
# ---------------------------------------------------------------------------


class TransientError(RuntimeError):
    """Raised by the injector to simulate a downstream failure."""


class LeakRunner:
    def __init__(self, config: LeakProbeConfig):
        self.config = config

    def run(self, target: TargetSpec) -> LeakProbeResult:
        tracker = ResourceTracker()
        rng = random.Random(self.config.seed)
        errors = 0

        for i in range(self.config.iterations):
            resource = target.acquire(tracker)
            try:
                if rng.random() < self.config.error_rate:
                    errors += 1
                    target.operation(resource)
                    # Some buggy ops bail before raising — give the injector a
                    # second chance after the operation in case the bug is
                    # "skipped release on early-return".
                    raise TransientError(f"injected at iter {i}")
                target.operation(resource)
            except TransientError:
                pass
            except Exception as exc:
                return LeakProbeResult(
                    name=target.name,
                    iterations=i + 1,
                    errors_injected=errors,
                    final_live=tracker.live,
                    high_water=tracker.stats["high_water"],
                    leaked=tracker.live > self.config.tolerance,
                    detail=f"unexpected {type(exc).__name__}: {exc}",
                )
            finally:
                # Caller's release. If caller forgot to release on error path,
                # this stub does nothing — the leak shows up in tracker.live.
                pass

        leaked = tracker.live > self.config.tolerance
        return LeakProbeResult(
            name=target.name,
            iterations=self.config.iterations,
            errors_injected=errors,
            final_live=tracker.live,
            high_water=tracker.stats["high_water"],
            leaked=leaked,
            detail=f"live={tracker.live}, high_water={tracker.stats['high_water']}",
        )


# ---------------------------------------------------------------------------
# Self-test fixtures — toy acquire/release pairs with planted bugs
# ---------------------------------------------------------------------------


def _make_good_pool() -> TargetSpec:
    """Correctly releases on every path."""

    def acquire(tracker: ResourceTracker) -> dict:
        tracker.on_acquire()
        return {"id": id(object())}

    def release(tracker: ResourceTracker, resource: dict) -> None:
        tracker.on_release()

    def operation(resource: dict) -> None:
        acquire_tracker = resource.get("_tracker")
        try:
            _ = resource["id"]
            raise TransientError("simulated downstream")
        finally:
            # GOOD: always releases
            if acquire_tracker:
                acquire_tracker.on_release()

    # The "operation" pattern above leaks because tracker isn't on resource.
    # Use a simpler wrapper: acquire installs the tracker on the resource so
    # release can find it.
    def acquire_v2(tracker: ResourceTracker) -> dict:
        tracker.on_acquire()
        return {"id": id(object()), "_tracker": tracker, "_released": False}

    def operation_good(resource: dict) -> None:
        tracker = resource["_tracker"]
        try:
            _ = resource["id"]
            raise TransientError("simulated")
        finally:
            if not resource["_released"]:
                tracker.on_release()
                resource["_released"] = True

    return TargetSpec(
        name="good_pool",
        acquire=acquire_v2,
        release=lambda t, r: (t.on_release() if not r["_released"] else None) or r.update(_released=True),
        operation=operation_good,
    )


def _make_leaky_pool() -> TargetSpec:
    """Releases only on the happy path — leaks on every error."""

    def acquire(tracker: ResourceTracker) -> dict:
        tracker.on_acquire()
        return {"id": id(object()), "_tracker": tracker, "_released": False}

    def operation_bad(resource: dict) -> None:
        # BUG: this raises before releasing on the error path, and the
        # caller doesn't release in finally.
        raise TransientError("downstream failure")

    return TargetSpec(
        name="leaky_pool_error_path",
        acquire=acquire,
        release=lambda t, r: None,
        operation=operation_bad,
    )


def _make_leaky_fd() -> TargetSpec:
    """A file-descriptor-leak analog: ‘open’ but never ‘close’ on error."""

    def acquire(tracker: ResourceTracker) -> dict:
        tracker.on_acquire()
        return {"fd": id(object()), "_tracker": tracker}

    def operation_bad(resource: dict) -> None:
        # BUG: silently returns on some inputs without ever releasing.
        if id(resource) % 3 == 0:
            raise TransientError("fd error")
        # otherwise: forgot to release here too.

    return TargetSpec(
        name="leaky_fd",
        acquire=acquire,
        release=lambda t, r: None,
        operation=operation_bad,
    )


def _make_good_with_context() -> TargetSpec:
    """Uses a context-manager pattern internally — never leaks."""

    class _Resource:
        def __init__(self, tracker: ResourceTracker):
            self.tracker = tracker
            tracker.on_acquire()
            self.released = False

        def close(self) -> None:
            if not self.released:
                self.tracker.on_release()
                self.released = True

    def acquire(tracker: ResourceTracker) -> _Resource:
        return _Resource(tracker)

    def release(tracker: ResourceTracker, r: _Resource) -> None:
        r.close()

    def operation_good(resource: _Resource) -> None:
        try:
            raise TransientError("simulated")
        finally:
            resource.close()

    return TargetSpec(
        name="good_context_manager",
        acquire=acquire,
        release=release,
        operation=operation_good,
    )


def _make_double_release() -> TargetSpec:
    """Releases twice on success — under-counts live resources, eventually negative."""

    def acquire(tracker: ResourceTracker) -> dict:
        tracker.on_acquire()
        return {"_tracker": tracker, "_released": False}

    def operation_bad(resource: dict) -> None:
        tracker = resource["_tracker"]
        tracker.on_release()
        tracker.on_release()  # BUG: double release
        resource["_released"] = True

    return TargetSpec(
        name="double_release",
        acquire=acquire,
        release=lambda t, r: None,
        operation=operation_bad,
    )


# ---------------------------------------------------------------------------
# TEETH: error-PATH information leak.
#
# The other failure mode of the error path (besides leaking a *resource*) is
# leaking *information*: an exception handler that echoes the raw exception, an
# internal filesystem path, a stack frame, or — worst — a live credential into
# the response body or the log it writes. "Generation of Error Message
# Containing Sensitive Information" (CWE-209) is a perennial OWASP finding, and
# LLM-generated handlers reach for `return {"error": str(exc)}` constantly,
# which happily serialises a connection string with its password in it.
#
# The teeth model an error HANDLER:
#     handle(scenario: ErrorScenario) -> str   # the user-visible error message
# A FROZEN corpus pins, for each scenario, the literal secret/internal token
# that MUST NOT appear in the handler's output. The correct ORACLE sanitises:
# it returns a fixed, public-safe message keyed only by an error *class*, never
# interpolating the raised exception, the path, or the secret. Each MUTANT is a
# faithful real-world leak: echo the raw exception text (which carries the DSN
# password), splice the internal filesystem path into a 404, or attach a debug
# field that dumps the whole config including the API key.
#
# prove(handle) drives the handler across every frozen scenario and returns True
# iff ANY forbidden token survives into the output (the leak got through) — or
# the handler crashes / changes the public message it is contractually required
# to return. It is NON-CIRCULAR: every forbidden token + every required public
# message is a literal baked into the corpus, never read back from the oracle.
# Pure + deterministic: no clock/network/filesystem/RNG/thread-timing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorScenario:
    """One frozen error case: what blew up, and the exact tokens that must not leak.

    ``forbidden`` are literal substrings (a credential, an internal absolute
    path, a raw stack-frame marker) that the handler's output MUST NOT contain.
    ``public_message`` is the exact sanitized string a correct handler returns —
    pinned so a handler cannot "sanitize" by emptying or mangling the message.
    """

    name: str
    kind: str                       # error class the handler dispatches on
    raw_exception: str              # the str(exc) a naive handler would echo
    internal_path: str              # an internal absolute path on the error path
    secret: str                     # a live credential present in scope
    forbidden: tuple[str, ...]      # substrings that MUST NOT appear in output
    public_message: str             # the exact sanitized message a good handler returns
    note: str = ""


# Cases chosen so the correct (sanitizing) oracle returns each public_message
# verbatim with zero forbidden tokens, while at least one planted mutant leaks a
# forbidden token (or alters the public message) on each. All literals are
# authored here; nothing is derived from the oracle at runtime.
LEAK_CORPUS: tuple[ErrorScenario, ...] = (
    # DB connect failure: the DSN password is the crown-jewel leak. A handler
    # that echoes str(exc) ships "S3cr3t!" straight to the client.
    ErrorScenario(
        name="db_connect_dsn_password",
        kind="db_error",
        raw_exception=(
            "OperationalError: could not connect to "
            "postgresql://app:S3cr3t!@10.0.0.5:5432/prod"
        ),
        internal_path="/srv/app/db/pool.py",
        secret="S3cr3t!",
        forbidden=(
            "S3cr3t!",
            "postgresql://app:S3cr3t!@10.0.0.5:5432/prod",
            "/srv/app/db/pool.py",
            "OperationalError",
        ),
        public_message="A database error occurred. Please try again later.",
        note="naive str(exc) echo leaks the DSN password into the response",
    ),
    # Missing file: the absolute internal path discloses deploy layout. A 404
    # that interpolates the path hands an attacker the directory structure.
    ErrorScenario(
        name="file_not_found_internal_path",
        kind="not_found",
        raw_exception=(
            "FileNotFoundError: [Errno 2] No such file or directory: "
            "'/srv/app/secrets/users.db'"
        ),
        internal_path="/srv/app/secrets/users.db",
        secret="users.db",
        forbidden=(
            "/srv/app/secrets/users.db",
            "FileNotFoundError",
            "Errno 2",
        ),
        public_message="The requested resource was not found.",
        note="interpolating the internal path into a 404 leaks deploy layout",
    ),
    # Auth failure: the API key lives in the config the debug dump attaches. A
    # handler that bolts on a "debug" field with the config dumps the key.
    ErrorScenario(
        name="auth_failure_api_key_debug_field",
        kind="auth_error",
        raw_exception="AuthError: token rejected by upstream",
        internal_path="/srv/app/auth/verify.py",
        secret="sk-live-AKIA9f8h2x7QWERTY",  # allowlist secret  (intentional fake fixture: proves the redactor strips a realistic key)
        forbidden=(
            "sk-live-AKIA9f8h2x7QWERTY",
            "/srv/app/auth/verify.py",
            'Traceback (most recent call last)',
        ),
        public_message="Authentication failed.",
        note="a debug field that dumps config leaks the live API key",
    ),
)


# --- ORACLE: a correct, sanitizing error handler --------------------------- #

# The ONLY thing a sanitizing handler is allowed to vary on is the error class.
# It never interpolates the exception, the path, or any secret.
_PUBLIC_BY_KIND: dict[str, str] = {
    "db_error": "A database error occurred. Please try again later.",
    "not_found": "The requested resource was not found.",
    "auth_error": "Authentication failed.",
}


def oracle_handle(scenario: ErrorScenario) -> str:
    """Correct handler: returns a fixed public message keyed on the error class.

    Crucially it ignores ``raw_exception``, ``internal_path`` and ``secret``
    entirely — the sanitized message is a constant per class, so no caller-
    controlled or sensitive data can ride out in the response.
    """
    return _PUBLIC_BY_KIND.get(scenario.kind, "An unexpected error occurred.")


# --- Planted buggy twins (each models a genuine real-world leak) ----------- #

def leak_raw_exception(scenario: ErrorScenario) -> str:
    """BUG: echoes the raw exception text into the response.

    The single most common LLM-authored handler: ``return {"error": str(exc)}``.
    For the DB case this ships the full DSN — including the password — to the
    client. Models CWE-209 information exposure through an error message.
    """
    return f"Error: {scenario.raw_exception}"


def leak_internal_path(scenario: ErrorScenario) -> str:
    """BUG: splices the internal filesystem path into the message.

    A 404/handler that does ``f"Could not load {path}"`` discloses the server's
    absolute directory layout (and here the name of a secrets DB), a real
    reconnaissance leak from over-detailed not-found responses.
    """
    return f"Could not load resource at {scenario.internal_path}"


def leak_secret_debug_field(scenario: ErrorScenario) -> str:
    """BUG: attaches a 'debug' suffix that dumps in-scope config incl. the secret.

    The classic "leave debug on in prod" defect — the handler appends a debug
    blob meant for staging that serialises the whole config, leaking the live
    API key. Models a sensitive value escaping via an error/debug channel.
    """
    public = _PUBLIC_BY_KIND.get(scenario.kind, "An unexpected error occurred.")
    return f"{public} [debug: api_key={scenario.secret}]"


def prove(handle: Callable[[ErrorScenario], str]) -> bool:
    """True iff ``handle`` LEAKS on any frozen scenario (i.e. the bug is caught).

    A leak is: any forbidden token appearing in the output, OR the handler not
    returning the exact public message the scenario contractually requires
    (so a handler cannot dodge the check by mangling/emptying the message), OR
    raising on a corpus case. Judged purely against the frozen literals in
    LEAK_CORPUS — never against the oracle object — so it is non-circular and
    fully deterministic (no clock/network/filesystem/RNG/thread-timing).
    """
    for scenario in LEAK_CORPUS:
        try:
            out = handle(scenario)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if not isinstance(out, str):
            return True
        # 1. No forbidden token may survive into the user-visible output.
        for token in scenario.forbidden:
            if token in out:
                return True
        # 2. The output must be the exact sanitized public message. This makes
        #    a leak that ALSO keeps the public text (the debug-field mutant)
        #    detectable, and stops a "sanitizer" that just returns "" from
        #    passing vacuously.
        if out != scenario.public_message:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_handle,
    mutants=(
        Mutant("echo_raw_exception", leak_raw_exception,
               "return str(exc) — leaks the DSN password from the DB error into "
               "the response (CWE-209)"),
        Mutant("leak_internal_path", leak_internal_path,
               "interpolates the internal absolute path into a not-found message "
               "— discloses deploy layout / secrets-db name"),
        Mutant("leak_secret_debug_field", leak_secret_debug_field,
               "appends a debug blob dumping config — leaks the live API key "
               "while still returning the public message"),
    ),
    corpus_size=len(LEAK_CORPUS),
    kind="oracle_swap",
    notes="an error handler must return a fixed sanitized message per error "
          "class and never leak a credential, internal path, or raw exception "
          "into the response/log",
)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def list_scenarios() -> list[str]:
    return [
        "good_pool",
        "leaky_pool_error_path",
        "leaky_fd",
        "good_context_manager",
        "double_release",
    ]


def list_leak_scenarios() -> list[str]:
    """Names of the frozen error-path information-leak corpus cases."""
    return [s.name for s in LEAK_CORPUS]


def _self_test_targets() -> list[TargetSpec]:
    return [
        _make_good_pool(),
        _make_leaky_pool(),
        _make_leaky_fd(),
        _make_good_with_context(),
        _make_double_release(),
    ]


def _run_self_test(config: LeakProbeConfig, verbose: bool = False) -> int:
    runner = LeakRunner(config)
    results = [runner.run(t) for t in _self_test_targets()]

    if verbose:
        for r in results:
            print(f"  {r.name}: leaked={r.leaked} final_live={r.final_live} "
                  f"errors_injected={r.errors_injected}/{r.iterations}")

    # Acceptance:
    expectations = {
        "good_pool": False,
        "leaky_pool_error_path": True,
        "leaky_fd": True,
        "good_context_manager": False,
        "double_release": True,  # final_live is negative — still a balance violation
    }

    failures: list[str] = []
    for r in results:
        expected_leak = expectations.get(r.name, False)
        observed_leak = r.leaked or r.final_live < 0
        if observed_leak != expected_leak:
            failures.append(f"{r.name}: expected leaked={expected_leak} "
                            f"got final_live={r.final_live}")

    print(f"Ran {len(results)} leak scenarios.")
    for r in results:
        verdict = "LEAK" if (r.leaked or r.final_live < 0) else "OK  "
        print(f"  {verdict}  {r.name:30s} final_live={r.final_live:+d} "
              f"errors={r.errors_injected}/{r.iterations}")

    if failures:
        print("FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: every scenario matched its expectation.")
    return 0


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------


def _run_report_self_test(config: LeakProbeConfig, as_json: bool = False) -> int:
    report = Report("core/error_path_leak")

    # 1. Resource-leak scenarios still match their expectations (existing logic).
    runner = LeakRunner(config)
    leak_expectations = {
        "good_pool": False,
        "leaky_pool_error_path": True,
        "leaky_fd": True,
        "good_context_manager": False,
        "double_release": True,  # final_live goes negative — still a balance violation
    }
    for target in _self_test_targets():
        r = runner.run(target)
        observed = r.leaked or r.final_live < 0
        report.add(f"resource_leak:{r.name}", leak_expectations.get(r.name, False),
                   observed, detail=r.detail)

    # 2. The sanitizing oracle returns the exact public message and leaks nothing
    #    on every frozen error-path information-leak scenario.
    for sc in LEAK_CORPUS:
        out = oracle_handle(sc)
        report.add(f"oracle_message:{sc.name}", sc.public_message, out, detail=sc.note)
        leaked = any(tok in out for tok in sc.forbidden)
        report.record(f"oracle_no_leak:{sc.name}", not leaked, detail=sc.note)

    # 3. Teeth: prove(oracle) is False AND every planted leak mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resource- and information-leak detector on the error path",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--list-leak-scenarios", action="store_true",
                   help="List the frozen error-path information-leak corpus cases")
    p.add_argument("--iterations", type=int, default=1000)
    p.add_argument("--error-rate", type=float, default=0.3)
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.list_leak_scenarios:
        for s in list_leak_scenarios():
            print(s)
        return 0
    config = LeakProbeConfig(iterations=args.iterations, error_rate=args.error_rate)
    if args.self_test or args.json:
        return _run_report_self_test(config, as_json=args.json)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
