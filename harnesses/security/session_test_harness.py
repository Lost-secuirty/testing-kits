#!/usr/bin/env python3
"""
session_test_harness.py — Session management & CSRF (A07:2025 / A01:2025).
==========================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth``
contract is itself pure stdlib).

Complements authz/jwt by covering the session layer: fixation, CSRF token
validation, session-id entropy, and absolute/idle timeout. Maps to OWASP Top 10
2025 — A07:2025 Authentication Failures (session fixation, session-id entropy,
session timeout) and A01:2025 Broken Access Control (CSRF on state-changing
requests).

Hotspots / attacks exercised:
- Session fixation: session id not rotated on privilege change/login. (CWE-384)
- Missing/invalid CSRF token on a state-changing request. (CWE-352)
- Weak session id (too short / constant / low entropy). (CWE-330/331)
- Absolute/idle session timeout not enforced. (CWE-613)

Checkers never raise on hostile input; they return (flagged, reason).

TEETH: the harness's own session auditor (oracle_session_audit) judged against a
FROZEN corpus of (kind, payload, should_flag) literals. Each planted Mutant is a
realistic session-control defect (a fixation guard that only rotates without the
privilege-change guard, a CSRF validator that uses == instead of a constant-time
compare AND accepts an empty submitted token, an entropy/timeout off-by-one at the
boundary). prove() compares each auditor to the frozen should_flag literal — never
to the oracle — so it is non-circular and deterministic (no clock/network/
filesystem/RNG; the timeout case carries its clock as injected literals).

Usage:
    python harnesses/security/session_test_harness.py --self-test
    python harnesses/security/session_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import hmac
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


class SessionFixationChecker:
    """Session id must change when privilege level changes (login/elevation)."""

    def check(self, old_id: str, new_id: str, privilege_changed: bool) -> tuple[bool, str]:
        if privilege_changed and old_id == new_id:
            return True, "Session id not rotated on privilege change (fixation, CWE-384)"
        return False, "Session id handling acceptable"


class CSRFTokenValidator:
    """Constant-time CSRF token comparison; missing token is rejected."""

    def validate(self, submitted: str, expected: str) -> tuple[bool, str]:
        if not submitted:
            return True, "Missing CSRF token on state-changing request (CWE-352)"
        if not expected:
            return True, "No expected CSRF token bound to session (CWE-352)"
        if not hmac.compare_digest(str(submitted), str(expected)):
            return True, "CSRF token mismatch (CWE-352)"
        return False, "CSRF token valid"


class SessionIdEntropyChecker:
    """Session ids must be long and non-constant."""

    def __init__(self, min_chars: int = 32) -> None:
        self.min_chars = min_chars

    def check(self, session_id: str) -> tuple[bool, str]:
        sid = session_id or ""
        if len(sid) < self.min_chars:
            return True, f"Session id length {len(sid)} < {self.min_chars} (CWE-331)"
        if len(set(sid)) <= 2:
            return True, "Session id has near-zero entropy (CWE-330)"
        return False, "Session id length/entropy acceptable"


class SessionTimeoutChecker:
    """Enforce an absolute/idle session lifetime (injected clock)."""

    def check(self, issued_at: float, now: float, max_age_s: float) -> tuple[bool, str]:
        if now - issued_at > max_age_s:
            return True, f"Session age {now - issued_at:.0f}s exceeds max {max_age_s:.0f}s (CWE-613)"
        return False, "Session within lifetime"


_fix = SessionFixationChecker()
_csrf = CSRFTokenValidator()
_entropy = SessionIdEntropyChecker()
_timeout = SessionTimeoutChecker()

_GOOD_SID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # 32 hex chars


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
# TEETH: the session auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct checker and
# returns whether the request should be FLAGGED. Each Mutant is a faithful
# planted defect. prove() compares to the frozen should_flag literal only.
# ===========================================================================

@dataclass(frozen=True)
class SessionCase:
    """One frozen session-audit fixture. ``payload`` is the checker's positional args."""
    name: str
    kind: str  # "fixation" | "csrf" | "entropy" | "timeout"
    payload: tuple
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a checker. Includes the discriminators each mutant gets wrong.
SESSION_CORPUS: tuple[SessionCase, ...] = (
    # fixation — (old_id, new_id, privilege_changed)
    SessionCase("rotated_on_login", "fixation", ("old_sid", "new_sid", True), False),
    SessionCase("not_rotated_on_login", "fixation", ("same_sid", "same_sid", True), True),
    SessionCase("no_priv_change_same_id", "fixation", ("same_sid", "same_sid", False), False),
    # csrf — (submitted, expected)
    SessionCase("valid_csrf", "csrf", ("tok123", "tok123"), False),
    SessionCase("missing_csrf", "csrf", ("", "tok123"), True),
    SessionCase("csrf_mismatch", "csrf", ("evil", "tok123"), True),
    SessionCase("no_bound_csrf", "csrf", ("tok123", ""), True),
    SessionCase("both_csrf_empty", "csrf", ("", ""), True),  # no token at all: must flag
    # entropy — (session_id,)
    SessionCase("strong_sid", "entropy", (_GOOD_SID,), False),
    SessionCase("short_sid", "entropy", ("abc123",), True),
    SessionCase("constant_sid", "entropy", ("a" * 32,), True),
    SessionCase("boundary_len_sid", "entropy", ("abcd" * 8,), False),  # exactly 32, varied
    # timeout — (issued_at, now, max_age_s)
    SessionCase("within_timeout", "timeout", (1000.0, 1500.0, 3600.0), False),
    SessionCase("expired_session", "timeout", (1000.0, 99999.0, 3600.0), True),
    SessionCase("boundary_timeout", "timeout", (1000.0, 4600.0, 3600.0), False),  # age == max
)


