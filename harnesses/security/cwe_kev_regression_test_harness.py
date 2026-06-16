#!/usr/bin/env python3
"""
CWE / KEV regression test harness.

Maps current high-frequency vulnerability classes to deterministic fixtures.
The harness is intentionally fixture-driven: a useful implementation must allow
known-safe inputs and reject known-bad controls for each covered flaw class.

Self-test:
  python harnesses/security/cwe_kev_regression_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from urllib.parse import urlparse

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

Detector = Callable[[str], bool]
# An auditor under test: given a CWE class and a payload, decide whether the
# payload should be BLOCKED (flagged). The correct oracle dispatches to the
# harness's own DETECTORS; mutants weaken or over-broaden a single detector.
Auditor = Callable[[str, str], bool]


@dataclass(frozen=True)
class RegressionCase:
    name: str
    cwe: str
    payload: str
    should_block: bool
    note: str


@dataclass(frozen=True)
class RegressionResult:
    case: RegressionCase
    blocked: bool
    ok: bool


_SQLI = re.compile(r"('|--|\bOR\b\s+\d+=\d+|\bUNION\b\s+\bSELECT\b)", re.I)
_XSS = re.compile(r"<\s*script|onerror\s*=|javascript:", re.I)
_COMMAND = re.compile(r"(;|\|\||&&|`|\$\(|\bcat\s+/etc/passwd\b|\bwhoami\b)", re.I)
_CODE = re.compile(r"\b(eval|Function|exec|compile)\s*\(", re.I)
_DESERIAL = re.compile(r"(!!python/|pickle|__reduce__|java\.io\.Serializable)", re.I)
_TRAVERSAL = re.compile(r"(^|/|\\)\.\.(/|\\)|%2e%2e%2f|%2e%2e%5c", re.I)
_UPLOAD = re.compile(r"\.(php|jsp|asp|aspx|exe|sh|bat|cmd)(\b|$)", re.I)


def _csrf(payload: str) -> bool:
    return "method=post" in payload.lower() and "csrf=" not in payload.lower()


def _authz(payload: str) -> bool:
    low = payload.lower()
    return "role=user" in low and ("target=admin" in low or "account_id=other" in low)


def _ssrf(payload: str) -> bool:
    parsed = urlparse(payload)
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "169.254.169.254", "metadata.google.internal"}


def _resource_limit(payload: str) -> bool:
    try:
        requested = int(payload.split("=", 1)[1])
    except (IndexError, ValueError):
        return False
    return requested > 10_000


DETECTORS: dict[str, Detector] = {
    "CWE-79": lambda text: bool(_XSS.search(text)),
    "CWE-89": lambda text: bool(_SQLI.search(text)),
    "CWE-352": _csrf,
    "CWE-862": _authz,
    "CWE-22": lambda text: bool(_TRAVERSAL.search(text)),
    "CWE-78": lambda text: bool(_COMMAND.search(text)),
    "CWE-94": lambda text: bool(_CODE.search(text)),
    "CWE-434": lambda text: bool(_UPLOAD.search(text)),
    "CWE-502": lambda text: bool(_DESERIAL.search(text)),
    "CWE-918": _ssrf,
    "CWE-770": _resource_limit,
}


CASES: tuple[RegressionCase, ...] = (
    RegressionCase("safe_html", "CWE-79", "Plain title", False, "safe text"),
    RegressionCase("xss_script", "CWE-79", "<script>alert(1)</script>", True, "script tag"),
    RegressionCase("safe_lookup", "CWE-89", "name=alice", False, "plain query"),
    RegressionCase("sql_or_true", "CWE-89", "' OR 1=1 --", True, "classic SQL injection"),
    RegressionCase("post_with_csrf", "CWE-352", "method=POST&csrf=token", False, "token present"),
    RegressionCase("post_no_csrf", "CWE-352", "method=POST&amount=100", True, "token missing"),
    RegressionCase("own_account", "CWE-862", "role=user&account_id=self", False, "same account"),
    RegressionCase("other_account", "CWE-862", "role=user&account_id=other", True, "IDOR/authz bypass"),
    RegressionCase("safe_path", "CWE-22", "images/avatar.png", False, "normal relative path"),
    RegressionCase("path_traversal", "CWE-22", "../../etc/passwd", True, "parent traversal"),
    RegressionCase("safe_arg", "CWE-78", "report-2026", False, "plain argument"),
    RegressionCase("command_chain", "CWE-78", "report; cat /etc/passwd", True, "shell chain"),
    RegressionCase("safe_formula", "CWE-94", "sum(price)", False, "domain expression"),
    RegressionCase("eval_payload", "CWE-94", "eval(user_input)", True, "dynamic eval"),
    RegressionCase("safe_upload", "CWE-434", "photo.png", False, "allowed extension"),
    RegressionCase("script_upload", "CWE-434", "shell.php", True, "server executable extension"),
    RegressionCase("safe_json", "CWE-502", '{"kind":"note"}', False, "plain JSON"),
    RegressionCase("pickle_hint", "CWE-502", "pickle __reduce__", True, "dangerous deserialization"),
    RegressionCase("safe_url", "CWE-918", "https://example.com/status", False, "public host"),
    RegressionCase("metadata_url", "CWE-918", "http://169.254.169.254/latest", True, "metadata SSRF"),
    RegressionCase("small_page", "CWE-770", "limit=100", False, "bounded request"),
    RegressionCase("huge_page", "CWE-770", "limit=1000000", True, "unbounded request"),
)


def list_cases() -> list[str]:
    return [case.name for case in CASES]


def run_case(case: RegressionCase) -> RegressionResult:
    detector = DETECTORS[case.cwe]
    blocked = detector(case.payload)
    return RegressionResult(case=case, blocked=blocked, ok=blocked == case.should_block)


def run_all() -> list[RegressionResult]:
    return [run_case(case) for case in CASES]


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (cwe, payload) -> should_flag LITERAL.
#
# This is a security AUDITOR: an "auditor impl" is a callable
# ``audit(cwe: str, payload: str) -> bool`` returning True iff the payload
# should be BLOCKED for that CWE class. The harness only has teeth if it CATCHES
# an auditor that MISSES a known-vulnerable payload (a false negative — the
# dangerous failure) or FALSE-FLAGS a known-safe payload (a false positive —
# which erodes trust and gets the control disabled).
#
# prove(audit_impl) judges the impl ONLY against AUDIT_CORPUS, whose
# ``should_flag`` booleans are hand-derived from the CWE/KEV ground truth (a
# script tag IS XSS; ``%2e%2e%2f`` IS path traversal; ``UNION SELECT`` IS SQLi),
# NOT read back from DETECTORS at runtime. So the check is non-circular:
# corrupting a frozen literal makes prove(oracle) flip False -> True.
# prove(impl) is True iff the impl's verdict diverges from the frozen literal on
# any case (or it raises) — i.e. the planted auditor defect is caught.
#
# Pure + deterministic: string/regex matching only, no RNG, clock, network,
# filesystem, or threads.
#
# The corpus deliberately covers BOTH the known-vulnerable variant and the
# known-safe variant for the three CWE classes the mutants attack (SQLi,
# path traversal, XSS), so a weakened/over-narrow detector is caught on its own
# class and a clean detector stays clean on the safe twins.
#
# The three planted mutants model genuine real-world detector regressions:
#
#   * weak_sqli_auditor — narrows the SQLi regex to only the literal
#     ``' OR <n>=<n>`` tautology and DROPS the ``UNION SELECT`` alternative, so a
#     ``UNION SELECT`` exfiltration payload sails through (a false negative — the
#     classic over-fitted WAF rule that misses the next injection shape);
#   * weak_traversal_auditor — narrows the path-traversal regex to LITERAL
#     ``../`` only and drops the percent-encoded ``%2e%2e%2f`` / ``%2e%2e%5c``
#     alternatives, so a URL-encoded traversal (a real KEV bypass technique)
#     is missed;
#   * overbroad_xss_auditor — broadens the XSS check to flag ANY ``<`` or ``>``
#     character, so benign prose containing a comparison ("a < b") is
#     FALSE-FLAGGED (a false positive that floods triage and gets the rule muted).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditCase:
    """One frozen auditor case with a literal, hand-derived block decision."""

    name: str
    cwe: str
    payload: str
    should_flag: bool   # the EXACT block decision a correct auditor must make
    note: str = ""


# Every ``should_flag`` is a constant derived from the CWE/KEV ground truth,
# never read from DETECTORS at runtime. Cases are chosen so the correct oracle
# matches every literal AND each planted mutant gets at least one of them wrong.
AUDIT_CORPUS: tuple[AuditCase, ...] = (
    # --- SQLi (CWE-89): the over-narrow mutant misses UNION SELECT -----------
    AuditCase("sqli_or_tautology", "CWE-89", "' OR 1=1 --", True,
              "classic tautology injection — must block"),
    AuditCase("sqli_union_select", "CWE-89", "id=1 UNION SELECT password FROM users", True,
              "UNION-based exfiltration — the over-narrow WAF rule misses this"),
    AuditCase("sqli_safe_query", "CWE-89", "name=alice", False,
              "plain parameter — must not block"),
    # --- Path traversal (CWE-22): the over-narrow mutant misses URL-encoding -
    AuditCase("traversal_literal", "CWE-22", "../../etc/passwd", True,
              "literal parent traversal — must block"),
    AuditCase("traversal_encoded", "CWE-22", "%2e%2e%2fetc%2fpasswd", True,
              "percent-encoded traversal (KEV bypass) — narrow regex misses it"),
    AuditCase("traversal_safe_path", "CWE-22", "images/avatar.png", False,
              "normal relative path — must not block"),
    # --- XSS (CWE-79): the over-broad mutant false-flags benign comparison ---
    AuditCase("xss_script_tag", "CWE-79", "<script>alert(1)</script>", True,
              "script tag — must block"),
    AuditCase("xss_onerror", "CWE-79", "img src=x onerror=alert(1)", True,
              "event-handler injection — must block"),
    AuditCase("xss_safe_comparison", "CWE-79", "results where a < b and b > c", False,
              "prose with a comparison — must NOT block (over-broad regex false-flags)"),
)


def list_audit_cases() -> list[str]:
    """Names of the frozen auditor corpus cases (the teeth scenarios)."""
    return [case.name for case in AUDIT_CORPUS]


# --- ORACLE: reuse the harness's own correct detector dispatch ---------------

def oracle_audit(cwe: str, payload: str) -> bool:
    """Correct auditor: dispatch to the harness's own DETECTORS and return its
    block decision. This is the harness's real control surface, reused as-is."""
    return bool(DETECTORS[cwe](payload))


