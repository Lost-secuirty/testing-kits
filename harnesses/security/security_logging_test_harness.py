#!/usr/bin/env python3
"""
security_logging_test_harness.py — Security Logging & Alerting Failures (A09:2025).
===================================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib).

pii_redaction_test_harness covers the "don't log secrets" side. A09 also requires
that security events ARE logged, can't be forged, and trip alerts. This harness
covers that complementary, positive side. Maps to OWASP Top 10 2025 —
A09:2025 Security Logging and Alerting Failures.

Hotspots / attacks exercised:
- Missing audit events: login success/failure, authz denial, admin action. (CWE-778)
- Log injection / forging via CRLF or control characters. (CWE-117)
- Alert threshold logic: N failures in a window must alert; fewer must not.
- Tamper-evidence: a hash-chained log detects a mid-sequence edit. (CWE-345)

Checkers never raise on hostile input; they return findings or (flagged, reason).

TEETH: the harness's own logging auditor (oracle_security_logging_audit) judged
against a FROZEN corpus of (kind, payload, should_flag) literals. Each planted
Mutant is a realistic logging-control defect (an audit checker that ignores a
required event, an injection checker that only looks for "\\n" and misses bare
"\\r", an alert threshold that is off-by-one at the boundary, a hash-chain verify
that only checks the prev-pointer and not the recomputed hash). prove() compares
each auditor to the frozen should_flag literal — never to the oracle — so it is
non-circular and deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/security_logging_test_harness.py --self-test
    python harnesses/security/security_logging_test_harness.py --json
    python harnesses/security/security_logging_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class LogFinding:
    check_name: str
    severity: str
    description: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"Invalid severity: {self.severity}")


# ---------------------------------------------------------------------------
# AuditCoverageChecker (CWE-778)
# ---------------------------------------------------------------------------

class AuditCoverageChecker:
    """Compare emitted audit events against a required set."""

    def missing(self, required: Sequence[str], emitted: Sequence[str]) -> list[str]:
        emitted_set = set(emitted or [])
        return [evt for evt in (required or []) if evt not in emitted_set]

    def check(self, required: Sequence[str], emitted: Sequence[str]) -> list[LogFinding]:
        return [
            LogFinding("AuditCoverageChecker", "HIGH",
                       f"Required security event not logged: '{evt}'", evt)
            for evt in self.missing(required, emitted)
        ]


# ---------------------------------------------------------------------------
# LogInjectionChecker (CWE-117)
# ---------------------------------------------------------------------------

# Matches a forged log-level prefix (DEBUG:/ADMIN:/etc.) only at the START of a
# line (multiline ^). Anchoring to a line start is what keeps this safe: attacker
# content can only forge a record by sitting at a line boundary, so a level token
# buried mid-line (e.g. in an already-escaped, single-physical-line message where
# the newline is now the literal text "\n") is NOT a forged prefix and stays clean.
_FAKE_LEVEL_RE = re.compile(r"(?im)^\s*(?:DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|ADMIN)\s*:")


class LogInjectionChecker:
    def check(self, raw_message: str) -> tuple[bool, str]:
        msg = raw_message if isinstance(raw_message, str) else str(raw_message)
        if "\n" in msg or "\r" in msg:
            return True, "Newline/CRLF in log message enables line injection (CWE-117)"
        if "\x1b" in msg:
            return True, "ANSI escape sequence in log message (CWE-117)"
        if any(ord(ch) < 32 and ch not in "\t" for ch in msg):
            return True, "Control character in log message (CWE-117)"
        if _FAKE_LEVEL_RE.search(msg):
            return True, "Forged log-level prefix at line start spoofs a real record (CWE-117)"
        return False, "Log message has no injectable control characters"

    def escape(self, raw_message: str) -> str:
        """Safe encoding that neutralizes injection (the recommended fix)."""
        msg = raw_message if isinstance(raw_message, str) else str(raw_message)
        return (msg.replace("\\", "\\\\")
                   .replace("\n", "\\n")
                   .replace("\r", "\\r")
                   .replace("\x1b", "\\x1b"))


# ---------------------------------------------------------------------------
# AlertThreshold
# ---------------------------------------------------------------------------

class AlertThreshold:
    """Sliding-window failure counter with an injected clock (no real time read)."""

    def __init__(self) -> None:
        self._events: list[float] = []

    def record(self, timestamp: float) -> None:
        self._events.append(float(timestamp))

    def should_alert(self, now: float, window_s: float, threshold: int) -> bool:
        cutoff = now - window_s
        recent = [t for t in self._events if t >= cutoff]
        return len(recent) >= threshold


# ---------------------------------------------------------------------------
# HashChainLog (CWE-345)
# ---------------------------------------------------------------------------

class HashChainLog:
    """Append-only, hash-chained log. Editing any entry breaks verify()."""

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self.entries: list[dict[str, str]] = []

    def _link(self, prev_hash: str, data: str) -> str:
        return hashlib.sha256((prev_hash + "|" + data).encode("utf-8")).hexdigest()

    def append(self, data: str) -> None:
        prev = self.entries[-1]["hash"] if self.entries else self.GENESIS
        self.entries.append({"data": data, "prev": prev, "hash": self._link(prev, data)})

    def verify(self) -> tuple[bool, int]:
        prev = self.GENESIS
        for idx, entry in enumerate(self.entries):
            if entry["prev"] != prev:
                return False, idx
            if self._link(prev, entry["data"]) != entry["hash"]:
                return False, idx
            prev = entry["hash"]
        return True, -1


_audit = AuditCoverageChecker()
_inject = LogInjectionChecker()

_REQUIRED = ("login_success", "login_failure", "authz_denied", "admin_action")
_FULL = ("login_success", "login_failure", "authz_denied", "admin_action", "logout")
_PARTIAL = ("login_success", "logout")


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
# TEETH: the logging auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct checker and
# returns whether the situation should be FLAGGED. Each Mutant is a faithful
# planted defect. prove() compares to the frozen should_flag literal only.
# ===========================================================================

@dataclass(frozen=True)
class LoggingCase:
    """One frozen logging-audit fixture. ``payload`` is the checker's args."""
    name: str
    kind: str  # "audit" | "inject" | "alert" | "chain"
    payload: tuple
    should_flag: bool


