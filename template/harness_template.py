#!/usr/bin/env python3
"""
<name>_test_harness.py — One-line purpose of this harness.
==========================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib).

GOLD shape — every in-scope harness must satisfy the hardened gate
(``tools/proof_audit.py``). Required pieces:

  - a frozen ``@dataclass`` fixture CORPUS with explicit expectations;
  - a correct ORACLE and >=1 intentionally BUGGY twin (a realistic planted defect);
  - ``prove(impl) -> bool``: True iff ``impl`` is *caught* against the corpus
    (pure + deterministic — no clock/network/filesystem I/O; seed any RNG);
  - a module-level ``TEETH = Teeth(...)`` so the gate verifies real teeth
    (declaring TEETH promotes the harness from `pending` to `required`);
  - ``_run_self_test()`` builds a ``Report``, asserts the fixtures and the teeth,
    and returns ``report.emit(...)`` as the exit code (0 green / 1 failed loud);
  - argparse: ``--self-test`` / ``--json`` / ``--list-scenarios``.

Networked harnesses keep ``serve_forever`` under ``main`` only (never at import)
and still expose ``TEETH`` over the in-process oracle — never by binding a port.

Run:
  python harnesses/<cat>/<name>_test_harness.py --self-test
  python harnesses/<cat>/<name>_test_harness.py --json
  python harnesses/<cat>/<name>_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Callable

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture corpus — frozen, explicit, includes cases the buggy twin gets WRONG.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Case:
    name: str
    value: str
    expected_valid: bool
    note: str = ""


CASES: tuple[Case, ...] = (
    Case("palindrome", "abccba", True, "a true palindrome — both impls accept"),
    # The case that gives the harness teeth: endpoints match but it is NOT a
    # palindrome, so the correct oracle rejects it and the buggy twin wrongly
    # accepts it. Without a case like this the planted bug would go uncaught.
    Case("endpoints_match_only", "abca", False, "buggy endpoint-only check wrongly accepts"),
    Case("too_short", "a", False, "single char is not a valid pair"),
)


# --------------------------------------------------------------------------- #
# Oracle (correct) and the intentionally buggy twin.
# --------------------------------------------------------------------------- #
def oracle(value: str) -> bool:
    """CHANGE: the correct invariant under test (here: even-length palindrome)."""
    return len(value) >= 2 and value == value[::-1]


def buggy(value: str) -> bool:
    """CHANGE: a realistic defect (here: checks only the first/last character)."""
    return len(value) >= 2 and value[0] == value[-1]


# --------------------------------------------------------------------------- #
# Scenarios + teeth.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [c.name for c in CASES]


def prove(impl: Callable[[str], bool]) -> bool:
    """True iff ``impl`` disagrees with the corpus on any case (i.e. is caught)."""
    for case in CASES:
        try:
            if impl(case.value) != case.expected_valid:
                return True
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle,
    mutants=(Mutant("endpoints_only", buggy, "checks only endpoints, not the whole value"),),
    corpus_size=len(CASES),
    kind="oracle_swap",  # or "auditor" / "statistical" — see harnesses/_teeth.py
    notes="CHANGE: the invariant the planted bug violates",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def _run_self_test(as_json: bool = False) -> int:
    report = Report("<cat>/<name>")  # CHANGE to "category/name"
    for case in CASES:
        report.add(f"case:{case.name}", case.expected_valid, oracle(case.value), detail=case.note)
    report.assert_teeth(TEETH)  # oracle is clean + every planted mutant is caught
    return report.emit(as_json=as_json)


# --------------------------------------------------------------------------- #
# CLI — default action is the self-test (repo convention).
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="<one-line description>")
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
