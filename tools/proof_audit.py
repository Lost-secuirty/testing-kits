#!/usr/bin/env python3
"""Audit proof coverage for every testing-kits harness.

Hardened gate (Batch 0 of the upgrade campaign). A harness is "proven" only with
real evidence it catches a bug — not because its source merely contains keyword
markers. Three scopes:

* ``required`` — a non-legacy harness that declares a module-level ``TEETH``. It
  MUST pass the universal swap-check (correct oracle not flagged; every planted
  mutant caught; non-empty corpus), have a paired unittest, and (when run) a green
  self-test. Declaring ``TEETH`` is the opt-in; there is no separate allowlist.
* ``pending`` — a non-legacy harness with no ``TEETH`` yet. Reported and counted,
  but NOT blocking, so the gate can be honest-strong without red-locking ``main``
  mid-campaign. Each batch moves harnesses from ``pending`` to ``required``.
* ``legacy`` — categories not in scope for the campaign (``pharmacy``). These keep
  the older keyword/self-test soft check so their status is preserved.

The keyword markers below are retained only as advisory diagnostics; they no longer
decide pass/fail for ``required``/``pending`` harnesses.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

try:
    from tools.harness_registry import HarnessRecord, REPO_ROOT, discover_harnesses, run_self_test
except ModuleNotFoundError:  # direct script execution from tools/
    from harness_registry import HarnessRecord, REPO_ROOT, discover_harnesses, run_self_test

# Categories kept on the older soft gate (out of scope for the teeth campaign).
LEGACY_CATEGORIES = {"pharmacy"}

# Advisory only — kept for diagnostics, no longer a pass/fail input for in-scope harnesses.
SAFE_MARKERS = (
    "safe", "good", "valid", "allowed", "pass", "passes", "clean",
    "deterministic", "ok", "expected", "success",
)
BAD_MARKERS = (
    "bad", "buggy", "vulnerable", "unsafe", "invalid", "planted", "reject",
    "rejected", "fail", "fails", "caught", "detect", "detected", "negative",
    "violation", "leak", "nondeterministic",
)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").lower()


def _has_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def run_teeth_check(path: Path, root: Path = REPO_ROOT, timeout_s: float = 90.0) -> dict:
    """Run ``tools/teeth_check.py`` in a subprocess and return its parsed result."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            [sys.executable, str(root / "tools" / "teeth_check.py"), str(path)],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"_runner_error": f"teeth check timed out after {timeout_s}s",
                "import_ok": False, "teeth_present": False}
    out = (proc.stdout or "").strip()
    if not out:
        return {"_runner_error": f"teeth check produced no output (rc={proc.returncode}): "
                                 f"{(proc.stderr or '').strip()[:200]}",
                "import_ok": False, "teeth_present": False}
    try:
        return json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return {"_runner_error": f"teeth check non-JSON output: {out[:200]}",
                "import_ok": False, "teeth_present": False}


def _teeth_failure_reasons(teeth: dict) -> list[str]:
    """Translate a teeth-check result into blocking failure strings (empty == clean)."""
    if teeth.get("_runner_error"):
        return [f"teeth check error: {teeth['_runner_error']}"]
    if not teeth.get("import_ok"):
        return [f"import error: {teeth.get('error') or 'unknown'}"]
    if not teeth.get("teeth_present"):
        return []  # pending — not a failure
    if teeth.get("error"):
        return [f"teeth error: {teeth['error']}"]
    reasons: list[str] = []
    if not teeth.get("oracle_clean"):
        reasons.append("oracle is flagged by its own prove() (expected prove(oracle) False)")
    uncaught = teeth.get("mutants_uncaught") or []
    if uncaught:
        reasons.append(f"mutants not caught: {', '.join(uncaught)}")
    if int(teeth.get("corpus_size", 0)) < 1:
        reasons.append("empty fixture corpus")
    return reasons


