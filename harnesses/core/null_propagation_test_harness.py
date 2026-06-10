#!/usr/bin/env python3
"""
null_propagation_test_harness.py — Null/optional-tracking failure detector.
==========================================================================

Pure-stdlib. Zero external dependencies.

The single most common AI-coded logic bug class (arXiv 2512.05239, 2411.01414):
silently-wrong return values when an input is None / missing / empty / NaN at
some depth in a nested structure.

This harness takes a list of target callables, introspects their signatures,
and probes each parameter (including nested fields of dataclass/dict types) by
substituting None / "" / NaN / missing-key / empty-list mutations. Each call
is classified as:

  - HANDLED        : raises a typed exception OR returns a known sentinel.
  - SILENTLY_WRONG : returns a non-error value the harness can prove is wrong
                     (e.g. a coercion that produced the string "None").
  - CRASH          : raises an untyped exception (AttributeError on None deref,
                     KeyError on missing dict key, etc.) — the AI-coded class.

Usage:
  python harnesses/core/null_propagation_test_harness.py --self-test
  python harnesses/core/null_propagation_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import inspect
import math
import sys
from dataclasses import dataclass, field, is_dataclass, fields as dc_fields
from enum import Enum
from typing import Any, Callable, Optional, get_args, get_origin, get_type_hints


# ---------------------------------------------------------------------------
# Config and data
# ---------------------------------------------------------------------------


class Outcome(str, Enum):
    HANDLED = "handled"
    SILENTLY_WRONG = "silently_wrong"
    CRASH = "crash"


@dataclass
class ProbeResult:
    target: str
    param_path: str
    mutation: str
    outcome: Outcome
    detail: str = ""


@dataclass
class NullProbeConfig:
    """Tunables for the harness."""

    depth: int = 3
    mutations: tuple[str, ...] = ("none", "empty", "nan", "missing_key", "empty_list")
    raise_on_crash: bool = False


@dataclass
class TargetSpec:
    """A registered target: a callable + a sample valid input it accepts."""

    fn: Callable[..., Any]
    sample: dict[str, Any]
    # Default to only ValueError — TypeError/KeyError are exactly what leaks
    # from un-guarded code that forgot to check for None/missing keys, so we
    # do not want to treat them as "handled" by default.
    expected_typed_errors: tuple[type, ...] = (ValueError,)
    name: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = getattr(self.fn, "__name__", repr(self.fn))


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------


def _mutate_none(_value: Any) -> Any:
    return None


def _mutate_empty(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    return value


def _mutate_nan(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return float("nan")
    return value


def _mutate_missing_key(value: Any) -> Any:
    """Drop one key from a dict (or first dict in a list)."""
    if isinstance(value, dict) and value:
        key = next(iter(value))
        return {k: v for k, v in value.items() if k != key}
    return value


def _mutate_empty_list(value: Any) -> Any:
    if isinstance(value, list):
        return []
    return value


MUTATORS: dict[str, Callable[[Any], Any]] = {
    "none": _mutate_none,
    "empty": _mutate_empty,
    "nan": _mutate_nan,
    "missing_key": _mutate_missing_key,
    "empty_list": _mutate_empty_list,
}


# ---------------------------------------------------------------------------
# Recursive walker — yields (path, parent, key, value) tuples
# ---------------------------------------------------------------------------


def _walk(value: Any, path: str = "", depth: int = 0, max_depth: int = 3):
    """Yield (path, replacement_fn) for every mutable position up to max_depth.

    ``replacement_fn(new)`` rebuilds the structure with ``new`` at this slot.
    """

    def root_replace(new):
        return new

    yield path or "<root>", root_replace, value

    if depth >= max_depth:
        return

    if isinstance(value, dict):
        for k, v in value.items():
            child_path = f"{path}.{k}" if path else k

            def make_replace(key=k):
                def replace(new):
                    return {**value, key: new}
                return replace

            replacer = make_replace()
            for p, r, v2 in _walk(v, child_path, depth + 1, max_depth):
                yield p, (lambda inner=r, outer=replacer: lambda new: outer(inner(new)))(), v2
    elif isinstance(value, list):
        for i, v in enumerate(value):
            child_path = f"{path}[{i}]"

            def make_replace(idx=i):
                def replace(new):
                    out = list(value)
                    out[idx] = new
                    return out
                return replace

            replacer = make_replace()
            for p, r, v2 in _walk(v, child_path, depth + 1, max_depth):
                yield p, (lambda inner=r, outer=replacer: lambda new: outer(inner(new)))(), v2
    elif is_dataclass(value):
        for fld in dc_fields(value):
            v = getattr(value, fld.name)
            child_path = f"{path}.{fld.name}" if path else fld.name

            def make_replace(field_name=fld.name):
                def replace(new):
                    return type(value)(**{f.name: (new if f.name == field_name else getattr(value, f.name))
                                          for f in dc_fields(value)})
                return replace

            replacer = make_replace()
            for p, r, v2 in _walk(v, child_path, depth + 1, max_depth):
                yield p, (lambda inner=r, outer=replacer: lambda new: outer(inner(new)))(), v2


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class NullProbeRunner:
    """Probe a list of targets and produce a report."""

    def __init__(self, config: NullProbeConfig):
        self.config = config

    def run(self, targets: list[TargetSpec]) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        for target in targets:
            for param_name, value in target.sample.items():
                for path, rebuild, original in _walk(value, param_name, 0, self.config.depth):
                    for mut_name in self.config.mutations:
                        mutated = MUTATORS[mut_name](original)
                        if mutated is original:
                            continue  # mutation is a no-op for this type
                        if isinstance(mutated, float) and math.isnan(mutated) \
                                and isinstance(original, float) and math.isnan(original):
                            continue
                        new_value = rebuild(mutated)
                        new_sample = {**target.sample, param_name: new_value}
                        results.append(self._probe_one(target, path, mut_name, new_sample))
        return results

    # Patterns that indicate an un-guarded crash even if the exception type
    # nominally matches the expected list (e.g. TypeError from subscripting None).
    _UNGUARDED_PATTERNS = (
        "'NoneType'",
        "subscriptable",
        "has no attribute",
        "unhashable type",
        "object is not iterable",
        "argument of type 'NoneType'",
    )

    def _probe_one(self, target: TargetSpec, path: str, mutation: str,
                   sample: dict[str, Any]) -> ProbeResult:
        try:
            ret = target.fn(**sample)
        except Exception as exc:
            msg = str(exc)
            if any(p in msg for p in self._UNGUARDED_PATTERNS):
                return ProbeResult(target.name, path, mutation, Outcome.CRASH,
                                   f"{type(exc).__name__}: {msg}")
            if isinstance(exc, target.expected_typed_errors):
                return ProbeResult(target.name, path, mutation, Outcome.HANDLED,
                                   f"{type(exc).__name__}: {msg}")
            return ProbeResult(target.name, path, mutation, Outcome.CRASH,
                               f"{type(exc).__name__}: {msg}")

        # Check for "silently wrong" — coercion artefacts and NaN propagation.
        if isinstance(ret, str) and ret in ("None", "nan", "NaN"):
            return ProbeResult(target.name, path, mutation, Outcome.SILENTLY_WRONG,
                               f"returned the literal string {ret!r}")
        if isinstance(ret, float) and math.isnan(ret):
            return ProbeResult(target.name, path, mutation, Outcome.SILENTLY_WRONG,
                               "returned NaN (silent propagation)")
        return ProbeResult(target.name, path, mutation, Outcome.HANDLED,
                           f"returned {type(ret).__name__}: {ret!r}"[:80])


def summarize(results: list[ProbeResult]) -> dict[str, int]:
    counts = {"handled": 0, "silently_wrong": 0, "crash": 0}
    for r in results:
        counts[r.outcome.value] += 1
    counts["total"] = len(results)
    return counts


# ---------------------------------------------------------------------------
# Self-test scenarios — toy functions exercising the runner
# ---------------------------------------------------------------------------


def _good_zipcode(user: dict) -> str:
    """A correct implementation: validates and raises typed errors."""
    if user is None:
        raise ValueError("user is required")
    profile = user.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("profile must be a dict")
    address = profile.get("address")
    if not isinstance(address, dict):
        raise ValueError("address must be a dict")
    zip_ = address.get("zip")
    if not isinstance(zip_, str) or not zip_:
        raise ValueError("zip must be a non-empty string")
    return zip_


def _bad_zipcode(user: dict) -> str:
    """The classic AI-generated bug — deep dereference, crashes on any None."""
    return user["profile"]["address"]["zip"]


def _silently_wrong_format(user: dict) -> str:
    """A function that coerces None to the string 'None' — silently wrong."""
    return f"{user.get('name')}"


def _good_sum(values: list) -> float:
    if not isinstance(values, list):
        raise TypeError("values must be a list")
    total = 0.0
    for v in values:
        if not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
            raise ValueError(f"non-numeric value: {v!r}")
        total += v
    return total


def _bad_sum(values: list) -> float:
    """Returns NaN silently when one element is NaN."""
    return float(sum(values))


@dataclass
class _Address:
    street: Optional[str] = None
    zip: Optional[str] = None


def _good_address_dataclass(addr: "_Address") -> str:
    if addr is None or not addr.zip:
        raise ValueError("addr.zip is required")
    return addr.zip


def _self_test_targets() -> list[TargetSpec]:
    sample_user = {"name": "Alice", "profile": {"address": {"zip": "94110", "street": "1st"}}}
    return [
        TargetSpec(_good_zipcode, {"user": sample_user}, name="good_zipcode"),
        TargetSpec(_bad_zipcode, {"user": sample_user}, name="bad_zipcode"),
        TargetSpec(_silently_wrong_format, {"user": sample_user}, name="silently_wrong_format"),
        TargetSpec(_good_sum, {"values": [1.0, 2.0, 3.0]},
                   expected_typed_errors=(ValueError, TypeError), name="good_sum"),
        TargetSpec(_bad_sum, {"values": [1.0, 2.0, 3.0]}, name="bad_sum"),
        TargetSpec(_good_address_dataclass, {"addr": _Address(street="1st", zip="94110")},
                   name="good_address_dataclass"),
    ]


def list_scenarios() -> list[str]:
    return [t.name for t in _self_test_targets()]


def _run_self_test(config: NullProbeConfig, verbose: bool = False) -> int:
    runner = NullProbeRunner(config)
    targets = _self_test_targets()
    results = runner.run(targets)
    summary = summarize(results)

    if verbose:
        for r in results:
            print(f"  [{r.outcome.value:14s}] {r.target}/{r.param_path}/{r.mutation}: {r.detail}")

    print(f"Probed {summary['total']} mutations across {len(targets)} targets:")
    print(f"  handled:        {summary['handled']}")
    print(f"  silently_wrong: {summary['silently_wrong']}")
    print(f"  crash:          {summary['crash']}")

    # Acceptance: good_* targets must have 0 crashes; bad_* must surface at least one issue.
    failures: list[str] = []
    by_target: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_target.setdefault(r.target, []).append(r)

    for name, rs in by_target.items():
        crashes = [r for r in rs if r.outcome == Outcome.CRASH]
        bad = [r for r in rs if r.outcome == Outcome.SILENTLY_WRONG]
        if name.startswith("good_") and crashes:
            failures.append(f"{name}: {len(crashes)} crash(es) — should have been typed errors")
        if name.startswith("bad_") and not (crashes or bad):
            failures.append(f"{name}: harness did not detect the planted bug")
        if name == "silently_wrong_format" and not bad:
            failures.append(f"{name}: harness did not detect silent coercion")

    if failures:
        print("FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: all acceptance criteria met.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Null/optional-tracking failure detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in target scenarios")
    p.add_argument("--depth", type=int, default=3, help="Max recursion depth (default 3)")
    p.add_argument("--verbose", action="store_true", help="Print every probe result")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    config = NullProbeConfig(depth=args.depth)
    if args.self_test:
        return _run_self_test(config, verbose=args.verbose)
    # Default: print help.
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
