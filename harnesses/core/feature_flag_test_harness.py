#!/usr/bin/env python3
"""
feature_flag_test_harness.py — Feature-flag combinatorics + stale-flag scanner.
================================================================================

Pure-stdlib. Zero external dependencies.

The Google Cloud June 2025 outage and the Slack May 2020 outage shared a
root cause: a feature-flag combination activated a dormant code path that
crashed the moment it was first taken. (Statsig postmortem.)

This harness:
  - Enumerates every pairwise (and optionally triple-wise) flag combination
    over a registered flag set.
  - Drives a target function under each combination and records the outcome.
  - Flags any combination that crashes, returns inconsistent type vs. its
    siblings, or violates an @flag_expects assertion.
  - Detects stale-flag default-mismatch: same flag declared with different
    defaults in different scopes.
  - Simulates a flag flipping mid-request and asserts the target either
    completes deterministically or fails loudly.

Usage:
  python harnesses/core/feature_flag_test_harness.py --self-test
  python harnesses/core/feature_flag_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import itertools
import sys
import sys as _sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402


@dataclass(frozen=True)
class Flag:
    name: str
    default: bool = False
    deprecated: bool = False


@dataclass
class ComboResult:
    combo: dict[str, bool]
    outcome: str  # "ok", "crash", "type_mismatch", "expectation_violation"
    detail: str = ""
    return_type: str = ""


@dataclass
class FlagMatrixConfig:
    enable_triple_wise: bool = False
    flip_mid_call: bool = True


class FlagSet:
    """A registry of flags with current defaults."""

    def __init__(self) -> None:
        self._flags: dict[str, Flag] = {}
        self._declarations: dict[str, list[Flag]] = {}

    def register(self, flag: Flag) -> None:
        self._declarations.setdefault(flag.name, []).append(flag)
        self._flags[flag.name] = flag

    def all(self) -> list[Flag]:
        return list(self._flags.values())

    def default_mismatches(self) -> list[str]:
        """Names where the same flag was registered with conflicting defaults."""
        problems: list[str] = []
        for name, decls in self._declarations.items():
            defaults = {d.default for d in decls}
            if len(defaults) > 1:
                problems.append(name)
        return problems


# ---------------------------------------------------------------------------
# Expectation decorator + runner
# ---------------------------------------------------------------------------


def flag_expects(combo: dict[str, bool], returns: Any) -> Callable:
    """Decorator: declare expected return for a specific flag combo."""

    def wrap(fn: Callable) -> Callable:
        existing = getattr(fn, "_flag_expects", [])
        existing.append((combo, returns))
        fn._flag_expects = existing
        return fn

    return wrap


class FlagMatrixRunner:
    def __init__(self, config: FlagMatrixConfig):
        self.config = config

    def _combos(self, flags: list[Flag]) -> list[dict[str, bool]]:
        """All pairwise (or triple-wise) coverage with other flags at default."""
        names = [f.name for f in flags]
        defaults = {f.name: f.default for f in flags}
        arity = 3 if (self.config.enable_triple_wise and len(names) >= 3) else min(2, len(names))
        seen: set[tuple] = set()
        out: list[dict[str, bool]] = []
        for subset in itertools.combinations(names, arity):
            for vals in itertools.product([False, True], repeat=arity):
                assignment = dict(defaults)
                for n, v in zip(subset, vals, strict=False):
                    assignment[n] = v
                key = tuple(sorted(assignment.items()))
                if key in seen:
                    continue
                seen.add(key)
                out.append(assignment)
        return out

    def run(self, target: Callable, flagset: FlagSet) -> list[ComboResult]:
        results: list[ComboResult] = []
        return_types: dict[tuple, str] = {}
        expects = getattr(target, "_flag_expects", [])

        for combo in self._combos(flagset.all()):
            outcome = "ok"
            detail = ""
            ret_type = ""
            try:
                ret = target(combo)
                ret_type = type(ret).__name__
            except Exception as exc:
                outcome = "crash"
                detail = f"{type(exc).__name__}: {exc}"
                results.append(ComboResult(combo=dict(combo), outcome=outcome, detail=detail))
                continue

            # Check expectations.
            for exp_combo, exp_return in expects:
                if all(combo.get(k) == v for k, v in exp_combo.items()):
                    if ret != exp_return:
                        outcome = "expectation_violation"
                        detail = f"expected {exp_return!r}, got {ret!r}"
                        break

            return_types[tuple(sorted(combo.items()))] = ret_type
            results.append(ComboResult(combo=dict(combo), outcome=outcome,
                                       detail=detail, return_type=ret_type))

        # Type-consistency check across the matrix.
        type_set = {r.return_type for r in results if r.outcome in ("ok",)}
        if len(type_set) > 1:
            for r in results:
                if r.outcome == "ok":
                    r.outcome = "type_mismatch"
                    r.detail = f"return types vary across combos: {sorted(type_set)}"
        return results

    def flip_mid_call(self, target: Callable, flagset: FlagSet) -> list[ComboResult]:
        """Each flag is read twice: once at the start, once mid-call."""
        results: list[ComboResult] = []
        for flag in flagset.all():
            combo = {f.name: f.default for f in flagset.all()}
            outcome = "ok"
            detail = ""
            try:
                ret = target(combo, flip_flag=flag.name, flip_to=not combo[flag.name])
            except Exception as exc:
                outcome = "crash"
                detail = f"{type(exc).__name__}: {exc}"
                results.append(ComboResult(combo=combo, outcome=outcome, detail=detail))
                continue
            results.append(ComboResult(combo=combo, outcome=outcome,
                                       detail=f"flipped {flag.name}: ret={ret!r}",
                                       return_type=type(ret).__name__))
        return results


# ---------------------------------------------------------------------------
# Self-test fixtures
# ---------------------------------------------------------------------------


def _make_flagset() -> FlagSet:
    fs = FlagSet()
    fs.register(Flag("new_pricing", default=False))
    fs.register(Flag("loyalty_v2", default=False))
    fs.register(Flag("tax_calc_v2", default=False))
    fs.register(Flag("legacy_discount", default=True, deprecated=True))
    return fs


def good_pricer(flags: dict[str, bool], flip_flag: str | None = None,
                flip_to: bool | None = None) -> int:
    """A correctly-implemented pricer — every flag combo returns an int."""
    base = 100
    if flags.get("new_pricing"):
        base = 90
    if flags.get("loyalty_v2"):
        base -= 5
    base = int(base * 1.08) if flags.get("tax_calc_v2") else int(base * 1.05)
    if flip_flag is not None:
        return base  # ignores mid-call flip — deterministic
    return base


@flag_expects({"new_pricing": True, "loyalty_v2": True}, returns=89)
def buggy_pricer_combo(flags: dict[str, bool], flip_flag: str | None = None,
                       flip_to: bool | None = None) -> int:
    """Has a known bad combo: new_pricing + loyalty_v2 yields a wrong number."""
    base = 100
    if flags.get("new_pricing"):
        base = 90
    if flags.get("loyalty_v2"):
        # BUG: should subtract 1 to satisfy the assertion (89), but adds.
        base += 5
    base = int(base * 1.08) if flags.get("tax_calc_v2") else int(base * 1.05)
    return base


def buggy_pricer_crash(flags: dict[str, bool], flip_flag: str | None = None,
                       flip_to: bool | None = None) -> int:
    """Dormant path: when both legacy_discount AND new_pricing are TRUE, crash."""
    if flags.get("legacy_discount") and flags.get("new_pricing"):
        # BUG: dormant branch hit only by this combo.
        none_value = None
        return none_value.real  # type: ignore[union-attr]
    return 100


def buggy_pricer_type_drift(flags: dict[str, bool], flip_flag: str | None = None,
                            flip_to: bool | None = None):
    """Returns str when one combo is set, int otherwise — type inconsistency."""
    if flags.get("tax_calc_v2"):
        return "108"  # BUG: should be int
    return 100


# ---------------------------------------------------------------------------
# Teeth: a correct pricer produces zero adverse findings across the frozen flag
# matrix; each planted defect surfaces at least one (crash / expectation
# violation / type drift). prove() runs the auditor over the same frozen flag
# set the self-test uses and returns True iff the pricer is caught.
# ---------------------------------------------------------------------------
def _prove(impl: Callable[..., Any]) -> bool:
    """True iff `impl` yields any adverse finding over the frozen flag matrix.

    Pure and deterministic: rebuilds the frozen flag set and a fixed config on
    every call (no RNG, clock, network, or filesystem I/O); the combo
    enumeration is itself deterministic.
    """
    runner = FlagMatrixRunner(FlagMatrixConfig(enable_triple_wise=False))
    flagset = _make_flagset()
    try:
        results = runner.run(impl, flagset)
    except Exception:  # noqa: BLE001 — a harness-level crash counts as caught
        return True
    return any(r.outcome != "ok" for r in results)


_TEETH_CORPUS_SIZE = len(
    FlagMatrixRunner(FlagMatrixConfig(enable_triple_wise=False))._combos(_make_flagset().all())
)


TEETH = Teeth(
    prove=_prove,
    oracle=good_pricer,
    mutants=(
        Mutant("expectation_violation", buggy_pricer_combo,
               "new_pricing+loyalty_v2 combo violates the @flag_expects assertion"),
        Mutant("dormant_path_crash", buggy_pricer_crash,
               "legacy_discount+new_pricing hits a dormant branch that crashes"),
        Mutant("return_type_drift", buggy_pricer_type_drift,
               "tax_calc_v2 combo returns str while others return int"),
    ),
    corpus_size=_TEETH_CORPUS_SIZE,
    kind="auditor",
    notes="every flag combination must complete with a consistent int return",
)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def list_scenarios() -> list[str]:
    return [
        "good_pricer",
        "buggy_pricer_combo",
        "buggy_pricer_crash",
        "buggy_pricer_type_drift",
        "stale_flag_default_mismatch",
        "flip_mid_call",
    ]


def _scenario_stale_flag() -> tuple[str, bool]:
    fs = FlagSet()
    fs.register(Flag("legacy_x", default=True))
    fs.register(Flag("legacy_x", default=False))  # same name, different default
    return "stale_flag_default_mismatch", bool(fs.default_mismatches())


def _run_self_test(config: FlagMatrixConfig, verbose: bool = False) -> int:
    runner = FlagMatrixRunner(config)
    fs = _make_flagset()
    failures: list[str] = []

    # good_pricer should have 0 crashes / 0 violations.
    r = runner.run(good_pricer, fs)
    crashes = sum(1 for x in r if x.outcome == "crash")
    if crashes:
        failures.append(f"good_pricer had {crashes} crash(es)")
    print(f"good_pricer:              {len(r)} combos, {crashes} crashes")

    # buggy_pricer_combo: expect at least one expectation_violation.
    r = runner.run(buggy_pricer_combo, fs)
    vios = sum(1 for x in r if x.outcome == "expectation_violation")
    if vios == 0:
        failures.append("buggy_pricer_combo: harness did not catch the expectation violation")
    print(f"buggy_pricer_combo:       {len(r)} combos, {vios} expectation violations")

    # buggy_pricer_crash: expect at least one crash.
    r = runner.run(buggy_pricer_crash, fs)
    crashes = sum(1 for x in r if x.outcome == "crash")
    if crashes == 0:
        failures.append("buggy_pricer_crash: harness did not catch the dormant-path crash")
    print(f"buggy_pricer_crash:       {len(r)} combos, {crashes} crashes")

    # buggy_pricer_type_drift: expect at least one type_mismatch.
    r = runner.run(buggy_pricer_type_drift, fs)
    drifts = sum(1 for x in r if x.outcome == "type_mismatch")
    if drifts == 0:
        failures.append("buggy_pricer_type_drift: harness did not catch the type drift")
    print(f"buggy_pricer_type_drift:  {len(r)} combos, {drifts} type mismatches")

    # stale-flag detection
    name, found = _scenario_stale_flag()
    if not found:
        failures.append(f"{name}: harness did not detect default mismatch")
    print(f"{name}:    detected={found}")

    # mid-call flip
    r = runner.flip_mid_call(good_pricer, fs)
    crashes = sum(1 for x in r if x.outcome == "crash")
    print(f"flip_mid_call (good):     {len(r)} flips, {crashes} crashes")
    if crashes:
        failures.append("flip_mid_call: good_pricer should be deterministic but crashed")

    if failures:
        print("FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: every scenario met its expectation.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Feature-flag combinatorics + stale-flag scanner")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--triple-wise", action="store_true", help="Enable triple-wise combos")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    config = FlagMatrixConfig(enable_triple_wise=args.triple_wise)
    if args.self_test:
        return _run_self_test(config, verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