# --- Planted buggy twins (each models a real auditor/regex regression) -------

# Over-narrow SQLi: drops the UNION SELECT alternative -> misses exfiltration.
_WEAK_SQLI = re.compile(r"('|\bOR\b\s+\d+=\d+)", re.I)
# Over-narrow traversal: literal ../ or ..\ only, no percent-encoded variants.
_WEAK_TRAVERSAL = re.compile(r"(^|/|\\)\.\.(/|\\)")


def weak_sqli_auditor(cwe: str, payload: str) -> bool:
    """BUG: a SQLi regex over-fitted to the tautology shape that DROPS the
    ``UNION SELECT`` alternative — a UNION-based injection is missed (false
    negative). All other CWE classes fall through to the correct detectors."""
    if cwe == "CWE-89":
        return bool(_WEAK_SQLI.search(payload))
    return bool(DETECTORS[cwe](payload))


def weak_traversal_auditor(cwe: str, payload: str) -> bool:
    """BUG: a path-traversal regex that matches only LITERAL ``../`` and drops
    the percent-encoded ``%2e%2e%2f`` / ``%2e%2e%5c`` alternatives, so a
    URL-encoded traversal (a real KEV bypass) sails through (false negative)."""
    if cwe == "CWE-22":
        return bool(_WEAK_TRAVERSAL.search(payload))
    return bool(DETECTORS[cwe](payload))


