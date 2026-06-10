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
import json
import re
import sys
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse


Detector = Callable[[str], bool]


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


def _run_self_test() -> int:
    results = run_all()
    failures = [result for result in results if not result.ok]
    if failures:
        for result in failures:
            print(
                f"FAIL {result.case.name}: expected blocked={result.case.should_block}, "
                f"got {result.blocked}",
                file=sys.stderr,
            )
        return 1
    print(f"OK: {len(results)} CWE/KEV regression controls passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CWE/KEV regression controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_cases()))
        return 0
    if args.json:
        print(json.dumps([result.__dict__ for result in run_all()], default=lambda obj: obj.__dict__, indent=2))
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
