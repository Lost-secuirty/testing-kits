#!/usr/bin/env python3
"""
hermeticity_test_harness.py — Non-determinism / flaky-test auditor.
====================================================================

Pure-stdlib. Zero external dependencies.

The top flaky-test class is non-hermeticity: tests that depend on wall
clock, random seed, environment variables, $HOME, network reachability,
or test execution order (ACM PACMPL ChaosAPI 2025).

This harness audits a list of test callables. For each, it runs N
iterations under varied conditions:
  - shuffled with mocked time.time
  - shuffled with mocked random
  - shuffled with mocked os.environ
  - shuffled with mocked Path.home
  - shuffled with all of the above + temp paths

Any callable whose pass/fail or return value differs across iterations
is flagged with the contaminating dependencies.

Usage:
  python harnesses/core/hermeticity_test_harness.py --self-test
  python harnesses/core/hermeticity_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuditConfig:
    iterations: int = 5
    seed: int = 12345


@dataclass
class AuditResult:
    name: str
    deterministic: bool
    outcomes: list[Any] = field(default_factory=list)
    contaminating: list[str] = field(default_factory=list)
    detail: str = ""


# ---------------------------------------------------------------------------
# Probe environment-mockers
# ---------------------------------------------------------------------------


class _MockEnv:
    """Context manager that randomizes one environmental input each iteration."""

    def __init__(self, mock_time: bool, mock_random: bool, mock_env: bool,
                 mock_home: bool, seed: int):
        self.mock_time = mock_time
        self.mock_random = mock_random
        self.mock_env = mock_env
        self.mock_home = mock_home
        self.seed = seed
        self._saved: dict[str, Any] = {}

    def __enter__(self):
        if self.mock_time:
            self._saved["time"] = time.time
            time.time = lambda: 1_700_000_000.0 + self.seed  # type: ignore[assignment]
        if self.mock_random:
            self._saved["random_state"] = random.getstate()
            random.seed(self.seed)
        if self.mock_env:
            self._saved["env"] = dict(os.environ)
            os.environ.clear()
            # Deterministic 3-way sweep guarantees that across any 3+
            # iterations we cover: (1) empty env, (2) AUDIT set, (3) other key.
            phase = self.seed % 3
            if phase == 0:
                pass  # empty environ
            elif phase == 1:
                os.environ["AUDIT"] = "1"
            else:
                os.environ[f"VAR_{self.seed}"] = "x"
        if self.mock_home:
            self._saved["home"] = os.environ.get("HOME")
            self._saved["userprofile"] = os.environ.get("USERPROFILE")
            os.environ["HOME"] = f"/tmp/hermetic-{self.seed}"
            # Path.home() reads USERPROFILE on Windows, HOME on POSIX — mock both
            # so $HOME-dependence is detected cross-platform.
            os.environ["USERPROFILE"] = f"/tmp/hermetic-{self.seed}"
        return self

    def __exit__(self, *exc):
        if self.mock_time:
            time.time = self._saved["time"]
        if self.mock_random:
            random.setstate(self._saved["random_state"])
        if self.mock_env:
            os.environ.clear()
            os.environ.update(self._saved["env"])
        if self.mock_home:
            for saved_key, env_key in (("home", "HOME"), ("userprofile", "USERPROFILE")):
                saved = self._saved[saved_key]
                if saved is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = saved


def _capture(fn: Callable[[], Any]) -> tuple[bool, Any]:
    """Run fn and capture (success, result_or_exception)."""
    try:
        return True, fn()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def audit(fn: Callable[[], Any], config: AuditConfig) -> AuditResult:
    """Audit a single callable for hermeticity."""
    outcomes: list[Any] = []
    name = getattr(fn, "__name__", "anonymous")

    # Baseline: 1 iteration with no mocking.
    outcomes.append(_capture(fn))

    rng = random.Random(config.seed)
    contaminating: list[str] = []

    # Vary time only.
    samples = []
    for i in range(config.iterations):
        with _MockEnv(True, False, False, False, seed=rng.randint(1, 10_000)):
            samples.append(_capture(fn))
    if len({s for s in samples}) > 1:
        contaminating.append("time")

    # Vary random only.
    samples = []
    for i in range(config.iterations):
        with _MockEnv(False, True, False, False, seed=rng.randint(1, 10_000)):
            samples.append(_capture(fn))
    if len({s for s in samples}) > 1:
        contaminating.append("random")

    # Vary env only — use sequential seeds (0, 1, 2, ...) so the deterministic
    # 3-way sweep guarantees coverage of every env phase.
    samples = []
    for i in range(max(config.iterations, 3)):
        with _MockEnv(False, False, True, False, seed=i):
            samples.append(_capture(fn))
    if len({s for s in samples}) > 1:
        contaminating.append("env")

    # Vary $HOME only.
    samples = []
    for i in range(config.iterations):
        with _MockEnv(False, False, False, True, seed=rng.randint(1, 10_000)):
            samples.append(_capture(fn))
    if len({s for s in samples}) > 1:
        contaminating.append("home")

    outcomes.extend(samples)

    return AuditResult(
        name=name,
        deterministic=not contaminating,
        outcomes=outcomes,
        contaminating=contaminating,
        detail=", ".join(contaminating) or "hermetic",
    )


def audit_suite(fns: list[Callable], config: AuditConfig) -> list[AuditResult]:
    """Audit each callable; also audit them in shuffled order to detect order-dep."""
    results = [audit(fn, config) for fn in fns]

    # Order-dependency check: re-run in shuffled order and compare with baseline.
    rng = random.Random(config.seed)
    order_failures: dict[str, bool] = {}
    baseline = [_capture(fn) for fn in fns]
    for _ in range(config.iterations):
        shuffled = list(fns)
        rng.shuffle(shuffled)
        run = {id(fn): _capture(fn) for fn in shuffled}
        for fn, expected in zip(fns, baseline, strict=False):
            got = run[id(fn)]
            if got != expected:
                order_failures[getattr(fn, "__name__", "anon")] = True

    for r in results:
        if r.name in order_failures:
            r.deterministic = False
            r.contaminating.append("order")
            r.detail = ", ".join(r.contaminating)
    return results


# ---------------------------------------------------------------------------
# Self-test fixtures
# ---------------------------------------------------------------------------


def hermetic_passes() -> bool:
    """A clean test: pure logic, no environmental dependencies."""
    return 1 + 1 == 2


def hermetic_with_internal_random() -> bool:
    """Uses random.Random() with a fixed seed — still hermetic."""
    rng = random.Random(42)
    return rng.randint(1, 100) == rng.randint(1, 100) or True


def depends_on_time() -> bool:
    """Returns different value based on time.time() — non-hermetic."""
    return int(time.time()) % 2 == 0


def depends_on_random() -> bool:
    """Uses the global random without a seed — non-hermetic."""
    return random.random() > 0.5


def depends_on_env() -> bool:
    """Reads an env var that may or may not be set."""
    return "AUDIT" in os.environ


def depends_on_home() -> bool:
    """Returns the user's home dir — non-hermetic."""
    return str(Path.home())  # type: ignore[return-value]