def audit_harnesses(
    records: list[HarnessRecord] | None = None,
    *,
    root: Path = REPO_ROOT,
    selftest_statuses: dict[str, str] | None = None,
    run_selftests: bool = False,
    run_teeth: bool = True,
    timeout_s: float = 90.0,
) -> dict:
    """Return machine-readable proof status for each harness under the hardened gate."""
    records = records if records is not None else discover_harnesses(root)
    selftest_statuses = dict(selftest_statuses or {})

    rows = []
    for record in records:
        if run_selftests and record.key not in selftest_statuses:
            status, _d, _t = run_self_test(record.path, record.root, timeout_s=timeout_s)
            selftest_statuses[record.key] = status
        selftest_status = selftest_statuses.get(record.key)

        legacy = record.category in LEGACY_CATEGORIES
        paired_exists = record.paired_test.exists()
        proof_exists = record.proof_test.exists()

        combined = "\n".join([
            _read_text(record.path), _read_text(record.paired_test), _read_text(record.proof_test),
        ])
        has_safe = _has_any(combined, SAFE_MARKERS)
        has_bad = _has_any(combined, BAD_MARKERS)

        row: dict = {
            "category": record.category,
            "name": record.name,
            "key": record.key,
            "path": record.rel_path,
            "paired_test": record.paired_test.relative_to(record.root).as_posix(),
            "paired_test_exists": paired_exists,
            "proof_test": record.proof_test.relative_to(record.root).as_posix(),
            "proof_test_exists": proof_exists,
            "selftest_status": selftest_status,
            "has_safe_control": has_safe,
            "has_bad_control": has_bad,
        }

        failures: list[str] = []
        warnings: list[str] = []
        proof_sources: list[str] = []
        if proof_exists:
            proof_sources.append("proof_file")
        if selftest_status == "OK":
            proof_sources.append("self_test_green")

        if legacy:
            scope = "legacy"
            if not paired_exists:
                failures.append("missing paired unittest")
            if selftest_status is not None and selftest_status != "OK":
                failures.append(f"self-test status {selftest_status}")
            if not has_safe:
                failures.append("missing safe/good control evidence")
            if not has_bad:
                failures.append("missing planted-bad/negative control evidence")
            if has_safe and has_bad:
                proof_sources.append("embedded_controls")
            row.update({"teeth_present": False, "teeth_verified": False})
        else:
            # When teeth checks are skipped, treat the harness as importable with no
            # teeth declared (pending) rather than inventing an import failure.
            teeth = (run_teeth_check(record.path, record.root, timeout_s=timeout_s)
                     if run_teeth else {"teeth_present": False, "import_ok": True})
            row.update({
                "teeth_present": bool(teeth.get("teeth_present")),
                "teeth_verified": bool(teeth.get("teeth_verified")),
                "teeth_kind": teeth.get("kind"),
                "oracle_clean": teeth.get("oracle_clean"),
                "mutants_total": teeth.get("mutants_total", 0),
                "mutants_caught": teeth.get("mutants_caught", 0),
                "mutants_uncaught": teeth.get("mutants_uncaught", []),
                "corpus_size": teeth.get("corpus_size", 0),
                "teeth_error": teeth.get("error") or teeth.get("_runner_error"),
            })
            teeth_reasons = _teeth_failure_reasons(teeth)
            if row["teeth_present"]:
                scope = "required"
                if not paired_exists:
                    failures.append("missing paired unittest")
                if selftest_status is not None and selftest_status != "OK":
                    failures.append(f"self-test status {selftest_status}")
                failures.extend(teeth_reasons)
                if row["teeth_verified"]:
                    proof_sources.append("teeth_swap")
            else:
                # pending OR a hard import/runner error (which IS blocking)
                if teeth_reasons:
                    scope = "required"  # broken import on an in-scope harness blocks
                    failures.extend(teeth_reasons)
                else:
                    scope = "pending"
                    warnings.append("no TEETH declared yet (pending upgrade)")
                    if selftest_status is not None and selftest_status != "OK":
                        warnings.append(f"self-test status {selftest_status}")

        row["scope"] = scope
        row["proof_sources"] = proof_sources
        row["warnings"] = warnings
        row["ok"] = not failures
        row["pending"] = scope == "pending"
        row["failures"] = failures
        rows.append(row)

    fail = sum(1 for r in rows if not r["ok"])
    pending = sum(1 for r in rows if r["pending"])
    proven = sum(1 for r in rows if r["ok"] and not r["pending"])
    required = sum(1 for r in rows if r["scope"] == "required")
    required_ok = sum(1 for r in rows if r["scope"] == "required" and r["ok"])
    legacy = sum(1 for r in rows if r["scope"] == "legacy")
    legacy_ok = sum(1 for r in rows if r["scope"] == "legacy" and r["ok"])

    if fail:
        header = f"{fail} proof gaps"
    elif pending:
        header = f"{required_ok} required proven, {pending} pending, {legacy} legacy"
    else:
        header = "all proven"

    return {
        "summary": {
            "total_harnesses": len(rows),
            "ok": proven,
            "fail": fail,
            "pending": pending,
            "required": required,
            "required_ok": required_ok,
            "legacy": legacy,
            "legacy_ok": legacy_ok,
            "header": header,
        },
        "per_harness": rows,
    }


def print_summary(result: dict) -> None:
    s = result["summary"]
    print(f"{s['ok']}/{s['total_harnesses']} harnesses proven ({s['header']}).")
    print(f"  required {s['required_ok']}/{s['required']} | pending {s['pending']} | "
          f"legacy {s['legacy_ok']}/{s['legacy']} | failing {s['fail']}")
    for row in result["per_harness"]:
        if not row["ok"]:
            tag = "FAIL"
        elif row["pending"]:
            tag = "PEND"
        else:
            tag = "OK"
        sources = ",".join(row["proof_sources"]) or "-"
        print(f"{tag:4s} {row['key']:38s} [{row['scope']:8s}] sources={sources}")
        for failure in row["failures"]:
            print(f"      - {failure}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit harness proof coverage (hardened gate).")
    parser.add_argument("--run-selftests", action="store_true",
                        help="run every harness with --self-test and require OK")
    parser.add_argument("--no-teeth", action="store_true",
                        help="skip the TEETH swap-check (diagnostics only)")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="per-harness self-test / teeth-check timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args()

    records = discover_harnesses()
    if not records:
        print("No harnesses found.", file=sys.stderr)
        return 2

    result = audit_harnesses(records, run_selftests=args.run_selftests,
                             run_teeth=not args.no_teeth, timeout_s=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)
    return 0 if result["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
