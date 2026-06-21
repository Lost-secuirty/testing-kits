#!/usr/bin/env python3
"""
unbounded_consumption_test_harness.py — OWASP LLM10:2025 Unbounded Consumption.
==============================================================================

Pure-stdlib. Zero external dependencies.

Covers denial-of-wallet / denial-of-service from uncapped model usage: oversized
requests, windowed token floods, runaway agent loops, and cost ceilings.
Deterministic (timestamps are supplied per-case, never read from the clock). Maps
to OWASP Top 10 for LLM Applications 2025 — LLM10:2025 Unbounded Consumption.

Hotspots / attacks exercised:
- Oversized single request (no per-request token cap). (CWE-770)
- Token flood within a time window (no rate cap). (CWE-770/799)
- Runaway loop: identical outputs repeated, or recursion past a depth. (CWE-674)
- Cost ceiling exceeded (denial-of-wallet). (CWE-770)

Checkers never raise on hostile input; they return (flagged, reason).

TEETH: the harness's own consumption auditor (oracle_consumption_audit) judged
against a FROZEN corpus of (kind, payload, should_flag) literals. Each planted
Mutant is a realistic unbounded-consumption defect (a per-request cap that is
off-by-one at the boundary, a loop detector that requires strictly-more than the
threshold instead of >=, a cost ceiling that compares for equality instead of
strict overflow). prove() compares each auditor to the frozen should_flag literal
— never to the oracle — so it is non-circular and deterministic (no
clock/network/filesystem/RNG).

Usage:
    python harnesses/ai/unbounded_consumption_test_harness.py --self-test
    python harnesses/ai/unbounded_consumption_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


class TokenBudget:
    """Per-request cap plus a sliding-window token total (timestamps supplied)."""

    def __init__(self) -> None:
        self._events: list[tuple[float, int]] = []

    def check_request(self, tokens: int, max_per_request: int) -> tuple[bool, str]:
        if tokens > max_per_request:
            return True, f"Request of {tokens} tokens exceeds per-request cap {max_per_request} (CWE-770)"
        return False, "Request within per-request cap"

    def record(self, ts: float, tokens: int) -> None:
        self._events.append((float(ts), int(tokens)))

    def window_total(self, now: float, window_s: float) -> int:
        return sum(t for (ts, t) in self._events if ts >= now - window_s)

    def over_window(self, now: float, window_s: float, max_tokens: int) -> tuple[bool, str]:
        total = self.window_total(now, window_s)
        if total > max_tokens:
            return True, f"{total} tokens in {window_s}s exceeds window cap {max_tokens} (CWE-770)"
        return False, f"{total} tokens within window cap"


class LoopGuard:
    """Detect runaway loops: repeated identical outputs or excessive recursion."""

    def repeated_output(self, outputs: Sequence[str], threshold: int = 3) -> tuple[bool, str]:
        if not outputs:
            return False, "No outputs"
        counts = Counter(outputs)
        _top, n = counts.most_common(1)[0]
        if n >= threshold:
            return True, f"Output repeated {n} times (>= {threshold}) — loop suspected (CWE-674)"
        return False, f"Max repeat {n} below threshold {threshold}"

    def check_depth(self, depth: int, max_depth: int) -> tuple[bool, str]:
        if depth > max_depth:
            return True, f"Recursion depth {depth} exceeds max {max_depth} (CWE-674)"
        return False, f"Depth {depth} within limit"


class CostCeiling:
    """Cap cumulative spend (denial-of-wallet)."""

    def check(self, cost: float, ceiling: float) -> tuple[bool, str]:
        if cost > ceiling:
            return True, f"Cost {cost} exceeds ceiling {ceiling} (denial-of-wallet, CWE-770)"
        return False, f"Cost {cost} within ceiling {ceiling}"


_budget = TokenBudget()
_loop = LoopGuard()
_cost = CostCeiling()


def _window_flagged(events: tuple[tuple[float, int], ...], now: float,
                    window_s: float, max_tokens: int) -> bool:
    """Replay ``events`` into a fresh budget and judge the windowed total.

    Pure: a new TokenBudget per call, no shared/global state, no clock.
    """
    b = TokenBudget()
    for ts, tok in events:
        b.record(ts, tok)
    return b.over_window(now=now, window_s=window_s, max_tokens=max_tokens)[0]


# ---------------------------------------------------------------------------
# Scenario results (legacy --verbose view)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n      {self.detail}"
        return msg


# ===========================================================================
# TEETH: the consumption auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct guard and
# returns whether the usage should be FLAGGED. Each Mutant is a faithful planted
# defect. prove() compares to the frozen should_flag literal only.
# ===========================================================================

@dataclass(frozen=True)
class ConsumptionCase:
    """One frozen consumption-audit fixture. ``payload`` is the guard's args."""
    name: str
    kind: str  # "request" | "window" | "repeat" | "depth" | "cost"
    payload: tuple
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a guard. Includes the discriminators each mutant gets wrong:
#  - request: a request exactly at the cap (boundary) must NOT flag.
#  - repeat:  a repeat count exactly equal to the threshold MUST flag (>=).
#  - cost:    a cost exactly equal to the ceiling must NOT flag (strict >).
CONSUMPTION_CORPUS: tuple[ConsumptionCase, ...] = (
    # per-request token cap
    ConsumptionCase("request_small", "request", (500, 4000), False),
    ConsumptionCase("request_oversized", "request", (50000, 4000), True),
    ConsumptionCase("request_at_cap", "request", (4000, 4000), False),
    # sliding-window token total
    ConsumptionCase("window_under",
                    "window", (((1000, 1000), (1001, 1000)), 1001, 60, 10000), False),
    ConsumptionCase("window_flood",
                    "window", (((1000, 3000), (1001, 3000), (1002, 3000),
                                (1003, 3000), (1004, 3000)), 1004, 60, 10000), True),
    # runaway loop: repeated identical outputs
    ConsumptionCase("repeat_none", "repeat", (("a", "b", "c"), 3), False),
    ConsumptionCase("repeat_flood", "repeat", (("x", "x", "x", "x"), 3), True),
    ConsumptionCase("repeat_at_threshold", "repeat", (("y", "y", "y"), 3), True),
    # runaway loop: recursion depth
    ConsumptionCase("depth_shallow", "depth", (3, 10), False),
    ConsumptionCase("depth_deep", "depth", (50, 10), True),
    # cost ceiling (denial-of-wallet)
    ConsumptionCase("cost_under", "cost", (1.50, 10.0), False),
    ConsumptionCase("cost_overrun", "cost", (100.0, 10.0), True),
    ConsumptionCase("cost_at_ceiling", "cost", (10.0, 10.0), False),
)


