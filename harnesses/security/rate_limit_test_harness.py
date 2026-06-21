#!/usr/bin/env python3
"""
rate_limit_test_harness.py — Insecure Design control slice (A06:2025).
======================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib).

A06 is largely architectural, but the highest-value design controls ARE
unit-testable: throttling, lockout, business-rule limits, and replay
protection. Scope is deliberately limited to mechanism, not "is the whole
design secure".

Maps to OWASP Top 10 A06:2025 Insecure Design (rate limiting / business-rule
abuse).

Hotspots / attacks exercised:
- Brute-force / credential-stuffing: per-key request throttle. (CWE-307/799)
- Missing lockout after repeated failures. (CWE-307)
- Business-logic abuse: negative/overflow quantity, price tampering. (CWE-840)
- Replay: reused nonce / idempotency key must be rejected. (CWE-294)

All time-based logic takes an injected clock for deterministic tests, so the
corpus and oracle stay pure (no real clock/network/filesystem/RNG).

TEETH: the harness's own design-control auditor (oracle_rate_limit_audit) judged
against a FROZEN corpus of (kind, payload, should_flag) literals. Each planted
Mutant is a realistic A06 control defect — a lockout that is off-by-one at the
threshold, a quantity rule that forgets to reject non-positive amounts, and a
replay guard that never records nonces. prove() compares each auditor to the
frozen should_flag literal — never to the oracle — so it is non-circular and
deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/rate_limit_test_harness.py --self-test
    python harnesses/security/rate_limit_test_harness.py --json
    python harnesses/security/rate_limit_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# SlidingWindowLimiter (CWE-307/799)
# ---------------------------------------------------------------------------

class SlidingWindowLimiter:
    """Allow up to ``max_requests`` per ``window_s`` per key. Clock is injected."""

    def __init__(self, max_requests: int, window_s: float) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        bucket = self._hits.setdefault(key, [])
        cutoff = now - self.window_s
        bucket[:] = [t for t in bucket if t >= cutoff]
        if len(bucket) < self.max_requests:
            bucket.append(now)
            return True
        return False


# ---------------------------------------------------------------------------
# LockoutPolicy (CWE-307)
# ---------------------------------------------------------------------------

class LockoutPolicy:
    """Lock an account after ``threshold`` failures inside ``cooldown_s``."""

    def __init__(self, threshold: int = 5, cooldown_s: float = 300) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures: dict[str, list[float]] = {}

    def record_failure(self, account: str, now: float) -> None:
        self._failures.setdefault(account, []).append(float(now))

    def is_locked(self, account: str, now: float) -> bool:
        recent = [t for t in self._failures.get(account, []) if t >= now - self.cooldown_s]
        return len(recent) >= self.threshold

    def reset(self, account: str) -> None:
        self._failures.pop(account, None)


# ---------------------------------------------------------------------------
# BusinessRuleChecker (CWE-840)
# ---------------------------------------------------------------------------

class BusinessRuleChecker:
    """Reject abusive business-rule inputs: bad quantities and tampered prices."""

    def check_quantity(self, quantity, max_quantity: int = 10_000) -> tuple[bool, str]:
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            return True, "Quantity is not an integer (CWE-840)"
        if quantity <= 0:
            return True, f"Non-positive quantity {quantity} (CWE-840)"
        if quantity > max_quantity:
            return True, f"Quantity {quantity} exceeds sane maximum {max_quantity} (abuse/overflow)"
        return False, f"Quantity {quantity} acceptable"

    def check_price(self, expected, submitted) -> tuple[bool, str]:
        if submitted != expected:
            return True, f"Client-submitted price {submitted} != server price {expected} (price tampering)"
        return False, "Price matches server value"


# ---------------------------------------------------------------------------
# ReplayGuard (CWE-294)
# ---------------------------------------------------------------------------

class ReplayGuard:
    """Reject reused nonces / idempotency keys (replay protection)."""

    def __init__(self) -> None:
        self._seen: set = set()

    def seen(self, nonce: str) -> bool:
        """True if ``nonce`` was already used (replay); else record it and return False."""
        if nonce in self._seen:
            return True
        self._seen.add(nonce)
        return False


_rules = BusinessRuleChecker()


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
# TEETH: the design-control auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct control and
# returns whether the request should be FLAGGED (abuse / missing control).
# Each Mutant is a faithful planted defect. prove() compares to the frozen
# should_flag literal only — never to the oracle.
# ===========================================================================

@dataclass(frozen=True)
class RateLimitCase:
    """One frozen design-control fixture.

    ``kind`` selects the control; ``payload`` carries that control's literal
    inputs (events are tuples so the whole case is hashable/frozen).
    """
    name: str
    kind: str  # "throttle" | "lockout" | "quantity" | "price" | "replay"
    payload: tuple
    should_flag: bool


# Frozen corpus. ``should_flag`` is the independent ground truth (hand-pinned),
# never read back from a control. Includes the discriminators each mutant gets
# wrong (e.g. lockout exactly at threshold, a non-positive quantity, a replay).
RATE_LIMIT_CORPUS: tuple[RateLimitCase, ...] = (
    # throttle: payload = (max_requests, window_s, ((key, now), ...))
    # flag iff the FINAL request in the sequence is denied.
    RateLimitCase("throttle_under_limit", "throttle",
                  (10, 60.0, (("ip", 1000.0), ("ip", 1001.0), ("ip", 1002.0))), False),
    RateLimitCase("throttle_over_limit", "throttle",
                  (3, 60.0, (("ip", 1000.0), ("ip", 1001.0), ("ip", 1002.0), ("ip", 1003.0))), True),
    RateLimitCase("throttle_window_reset", "throttle",
                  (2, 60.0, (("ip", 1000.0), ("ip", 1001.0), ("ip", 2000.0))), False),
    # lockout: payload = (threshold, cooldown_s, ((account, now), ...), check_at)
    # flag iff the account is locked at check_at.
    RateLimitCase("lockout_below_threshold", "lockout",
                  (5, 300.0, (("u", 1000.0), ("u", 1001.0), ("u", 1002.0)), 1002.0), False),
    RateLimitCase("lockout_at_threshold", "lockout",
                  (5, 300.0, (("u", 1000.0), ("u", 1001.0), ("u", 1002.0), ("u", 1003.0), ("u", 1004.0)), 1004.0),
                  True),
    RateLimitCase("lockout_cooldown_expired", "lockout",
                  (5, 300.0, (("u", 1000.0), ("u", 1001.0), ("u", 1002.0), ("u", 1003.0), ("u", 1004.0)), 9000.0),
                  False),
    # quantity: payload = (quantity,)
    RateLimitCase("quantity_valid", "quantity", (3,), False),
    RateLimitCase("quantity_negative", "quantity", (-1,), True),
    RateLimitCase("quantity_zero", "quantity", (0,), True),
    RateLimitCase("quantity_overflow", "quantity", (10_000_000,), True),
    RateLimitCase("quantity_boolean", "quantity", (True,), True),
    # price: payload = (expected, submitted)
    RateLimitCase("price_matches", "price", (100, 100), False),
    RateLimitCase("price_tampered", "price", (100, 1), True),
    # replay: payload = ((nonce, ...),) — flag iff the LAST nonce is a replay.
    RateLimitCase("replay_first_use", "replay", (("n1",),), False),
    RateLimitCase("replay_reused_nonce", "replay", (("n1", "n1"),), True),
    RateLimitCase("replay_distinct_nonces", "replay", (("n1", "n2", "n3"),), False),
)


def _throttle_flagged(max_requests: int, window_s: float,
                      events: Sequence[tuple[str, float]]) -> bool:
    lim = SlidingWindowLimiter(max_requests, window_s)
    allowed = True
    for key, now in events:
        allowed = lim.allow(key, now)
    return not allowed  # final request denied -> flagged


def _lockout_flagged(threshold: int, cooldown_s: float,
                     events: Sequence[tuple[str, float]], check_at: float) -> bool:
    lp = LockoutPolicy(threshold, cooldown_s)
    account = events[0][0] if events else ""
    for acct, now in events:
        lp.record_failure(acct, now)
    return lp.is_locked(account, check_at)


def _replay_flagged(nonces: Sequence[str]) -> bool:
    rg = ReplayGuard()
    flagged = False
    for nonce in nonces:
        flagged = rg.seen(nonce)
    return flagged  # final nonce was a replay -> flagged


def oracle_rate_limit_audit(case: RateLimitCase) -> bool:
    """Correct verdict: does this request violate a design control (flag it)?

    Pure over its argument — dispatches to the harness's own controls, no I/O.
    """
    if case.kind == "throttle":
        max_requests, window_s, events = case.payload
        return _throttle_flagged(max_requests, window_s, events)
    if case.kind == "lockout":
        threshold, cooldown_s, events, check_at = case.payload
        return _lockout_flagged(threshold, cooldown_s, events, check_at)
    if case.kind == "quantity":
        (quantity,) = case.payload
        return _rules.check_quantity(quantity)[0]
    if case.kind == "price":
        expected, submitted = case.payload
        return _rules.check_price(expected, submitted)[0]
    if case.kind == "replay":
        (nonces,) = case.payload
        return _replay_flagged(nonces)
    raise ValueError(f"unknown rate-limit case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic A06 control defect) -----------

def mutant_lockout_off_by_one(case: RateLimitCase) -> bool:
    """BUG: the lockout uses ``>`` instead of ``>=`` for the threshold, so an
    account with EXACTLY ``threshold`` recent failures is never locked — a real
    off-by-one that leaves brute-force one guess wider than intended."""
    if case.kind == "lockout":
        threshold, cooldown_s, events, check_at = case.payload
        failures = [float(now) for _, now in events if float(now) >= check_at - cooldown_s]
        return len(failures) > threshold  # BUG: > should be >=
    return oracle_rate_limit_audit(case)


def mutant_quantity_allows_nonpositive(case: RateLimitCase) -> bool:
    """BUG: the quantity rule drops the ``<= 0`` guard, so negative and zero
    quantities pass — the classic missing lower-bound that enables refund/credit
    abuse (e.g. ordering -1 items to credit an account)."""
    if case.kind == "quantity":
        (quantity,) = case.payload
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            return True
        return quantity > 10_000  # BUG: no check for quantity <= 0
    return oracle_rate_limit_audit(case)


def mutant_replay_never_records(case: RateLimitCase) -> bool:
    """BUG: the replay guard never records nonces, so a reused nonce /
    idempotency key is always treated as fresh — replay protection that does
    nothing (a stateless ``return False``)."""
    if case.kind == "replay":
        return False  # BUG: a replayed nonce is never flagged
    return oracle_rate_limit_audit(case)


def prove(audit: Callable[[RateLimitCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    ``RateLimitCase.should_flag`` constant, never against the oracle. An auditor
    that raises on a corpus case counts as caught.
    """
    for case in RATE_LIMIT_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_rate_limit_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_rate_limit_audit,
    mutants=(
        Mutant("lockout_off_by_one", mutant_lockout_off_by_one,
               "lockout uses > instead of >=, so an account at exactly the threshold is never locked"),
        Mutant("quantity_allows_nonpositive", mutant_quantity_allows_nonpositive,
               "quantity rule drops the <= 0 guard, so negative/zero quantities pass (credit abuse)"),
        Mutant("replay_never_records", mutant_replay_never_records,
               "replay guard never records nonces, so a reused nonce is always treated as fresh"),
    ),
    corpus_size=len(RATE_LIMIT_CORPUS),
    kind="auditor",
    notes="throttle (sliding window), lockout (>= threshold within cooldown), "
          "business rules (quantity bounds + price match), replay (reject reused nonce)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = RATE_LIMIT_CORPUS


