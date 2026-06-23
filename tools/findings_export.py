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

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

try:  # dual-context: imported as tools.findings_export (tests) or run as a script
    from tools._scenario import ScenarioRun, selftest_cli
except ImportError:
    from _scenario import ScenarioRun, selftest_cli

if TYPE_CHECKING:
    from tools._scenario import ScenarioResult

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


def _coerce_line(value: Any) -> int:
    """A non-numeric or negative line means 'unknown' -> 0 (never crash on bad input)."""
    try:
        line = int(value)
    except (TypeError, ValueError):
        return 0
    return line if line > 0 else 0


def normalize(finding: Any) -> dict[str, Any]:
    return {
        "rule_id": _field(finding, "rule_id", "check_name", default="GENERIC"),
        "cwe": _field(finding, "cwe", default=""),
        "severity": (_field(finding, "severity", default="MEDIUM") or "MEDIUM").upper(),
        "message": _field(finding, "message", "description", default=""),
        "file": _field(finding, "file", "path", default=""),
        "line": _coerce_line(_field(finding, "line", default=0)),
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
        rule = rules.setdefault(rid, {"id": rid, "name": rid})
        if n["cwe"]:
            # Aggregate every distinct CWE seen for this rule_id, even when the
            # first finding for the rule carried none.
            props = rule.setdefault("properties", {"cwe": n["cwe"], "tags": []})
            props.setdefault("cwe", n["cwe"])
            if n["cwe"] not in props["tags"]:
                props["tags"].append(n["cwe"])
        result: dict[str, Any] = {
            "ruleId": rid,
            "level": _LEVEL.get(n["severity"], "warning"),
            "message": {"text": n["message"]},
        }
        if n["file"]:
            physical: dict[str, Any] = {"artifactLocation": {"uri": n["file"]}}
            if n["line"] > 0:  # omit the region rather than fake line 1 for unknowns
                physical["region"] = {"startLine": n["line"]}
            result["locations"] = [{"physicalLocation": physical}]
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
# Scenarios + self-test
# ---------------------------------------------------------------------------

def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    run = ScenarioRun(verbose)
    check = run.check

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
    # robustness fixes
    check("11. non-numeric line coerced to 0",
          normalize({"rule_id": "x", "line": "N/A"})["line"] == 0)
    no_line = to_sarif([{"rule_id": "x", "severity": "HIGH", "message": "m", "file": "f.py"}])
    loc = no_line["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    check("12. region omitted when line unknown", "region" not in loc)
    agg = to_sarif([{"rule_id": "R", "cwe": "CWE-1", "message": "a"},
                    {"rule_id": "R", "cwe": "CWE-2", "message": "b"}])
    tags = agg["runs"][0]["tool"]["driver"]["rules"][0].get("properties", {}).get("tags", [])
    check("13. cwe aggregated across same rule_id", "CWE-1" in tags and "CWE-2" in tags)

    return run.results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


def _print_demo() -> int:
    print(json.dumps(to_sarif(SAMPLE_FINDINGS), indent=2))
    return 0


def main() -> int:
    return selftest_cli(
        "findings_export",
        "Export harness findings as SARIF 2.1.0 or JSON",
        "FINDINGS EXPORT (SARIF/JSON) — self-test mode",
        run_all_scenarios,
        extra={"demo": ("Print sample SARIF and exit", _print_demo)},
    )


if __name__ == "__main__":
    sys.exit(main())