def oracle_session_audit(case: SessionCase) -> bool:
    """Correct verdict: does this session request exhibit a session-mgmt flaw (flag it)?

    Pure over its argument — dispatches to the harness's own checkers, no I/O.
    The timeout case carries its clock as injected literals, so no real time is read.
    """
    if case.kind == "fixation":
        old_id, new_id, priv = case.payload
        return _fix.check(old_id, new_id, priv)[0]
    if case.kind == "csrf":
        submitted, expected = case.payload
        return _csrf.validate(submitted, expected)[0]
    if case.kind == "entropy":
        (session_id,) = case.payload
        return _entropy.check(session_id)[0]
    if case.kind == "timeout":
        issued_at, now, max_age = case.payload
        return _timeout.check(issued_at, now, max_age)[0]
    raise ValueError(f"unknown session case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic session-control defect) -------

def mutant_fixation_ignores_priv(case: SessionCase) -> bool:
    """BUG: the fixation guard only flags when the id is unchanged but ignores
    whether privilege actually changed, so it would flag a benign same-id request
    with no privilege change (false positive) — a real 'forgot the priv condition'
    defect. Misclassifies no_priv_change_same_id. Other guards correct."""
    if case.kind == "fixation":
        old_id, new_id, _priv = case.payload
        return old_id == new_id  # BUG: drops the privilege_changed condition
    return oracle_session_audit(case)


def mutant_csrf_empty_token_accepted(case: SessionCase) -> bool:
    """BUG: the CSRF validator uses a plain == compare AND treats an empty
    submitted token as matching an empty/absent expected token, so a request that
    sends no CSRF token slips through when the session has no bound token — a real
    'missing-token == missing-token' authentication-bypass defect. Misclassifies
    missing_csrf. Other guards correct."""
    if case.kind == "csrf":
        submitted, expected = case.payload
        return str(submitted) != str(expected)  # BUG: "" == "" reads as valid; not constant-time
    return oracle_session_audit(case)


def mutant_entropy_off_by_one(case: SessionCase) -> bool:
    """BUG: the entropy checker uses ``<=`` instead of ``<`` for the length floor,
    so an id of exactly min_chars (a perfectly acceptable length) is wrongly
    flagged — an inclusive/exclusive boundary error. Misclassifies
    boundary_len_sid. Other guards correct."""
    if case.kind == "entropy":
        (session_id,) = case.payload
        sid = session_id or ""
        if len(sid) <= 32:  # BUG: <= flags the allowed boundary length
            return True
        return len(set(sid)) <= 2
    return oracle_session_audit(case)


def mutant_timeout_off_by_one(case: SessionCase) -> bool:
    """BUG: the timeout checker uses ``>=`` instead of ``>``, so a session whose age
    is exactly max_age (still within its lifetime) is wrongly expired — an
    inclusive/exclusive boundary error. Misclassifies boundary_timeout. Other
    guards correct."""
    if case.kind == "timeout":
        issued_at, now, max_age = case.payload
        return (now - issued_at) >= max_age  # BUG: >= expires the allowed boundary
    return oracle_session_audit(case)


def prove(audit: Callable[[SessionCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    SessionCase.should_flag constant, never against the oracle. A guard that
    raises on a corpus case counts as caught.
    """
    for case in SESSION_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_session_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_session_audit,
    mutants=(
        Mutant("fixation_ignores_priv", mutant_fixation_ignores_priv,
               "fixation guard drops the privilege-change condition, flagging a benign same-id request"),
        Mutant("csrf_empty_token_accepted", mutant_csrf_empty_token_accepted,
               "CSRF validator treats an empty submitted token as matching an absent expected token, "
               "so a tokenless state-changing request slips through"),
        Mutant("entropy_off_by_one", mutant_entropy_off_by_one,
               "entropy checker uses <= for the length floor, flagging an id at exactly min_chars"),
        Mutant("timeout_off_by_one", mutant_timeout_off_by_one,
               "timeout checker uses >= instead of >, expiring a session at exactly max_age"),
    ),
    corpus_size=len(SESSION_CORPUS),
    kind="auditor",
    notes="fixation (rotate-on-priv-change), csrf (constant-time + non-empty token), "
          "entropy (strict < length floor + distinct-char check), timeout (strict > over max age)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = SESSION_CORPUS


def run_case(case: SessionCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_session_audit(case)


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

    check("1. rotated session accepted", _fix.check("old", "new", True)[0] is False)
    check("2. fixation flagged", _fix.check("same", "same", True)[0] is True)
    check("3. no-priv-change same id ok", _fix.check("same", "same", False)[0] is False)
    check("4. valid csrf accepted", _csrf.validate("t", "t")[0] is False)
    check("5. missing csrf flagged", _csrf.validate("", "t")[0] is True)
    check("6. csrf mismatch flagged", _csrf.validate("x", "t")[0] is True)
    check("7. strong sid accepted", _entropy.check(_GOOD_SID)[0] is False)
    check("8. short sid flagged", _entropy.check("abc")[0] is True)
    check("9. constant sid flagged", _entropy.check("a" * 40)[0] is True)
    check("10. within timeout accepted", _timeout.check(1000, 1500, 3600)[0] is False)
    check("11. expired session flagged", _timeout.check(1000, 99999, 3600)[0] is True)

    for case in SESSION_CORPUS:
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
    report = Report("security/session")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_session_audit by its module-global name is what the vacuity
    # gate's neuter breaks.
    for case in SESSION_CORPUS:
        report.add(f"session:{case.name}", case.should_flag,
                   oracle_session_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="session_test_harness",
        description="Session management & CSRF harness (A07/A01:2025, pure stdlib)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run all scenarios; exit 0 if all pass")
    p.add_argument("--json", action="store_true", help="Emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        for name in list_scenarios():
            print(name)
        return 0
    return _run_self_test(verbose=args.verbose, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