def _build_chain(datas: Sequence[str], tamper_idx: int | None = None) -> HashChainLog:
    """Construct a deterministic hash-chained log, optionally editing one entry's data."""
    log = HashChainLog()
    for d in datas:
        log.append(d)
    if tamper_idx is not None:
        log.entries[tamper_idx]["data"] = "forged"
    return log


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a checker. Includes the discriminators each mutant gets wrong.
LOGGING_CORPUS: tuple[LoggingCase, ...] = (
    # audit coverage (CWE-778): flag when a REQUIRED event is missing
    LoggingCase("full_coverage", "audit", (_REQUIRED, _FULL), False),
    LoggingCase("missing_authz_denied", "audit", (_REQUIRED, _PARTIAL), True),
    LoggingCase("missing_only_admin_action", "audit",
                (_REQUIRED, ("login_success", "login_failure", "authz_denied")), True),
    # log injection (CWE-117): flag any injectable control character
    LoggingCase("clean_message", "inject", ("user alice logged in",), False),
    LoggingCase("crlf_newline_injection", "inject", ("ok\nADMIN: deleted all",), True),
    LoggingCase("bare_cr_injection", "inject", ("ok\rADMIN: deleted all",), True),
    # forged log-level prefix at line start with NO newline/CR/control char: only
    # the _FAKE_LEVEL_RE level-prefix check catches it (the byte-level checks miss it).
    LoggingCase("forged_level_prefix", "inject", ("ADMIN: deleted all users",), True),
    LoggingCase("escaped_message_clean", "inject", (_inject.escape("ok\nADMIN: x"),), False),
    # alert threshold: flag (alert) when >= threshold failures fall in the window
    LoggingCase("below_threshold", "alert", ((1000, 1001, 1002), 1003, 60, 5), False),
    LoggingCase("at_threshold", "alert", ((1000, 1001, 1002, 1003, 1004), 1004, 60, 5), True),
    LoggingCase("over_threshold", "alert",
                ((1000, 1001, 1002, 1003, 1004, 1005), 1006, 60, 5), True),
    # tamper-evidence (CWE-345): flag when the chain does NOT verify
    LoggingCase("intact_chain", "chain", (("e1", "e2", "e3"), None), False),
    LoggingCase("tampered_middle", "chain", (("e1", "e2", "e3"), 1), True),
)


