#!/usr/bin/env python3
"""
combinatorial_coverage_test_harness.py — Pairwise/t-way coverage accounting.
=============================================================================

Pure-stdlib exploratory proof harness. It proves a finite interaction-coverage
auditor catches planted coverage defects without claiming exhaustive testing.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


@dataclass(frozen=True)
class Parameter:
    """One finite parameter in the modeled interaction space."""

    name: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class InteractionCase:
    """One generated test vector over the finite parameter model."""

    name: str
    assignments: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CoverageAudit:
    """Independent coverage verdict for a finite t-way suite."""

    required: frozenset[tuple[tuple[str, str], ...]]
    observed: frozenset[tuple[tuple[str, str], ...]]
    missing: frozenset[tuple[tuple[str, str], ...]]
    malformed_cases: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.missing and not self.malformed_cases


MODEL: tuple[Parameter, ...] = (
    Parameter("browser", ("chrome", "firefox")),
    Parameter("role", ("admin", "reader")),
    Parameter("format", ("json", "csv", "xml")),
)

PAIRWISE_SUITE: tuple[InteractionCase, ...] = (
    InteractionCase("chrome_admin_json", (("browser", "chrome"), ("role", "admin"), ("format", "json"))),
    InteractionCase("chrome_reader_csv", (("browser", "chrome"), ("role", "reader"), ("format", "csv"))),
    InteractionCase("firefox_admin_csv", (("browser", "firefox"), ("role", "admin"), ("format", "csv"))),
    InteractionCase("firefox_reader_json", (("browser", "firefox"), ("role", "reader"), ("format", "json"))),
    InteractionCase("chrome_admin_xml", (("browser", "chrome"), ("role", "admin"), ("format", "xml"))),
    InteractionCase("firefox_reader_xml", (("browser", "firefox"), ("role", "reader"), ("format", "xml"))),
)


def list_scenarios() -> list[str]:
    return [case.name for case in PAIRWISE_SUITE]


def exhaustive_count(model: Sequence[Parameter] = MODEL) -> int:
    """Return the full Cartesian-product size for the finite model."""

    total = 1
    for parameter in model:
        total *= len(parameter.values)
    return total


def required_interactions(
    model: Sequence[Parameter] = MODEL,
    *,
    strength: int = 2,
) -> frozenset[tuple[tuple[str, str], ...]]:
    """Enumerate every required t-way interaction for a finite parameter model."""

    if strength < 1:
        raise ValueError("strength must be >= 1")
    if strength > len(model):
        raise ValueError("strength cannot exceed number of parameters")

    required: set[tuple[tuple[str, str], ...]] = set()
    for parameter_group in itertools.combinations(model, strength):
        value_products = itertools.product(*(parameter.values for parameter in parameter_group))
        for value_group in value_products:
            required.add(
                tuple(
                    (parameter.name, value)
                    for parameter, value in zip(parameter_group, value_group, strict=True)
                )
            )
    return frozenset(required)


def _assignment_dict(case: InteractionCase) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for name, value in case.assignments:
        if name in assignments:
            raise ValueError(f"duplicate parameter {name!r} in case {case.name!r}")
        assignments[name] = value
    return assignments


def audit_coverage(
    suite: Sequence[InteractionCase],
    model: Sequence[Parameter] = MODEL,
    *,
    strength: int = 2,
) -> CoverageAudit:
    """Audit t-way interaction coverage without trusting the generator."""

    required = required_interactions(model, strength=strength)
    parameter_names = {parameter.name for parameter in model}
    allowed_values = {parameter.name: set(parameter.values) for parameter in model}

    observed: set[tuple[tuple[str, str], ...]] = set()
    malformed: list[str] = []

    for case in suite:
        try:
            assignments = _assignment_dict(case)
        except ValueError:
            malformed.append(case.name)
            continue

        missing_names = parameter_names - set(assignments)
        unknown_names = set(assignments) - parameter_names
        bad_values = {
            name
            for name, value in assignments.items()
            if name in allowed_values and value not in allowed_values[name]
        }

        if missing_names or unknown_names or bad_values:
            malformed.append(case.name)
            continue

        for parameter_group in itertools.combinations(model, strength):
            interaction = tuple((parameter.name, assignments[parameter.name]) for parameter in parameter_group)
            observed.add(interaction)

    return CoverageAudit(
        required=required,
        observed=frozenset(observed),
        missing=frozenset(required - observed),
        malformed_cases=tuple(malformed),
    )


def oracle_pairwise_suite() -> tuple[InteractionCase, ...]:
    """Return a small deterministic suite covering all pairwise interactions."""

    return PAIRWISE_SUITE


def missing_interaction_mutant() -> tuple[InteractionCase, ...]:
    """Bug: drops a case and loses required XML interactions."""

    return tuple(case for case in PAIRWISE_SUITE if case.name != "chrome_admin_xml")


def collapsed_value_mutant() -> tuple[InteractionCase, ...]:
    """Bug: collapses the browser dimension, hiding all firefox interactions."""

    collapsed: list[InteractionCase] = []
    for case in PAIRWISE_SUITE:
        assignments = tuple(
            (name, "chrome" if name == "browser" else value)
            for name, value in case.assignments
        )
        collapsed.append(InteractionCase(case.name, assignments))
    return tuple(collapsed)


def omitted_parameter_mutant() -> tuple[InteractionCase, ...]:
    """Bug: emits cases that omit the format dimension entirely."""

    return tuple(
        InteractionCase(
            case.name,
            tuple((name, value) for name, value in case.assignments if name != "format"),
        )
        for case in PAIRWISE_SUITE
    )


def prove(impl: Callable[[], Sequence[InteractionCase]]) -> bool:
    """True iff the generated suite is caught as incomplete or malformed."""

    try:
        audit = audit_coverage(impl(), MODEL, strength=2)
    except Exception:  # noqa: BLE001 - malformed generators are caught
        return True
    return not audit.passed


TEETH = Teeth(
    prove=prove,
    oracle=oracle_pairwise_suite,
    mutants=(
        Mutant("missing_interaction", missing_interaction_mutant, "drops a case and loses required pairs"),
        Mutant("collapsed_value", collapsed_value_mutant, "collapses a parameter value and loses interactions"),
        Mutant("omitted_parameter", omitted_parameter_mutant, "omits one required dimension from every case"),
    ),
    corpus_size=len(required_interactions(MODEL, strength=2)),
    kind="oracle_swap",
    notes="pairwise coverage accounting must catch incomplete or malformed finite suites",
)


def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/combinatorial_coverage")

    oracle_audit = audit_coverage(oracle_pairwise_suite())
    report.add(
        "required_pairwise_interactions",
        16,
        len(oracle_audit.required),
        detail="2x2 + 2x3 + 2x3 pairwise interactions",
    )
    report.add(
        "suite_smaller_than_exhaustive",
        True,
        len(PAIRWISE_SUITE) < exhaustive_count(MODEL),
        detail="pairwise suite is 6 cases; exhaustive suite is 12 cases",
    )
    report.add("oracle_pairwise_complete", True, oracle_audit.passed)

    missing_audit = audit_coverage(missing_interaction_mutant())
    report.add("missing_mutant_caught", False, missing_audit.passed)

    collapsed_audit = audit_coverage(collapsed_value_mutant())
    report.add("collapsed_value_mutant_caught", False, collapsed_audit.passed)

    omitted_audit = audit_coverage(omitted_parameter_mutant())
    report.add("omitted_parameter_mutant_caught", False, omitted_audit.passed)

    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit pairwise/t-way coverage for a tiny finite parameter model."
    )
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true", help="emit JSON findings")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0

    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
