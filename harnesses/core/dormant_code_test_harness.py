#!/usr/bin/env python3
"""
dormant_code_test_harness.py — Never-before-taken branches crash on first hit.
===============================================================================

Pure-stdlib. Zero external dependencies.

The Google Cloud June 12, 2025 outage was caused by a never-triggered error
branch that crashed the moment it was first taken: the logger reference was
None, and no test had ever exercised that code path. (Statsig postmortem.)

This harness uses ``sys.settrace`` to:
  1. Record every line of a target module taken during a normal workload.
  2. Identify lines that are *reachable* (parsed from the source) but never
     taken.
  3. Drive synthetic inputs against the target — blank strings, zero-length
     lists, unknown enum variants, never-before-seen field combos — and
     re-runs the trace to surface remaining dormant lines.

A line that survives both passes is genuinely dormant: a candidate for
production crashes the moment its trigger appears.

Usage:
  python harnesses/core/dormant_code_test_harness.py --self-test
  python harnesses/core/dormant_code_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import ast
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path as _Path
from typing import Any

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import Mutant, Report, Teeth

# ---------------------------------------------------------------------------
# Coverage probe
# ---------------------------------------------------------------------------


class CoverageProbe:
    """Track which lines of a target callable execute during a workload."""

    def __init__(self, target_filename: str | None = None):
        self.target_filename = target_filename
        self.taken: set[int] = set()
        self._prev_trace = None

    def _trace(self, frame, event, arg):
        if event != "line":
            return self._trace
        if self.target_filename and frame.f_code.co_filename != self.target_filename:
            return self._trace
        self.taken.add(frame.f_lineno)
        return self._trace

    def __enter__(self) -> CoverageProbe:
        self._prev_trace = sys.gettrace()
        sys.settrace(self._trace)
        return self

    def __exit__(self, *exc) -> None:
        sys.settrace(self._prev_trace)


# ---------------------------------------------------------------------------
# Static branch finder
# ---------------------------------------------------------------------------


def reachable_lines(source: str) -> set[int]:
    """Return the set of executable line numbers in source via AST."""
    tree = ast.parse(source)
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            lines.add(node.lineno)
    return lines


# ---------------------------------------------------------------------------
# Dormant-path driver
# ---------------------------------------------------------------------------


@dataclass
class DormantReport:
    target_name: str
    reachable: int
    taken_baseline: int
    taken_after_synth: int
    still_dormant: list[int] = field(default_factory=list)
    crashes_surfaced: list[str] = field(default_factory=list)


def drive_synthetic(target: Callable, target_filename: str,
                    inputs: list[dict[str, Any]],
                    baseline_taken: set[int]) -> tuple[set[int], list[str]]:
    """Run target with each synthetic input; collect new lines + crashes."""
    extra_taken: set[int] = set()
    crashes: list[str] = []
    for kwargs in inputs:
        probe = CoverageProbe(target_filename=target_filename)
        with probe:
            try:
                target(**kwargs)
            except Exception as exc:
                crashes.append(f"input={kwargs}: {type(exc).__name__}: {exc}")
        extra_taken |= probe.taken
    return extra_taken - baseline_taken, crashes


# ---------------------------------------------------------------------------
# TEETH: frozen dormant-path audit corpus + planted analyzer defects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DormantAuditCase:
    name: str
    reachable: frozenset[int]
    baseline_taken: frozenset[int]
    synthetic_taken: frozenset[int]
    crashes: tuple[str, ...]
    expected_events: tuple[str, ...]


DORMANT_AUDIT_CORPUS: tuple[DormantAuditCase, ...] = (
    DormantAuditCase(
        name="synthetic_input_reaches_crashing_branch",
        reachable=frozenset(range(1, 8)),
        baseline_taken=frozenset({1, 2, 3}),
        synthetic_taken=frozenset({3, 4, 5}),
        crashes=("AttributeError: 'NoneType' object has no attribute 'info'",),
        expected_events=("coverage_extended", "crash_surfaced", "still_dormant"),
    ),
    DormantAuditCase(
        name="synthetic_input_exhausts_reachable_lines",
        reachable=frozenset({1, 2, 3, 4}),
        baseline_taken=frozenset({1, 2}),
        synthetic_taken=frozenset({3, 4}),
        crashes=(),
        expected_events=("coverage_extended", "no_dormant_lines"),
    ),
    DormantAuditCase(
        name="synthetic_driver_misses_remaining_branch",
        reachable=frozenset({1, 2, 3, 4, 5}),
        baseline_taken=frozenset({1, 2, 3}),
        synthetic_taken=frozenset({1, 2, 3}),
        crashes=(),
        expected_events=("no_synthetic_extension", "still_dormant"),
    ),
)


def oracle_dormant_audit(case: DormantAuditCase) -> tuple[str, ...]:
    newly_taken = case.synthetic_taken - case.baseline_taken
    still_dormant = case.reachable - case.baseline_taken - newly_taken
    events: list[str] = []
    events.append("coverage_extended" if newly_taken else "no_synthetic_extension")
    if case.crashes:
        events.append("crash_surfaced")
    events.append("still_dormant" if still_dormant else "no_dormant_lines")
    return tuple(events)


def baseline_only_dormant_auditor(case: DormantAuditCase) -> tuple[str, ...]:
    still_dormant = case.reachable - case.baseline_taken
    events = ["no_synthetic_extension"]
    if case.crashes:
        events.append("crash_surfaced")
    events.append("still_dormant" if still_dormant else "no_dormant_lines")
    return tuple(events)


def crash_blind_dormant_auditor(case: DormantAuditCase) -> tuple[str, ...]:
    newly_taken = case.synthetic_taken - case.baseline_taken
    still_dormant = case.reachable - case.baseline_taken - newly_taken
    return (
        "coverage_extended" if newly_taken else "no_synthetic_extension",
        "still_dormant" if still_dormant else "no_dormant_lines",
    )


def overcovered_dormant_auditor(case: DormantAuditCase) -> tuple[str, ...]:
    newly_taken = case.reachable - case.baseline_taken
    events: list[str] = []
    events.append("coverage_extended" if newly_taken else "no_synthetic_extension")
    if case.crashes:
        events.append("crash_surfaced")
    events.append("no_dormant_lines")
    return tuple(events)


def prove(impl: Callable[[DormantAuditCase], tuple[str, ...]]) -> bool:
    return any(impl(case) != case.expected_events for case in DORMANT_AUDIT_CORPUS)


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_dormant_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_dormant_audit,
    mutants=(
        Mutant("baseline_only_dormant_auditor", baseline_only_dormant_auditor,
               "ignores synthetic coverage and misses newly activated branches"),
        Mutant("crash_blind_dormant_auditor", crash_blind_dormant_auditor,
               "extends coverage but fails to report first-hit crashes"),
        Mutant("overcovered_dormant_auditor", overcovered_dormant_auditor,
               "treats every reachable line as covered after the synthetic pass"),
    ),
    corpus_size=len(DORMANT_AUDIT_CORPUS),
    kind="auditor",
    notes="Frozen dormant-line, synthetic-extension, and first-hit crash corpus.",
)


# ---------------------------------------------------------------------------
# Self-test: synthesize a target module from source
# ---------------------------------------------------------------------------


TARGET_SOURCE = textwrap.dedent("""
    from __future__ import annotations
    LOGGER = None  # BUG: never initialized in production

    def normalize(name, kind="user", flags=None):
        if name is None:
            return None
        if not isinstance(name, str):
            raise TypeError("name must be string")
        if kind == "user":
            return name.strip().lower()
        if kind == "admin":
            return name.strip().upper()
        if kind == "deprecated":
            # DORMANT BRANCH: never exercised by the baseline workload.
            LOGGER.info("deprecated path hit")  # NPE on first hit
            return "DEPRECATED"
        if flags and flags.get("nuke"):
            raise ValueError("nuke flag is forbidden")
        return name