def oracle_security_logging_audit(case: LoggingCase) -> bool:
    """Correct verdict: should this logging situation be FLAGGED?

    Pure over its argument — dispatches to the harness's own checkers, no real I/O.
    """
    if case.kind == "audit":
        required, emitted = case.payload
        return len(_audit.missing(required, emitted)) > 0
    if case.kind == "inject":
        (message,) = case.payload
        return _inject.check(message)[0]
    if case.kind == "alert":
        events, now, window_s, threshold = case.payload
        a = AlertThreshold()
        for t in events:
            a.record(t)
        return a.should_alert(now=now, window_s=window_s, threshold=threshold)
    if case.kind == "chain":
        datas, tamper_idx = case.payload
        log = _build_chain(datas, tamper_idx)
        return not log.verify()[0]
    raise ValueError(f"unknown logging case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic logging-control defect) -------

def mutant_audit_ignores_admin_action(case: LoggingCase) -> bool:
    """BUG: the audit checker treats 'admin_action' as always-covered (a hardcoded
    exemption), so a log that never records admin actions is never flagged — a real
    'we forgot to require the highest-value event' coverage gap (CWE-778)."""
    if case.kind == "audit":
        required, emitted = case.payload
        emitted_set = set(emitted or ()) | {"admin_action"}  # BUG: admin_action exempted
        missing = [e for e in (required or ()) if e not in emitted_set]
        return len(missing) > 0
    return oracle_security_logging_audit(case)


def mutant_inject_newline_only(case: LoggingCase) -> bool:
    """BUG: the injection checker only looks for '\\n' and misses a bare '\\r'
    (and other control chars), so a CR-only forged record slips through — a real
    incomplete-CRLF-handling defect (CWE-117)."""
    if case.kind == "inject":
        (message,) = case.payload
        msg = message if isinstance(message, str) else str(message)
        return "\n" in msg  # BUG: only '\n', misses bare '\r' and control chars
    return oracle_security_logging_audit(case)


def mutant_alert_strict_greater(case: LoggingCase) -> bool:
    """BUG: the alert threshold uses '>' instead of '>=', so exactly ``threshold``
    failures in the window do NOT alert — an off-by-one that lets an attacker stay
    one event under the wire forever."""
    if case.kind == "alert":
        events, now, window_s, threshold = case.payload
        cutoff = now - window_s
        recent = [t for t in events if t >= cutoff]
        return len(recent) > threshold  # BUG: '>' misses the exact-threshold boundary
    return oracle_security_logging_audit(case)


def mutant_chain_skips_hash_recompute(case: LoggingCase) -> bool:
    """BUG: the chain verifier only checks each entry's stored prev-pointer and never
    recomputes the hash from the data, so editing an entry's payload in place (which
    leaves prev intact) is NOT detected — defeats tamper-evidence (CWE-345)."""
    if case.kind == "chain":
        datas, tamper_idx = case.payload
        log = _build_chain(datas, tamper_idx)
        prev = log.GENESIS
        for entry in log.entries:
            if entry["prev"] != prev:  # BUG: never recompute _link(prev, data)
                return True
            prev = entry["hash"]
        return False
    return oracle_security_logging_audit(case)


def prove(audit: Callable[[LoggingCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    LoggingCase.should_flag constant, never against the oracle. A checker that
    raises on a corpus case counts as caught.
    """
    for case in LOGGING_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_security_logging_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_security_logging_audit,
    mutants=(
        Mutant("audit_ignores_admin_action", mutant_audit_ignores_admin_action,
               "audit checker exempts 'admin_action', so a log that never records it is never flagged"),
        Mutant("inject_newline_only", mutant_inject_newline_only,
               "injection checker only looks for '\\n', so a bare '\\r' forged record slips through"),
        Mutant("alert_strict_greater", mutant_alert_strict_greater,
               "alert threshold uses '>' not '>=', so exactly N failures in the window never alert"),
        Mutant("chain_skips_hash_recompute", mutant_chain_skips_hash_recompute,
               "chain verify checks only the prev-pointer, never recomputing the hash, so an in-place edit is missed"),
    ),
    corpus_size=len(LOGGING_CORPUS),
    kind="auditor",
    notes="audit coverage (required-event membership), log injection (any control char), "
          "alert threshold (>= over the window), hash-chain tamper-evidence (prev + recomputed hash)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = LOGGING_CORPUS


def run_case(case: LoggingCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_security_logging_audit(case)


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

    check("1. full audit coverage clean", len(_audit.missing(_REQUIRED, _FULL)) == 0)
    check("2. missing audit event flagged", len(_audit.missing(_REQUIRED, _PARTIAL)) >= 1)
    check("3. clean message accepted", _inject.check("user alice logged in")[0] is False)
    check("4. CRLF injection flagged", _inject.check("ok\nADMIN: deleted all")[0] is True)
    check("5. bare CR injection flagged", _inject.check("ok\rADMIN: deleted all")[0] is True)
    check("6. escaped message is clean", _inject.check(_inject.escape("ok\nADMIN: x"))[0] is False)

    a_under = AlertThreshold()
    for t in (1000, 1001, 1002):
        a_under.record(t)
    check("7. below threshold no alert",
          a_under.should_alert(now=1003, window_s=60, threshold=5) is False)

    a_over = AlertThreshold()
    for t in range(6):
        a_over.record(1000 + t)
    check("8. over threshold alerts",
          a_over.should_alert(now=1006, window_s=60, threshold=5) is True)

    check("9. intact chain verifies", HashChainLog().verify()[0] is True)

    log = HashChainLog()
    for d in ("a", "b", "c"):
        log.append(d)
    log.entries[1]["data"] = "x"
    ok, idx = log.verify()
    check("10. tampered chain detected at correct index",
          (ok is False) and (idx == 1), f"idx={idx}")

    for case in LOGGING_CORPUS:
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
    report = Report("security/security_logging")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_security_logging_audit by its module-global name is what the
    # vacuity gate's neuter breaks.
    for case in LOGGING_CORPUS:
        report.add(f"logging:{case.name}", case.should_flag,
                   oracle_security_logging_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="security_logging_test_harness",
        description="OWASP A09:2025 Security Logging and Alerting Failures harness (pure stdlib)",
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
