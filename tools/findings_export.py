#!/usr/bin/env python3
"""
findings_export.py — Export harness findings as SARIF 2.1.0 or JSON.
====================================================================

Pure-stdlib. Zero external dependencies.

Normalizes findings from any harness (they expose check_name/rule_id, severity,
description/message, optional cwe/file/line) into SARIF 2.1.0 for CI code-scanning
dashboards (GitHub code scanning, etc.) or a flat JSON array. Accepts either dict
findings or simple objects with those attributes, so a harness can emit through it
without adopting a shared base class.

Usage:
    python tools/findings_export.py --self-test
    python tools/findings_export.py --demo            # print sample SARIF
    python tools/findings_export.py --list-scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

_LEVEL = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "note"}


def _field(finding: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(finding, dict):
            if name in finding and finding[name] not in (None, ""):
                return finding[name]
        else:
            val = getattr(finding, name, None)
            if val not in (None, ""):
                return val
    return default


def normalize(finding: Any) -> dict[str, Any]:
    return {
        "rule_id": _field(finding, "rule_id", "check_name", default="GENERIC"),
        "cwe": _field(finding, "cwe", default=""),
        "severity": (_field(finding, "severity", default="MEDIUM") or "MEDIUM").upper(),
        "message": _field(finding, "message", "description", default=""),
        "file": _field(finding, "file", "path", default=""),
        "line": int(_field(finding, "line", default=0) or 0),
    }


def to_json(findings: list[Any], indent: int = 2) -> str:
    return json.dumps([normalize(f) for f in findings], indent=indent)


def to_sarif(findings: list[Any], tool_name: str = "testing-kits",
             tool_version: str = "0.2.0") -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for f in findings:
        n = normalize(f)
        rid = n["rule_id"]
        if rid not in rules:
            rule: dict[str, Any] = {"id": rid, "name": rid}
            if n["cwe"]:
                rule["properties"] = {"cwe": n["cwe"], "tags": [n["cwe"]]}
            rules[rid] = rule
        result: dict[str, Any] = {
            "ruleId": rid,
            "level": _LEVEL.get(n["severity"], "warning"),
            "message": {"text": n["message"]},
        }
        if n["file"]:
            result["locations"] = [{
                "physicalLocation": {
                    "artifactLocation": {"uri": n["file"]},
                    "region": {"startLine": max(1, n["line"])},
                }
            }]
        results.append(result)
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {"driver": {"name": tool_name, "version": tool_version,
                                "rules": list(rules.values())}},
            "results": results,
        }],
    }


def is_valid_sarif(doc: dict[str, Any]) -> bool:
    """Minimal structural validity check for SARIF 2.1.0."""
    if doc.get("version") != "2.1.0":
        return False
    runs = doc.get("runs")
    if not isinstance(runs, list) or not runs:
        return False
    run = runs[0]
    if "tool" not in run or "driver" not in run["tool"]:
        return False
    if not isinstance(run.get("results"), list):
        return False
    for res in run["results"]:
        if "ruleId" not in res or "message" not in res or "level" not in res:
            return False
    return True


# Sample findings mixing dicts and a simple object (mirrors harness finding shapes).
@dataclass
class _SampleFinding:
    rule_id: str
    cwe: str
    severity: str
    message: str
    line: int = 0


SAMPLE_FINDINGS: list[Any] = [
    {"check_name": "CryptoChecker", "severity": "HIGH", "description": "md5 used", "cwe": "CWE-327"},
    {"rule_id": "PY-SHELL-TRUE", "cwe": "CWE-78", "severity": "CRITICAL",
     "message": "shell=True", "file": "app.py", "line": 12},
    _SampleFinding("PY-WEAK-RANDOM", "CWE-330", "MEDIUM", "random for token", 5),
    {"check_name": "CookieFlagChecker", "severity": "LOW", "description": "missing SameSite"},
]


# ---------------------------------------------------------------------------
# Scenario results + self-test
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


def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        r = ScenarioResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)

    sarif = to_sarif(SAMPLE_FINDINGS)
    check("1. sarif version 2.1.0", sarif["version"] == "2.1.0")
    check("2. sarif structurally valid", is_valid_sarif(sarif))
    check("3. result count matches findings",
          len(sarif["runs"][0]["results"]) == len(SAMPLE_FINDINGS))
    levels = [r["level"] for r in sarif["runs"][0]["results"]]
    check("4. severity->level mapped",
          levels == ["error", "error", "warning", "note"], str(levels))
    check("5. cwe carried into rule properties",
          any(r.get("properties", {}).get("cwe") == "CWE-327"
              for r in sarif["runs"][0]["tool"]["driver"]["rules"]))
    check("6. location emitted when file present",
          any("locations" in r for r in sarif["runs"][0]["results"]))
    parsed = json.loads(to_json(SAMPLE_FINDINGS))
    check("7. json round-trips", isinstance(parsed, list) and len(parsed) == len(SAMPLE_FINDINGS))
    check("8. json normalizes rule_id from check_name",
          parsed[0]["rule_id"] == "CryptoChecker")
    check("9. invalid sarif rejected", is_valid_sarif({"version": "1.0"}) is False)
    check("10. object findings supported", parsed[2]["rule_id"] == "PY-WEAK-RANDOM")

    return results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


def _run_self_test(verbose: bool = False) -> int:
    print("\n  FINDINGS EXPORT (SARIF/JSON) — self-test mode")
    print("  " + "=" * 52)
    results = run_all_scenarios(verbose=verbose)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    if not verbose:
        for r in results:
            print(r)
    print()
    print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
    print()
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="findings_export",
        description="Export harness findings as SARIF 2.1.0 or JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run all scenarios; exit 0 if all pass")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--demo", action="store_true", help="Print sample SARIF and exit")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for name in list_scenarios():
            print(name)
        return 0
    if args.demo:
        print(json.dumps(to_sarif(SAMPLE_FINDINGS), indent=2))
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
