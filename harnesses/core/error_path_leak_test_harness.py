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
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


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
        tracker = resource["_tracker"]
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
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resource-leak detector on the error path",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
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
    config = LeakProbeConfig(iterations=args.iterations, error_rate=args.error_rate)
    if args.self_test:
        return _run_self_test(config, verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
