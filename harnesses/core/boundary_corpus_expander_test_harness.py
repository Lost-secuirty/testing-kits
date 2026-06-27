#!/usr/bin/env python3
"""
boundary_corpus_expander_test_harness.py — Boundary expansion that keeps the anchors.
=====================================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib). Deterministic — no clock, RNG, network, or filesystem.

Boundary-value analysis and equivalence partitioning say the bugs cluster at the
edges: ``None``, empty, zero, ``-1``, the type maximum, a Unicode separator. A useful
way to grow a test corpus is to take a hand-curated base — which already contains the
*planted-bad anchors* you must never lose — and deterministically fold in the missing
boundary values, like a fuzzer seeding its evolved corpus from a fixed seed set. The
expander must (1) preserve every base anchor, (2) add each declared boundary class
exactly once, (3) dedup against what is already present, and (4) invent nothing.

This harness proves that discipline. The oracle ``expand`` merges a base corpus with the
declared boundary values, preserving order and anchors and deduping. The planted mutants
are realistic expander bugs: drop an anchor, skip a boundary class, invent a value no
class declared, or append duplicates and count them as new coverage.

Run:
  python harnesses/core/boundary_corpus_expander_test_harness.py --self-test
  python harnesses/core/boundary_corpus_expander_test_harness.py --json
  python harnesses/core/boundary_corpus_expander_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

Case = Any

# Unicode LINE SEPARATOR (U+2028), built via chr() so the source stays ASCII.
_USEP = chr(0x2028)

# The declared boundary classes (name → canonical value). A correct expander adds each
# of these once, unless the base already supplies the value.
BOUNDARY_CLASSES: tuple[tuple[str, Case], ...] = (
    ("none", None),
    ("empty", ""),
    ("zero", 0),
    ("neg_one", -1),
    ("int_max", 2147483647),
    ("unicode_sep", _USEP),
)
_BOUNDARY_VALUES: tuple[Case, ...] = tuple(value for _name, value in BOUNDARY_CLASSES)


def _contains(seq: Sequence[Case], value: Case) -> bool:
    """Identity-then-equality membership that treats e.g. 0 and False as distinct."""
    return any(type(v) is type(value) and v == value for v in seq)


def expand(base: Sequence[Case], boundary: Sequence[Case]) -> tuple[Case, ...]:
    """ORACLE: base corpus then each not-yet-present boundary value, order-preserving.

    Every base element (including planted-bad anchors) is kept; each boundary value is
    appended once iff absent; nothing else is added.
    """
    result = list(base)
    for value in boundary:
        if not _contains(result, value):
            result.append(value)
    return tuple(result)


# --------------------------------------------------------------------------- #
# Planted buggy twins.
# --------------------------------------------------------------------------- #
def _bug_drops_anchor(base: Sequence[Case], boundary: Sequence[Case]) -> tuple[Case, ...]:
    """BUG: filters the base before expanding, silently dropping planted-bad anchors."""
    result = [v for v in base if v not in ANCHORS]
    for value in boundary:
        if not _contains(result, value):
            result.append(value)
    return tuple(result)


def _bug_skips_boundary_class(base: Sequence[Case], boundary: Sequence[Case]) -> tuple[Case, ...]:
    """BUG: skips the last boundary class, leaving an edge value uncovered."""
    result = list(base)
    for value in boundary[:-1]:
        if not _contains(result, value):
            result.append(value)
    return tuple(result)


def _bug_invents_value(base: Sequence[Case], boundary: Sequence[Case]) -> tuple[Case, ...]:
    """BUG: invents a value no boundary class declared (unjustified case)."""
    result = list(base)
    for value in boundary:
        if not _contains(result, value):
            result.append(value)
    result.append("INVENTED")
    return tuple(result)


def _bug_counts_duplicates(base: Sequence[Case], boundary: Sequence[Case]) -> tuple[Case, ...]:
    """BUG: appends boundary values without deduping, inflating coverage with repeats."""
    return tuple(list(base) + list(boundary))


# --------------------------------------------------------------------------- #
# Frozen corpus — (name, base, expected). Anchors that MUST survive expansion are in
# ANCHORS. Expected tuples are written by hand (base + absent boundary values), so a
# neutered ``expand`` disagrees and the self-test goes red.
# --------------------------------------------------------------------------- #
ANCHORS: frozenset[Case] = frozenset({"PLANTED_BAD"})

_BASE_BASIC: tuple[Case, ...] = ("alpha", "beta", "PLANTED_BAD")
_BASE_OVERLAP: tuple[Case, ...] = ("alpha", 0, "PLANTED_BAD")  # base already holds a boundary value
_BASE_EMPTY: tuple[Case, ...] = ()

CORPUS: tuple[tuple[str, tuple[Case, ...], tuple[Case, ...]], ...] = (
    # No overlap: all six boundary values are appended after the base.
    ("basic", _BASE_BASIC,
     ("alpha", "beta", "PLANTED_BAD", None, "", 0, -1, 2147483647, _USEP)),
    # Base already contains 0, so the "zero" class is deduped away.
    ("dedup_overlap", _BASE_OVERLAP,
     ("alpha", 0, "PLANTED_BAD", None, "", -1, 2147483647, _USEP)),
    # Empty base: the expansion is exactly the boundary set.
    ("empty_base", _BASE_EMPTY,
     (None, "", 0, -1, 2147483647, _USEP)),
)


def prove(impl: Callable[[Sequence[Case], Sequence[Case]], tuple[Case, ...]]) -> bool:
    """True iff `impl` (an expander) deviates from the frozen expansion on any case."""
    for _name, base, expected in CORPUS:
        try:
            if impl(base, _BOUNDARY_VALUES) != expected:
                return True
        except Exception:  # noqa: BLE001 — a crash on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["expand"]

TEETH = Teeth(
    prove=prove,
    oracle=expand,
    mutants=(
        Mutant("drops_anchor", _bug_drops_anchor,
               "filters the base first, silently dropping planted-bad anchors"),
        Mutant("skips_boundary_class", _bug_skips_boundary_class,
               "skips a declared boundary class, leaving an edge uncovered"),
        Mutant("invents_value", _bug_invents_value,
               "adds a value no boundary class declared"),
        Mutant("counts_duplicates", _bug_counts_duplicates,
               "appends without deduping, inflating coverage with repeats"),
    ),
    corpus_size=len(CORPUS),
    kind="oracle_swap",
    notes="boundary expansion must preserve anchors, dedup, and invent nothing",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [name for name, *_rest in CORPUS] + [m.name for m in TEETH.mutants]


def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/boundary_corpus_expander")

    # ORACLE STRENGTH (vacuity gate): anchor expand's EXACT output against the
    # hand-written expectations. A neutered expand disagrees with these literals.
    for name, base, expected in CORPUS:
        actual = expand(base, _BOUNDARY_VALUES)
        report.add(f"expand:{name}", list(expected), list(actual),
                   detail=f"{len(base)} base -> {len(expected)} expanded")
        if not as_json:
            print(f"expand:{name:<14} {len(base)} base -> {len(actual)} expanded")

    # Anchor preservation: every required anchor survives expansion of a base holding it.
    expanded_basic = expand(_BASE_BASIC, _BOUNDARY_VALUES)
    for anchor in ANCHORS:
        report.record(f"anchor_preserved:{anchor}", anchor in expanded_basic,
                      detail="planted-bad anchors must never be dropped")

    # Idempotence: expanding an already-expanded corpus adds nothing new.
    report.add("idempotent", list(expanded_basic),
               list(expand(expanded_basic, _BOUNDARY_VALUES)),
               detail="re-expanding must be a no-op")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Boundary expansion that preserves anchors")
    p.add_argument("--self-test", action="store_true", help="run built-in checks")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true")
    args = p.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