def oracle_consumption_audit(case: ConsumptionCase) -> bool:
    """Correct verdict: does this usage exhibit unbounded consumption (flag it)?

    Pure over its argument — dispatches to the harness's own guards, no I/O.
    """
    if case.kind == "request":
        tokens, max_per_request = case.payload
        return _budget.check_request(tokens, max_per_request)[0]
    if case.kind == "window":
        events, now, window_s, max_tokens = case.payload
        return _window_flagged(events, now, window_s, max_tokens)
    if case.kind == "repeat":
        outputs, threshold = case.payload
        return _loop.repeated_output(outputs, threshold)[0]
    if case.kind == "depth":
        depth, max_depth = case.payload
        return _loop.check_depth(depth, max_depth)[0]
    if case.kind == "cost":
        cost, ceiling = case.payload
        return _cost.check(cost, ceiling)[0]
    raise ValueError(f"unknown consumption case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic consumption-control defect) ----

def mutant_request_off_by_one(case: ConsumptionCase) -> bool:
    """BUG: the per-request cap uses >= instead of >, so a request sized at exactly
    the permitted maximum is wrongly flagged — an inclusive/exclusive boundary
    error that rejects legitimate max-size requests. Other guards correct."""
    if case.kind == "request":
        tokens, max_per_request = case.payload
        return bool(tokens >= max_per_request)  # BUG: >= flags the allowed boundary
    return oracle_consumption_audit(case)


def mutant_repeat_strict_threshold(case: ConsumptionCase) -> bool:
    """BUG: the loop detector uses > threshold instead of >= threshold, so an output
    repeated exactly ``threshold`` times slips through — a real off-by-one that lets
    a tight loop run one full cycle past the intended trip point."""
    if case.kind == "repeat":
        outputs, threshold = case.payload
        if not outputs:
            return False
        _top, n = Counter(outputs).most_common(1)[0]
        return bool(n > threshold)  # BUG: strict > misses n == threshold
    return oracle_consumption_audit(case)


def mutant_cost_inclusive_ceiling(case: ConsumptionCase) -> bool:
    """BUG: the cost ceiling flags on cost >= ceiling instead of cost > ceiling, so a
    request that lands exactly on the budget is rejected — a denial-of-wallet guard
    that is too eager and blocks the last legitimate spend at the cap."""
    if case.kind == "cost":
        cost, ceiling = case.payload
        return bool(cost >= ceiling)  # BUG: >= rejects the allowed exact ceiling
    return oracle_consumption_audit(case)


def prove(audit: Callable[[ConsumptionCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    ConsumptionCase.should_flag constant, never against the oracle. A guard that
    raises on a corpus case counts as caught.
    """
    for case in CONSUMPTION_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_consumption_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_consumption_audit,
    mutants=(
        Mutant("request_off_by_one", mutant_request_off_by_one,
               "per-request cap uses >= instead of >, flagging a request sized at exactly the permitted maximum"),
        Mutant("repeat_strict_threshold", mutant_repeat_strict_threshold,
               "loop detector uses > threshold instead of >=, so an output repeated exactly threshold times slips through"),
        Mutant("cost_inclusive_ceiling", mutant_cost_inclusive_ceiling,
               "cost ceiling flags on cost >= ceiling instead of >, rejecting a spend that lands exactly on the budget"),
    ),
    corpus_size=len(CONSUMPTION_CORPUS),
    kind="auditor",
    notes="per-request cap (strict > over max), sliding-window total (strict >), "
          "repeated-output loop (>= threshold), recursion depth (strict >), "
          "cost ceiling (strict > over budget)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = CONSUMPTION_CORPUS


def run_case(case: ConsumptionCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_consumption_audit(case)


# ---------------------------------------------------------------------------
# Legacy scenario view (kept for the paired unittest + --verbose)
# ---------------------------------------------------------------------------

def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        r = ScenarioResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)

    flood = ((1000, 3000), (1001, 3000), (1002, 3000), (1003, 3000), (1004, 3000))
    light = ((1000, 1000), (1001, 1000))

    check("1. request within cap accepted", _budget.check_request(500, 4000)[0] is False)
    check("2. oversized request flagged", _budget.check_request(50000, 4000)[0] is True)
    check("3. window under cap accepted", _window_flagged(light, 1001, 60, 10000) is False)
    check("4. window flood flagged", _window_flagged(flood, 1004, 60, 10000) is True)
    check("5. no loop accepted", _loop.repeated_output(["a", "b", "c"])[0] is False)
    check("6. repeated output flagged", _loop.repeated_output(["x", "x", "x"])[0] is True)
    check("7. shallow depth accepted", _loop.check_depth(3, 10)[0] is False)
    check("8. deep recursion flagged", _loop.check_depth(50, 10)[0] is True)
    check("9. cost within ceiling accepted", _cost.check(1.5, 10.0)[0] is False)
    check("10. cost overrun flagged", _cost.check(100.0, 10.0)[0] is True)

    for case in CONSUMPTION_CORPUS:
        check(f"proof:{case.name}", run_case(case) == case.should_flag,
              f"expected flag={case.should_flag}")

    return results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


# ---------------------------------------------------------------------------
# Report-based self-test — exercises the oracle by module-global name (so the
# vacuity gate's neuter is caught here) and asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    report = Report("ai/unbounded_consumption")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_consumption_audit by its module-global name is what the
    # vacuity gate's neuter breaks.
    for case in CONSUMPTION_CORPUS:
        report.add(f"consumption:{case.name}", case.should_flag,
                   oracle_consumption_audit(case), detail=case.kind)

    # The legacy scenario checks (guards exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unbounded_consumption_test_harness",
        description="OWASP LLM10:2025 Unbounded Consumption harness (pure stdlib)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run all scenarios; exit 0 if all pass")
    p.add_argument("--json", action="store_true", help="Emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        for name in list_scenarios():
            print(name)
        return 0
    return _run_self_test(verbose=args.verbose, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
