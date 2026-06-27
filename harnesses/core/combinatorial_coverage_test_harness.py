#!/usr/bin/env python3
"""
combinatorial_coverage_test_harness.py — Pairwise (t=2) covering-array accounting.
==================================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib).

Combinatorial / t-way testing (NIST ACTS, Microsoft PICT, covering arrays) rests on
the empirical finding that the large majority of software faults are triggered by a
single parameter or a *pair* of parameters interacting (NIST SP 800-142). A pairwise
test set is only useful if it actually exercises **every** required 2-way value pair;
a generator that silently drops a pair, or an accountant that *claims* full coverage
without checking, gives false confidence.

This harness proves the **coverage-accounting math**, not just combo generation
(``core/feature_flag`` already enumerates flag combinations and drives a target under
them — a different concern). The oracle here, ``missing_pairs``, returns the set of
required 2-way pairs a candidate test set fails to cover; a test set achieves full
pairwise coverage iff that set is empty. The planted mutants are realistic ways the
accounting goes wrong: collapsing distinct values, ignoring a parameter, declaring
"covered" unconditionally, or judging only the first row.

A correct greedy covering-array generator (``pairwise``) is included and shown, in the
self-test, to produce a test set the oracle confirms has zero missing pairs.

Run:
  python harnesses/core/combinatorial_coverage_test_harness.py --self-test
  python harnesses/core/combinatorial_coverage_test_harness.py --json
  python harnesses/core/combinatorial_coverage_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# A model value is one (parameter, value) assignment; a pair is the unordered pair
# of two such assignments from *distinct* parameters; a row is a full assignment of
# every parameter; a test set is a sequence of rows.
Element = tuple[str, str]
Pair = frozenset  # frozenset[Element], exactly two elements from distinct parameters
Row = tuple[Element, ...]
Model = Mapping[str, tuple[str, ...]]

# --------------------------------------------------------------------------- #
# Finite parameter model under test (small + explicit so expectations are by-hand).
# --------------------------------------------------------------------------- #
MODEL: Model = {
    "os": ("linux", "windows"),
    "arch": ("x64", "arm"),
    "tls": ("on", "off"),
}


def _pair(a: Element, b: Element) -> Pair:
    """The canonical unordered pair of two assignments (order-independent)."""
    return frozenset((a, b))


def required_pairs(model: Model) -> frozenset[Pair]:
    """Every 2-way value pair the model requires a covering test set to exercise.

    Independent of the oracle ``missing_pairs`` — used to anchor the corpus's
    expected values so neutering the oracle still reddens the self-test.
    """
    out: set[Pair] = set()
    for p1, p2 in itertools.combinations(sorted(model), 2):
        for v1 in model[p1]:
            for v2 in model[p2]:
                out.add(_pair((p1, v1), (p2, v2)))
    return frozenset(out)


def _row_pairs(row: Row) -> frozenset[Pair]:
    """All 2-way pairs a single (fully-assigned) row covers."""
    return frozenset(_pair(a, b) for a, b in itertools.combinations(row, 2))


def covered_pairs(test_set: Iterable[Row]) -> frozenset[Pair]:
    """The union of 2-way pairs covered across every row of a test set."""
    out: set[Pair] = set()
    for row in test_set:
        out |= _row_pairs(row)
    return frozenset(out)


# --------------------------------------------------------------------------- #
# Oracle (correct) and the intentionally buggy twins.
# --------------------------------------------------------------------------- #
def missing_pairs(test_set: Iterable[Row], model: Model) -> frozenset[Pair]:
    """ORACLE: required 2-way pairs the test set fails to cover (∅ ⇒ full coverage)."""
    return frozenset(required_pairs(model) - covered_pairs(test_set))


def _bug_collapse_values(test_set: Iterable[Row], model: Model) -> frozenset[Pair]:
    """BUG: collapses every value of a parameter to one bucket, so it only ever
    requires/covers one pair per parameter-pair — under-reports real gaps."""
    flat = {p: ("",) for p in model}
    remap = lambda row: tuple((p, "") for p, _ in row)  # noqa: E731
    return frozenset(required_pairs(flat) - covered_pairs(remap(r) for r in test_set))


def _bug_ignore_param(test_set: Iterable[Row], model: Model) -> frozenset[Pair]:
    """BUG: drops the last parameter from the required set, so gaps that involve
    only that parameter are never reported."""
    if not model:
        return missing_pairs(test_set, model)
    dropped = sorted(model)[-1]
    reduced = {p: v for p, v in model.items() if p != dropped}
    return frozenset(required_pairs(reduced) - covered_pairs(test_set))


def _bug_always_covered(test_set: Iterable[Row], model: Model) -> frozenset[Pair]:
    """BUG: declares full coverage without checking anything."""
    return frozenset()


def _bug_first_row_only(test_set: Iterable[Row], model: Model) -> frozenset[Pair]:
    """BUG: judges coverage from the first row alone, ignoring the rest of the set."""
    rows = list(test_set)[:1]
    return frozenset(required_pairs(model) - covered_pairs(rows))


# --------------------------------------------------------------------------- #
# A correct greedy covering-array generator (demonstrated, not the teeth subject).
# --------------------------------------------------------------------------- #
def pairwise(model: Model) -> tuple[Row, ...]:
    """Greedy set-cover over the full Cartesian product → a pairwise test set.

    Deterministic and pure: candidate rows are enumerated in model order and the
    first row covering the most still-needed pairs is taken until none remain.
    Guaranteed complete because the Cartesian product covers every pair.
    """
    params = sorted(model)
    candidates = [
        tuple(zip(params, combo, strict=True))
        for combo in itertools.product(*(model[p] for p in params))
    ]
    needed = set(required_pairs(model))
    chosen: list[Row] = []
    while needed:
        best: Row | None = None
        best_cov = 0
        for row in candidates:
            cov = len(_row_pairs(row) & needed)
            if cov > best_cov:
                best, best_cov = row, cov
        if best is None:
            break
        chosen.append(best)
        needed -= _row_pairs(best)
        candidates.remove(best)
    return tuple(chosen)


# --------------------------------------------------------------------------- #
# Frozen corpus — (name, test_set, expected_missing). Expectations are derived from
# ``required_pairs`` + ``_pair`` only (never from the oracle ``missing_pairs``), so a
# neutered oracle disagrees with them and the self-test goes red.
# --------------------------------------------------------------------------- #
_CA: tuple[Row, ...] = (
    (("os", "linux"), ("arch", "x64"), ("tls", "on")),
    (("os", "linux"), ("arch", "arm"), ("tls", "off")),
    (("os", "windows"), ("arch", "x64"), ("tls", "off")),
    (("os", "windows"), ("arch", "arm"), ("tls", "on")),
)

# Covered by the first row of _CA (linux/x64/on) — used to anchor the single-row case.
_ROW1_PAIRS: frozenset[Pair] = frozenset({
    _pair(("os", "linux"), ("arch", "x64")),
    _pair(("os", "linux"), ("tls", "on")),
    _pair(("arch", "x64"), ("tls", "on")),
})

CORPUS: tuple[tuple[str, tuple[Row, ...], frozenset[Pair]], ...] = (
    # A complete pairwise array leaves nothing uncovered.
    ("complete", _CA, frozenset()),
    # No rows ⇒ every required pair is missing.
    ("empty", (), required_pairs(MODEL)),
    # Dropping _CA's 4th row leaves exactly the three pairs that row uniquely supplied.
    ("missing_three", _CA[:3], frozenset({
        _pair(("os", "windows"), ("arch", "arm")),
        _pair(("os", "windows"), ("tls", "on")),
        _pair(("arch", "arm"), ("tls", "on")),
    })),
    # A single row covers its own three pairs; the other nine remain missing.
    ("single_row", (_CA[0],), required_pairs(MODEL) - _ROW1_PAIRS),
)


# --------------------------------------------------------------------------- #
# Teeth: prove(impl) is True iff `impl` (an accountant) disagrees with the frozen
# corpus on any case — i.e. is caught. Pure + deterministic; never calls the oracle.
# --------------------------------------------------------------------------- #
def prove(impl: Callable[[Iterable[Row], Model], frozenset]) -> bool:
    """True iff `impl` mis-accounts coverage on any frozen corpus case."""
    for _name, test_set, expected in CORPUS:
        try:
            if impl(test_set, MODEL) != expected:
                return True
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["missing_pairs"]

TEETH = Teeth(
    prove=prove,
    oracle=missing_pairs,
    mutants=(
        Mutant("collapse_values", _bug_collapse_values,
               "treats all values of a parameter as equal, hiding real pair gaps"),
        Mutant("ignore_parameter", _bug_ignore_param,
               "omits one parameter from the required set, missing its pair gaps"),
        Mutant("always_covered", _bug_always_covered,
               "reports full coverage without checking any pair"),
        Mutant("first_row_only", _bug_first_row_only,
               "judges coverage from the first row alone, ignoring the rest"),
    ),
    corpus_size=len(CORPUS),
    kind="oracle_swap",
    notes="a claimed covering array must actually cover every required 2-way pair",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [name for name, _ts, _exp in CORPUS] + [
        "pairwise_generator_full_coverage",
        *(m.name for m in TEETH.mutants),
    ]


def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/combinatorial_coverage")

    # ORACLE STRENGTH (vacuity gate): call missing_pairs by its module-global name
    # against the frozen corpus. Neutering it disagrees with the hand-anchored
    # expectations below, turning these checks red.
    for name, test_set, expected in CORPUS:
        report.add(f"missing_pairs:{name}", sorted(map(sorted, expected)),
                   sorted(map(sorted, missing_pairs(test_set, MODEL))),
                   detail=f"{len(expected)} pair(s) expected missing")
        if not as_json:
            print(f"missing_pairs:{name:<14} {len(missing_pairs(test_set, MODEL))} missing")

    # A real greedy generator must produce a set the oracle confirms is complete.
    generated = pairwise(MODEL)
    gen_missing = missing_pairs(generated, MODEL)
    report.add("pairwise_generator_full_coverage", frozenset(), gen_missing,
               detail=f"{len(generated)} rows generated for {len(required_pairs(MODEL))} pairs")
    if not as_json:
        print(f"pairwise generator:        {len(generated)} rows, {len(gen_missing)} missing")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pairwise covering-array coverage accounting")
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
