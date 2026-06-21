#!/usr/bin/env python3
"""
misconfig_test_harness.py — Security Misconfiguration harness (A02:2025).
=========================================================================

Pure-stdlib. Zero external dependencies.

Complements security_test_harness.HeaderSecurityAudit (response headers) by
covering the rest of A02. Deliberately disjoint from the header audit. Maps to
OWASP Top 10 2025 — A02:2025 Security Misconfiguration.

Hotspots / attacks exercised:
- Debug/verbose mode enabled in production (DEBUG=True, development mode). (CWE-489/215)
- Permissive CORS (Allow-Origin: * combined with credentials). (CWE-942)
- Default / weak credentials present in config. (CWE-1392/798)
- Insecure cookie flags (missing Secure / HttpOnly / SameSite). (CWE-614/1004)
- Over-permissive file permissions (world-writable, secrets readable). (CWE-732)

Checkers never raise on hostile input; they return (flagged, reason) or findings.

TEETH: the harness's own misconfiguration auditor (oracle_misconfig_audit) judged
against a FROZEN corpus of (kind, payload, should_flag) literals. Each planted
Mutant is a realistic misconfig-control defect (a CORS guard that forgets the
wildcard-without-credentials case, a default-credential scanner that is
case-sensitive on the username, a file-permission checker that ignores the
group-readable bit on secrets). prove() compares each auditor to the frozen
should_flag literal — never to the oracle — so it is non-circular and
deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/misconfig_test_harness.py --self-test
    python harnesses/security/misconfig_test_harness.py --json
    python harnesses/security/misconfig_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class MisconfigFinding:
    check_name: str
    severity: str
    description: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"Invalid severity: {self.severity}")


# ---------------------------------------------------------------------------
# DebugModeChecker (CWE-489)
# ---------------------------------------------------------------------------

_DEBUG_KEYS = {"debug", "development", "dev_mode", "flask_debug", "django_debug"}


class DebugModeChecker:
    def check(self, config: dict) -> list[MisconfigFinding]:
        findings: list[MisconfigFinding] = []
        for key, value in (config or {}).items():
            k = str(key).lower()
            if k in _DEBUG_KEYS and bool(value):
                findings.append(MisconfigFinding(
                    "DebugModeChecker", "HIGH",
                    f"Debug/development mode enabled via '{key}'", f"{key}={value!r}"))
            if k in ("env", "environment") and str(value).lower() in ("dev", "development", "debug"):
                findings.append(MisconfigFinding(
                    "DebugModeChecker", "MEDIUM",
                    f"Non-production environment '{value}' in config", f"{key}={value!r}"))
        return findings


# ---------------------------------------------------------------------------
# CORSChecker (CWE-942)
# ---------------------------------------------------------------------------

class CORSChecker:
    def check(self, allow_origin: str, allow_credentials: bool = False) -> tuple[bool, str]:
        origin = (allow_origin or "").strip()
        if origin == "*" and allow_credentials:
            return True, "Wildcard Allow-Origin '*' WITH credentials (CWE-942, CRITICAL)"
        if origin == "*":
            return True, "Wildcard Allow-Origin '*' (CWE-942)"
        if origin.lower() == "null":
            return True, "Allow-Origin 'null' is bypassable (CWE-942)"
        return False, f"CORS origin '{allow_origin}' is explicit"

    def severity(self, allow_origin: str, allow_credentials: bool = False) -> str:
        return "CRITICAL" if (allow_origin or "").strip() == "*" and allow_credentials else "MEDIUM"


# ---------------------------------------------------------------------------
# DefaultCredChecker (CWE-1392)
# ---------------------------------------------------------------------------

_DEFAULT_CREDS = {
    ("admin", "admin"), ("admin", "password"), ("admin", "changeme"),
    ("root", "root"), ("root", "toor"), ("root", ""),
    ("postgres", "postgres"), ("guest", "guest"), ("user", "password"),
    ("sa", ""), ("administrator", "administrator"),
}


class DefaultCredChecker:
    def scan(self, credentials: dict[str, str]) -> list[MisconfigFinding]:
        findings: list[MisconfigFinding] = []
        for user, pwd in (credentials or {}).items():
            u, p = str(user).lower(), str(pwd)
            if (u, p.lower()) in _DEFAULT_CREDS or (u, p) in _DEFAULT_CREDS:
                findings.append(MisconfigFinding(
                    "DefaultCredChecker", "CRITICAL",
                    f"Default/known credential for '{user}'", f"{user}:***"))
            elif p == "":
                findings.append(MisconfigFinding(
                    "DefaultCredChecker", "HIGH",
                    f"Empty password for '{user}'", f"{user}:<empty>"))
        return findings


# ---------------------------------------------------------------------------
# CookieFlagChecker (CWE-614/1004)
# ---------------------------------------------------------------------------

class CookieFlagChecker:
    def check(self, set_cookie_header: str) -> list[MisconfigFinding]:
        findings: list[MisconfigFinding] = []
        header = set_cookie_header or ""
        low = header.lower()
        name = header.split("=", 1)[0].strip() if "=" in header else "cookie"
        if "secure" not in low:
            findings.append(MisconfigFinding("CookieFlagChecker", "MEDIUM",
                                             f"Cookie '{name}' missing Secure", header))
        if "httponly" not in low:
            findings.append(MisconfigFinding("CookieFlagChecker", "MEDIUM",
                                             f"Cookie '{name}' missing HttpOnly", header))
        if "samesite" not in low:
            findings.append(MisconfigFinding("CookieFlagChecker", "LOW",
                                             f"Cookie '{name}' missing SameSite", header))
        return findings


# ---------------------------------------------------------------------------
# FilePermissionChecker (CWE-732)
# ---------------------------------------------------------------------------

class FilePermissionChecker:
    def check(self, mode_octal: int, *, is_secret: bool = False) -> tuple[bool, str]:
        mode = mode_octal & 0o777
        if mode & 0o002:
            return True, f"World-writable file (mode {oct(mode)}) (CWE-732)"
        if is_secret and (mode & 0o077):
            return True, f"Secret readable by group/other (mode {oct(mode)}) (CWE-732)"
        if mode & 0o001 and is_secret:
            return True, f"Secret world-executable (mode {oct(mode)}) (CWE-732)"
        return False, f"Permissions {oct(mode)} acceptable"


_debug = DebugModeChecker()
_cors = CORSChecker()
_creds = DefaultCredChecker()
_cookie = CookieFlagChecker()
_perm = FilePermissionChecker()

_HARDENED_COOKIE = "sid=abc; Secure; HttpOnly; SameSite=Strict"
_FLAGLESS_COOKIE = "sid=abc; Path=/"


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
# TEETH: the misconfiguration auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct checker and
# returns whether the configuration should be FLAGGED. Each Mutant is a
# faithful planted defect. prove() compares to the frozen should_flag literal.
# ===========================================================================

@dataclass(frozen=True)
class MisconfigCase:
    """One frozen misconfig-audit fixture. ``payload`` is the checker's args."""
    name: str
    kind: str  # "debug" | "cors" | "creds" | "cookie" | "perm"
    payload: tuple
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a checker. Includes the discriminators each mutant gets wrong.
MISCONFIG_CORPUS: tuple[MisconfigCase, ...] = (
    # debug
    MisconfigCase("debug_off", "debug", ({"DEBUG": False},), False),
    MisconfigCase("debug_on", "debug", ({"DEBUG": True},), True),
    MisconfigCase("dev_environment", "debug", ({"ENV": "development"},), True),
    # cors
    MisconfigCase("cors_explicit", "cors", ("https://app.example.com", True), False),
    MisconfigCase("cors_wildcard_creds", "cors", ("*", True), True),
    MisconfigCase("cors_wildcard_alone", "cors", ("*", False), True),
    MisconfigCase("cors_null_origin", "cors", ("null", False), True),
    # creds
    MisconfigCase("creds_strong", "creds", ({"admin": "S3cur3!longpw"},), False),
    MisconfigCase("creds_default", "creds", ({"admin": "admin"},), True),
    MisconfigCase("creds_uppercase_user", "creds", ({"Admin": "admin"},), True),
    MisconfigCase("creds_empty_pw", "creds", ({"svc": ""},), True),
    # cookie
    MisconfigCase("cookie_hardened", "cookie", (_HARDENED_COOKIE,), False),
    MisconfigCase("cookie_flagless", "cookie", (_FLAGLESS_COOKIE,), True),
    MisconfigCase("cookie_samesite_only_missing", "cookie", ("sid=abc; Secure; HttpOnly",), True),
    # perm
    MisconfigCase("perm_600_secret", "perm", (0o600, True), False),
    MisconfigCase("perm_world_writable", "perm", (0o666, False), True),
    MisconfigCase("perm_secret_group_readable", "perm", (0o640, True), True),
    MisconfigCase("perm_secret_other_readable", "perm", (0o644, True), True),
)


