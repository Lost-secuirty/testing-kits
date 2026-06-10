#!/usr/bin/env python3
"""Audit proof coverage for every testing-kits harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

try:
    from tools.harness_registry import HarnessRecord, REPO_ROOT, discover_harnesses, run_self_test
except ModuleNotFoundError:  # direct script execution from tools/
    from harness_registry import HarnessRecord, REPO_ROOT, discover_harnesses, run_self_test

SAFE_MARKERS = (
    "safe",
    "good",
    "valid",
    "allowed",
    "pass",
    "passes",
    "clean",
    "deterministic",
    "ok",
    "expected",
    "success",
)

BAD_MARKERS = (
    "bad",
    "buggy",
    "vulnerable",
    "unsafe",
    "invalid",
    "planted",
    "reject",
    "rejected",
    "fail",
    "fails",
    "caught",
    "detect",
    "detected",
    "negative",
    "violation",
    "leak",
    "nondeterministic",
)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").lower()


def _has_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def audit_harnesses(
    records: list[HarnessRecord] | None = None,
    *,
    root: Path = REPO_ROOT,
    selftest_statuses: dict[str, str] | None = None,
    run_selftests: bool = False,
    timeout_s: float = 90.0,
) -> dict:
    """Return machine-readable proof status for each harness."""
    records = records if records is not None else discover_harnesses(root)
    selftest_statuses = dict(selftest_statuses or {})

    rows = []
    for record in records:
        if run_selftests and record.key not in selftest_statuses:
            status, _duration, _tail = run_self_test(record.path, record.root, timeout_s=timeout_s)
            selftest_statuses[record.key] = status

        harness_text = _read_text(record.path)
        paired_text = _read_text(record.paired_test)
        proof_text = _read_text(record.proof_test)
        combined = "\n".join([harness_text, paired_text, proof_text])

        has_safe_control = _has_any(combined, SAFE_MARKERS)
        has_bad_control = _has_any(combined, BAD_MARKERS)
        paired_exists = record.paired_test.exists()
        proof_exists = record.proof_test.exists()
        selftest_status = selftest_statuses.get(record.key)

        failures: list[str] = []
        if not paired_exists:
            failures.append("missing paired unittest")
        if selftest_status is not None and selftest_status != "OK":
            failures.append(f"self-test status {selftest_status}")
        if not has_safe_control:
            failures.append("missing safe/good control evidence")
        if not has_bad_control:
            failures.append("missing planted-bad/negative control evidence")

        proof_sources = []
        if proof_exists:
            proof_sources.append("proof_file")
        if has_safe_control and has_bad_control:
            proof_sources.append("embedded_controls")
        if selftest_status == "OK":
            proof_sources.append("self_test_green")

        rows.append({
            "category": record.category,
            "name": record.name,
            "key": record.key,
            "path": record.rel_path,
            "paired_test": record.paired_test.relative_to(record.root).as_posix(),
            "paired_test_exists": paired_exists,
            "proof_test": record.proof_test.relative_to(record.root).as_posix(),
            "proof_test_exists": proof_exists,
            "selftest_status": selftest_status,
            "has_safe_control": has_safe_control,
            "has_bad_control": has_bad_control,
            "proof_sources": proof_sources,
            "ok": not failures,
            "failures": failures,
        })

    ok = sum(1 for row in rows if row["ok"])
    fail = len(rows) - ok
    return {
        "summary": {
            "total_harnesses": len(rows),
            "ok": ok,
            "fail": fail,
            "header": "all proven" if fail == 0 else f"{fail} proof gaps",
        },
        "per_harness": rows,
    }


def print_summary(result: dict) -> None:
    summary = result["summary"]
    print(f"{summary['ok']}/{summary['total_harnesses']} harnesses proven ({summary['header']}).")
    for row in result["per_harness"]:
        status = "OK" if row["ok"] else "FAIL"
        sources = ",".join(row["proof_sources"]) or "-"
        print(f"{status:4s} {row['key']:36s} sources={sources}")
        for failure in row["failures"]:
            print(f"      - {failure}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit harness proof coverage.")
    parser.add_argument("--run-selftests", action="store_true",
                        help="run every harness with --self-test and require OK")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="per-harness self-test timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args()

    result = audit_harnesses(run_selftests=args.run_selftests, timeout_s=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)
    return 0 if result["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