def overbroad_xss_auditor(cwe: str, payload: str) -> bool:
    """BUG: an XSS check broadened to flag ANY ``<`` or ``>`` character. Benign
    prose containing a comparison is FALSE-FLAGGED (false positive), and an
    attribute-context payload with no angle brackets (e.g. an ``onerror=`` event
    handler) is MISSED (false negative) — an over-broad rule that is still wrong
    both ways."""
    if cwe == "CWE-79":
        return ("<" in payload) or (">" in payload)
    return bool(DETECTORS[cwe](payload))


def prove(impl: Auditor) -> bool:
    """True iff ``impl`` DIVERGES from the frozen AUDIT_CORPUS on any case (i.e.
    the auditor defect is caught): it misses a payload that must be flagged
    (false negative) or flags one that must not be (false positive), or raises.

    Non-circular + deterministic: every ``should_flag`` is a literal baked into
    AUDIT_CORPUS, never read from DETECTORS; string/regex matching only, no
    RNG/clock/network/filesystem. An impl that raises on a corpus case counts
    as caught.
    """
    for case in AUDIT_CORPUS:
        try:
            verdict = bool(impl(case.cwe, case.payload))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_audit,
    mutants=(
        Mutant("weak_sqli_auditor", weak_sqli_auditor,
               "SQLi regex over-fitted to the ' OR n=n tautology drops the UNION "
               "SELECT alternative -> a UNION-based exfiltration payload is missed"),
        Mutant("weak_traversal_auditor", weak_traversal_auditor,
               "path-traversal regex matches only literal ../ and drops the "
               "percent-encoded %2e%2e%2f variant -> a URL-encoded KEV bypass is missed"),
        Mutant("overbroad_xss_auditor", overbroad_xss_auditor,
               "XSS check broadened to flag any < or > char -> benign prose with a "
               "comparison is false-flagged (a false positive that mutes the rule)"),
    ),
    corpus_size=len(AUDIT_CORPUS),
    kind="auditor",
    notes="a CWE auditor must block every known-vulnerable payload and allow every "
          "known-safe one: an over-narrow detector misses a vuln, an over-broad one "
          "false-flags clean input",
)


def _run_self_test(as_json: bool = False) -> int:
    report = Report("security/cwe_kev")

    # 1. KEEP the original control: every catalogued regression case must match
    #    its expected block decision (no detector regressed).
    for result in run_all():
        report.add(
            f"control:{result.case.name}",
            result.case.should_block,
            result.blocked,
            detail=f"{result.case.cwe} {result.case.note}",
        )

    # 2. The correct oracle reproduces every frozen auditor literal exactly
    #    (the non-circular corpus the teeth are judged against).
    for case in AUDIT_CORPUS:
        report.add(
            f"oracle_audit:{case.name}",
            case.should_flag,
            oracle_audit(case.cwe, case.payload),
            detail=f"{case.cwe} {case.note}",
        )

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CWE/KEV regression controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable self-test findings (implies --self-test)")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_cases()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