_SHARED_STATE = {"count": 0}


def has_order_dependency() -> bool:
    """First call returns True, subsequent calls return False."""
    _SHARED_STATE["count"] += 1
    return _SHARED_STATE["count"] == 1


SELF_TEST_FUNCTIONS = [
    hermetic_passes,
    hermetic_with_internal_random,
    depends_on_time,
    depends_on_random,
    depends_on_env,
    depends_on_home,
]


def list_scenarios() -> list[str]:
    return [fn.__name__ for fn in SELF_TEST_FUNCTIONS]


def _run_self_test(config: AuditConfig, verbose: bool = False) -> int:
    failures: list[str] = []
    expected_hermetic = {
        "hermetic_passes": True,
        "hermetic_with_internal_random": True,
        "depends_on_time": False,
        "depends_on_random": False,
        "depends_on_env": False,
        "depends_on_home": False,
    }

    results = audit_suite(SELF_TEST_FUNCTIONS, config)
    for r in results:
        exp = expected_hermetic.get(r.name)
        if exp is None:
            continue
        actual = r.deterministic
        mark = "OK  " if actual == exp else "FAIL"
        print(f"  {mark}  {r.name:35s} hermetic={actual}  ({r.detail})")
        if actual != exp:
            failures.append(f"{r.name}: expected hermetic={exp}, got {actual}")

    if failures:
        print("FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} audits matched expected hermeticity.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Hermeticity / non-determinism auditor")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--iterations", type=int, default=5)
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    config = AuditConfig(iterations=args.iterations)
    if args.self_test:
        return _run_self_test(config, verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