""").strip()


def _load_target_module() -> tuple[Any, str]:
    import importlib.util
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(TARGET_SOURCE + "\n")
        tmp_name = tmp.name
    spec = importlib.util.spec_from_file_location("dormant_target", tmp_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, tmp_name


def _baseline_workload(module: Any) -> None:
    module.normalize("Alice")
    module.normalize("Bob", kind="user")
    module.normalize("Carol", kind="admin")
    module.normalize(None)


def _synthetic_inputs() -> list[dict[str, Any]]:
    return [
        {"name": "Eve", "kind": "deprecated"},  # forces the dormant branch
        {"name": "Frank", "kind": "unknown_variant"},
        {"name": "Grace", "kind": "user", "flags": {"nuke": True}},
        {"name": "", "kind": "user"},
    ]


def list_scenarios() -> list[str]:
    return ["baseline_coverage", "synthetic_drive", "report"]


def _run_self_test(verbose: bool = False) -> int:
    module, filename = _load_target_module()
    reach = reachable_lines(TARGET_SOURCE)

    # Baseline pass
    probe = CoverageProbe(target_filename=filename)
    with probe:
        _baseline_workload(module)
    baseline_taken = set(probe.taken)

    # Synthetic pass
    new_taken, crashes = drive_synthetic(module.normalize, filename,
                                         _synthetic_inputs(), baseline_taken)

    still_dormant = sorted(reach - baseline_taken - new_taken)

    report = DormantReport(
        target_name="normalize",
        reachable=len(reach),
        taken_baseline=len(baseline_taken),
        taken_after_synth=len(baseline_taken | new_taken),
        still_dormant=still_dormant,
        crashes_surfaced=crashes,
    )

    print(f"reachable lines:        {report.reachable}")
    print(f"taken at baseline:      {report.taken_baseline}")
    print(f"taken after synthetic:  {report.taken_after_synth}")
    print(f"still dormant lines:    {report.still_dormant}")
    print(f"crashes surfaced:       {len(report.crashes_surfaced)}")
    if verbose:
        for c in report.crashes_surfaced:
            print(f"  - {c}")

    # Acceptance:
    failures: list[str] = []
    if not report.crashes_surfaced:
        failures.append("synthetic inputs failed to surface the planted dormant-path NPE")
    if report.taken_after_synth <= report.taken_baseline:
        failures.append("synthetic pass did not extend coverage")

    if failures:
        print("FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    report = Report("core/dormant_code")
    for case in DORMANT_AUDIT_CORPUS:
        report.add(
            f"oracle_dormant_audit:{case.name}",
            list(case.expected_events),
            list(oracle_dormant_audit(case)),
        )
    report.assert_teeth(TEETH)
    if not report.passed:
        return report.emit()
    print("OK: dormant-path crash surfaced; coverage extended by synthetic inputs.")
    return report.emit()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Dormant-code / dead-path activation harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