def oracle_misconfig_audit(case: MisconfigCase) -> bool:
    """Correct verdict: is this configuration a security misconfiguration (flag it)?

    Pure over its argument — dispatches to the harness's own checkers, no I/O.
    """
    if case.kind == "debug":
        (config,) = case.payload
        return len(_debug.check(config)) > 0
    if case.kind == "cors":
        allow_origin, allow_credentials = case.payload
        return _cors.check(allow_origin, allow_credentials)[0]
    if case.kind == "creds":
        (credentials,) = case.payload
        return len(_creds.scan(credentials)) > 0
    if case.kind == "cookie":
        (header,) = case.payload
        return len(_cookie.check(header)) > 0
    if case.kind == "perm":
        mode, is_secret = case.payload
        return _perm.check(mode, is_secret=is_secret)[0]
    raise ValueError(f"unknown misconfig case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic misconfig-control defect) ------

def mutant_cors_wildcard_only_with_creds(case: MisconfigCase) -> bool:
    """BUG: the CORS guard only flags a wildcard origin when credentials are ALSO
    enabled, so a bare 'Allow-Origin: *' (still a CWE-942 leak of any cross-origin
    response) slips through. A real 'only the credentialed case is dangerous'
    misjudgement. Other checkers correct."""
    if case.kind == "cors":
        allow_origin, allow_credentials = case.payload
        origin = (allow_origin or "").strip()
        if origin == "*":
            return bool(allow_credentials)  # BUG: wildcard alone is not flagged
        return origin.lower() == "null"
    return oracle_misconfig_audit(case)


def mutant_creds_case_sensitive_user(case: MisconfigCase) -> bool:
    """BUG: the default-credential scanner compares the username case-sensitively,
    so 'Admin:admin' (a real default account on many appliances) is not matched
    against the lowercase default-cred table. A genuine normalization defect.
    Other checkers correct."""
    if case.kind == "creds":
        (credentials,) = case.payload
        for user, pwd in (credentials or {}).items():
            u, p = str(user), str(pwd)  # BUG: no .lower() on the username
            if (u, p.lower()) in _DEFAULT_CREDS or (u, p) in _DEFAULT_CREDS:
                return True
            if p == "":
                return True
        return False
    return oracle_misconfig_audit(case)


def mutant_perm_ignores_group_read(case: MisconfigCase) -> bool:
    """BUG: the file-permission checker only treats the 'other'-readable bit (0o004)
    as a secret leak and ignores the group-readable bit (0o040), so a secret at
    mode 0o640 readable by the whole group is wrongly accepted. A real off-by-mask
    error. Other checkers correct."""
    if case.kind == "perm":
        mode_octal, is_secret = case.payload
        mode = mode_octal & 0o777
        if mode & 0o002:
            return True
        if is_secret and (mode & 0o004):  # BUG: only 'other', misses group 0o040
            return True
        return bool(mode & 0o001 and is_secret)
    return oracle_misconfig_audit(case)


def prove(audit: Callable[[MisconfigCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    MisconfigCase.should_flag constant, never against the oracle. An auditor that
    raises on a corpus case counts as caught.
    """
    for case in MISCONFIG_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_misconfig_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_misconfig_audit,
    mutants=(
        Mutant("cors_wildcard_only_with_creds", mutant_cors_wildcard_only_with_creds,
               "CORS guard only flags a wildcard origin when credentials are also on, so a bare 'Allow-Origin: *' slips through"),
        Mutant("creds_case_sensitive_user", mutant_creds_case_sensitive_user,
               "default-cred scanner compares the username case-sensitively, so 'Admin:admin' is not matched"),
        Mutant("perm_ignores_group_read", mutant_perm_ignores_group_read,
               "permission checker only treats the other-readable bit as a leak and ignores group-readable, so a secret at mode 0o640 is accepted"),
    ),
    corpus_size=len(MISCONFIG_CORPUS),
    kind="auditor",
    notes="debug/dev mode (CWE-489), permissive CORS (CWE-942), default/empty creds (CWE-1392), "
          "insecure cookie flags (CWE-614/1004), over-permissive file modes (CWE-732)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = MISCONFIG_CORPUS


def run_case(case: MisconfigCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_misconfig_audit(case)


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

    check("1. debug off clean", len(_debug.check({"DEBUG": False})) == 0)
    check("2. debug on flagged", len(_debug.check({"DEBUG": True})) >= 1)
    check("3. dev environment flagged", len(_debug.check({"ENV": "development"})) >= 1)
    check("4. explicit CORS accepted", _cors.check("https://app.example.com", True)[0] is False)
    check("5. wildcard+credentials flagged", _cors.check("*", True)[0] is True)
    check("6. wildcard alone flagged", _cors.check("*")[0] is True)
    check("7. strong creds clean", len(_creds.scan({"admin": "S3cur3!longpw"})) == 0)
    check("8. default creds flagged", len(_creds.scan({"admin": "admin"})) >= 1)
    check("9. empty password flagged", len(_creds.scan({"svc": ""})) >= 1)
    check("10. hardened cookie clean", len(_cookie.check(_HARDENED_COOKIE)) == 0)
    check("11. flagless cookie flagged", len(_cookie.check(_FLAGLESS_COOKIE)) >= 1)
    check("12. mode 600 secret accepted", _perm.check(0o600, is_secret=True)[0] is False)
    check("13. world-writable flagged", _perm.check(0o666)[0] is True)
    check("14. secret readable by others flagged", _perm.check(0o644, is_secret=True)[0] is True)

    for case in MISCONFIG_CORPUS:
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
    report = Report("security/misconfig")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_misconfig_audit by its module-global name is what the vacuity
    # gate's neuter breaks.
    for case in MISCONFIG_CORPUS:
        report.add(f"misconfig:{case.name}", case.should_flag,
                   oracle_misconfig_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="misconfig_test_harness",
        description="Security Misconfiguration harness (A02:2025, pure stdlib)",
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
