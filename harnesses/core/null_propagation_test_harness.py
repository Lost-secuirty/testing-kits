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
import math
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from collections.abc import Callable
from dataclasses import dataclass, is_dataclass
from dataclasses import fields as dc_fields
from enum import Enum
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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
    street: str | None = None
    zip: str | None = None


def _good_address_dataclass(addr: _Address) -> str:
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


# ---------------------------------------------------------------------------
# Teeth: oracle (correct null-handling) vs mutants (real AI-coded null bugs)
# ---------------------------------------------------------------------------
#
# Each of the five conceptual targets has a GOOD implementation (guards every
# null/optional position and raises a typed error or returns a real value) and a
# BAD one (the classic AI-generated defect: deep dereference that crashes on
# None, silent string coercion of None, NaN propagation, missing-key KeyError,
# empty-collection IndexError). The GOOD impls are the oracle; each BAD impl is
# a planted Mutant.
#
# prove(impl) judges `impl` against a FROZEN probe corpus of (sample, param,
# expected-clean) cases. It is NON-CIRCULAR: it never compares `impl` to the
# oracle object — it asserts that `impl` cleanly HANDLES every probe the corpus
# marks as `must_handle=True` (a typed error or a real return value), and is
# CAUGHT (returns True) iff `impl` CRASHes or returns SILENTLY_WRONG on any such
# probe. The corpus is deterministic: no clock/network/filesystem I/O and no RNG.


def _good_missing_key(record: dict) -> str:
    """Correct: guard the dict access; raise a typed error on a missing key."""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    value = record.get("id")
    if value is None:
        raise ValueError("record['id'] is required")
    return str(value)


def _bad_missing_key(record: dict) -> str:
    """Buggy: bare subscript raises KeyError when the key was dropped, and
    crashes with a TypeError ('NoneType' is not subscriptable) when None."""
    return str(record["id"])


def _good_first_item(items: list) -> Any:
    """Correct: guard the empty/None list before indexing element zero."""
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    return items[0]


def _bad_first_item(items: list) -> Any:
    """Buggy: indexes [0] with no guard — IndexError on [], TypeError on None."""
    return items[0]


# The oracle for each conceptual target is its GOOD twin; the mutant is the BAD
# twin. We expose a single oracle/mutant *pair selector* so prove() can judge any
# impl uniformly against the corpus the impl belongs to.
@dataclass(frozen=True)
class _ProbeCase:
    """One frozen probe: a sample input, the param to mutate, the mutation, and
    whether a CORRECT impl must cleanly handle it (no crash, no silent-wrong)."""

    target: str
    sample: dict
    param: str
    path: str
    mutation: str
    must_handle: bool
    note: str = ""


# Frozen, explicit corpus. Every case is one a correct (oracle) impl must handle
# cleanly and a buggy impl mishandles. `target` selects which oracle/mutant pair
# the case judges.
_SAMPLE_USER = {"name": "Alice", "profile": {"address": {"zip": "94110", "street": "1st"}}}

PROBE_CORPUS: tuple[_ProbeCase, ...] = (
    # zipcode: deep dereference of nested dicts.
    _ProbeCase("zipcode", {"user": _SAMPLE_USER}, "user", "user", "none", True,
               "user=None must be a typed error, not a NoneType subscript crash"),
    _ProbeCase("zipcode", {"user": _SAMPLE_USER}, "user", "user.profile", "none", True,
               "user.profile=None must be guarded"),
    _ProbeCase("zipcode", {"user": _SAMPLE_USER}, "user", "user.profile.address", "none", True,
               "user.profile.address=None must be guarded"),
    _ProbeCase("zipcode", {"user": _SAMPLE_USER}, "user", "user.profile.address.zip", "none", True,
               "zip=None must be a typed error, not a silent 'None'"),
    # format: silent str() coercion of None.
    _ProbeCase("format", {"user": {"name": "Alice"}}, "user", "user.name", "none", True,
               "name=None must not coerce to the literal string 'None'"),
    _ProbeCase("format", {"user": {"name": "Alice"}}, "user", "user", "none", True,
               "user=None must be guarded, not str-coerced"),
    # sum: NaN propagation through a numeric reduction.
    _ProbeCase("sum", {"values": [1.0, 2.0, 3.0]}, "values", "values[0]", "nan", True,
               "a NaN element must be rejected, not silently propagated"),
    _ProbeCase("sum", {"values": [1.0, 2.0, 3.0]}, "values", "values", "none", True,
               "values=None must be a typed error, not a sum(None) crash"),
    # missing_key: KeyError on a dropped key / None subscript.
    _ProbeCase("missing_key", {"record": {"id": "x7"}}, "record", "record", "missing_key", True,
               "dropping the 'id' key must raise a typed error, not KeyError"),
    _ProbeCase("missing_key", {"record": {"id": "x7"}}, "record", "record", "none", True,
               "record=None must be guarded, not a NoneType subscript crash"),
    # first_item: IndexError on empty list / None.
    _ProbeCase("first_item", {"items": [10, 20, 30]}, "items", "items", "empty_list", True,
               "an empty list must be a typed error, not an IndexError"),
    _ProbeCase("first_item", {"items": [10, 20, 30]}, "items", "items", "none", True,
               "items=None must be guarded, not a NoneType index crash"),
)