def run_case(case: RateLimitCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_rate_limit_audit(case)


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

    check("1. under limit allowed",
          _throttle_flagged(10, 60.0, [("ip", 1000.0 + i) for i in range(5)]) is False)
    check("2. over limit denied",
          _throttle_flagged(3, 60.0, [("ip", 1000.0 + i) for i in range(4)]) is True)
    check("3. window resets after elapse",
          _throttle_flagged(2, 60.0, [("ip", 1000.0), ("ip", 1001.0), ("ip", 2000.0)]) is False)
    check("4. no lockout below threshold",
          _lockout_flagged(5, 300.0, [("u", 1000.0 + i) for i in range(3)], 1002.0) is False)
    check("5. lockout at threshold",
          _lockout_flagged(5, 300.0, [("u", 1000.0 + i) for i in range(5)], 1004.0) is True)
    check("6. valid quantity accepted", _rules.check_quantity(3)[0] is False)
    check("7. negative quantity flagged", _rules.check_quantity(-1)[0] is True)
    check("8. zero quantity flagged", _rules.check_quantity(0)[0] is True)
    check("9. overflow quantity flagged", _rules.check_quantity(10_000_000)[0] is True)
    check("10. boolean quantity flagged", _rules.check_quantity(True)[0] is True)
    check("11. matching price accepted", _rules.check_price(100, 100)[0] is False)
    check("12. price tampering flagged", _rules.check_price(100, 1)[0] is True)
    check("13. first nonce accepted", _replay_flagged(["n1"]) is False)
    check("14. replayed nonce flagged", _replay_flagged(["n1", "n1"]) is True)

    for case in RATE_LIMIT_CORPUS:
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
    report = Report("security/rate_limit")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_rate_limit_audit by its module-global name is what the
    # vacuity gate's neuter breaks.
    for case in RATE_LIMIT_CORPUS:
        report.add(f"control:{case.name}", case.should_flag,
                   oracle_rate_limit_audit(case), detail=case.kind)

    # The legacy scenario checks (controls exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rate_limit_test_harness",
        description="OWASP A06:2025 Insecure Design control slice: throttle/lockout/replay (pure stdlib)",
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
