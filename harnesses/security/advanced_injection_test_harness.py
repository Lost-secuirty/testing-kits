#!/usr/bin/env python3
"""
advanced_injection_test_harness.py — A05:2025 Injection (SSTI / NoSQL / LDAP).
==============================================================================

Pure-stdlib. Zero external dependencies.

Extends the existing SQL/command/path/XSS coverage in security_test_harness with
the injection classes AI-generated code commonly misses. Maps to OWASP Top 10
2025 — A05:2025 Injection (SSTI / NoSQL / LDAP).

Hotspots / attacks exercised:
- Server-Side Template Injection: template expressions in user input. (CWE-1336/94)
- NoSQL operator injection: $-operators smuggled through a query value. (CWE-943)
- LDAP injection: LDAP filter metacharacters in user input. (CWE-90)

Checkers never raise on hostile input; they return (flagged, reason).

TEETH: the harness's own injection auditor (oracle_injection_audit) judged against
a FROZEN corpus of (kind, payload, should_flag) literals. Each planted Mutant is a
realistic injection-detection defect (an SSTI pattern set that only knows Jinja and
misses ${...} EL, a NoSQL checker that only inspects dict keys and not string
values, an LDAP special-char set missing the * wildcard). prove() compares each
auditor to the frozen should_flag literal — never to the oracle — so it is
non-circular and deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/advanced_injection_test_harness.py --self-test
    python harnesses/security/advanced_injection_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

_SSTI_PATTERNS = [
    re.compile(r"\{\{.*?\}\}"),     # Jinja/Twig/Handlebars
    re.compile(r"\{%.*?%\}"),       # Jinja statement
    re.compile(r"\$\{.*?\}"),       # JSP/EL, Thymeleaf, template literals
    re.compile(r"<%.*?%>"),         # ERB/EJS/ASP
    re.compile(r"#\{.*?\}"),        # Ruby / SpEL
]


class SSTIChecker:
    """Flag template expressions smuggled through user input (SSTI)."""

    def check(self, value: str) -> tuple[bool, str]:
        text = value if isinstance(value, str) else str(value)
        for pattern in _SSTI_PATTERNS:
            if pattern.search(text):
                return True, "Template expression in user input (SSTI, CWE-1336)"
        return False, "No template expression in input"


_NOSQL_OPERATORS = {
    "$ne", "$gt", "$gte", "$lt", "$lte", "$where", "$regex",
    "$or", "$and", "$in", "$nin", "$exists", "$expr", "$function",
}


class NoSQLInjectionChecker:
    """Flag NoSQL ($-prefixed) operators smuggled through a query value."""

    def check(self, value: Any) -> tuple[bool, str]:
        if isinstance(value, dict):
            for key in value:
                if isinstance(key, str) and key in _NOSQL_OPERATORS:
                    return True, f"NoSQL operator '{key}' in user-controlled value (CWE-943)"
        if isinstance(value, str):
            for op in ("$where", "$ne", "$gt", "$regex", "$function"):
                if op in value:
                    return True, f"NoSQL operator '{op}' in string value (CWE-943)"
        return False, "No NoSQL operator in value"


_LDAP_SPECIAL = set("()|&*\\\x00/")


class LDAPInjectionChecker:
    """Flag LDAP filter metacharacters in user input (LDAP injection)."""

    def check(self, value: str) -> tuple[bool, str]:
        text = value if isinstance(value, str) else str(value)
        # ignore characters that are already part of a valid \\XX escape sequence
        unescaped = re.sub(r"\\[0-9a-fA-F]{2}", "", text)
        bad = sorted({ch for ch in unescaped if ch in _LDAP_SPECIAL})
        if bad:
            shown = [repr(c) for c in bad]
            return True, f"LDAP filter metacharacter(s) {shown} in input (CWE-90)"
        return False, "No LDAP metacharacters in input"

    def escape(self, value: str) -> str:
        out = []
        for ch in value:
            if ch in _LDAP_SPECIAL:
                out.append(f"\\{ord(ch):02x}")
            else:
                out.append(ch)
        return "".join(out)


_ssti = SSTIChecker()
_nosql = NoSQLInjectionChecker()
_ldap = LDAPInjectionChecker()


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
# TEETH: the injection auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct checker and
# returns whether the input should be FLAGGED. Each Mutant is a faithful
# planted defect. prove() compares to the frozen should_flag literal only.
# ===========================================================================

@dataclass(frozen=True)
class InjectionCase:
    """One frozen injection-audit fixture. ``payload`` is the checker's single arg."""
    name: str
    kind: str  # "ssti" | "nosql" | "ldap"
    payload: Any
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a checker. Includes the discriminators each mutant gets wrong.
INJECTION_CORPUS: tuple[InjectionCase, ...] = (
    # SSTI
    InjectionCase("ssti_plain_text", "ssti", "Hello Alice", False),
    InjectionCase("ssti_jinja", "ssti", "{{7*7}}", True),
    InjectionCase("ssti_el", "ssti", "${T(java.lang.Runtime)}", True),
    InjectionCase("ssti_erb", "ssti", "<%= x %>", True),
    # NoSQL
    InjectionCase("nosql_plain_dict", "nosql", {"user": "alice"}, False),
    InjectionCase("nosql_operator_key", "nosql", {"$ne": None}, True),
    InjectionCase("nosql_where_string", "nosql", "'; return this.$where", True),
    # LDAP
    InjectionCase("ldap_plain_username", "ldap", "john.doe", False),
    InjectionCase("ldap_wildcard", "ldap", "*)(uid=*", True),
    InjectionCase("ldap_escaped_clean", "ldap", "john\\2adoe", False),
    # Discriminator: a bare '*' wildcard whose only special char is '*'. The
    # oracle flags it; the ldap_no_wildcard mutant (which forgot '*') does not.
    InjectionCase("ldap_bare_wildcard", "ldap", "admin*", True),
)