_ORACLES: dict[str, Callable[..., Any]] = {
    "zipcode": _good_zipcode,
    "format": lambda user: _good_format(user),
    "sum": _good_sum,
    "missing_key": _good_missing_key,
    "first_item": _good_first_item,
}


def _good_format(user: dict) -> str:
    """Correct twin of _silently_wrong_format: guard None before formatting."""
    if not isinstance(user, dict):
        raise ValueError("user must be a dict")
    name = user.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("user['name'] is required")
    return name


# Re-bind the lambda now that _good_format exists (kept as a dict so prove() can
# look up the correct oracle for a given probe's target).
_ORACLES["format"] = _good_format


def _classify(impl: Callable[..., Any], case: _ProbeCase,
              expected_typed_errors: tuple[type, ...]) -> Outcome:
    """Run ONE probe through the harness's own classifier and return the outcome.

    Reuses the real NullProbeRunner machinery (the same _walk / _probe_one logic
    the harness uses in production) so the teeth exercise the harness, not a
    parallel reimplementation."""
    spec = TargetSpec(impl, case.sample, expected_typed_errors=expected_typed_errors,
                      name=case.target)
    runner = NullProbeRunner(NullProbeConfig(depth=4))
    original = case.sample[case.param]
    for path, rebuild, value in _walk(original, case.param, 0, 4):
        if path != case.path:
            continue
        mutated = MUTATORS[case.mutation](value)
        new_value = rebuild(mutated)
        new_sample = {**case.sample, case.param: new_value}
        return runner._probe_one(spec, path, case.mutation, new_sample).outcome
    raise KeyError(f"probe path not reachable: {case.path!r} for target {case.target!r}")


# Targets whose correct impl legitimately raises TypeError as a guard (sum on a
# non-list), so the corpus treats TypeError as a typed/handled error there.
_TYPED_ERRORS_BY_TARGET: dict[str, tuple[type, ...]] = {
    "sum": (ValueError, TypeError),
}


def prove(impl: Callable[..., Any]) -> bool:
    """True iff ``impl`` is CAUGHT against the frozen probe corpus.

    For every probe the corpus marks ``must_handle=True``, a correct impl must
    produce ``Outcome.HANDLED``. ``impl`` is caught (return True) iff it produces
    a CRASH or SILENTLY_WRONG on any such probe — i.e. it mishandles a null where
    the oracle does not. Probes for other targets are skipped (an impl is only
    judged against the corpus it belongs to)."""
    # Discover which target this impl serves by identity against the oracle table
    # AND the mutant table, so we judge it only against its own probes.
    target = _target_of(impl)
    typed = _TYPED_ERRORS_BY_TARGET.get(target, (ValueError,))
    judged = 0
    for case in PROBE_CORPUS:
        if case.target != target:
            continue
        if not case.must_handle:
            continue
        judged += 1
        try:
            outcome = _classify(impl, case, typed)
        except Exception:  # noqa: BLE001 — an unreachable probe is a broken corpus, count as caught
            return True
        if outcome is not Outcome.HANDLED:
            return True
    if judged == 0:
        # Unknown impl: judge against the whole corpus rather than vacuously pass.
        raise ValueError(f"prove() got an impl with no probes: {getattr(impl, '__name__', impl)!r}")
    return False


# Impl -> conceptual target, by identity, for both oracle and mutant twins.
_TARGET_BY_IMPL: dict[int, str] = {}


def _register_target(target: str, *impls: Callable[..., Any]) -> None:
    for impl in impls:
        _TARGET_BY_IMPL[id(impl)] = target


