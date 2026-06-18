#!/usr/bin/env python3
"""Shared teeth + reporting contract for testing-kits harnesses.

Pure standard library, zero third-party imports — like every harness. This module
is the *one* place that defines what "has teeth", "fails loud", and "reports its
findings" mean, so a single generic gate (``tools/proof_audit.py``) can verify any
harness without per-harness special-casing.

It is named with a leading underscore and lives one directory level up from the
category folders, so harness discovery (which globs ``harnesses/*/*.py``) never
treats it as a harness.

Two concepts:

1. ``Teeth`` — the uniform "does this harness catch a real bug" surface. A harness
   declares a module-level ``TEETH = Teeth(...)`` pointing at its own correct
   ``oracle`` and one-or-more planted ``Mutant`` defects, plus a ``prove(impl)``
   predicate that returns ``True`` iff ``impl`` is *caught*. The gate then asserts
   ``prove(oracle) is False`` (the correct impl is NOT flagged) and
   ``prove(mutant.impl) is True`` for every mutant (every planted bug IS flagged).
   Declaring ``TEETH`` is the opt-in that promotes a harness from ``pending`` to
   ``required`` in the gate — there is no separate allowlist to keep in sync.

2. ``Report`` — the fail-loud + structured-findings contract for ``--self-test``.
   A harness builds a ``Report``, adds ``Check``s, and returns ``report.emit(...)``
   as its process exit code: 0 green, 1 a check failed, 2 the harness is broken.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Teeth: the uniform "catches a real bug" surface
# ---------------------------------------------------------------------------

# What `prove` is applied to varies by harness shape ("oracle_swap" => a predicate
# under test, "auditor" => a finding-producing auditor, "statistical" => a sampler
# factory). `prove` absorbs that variance; the gate only ever sees Callable -> bool.
KINDS = ("oracle_swap", "auditor", "statistical")


@dataclass(frozen=True)
class Mutant:
    """One intentionally planted defect and the real-world bug it models."""

    name: str
    impl: Callable[..., Any]
    note: str = ""


@dataclass(frozen=True)
class Teeth:
    """The uniform teeth surface a harness exposes as module-level ``TEETH``.

    ``prove(impl) -> bool`` MUST be pure and deterministic (seed any RNG; no
    network, clock, or filesystem I/O) and return ``True`` iff ``impl`` is caught
    when judged against the harness's frozen fixture corpus.
    """

    prove: Callable[[Callable[..., Any]], bool]
    oracle: Callable[..., Any]
    mutants: tuple[Mutant, ...]
    corpus_size: int = 0
    kind: str = "oracle_swap"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Teeth.kind must be one of {KINDS}, got {self.kind!r}")
        if not self.mutants:
            raise ValueError("Teeth requires at least one Mutant")


def verify(teeth: Teeth) -> dict:
    """Run the universal swap-check on a ``Teeth`` object and return a result dict.

    Never raises: any exception from ``prove`` is captured into ``error`` and the
    harness is reported unverified. Callers (the gate) decide pass/fail from
    ``teeth_verified``.
    """
    out: dict[str, Any] = {
        "teeth_present": True,
        "kind": teeth.kind,
        "corpus_size": 0,
        "oracle_clean": None,
        "mutants_total": len(teeth.mutants),
        "mutants_caught": 0,
        "mutants_uncaught": [],
        "teeth_verified": False,
        "error": None,
    }
    try:
        out["corpus_size"] = int(teeth.corpus_size)
        out["oracle_clean"] = teeth.prove(teeth.oracle) is False
        caught = 0
        uncaught: list[str] = []
        for mutant in teeth.mutants:
            if teeth.prove(mutant.impl) is True:
                caught += 1
            else:
                uncaught.append(mutant.name)
        out["mutants_caught"] = caught
        out["mutants_uncaught"] = uncaught
        out["teeth_verified"] = bool(
            out["oracle_clean"]
            and out["mutants_total"] >= 1
            and caught == out["mutants_total"]
            and out["corpus_size"] >= 1
        )
    except Exception as exc:  # noqa: BLE001 — report any failure, never crash the gate
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return out


# ---------------------------------------------------------------------------
# Report: the fail-loud + structured-findings contract for --self-test
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


@dataclass(frozen=True)
class Check:
    """One assertion the self-test made, with both sides recorded for diagnosis."""

    check: str
    expected: Any
    actual: Any
    passed: bool
    detail: str = ""


@dataclass
class Report:
    """Collects ``Check``s and emits a single, loud pass/fail verdict + exit code."""

    harness: str
    checks: list[Check] = field(default_factory=list)

    def add(self, check: str, expected: Any, actual: Any, *, detail: str = "") -> bool:
        passed = expected == actual
        self.checks.append(Check(check, _json_safe(expected), _json_safe(actual), passed, detail))
        return passed

    def record(self, check: str, passed: bool, *, detail: str = "") -> bool:
        """Add a check from a boolean (when there is no natural expected/actual pair)."""
        self.checks.append(Check(check, True, bool(passed), bool(passed), detail))
        return passed

    def assert_teeth(self, teeth: Teeth) -> bool:
        """Wire the universal swap-check into a harness's own --self-test.

        Adds: the correct oracle is not flagged, and every planted mutant is caught.
        This makes a harness fail its OWN self-test loudly if its teeth ever break,
        not only the external gate.
        """
        result = verify(teeth)
        if result["error"]:
            self.record("teeth_no_error", False, detail=result["error"])
            return False
        self.record("teeth_oracle_clean", bool(result["oracle_clean"]),
                    detail="prove(oracle) must be False")
        for mutant in teeth.mutants:
            caught = mutant.name not in result["mutants_uncaught"]
            self.record(f"teeth_catches:{mutant.name}", caught, detail=mutant.note)
        self.record("teeth_corpus_nonempty", result["corpus_size"] >= 1,
                    detail=f"corpus_size={result['corpus_size']}")
        return result["teeth_verified"]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def n_failed(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def to_dict(self) -> dict:
        return {
            "harness": self.harness,
            "passed": self.passed,
            "n_checks": len(self.checks),
            "n_failed": self.n_failed,
            "findings": [dataclasses.asdict(c) for c in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=repr)

    def emit(self, as_json: bool = False) -> int:
        """Print the report and return the process exit code (0 green / 1 failed)."""
        if as_json:
            print(self.to_json())
        else:
            for c in self.checks:
                if not c.passed:
                    extra = f" - {c.detail}" if c.detail else ""
                    print(f"FAIL {c.check}: expected={c.expected!r} actual={c.actual!r}{extra}",
                          file=sys.stderr)
            mark = "OK" if self.passed else "FAILED"
            tail = "" if self.passed else f" ({self.n_failed} failed)"
            stream = sys.stdout if self.passed else sys.stderr
            print(f"{mark}: {self.harness} - {len(self.checks)} checks{tail}", file=stream)
        return 0 if self.passed else 1


def serve_mock_server_until_interrupt(start_server: Callable[[int], Any],
                                      port: int,
                                      label: str) -> int:
    """Run a legacy harness mock server until Ctrl+C, then shut it down."""
    import time as _time
    server = start_server(port)
    print(f"  {label} on http://127.0.0.1:{port} - Ctrl+C to stop")
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
    return 0


def emit_legacy_self_test(title: str,
                          run_all_scenarios: Callable[..., list[Any]],
                          verbose: bool,
                          emit_report: Callable[[list[Any]], int]) -> int:
    """Print the older list-of-results self-test format, then emit a Report."""
    print(f"\n  {title} - self-test mode")
    print("  " + "=" * 52)
    results = run_all_scenarios(verbose=verbose)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    if not verbose:
        for r in results:
            print(r)
    print()
    print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
    print()
    if failed:
        return 1
    return emit_report(results)
