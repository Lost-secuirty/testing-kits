#!/usr/bin/env python3
"""gate_canary.py — do testing-kits' anti-bug gates still BITE?

Vacuous green is the failure class the gates themselves cannot see: a gate that
passes while inert — a secret regex quietly neutered so it matches nothing, or a
teeth swap-check that "verifies" a harness whose planted bug is never actually
caught. Every such gate was proven to bite once, by hand, when it was built. This
script makes that a STANDING check: each gate is run against a KNOWN-BAD fixture and
the canary FAILS unless the gate fails too.

Principle (ported from the Codex slot repo that pioneered this): run the REAL gate
code on fresh known-bad input. A canary that re-implements or softens the gate
proves nothing, so this imports the live ``scan_line`` and the live teeth
``verify`` — never a private copy.

Covered (pure standard library, zero dependencies, cross-platform):
  - secret scanner  the live discrete patterns and GENERIC_SECRET_ASSIGNMENT must
                    still flag known secrets — including the compound-keyword forms
                    (client_secret / access_token) the old ``\\b`` regex missed —
                    while leaving a clean line alone.
  - teeth engine    the universal swap-check (``harnesses/_teeth.verify``) must
                    VERIFY a genuinely-biting ``Teeth`` and REFUSE a vacuous one: a
                    ``prove()`` that never catches the mutant, one that flags
                    everything (including the correct oracle), or an empty corpus.

Self-canarying elsewhere (deliberately not duplicated here): every ``required``
harness proves its own teeth via the swap-check in ``tools/proof_audit.py``; the
advisory mutmut lane deepens it; control-audit and the secret/PII scan run as their
own required CI jobs. This script is the meta-check that the two rot-prone
regex/predicate gates above have not silently gone soft.

Exit: 0 = every gate bit on known-bad input; 1 = a gate has gone soft — fix the
GATE (or the fixture, if the gate legitimately changed), not this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run from anywhere: put the repo root on sys.path so the REAL gate code imports.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harnesses import _teeth  # noqa: E402  (import after sys.path bootstrap)
from tools import scan_staged  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, note: str = "") -> bool:
    """Print and collect one canary result. ``passed`` True = the gate bit."""
    _results.append((name, passed, note))
    tail = f" - {note}" if note else ""
    print(f"{'PASS' if passed else 'FAIL'}  | {name}{tail}")
    return passed


# ---------------------------------------------------------------------------
# 1. secret scanner — known secrets must be flagged as a BLOCK (a non-PII hit);
#    the compound-keyword forms the old \b regex missed must bite; a clean line
#    must not. scan_line is called through the module so a softened gate is seen.
# ---------------------------------------------------------------------------
def canary_scanner() -> None:
    # Built from parts so this source never trips the repo's own secret gate.
    block_fixtures = {
        "AWS access key": "AKIA" + "IOSFODNN7EXAMPLE",
        "GitHub token": "ghp_" + ("a" * 36),
        "client_secret assignment": "client_secret" + " = " + "'" + ("A" * 24) + "'",
        "access_token assignment": "access_token" + " = " + ("B" * 32),
    }
    pii_kinds = scan_staged._PII_KINDS
    for label, line in block_fixtures.items():
        hits = scan_staged.scan_line(line)
        blocked = any(hit not in pii_kinds for hit in hits)
        record(f"scanner: BITES on {label}", blocked, f"hits={hits}")

    clean = "a normal line of prose about tokens and secrets in passing"
    clean_hits = scan_staged.scan_line(clean)
    record("scanner: clean line is not blocked",
           not any(hit not in pii_kinds for hit in clean_hits), f"hits={clean_hits}")


# ---------------------------------------------------------------------------
# 2. teeth engine — the universal swap-check must VERIFY a biting Teeth and
#    REFUSE a vacuous one. verify() is called through the module for the same
#    reason: a softened engine must be observable here.
# ---------------------------------------------------------------------------
def canary_teeth() -> None:
    def oracle() -> str:
        return "good"

    def planted_bug() -> str:
        return "bad"

    mutant = _teeth.Mutant("returns_bad", planted_bug, "models a wrong output")

    # (a) a genuinely biting prove(): catches the bug, clears the oracle.
    biting = _teeth.Teeth(prove=lambda impl: impl() == "bad", oracle=oracle,
                          mutants=(mutant,), corpus_size=1)
    record("teeth: engine VERIFIES a genuinely-biting Teeth",
           _teeth.verify(biting)["teeth_verified"] is True)

    # (b) a prove() that never catches anything — the mutant must survive.
    never = _teeth.verify(_teeth.Teeth(prove=lambda impl: False, oracle=oracle,
                                       mutants=(mutant,), corpus_size=1))
    record("teeth: engine REFUSES a prove() that never catches",
           never["teeth_verified"] is False and never["mutants_uncaught"] == ["returns_bad"])

    # (c) a prove() that flags EVERYTHING — cannot tell the oracle from the bug.
    always = _teeth.verify(_teeth.Teeth(prove=lambda impl: True, oracle=oracle,
                                        mutants=(mutant,), corpus_size=1))
    record("teeth: engine REFUSES a prove() that flags the clean oracle",
           always["teeth_verified"] is False and always["oracle_clean"] is False)

    # (d) an empty fixture corpus must never verify, even with a correct swap.
    empty = _teeth.verify(_teeth.Teeth(prove=lambda impl: impl() == "bad", oracle=oracle,
                                       mutants=(mutant,), corpus_size=0))
    record("teeth: engine REFUSES an empty fixture corpus",
           empty["teeth_verified"] is False)


def run() -> int:
    """Run every canary and return the process exit code (0 green / 1 a gate soft)."""
    _results.clear()
    print("GATE CANARY - proving every anti-bug gate still fails on known-bad input\n")
    canary_scanner()
    canary_teeth()
    failed = [name for name, ok, _ in _results if not ok]
    print(f"\n--- SUMMARY: {len(_results) - len(failed)}/{len(_results)} canaries pass ---")
    if failed:
        print("A gate has gone soft (vacuous green) - fix the GATE, not this script:")
        for name in failed:
            print(f"- {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