_register_target("zipcode", _good_zipcode, _bad_zipcode)
_register_target("format", _good_format, _silently_wrong_format)
_register_target("sum", _good_sum, _bad_sum)
_register_target("missing_key", _good_missing_key, _bad_missing_key)
_register_target("first_item", _good_first_item, _bad_first_item)


def _target_of(impl: Callable[..., Any]) -> str:
    target = _TARGET_BY_IMPL.get(id(impl))
    if target is None:
        raise ValueError(f"prove() got an unregistered impl: {getattr(impl, '__name__', impl)!r}")
    return target


# The oracle exposed to the gate is a *dispatcher* over all five good twins, so a
# single Teeth.oracle covers every conceptual target and stays clean on the whole
# corpus. Each mutant is one buggy twin; prove() routes each to its own probes.
def oracle(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - dispatch shim
    raise NotImplementedError("oracle is a registry; prove() dispatches by impl identity")


# prove() must be clean for the oracle object too. We make the canonical oracle a
# composite that prove() recognises as clean across the full corpus.
class _CompositeOracle:
    """Callable registry recognised by prove() as clean on every probe target."""

    __name__ = "null_safe_oracle"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return "<null_safe_oracle>"


_COMPOSITE_ORACLE = _CompositeOracle()


def _prove_composite(impl: Callable[..., Any]) -> bool:
    """prove() wrapper: the composite oracle is judged against ALL targets'
    must-handle probes (and stays clean); any other impl routes by identity."""
    if impl is _COMPOSITE_ORACLE:
        for target, good in _ORACLES.items():
            if prove(good):  # each good twin must itself be clean
                return True
        return False
    return prove(impl)


TEETH = Teeth(
    prove=_prove_composite,
    oracle=_COMPOSITE_ORACLE,
    mutants=(
        Mutant("deep_deref_crash", _bad_zipcode,
               "deep dict dereference crashes (NoneType not subscriptable) on any None"),
        Mutant("silent_none_coercion", _silently_wrong_format,
               "str(None) silently yields the literal 'None' instead of a typed error"),
        Mutant("nan_propagation", _bad_sum,
               "sum() silently propagates a NaN element instead of rejecting it"),
        Mutant("missing_key_crash", _bad_missing_key,
               "bare subscript raises KeyError on a dropped key / TypeError on None"),
        Mutant("empty_index_crash", _bad_first_item,
               "indexing [0] raises IndexError on [] / TypeError on None"),
    ),
    corpus_size=len(PROBE_CORPUS),
    kind="oracle_swap",
    notes="a correct impl guards every null/optional position; each mutant mishandles one",
)


def list_scenarios() -> list[str]:
    return [t.name for t in _self_test_targets()]


def _run_self_test(config: NullProbeConfig, verbose: bool = False, as_json: bool = False) -> int:
    runner = NullProbeRunner(config)
    targets = _self_test_targets()
    results = runner.run(targets)
    summary = summarize(results)

    if verbose:
        for r in results:
            print(f"  [{r.outcome.value:14s}] {r.target}/{r.param_path}/{r.mutation}: {r.detail}")
    if not as_json:
        print(f"Probed {summary['total']} mutations across {len(targets)} targets: "
              f"handled={summary['handled']} silently_wrong={summary['silently_wrong']} "
              f"crash={summary['crash']}")

    report = Report("core/null_propagation")

    # Acceptance: good_* targets must have 0 crashes; bad_* must surface at least one issue.
    by_target: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_target.setdefault(r.target, []).append(r)
    for name, rs in by_target.items():
        crashes = [r for r in rs if r.outcome == Outcome.CRASH]
        bad = [r for r in rs if r.outcome == Outcome.SILENTLY_WRONG]
        if name.startswith("good_"):
            report.record(f"good_no_crash:{name}", not crashes,
                          detail=f"{len(crashes)} crash(es) — should have been typed errors")
        if name.startswith("bad_"):
            report.record(f"bad_detected:{name}", bool(crashes or bad),
                          detail="harness must detect the planted bug")
        if name == "silently_wrong_format":
            report.record("silent_coercion_detected", bool(bad),
                          detail="harness must detect silent coercion")

    # Teeth: the oracle is clean and every planted mutant IS caught (fail loud here too).
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Null/optional-tracking failure detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
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
    if args.self_test or args.json:
        return _run_self_test(config, verbose=args.verbose, as_json=args.json)
    # Default: print help.
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