def oracle_injection_audit(case: InjectionCase) -> bool:
    """Correct verdict: does this input carry an injection payload (flag it)?

    Pure over its argument — dispatches to the harness's own checkers, no I/O.
    """
    if case.kind == "ssti":
        return _ssti.check(case.payload)[0]
    if case.kind == "nosql":
        return _nosql.check(case.payload)[0]
    if case.kind == "ldap":
        return _ldap.check(case.payload)[0]
    raise ValueError(f"unknown injection case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic injection-detection defect) ---

# A mutant SSTI pattern set that only knows Jinja {{...}} and forgot the rest.
_MUT_SSTI_PATTERNS = [re.compile(r"\{\{.*?\}\}")]


def mutant_ssti_jinja_only(case: InjectionCase) -> bool:
    """BUG: the SSTI matcher only recognises Jinja '{{...}}' and misses '${...}'
    EL / '<%...%>' ERB expressions — a real 'we only tested our own templating
    engine' gap, so ${T(java.lang.Runtime)} slips through unflagged."""
    if case.kind == "ssti":
        text = case.payload if isinstance(case.payload, str) else str(case.payload)
        return any(p.search(text) for p in _MUT_SSTI_PATTERNS)  # BUG: incomplete set
    return oracle_injection_audit(case)


def mutant_nosql_keys_only(case: InjectionCase) -> bool:
    """BUG: the NoSQL checker only inspects dict KEYS and never scans string
    values, so an operator smuggled inside a string (e.g. '$where' in a JS
    expression) is missed — a real type-narrowing defect."""
    if case.kind == "nosql":
        value = case.payload
        if isinstance(value, dict):  # BUG: drops the string-scanning branch
            for key in value:
                if isinstance(key, str) and key in _NOSQL_OPERATORS:
                    return True
        return False
    return oracle_injection_audit(case)


# A mutant LDAP special-char set that forgot the '*' wildcard.
_MUT_LDAP_SPECIAL = set("()|&\\\x00/")


def mutant_ldap_no_wildcard(case: InjectionCase) -> bool:
    """BUG: the LDAP metacharacter set omits '*', so a wildcard-only injection
    like '*' (or '*)(uid=*' whose other chars are stripped first) is no longer
    flagged — a real 'we forgot the wildcard' filter-escaping gap."""
    if case.kind == "ldap":
        text = case.payload if isinstance(case.payload, str) else str(case.payload)
        unescaped = re.sub(r"\\[0-9a-fA-F]{2}", "", text)
        return any(ch in _MUT_LDAP_SPECIAL for ch in unescaped)  # BUG: '*' missing
    return oracle_injection_audit(case)


def prove(audit: Callable[[InjectionCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    InjectionCase.should_flag constant, never against the oracle. A checker that
    raises on a corpus case counts as caught.
    """
    for case in INJECTION_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_injection_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_injection_audit,
    mutants=(
        Mutant("ssti_jinja_only", mutant_ssti_jinja_only,
               "SSTI matcher only knows Jinja '{{...}}' and misses '${...}' EL, so it slips through"),
        Mutant("nosql_keys_only", mutant_nosql_keys_only,
               "NoSQL checker only inspects dict keys and never scans string values, missing '$where' in a string"),
        Mutant("ldap_no_wildcard", mutant_ldap_no_wildcard,
               "LDAP special-char set omits '*', so a wildcard filter-injection is not flagged"),
    ),
    corpus_size=len(INJECTION_CORPUS),
    kind="auditor",
    notes="SSTI (full template-syntax pattern set), NoSQL (dict-key + string-value scan), "
          "LDAP (filter metacharacters incl. the '*' wildcard, escape-aware)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = INJECTION_CORPUS


def run_case(case: InjectionCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_injection_audit(case)


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

    check("1. plain text no SSTI", _ssti.check("Hello")[0] is False)
    check("2. jinja SSTI flagged", _ssti.check("{{7*7}}")[0] is True)
    check("3. EL SSTI flagged", _ssti.check("${x}")[0] is True)
    check("4. ERB SSTI flagged", _ssti.check("<%= x %>")[0] is True)
    check("5. plain dict query clean", _nosql.check({"user": "a"})[0] is False)
    check("6. nosql operator flagged", _nosql.check({"$ne": None})[0] is True)
    check("7. nosql where-string flagged", _nosql.check("$where: 1")[0] is True)
    check("8. plain username no LDAP", _ldap.check("john.doe")[0] is False)
    check("9. ldap metachar flagged", _ldap.check("*)(uid=*")[0] is True)
    check("10. ldap escape neutralizes", _ldap.check(_ldap.escape("*)(uid=*"))[0] is False)

    for case in INJECTION_CORPUS:
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
    report = Report("security/advanced_injection")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_injection_audit by its module-global name is what the
    # vacuity gate's neuter breaks.
    for case in INJECTION_CORPUS:
        report.add(f"injection:{case.name}", case.should_flag,
                   oracle_injection_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="advanced_injection_test_harness",
        description="OWASP A05:2025 Injection (SSTI / NoSQL / LDAP) harness (pure stdlib)",
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
