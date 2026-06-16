#!/usr/bin/env python3
"""
CI workflow hardening test harness.

Audits GitHub Actions workflow definitions for the poisoned-pipeline /
pwn-request CWE class (untrusted code execution and privilege escalation in
CI). This is a distinct surface from the supplychain harness (dependency
hashes / slopsquat) and the cwe_kev catalog (payload regression): neither of
those inspects CI configuration.

Provenance: ported from control_audit.py (used across several lostsoulfs
repos). The original parses .github/workflows/*.yml with PyYAML, then applies
dict rules. This library is STANDARD-LIBRARY ONLY, so the oracle here operates
on ALREADY-PARSED workflow objects represented as plain Python dicts. Fixtures
are frozen as JSON strings (parsed with json.loads inside each case) rather
than raw .yml text, so no third-party YAML parser is needed and the frozen
dataclass cases stay hashable. The PyYAML quirk where the YAML key 'on' can be
decoded as the boolean key True is reproduced explicitly: the auditor checks
both data['on'] and data[True].

Rules ported faithfully from the source:
  - workflow-permissions  : top-level 'permissions' key missing.
  - workflow-concurrency  : 'concurrency' is not a dict whose 'group' is a str
                            containing 'github.ref' AND whose 'cancel-in-progress'
                            is True or a conditional string starting with '${{'.
  - pull-request-target   : the 'on' events include 'pull_request_target'
                            (checked under both 'on' and the boolean True key).
  - fork-checkout         : a step 'with'.ref contains 'github.head_ref'.
  - workflow-jobs         : 'jobs' is not a non-empty dict.
  - job-timeout           : a job lacks 'timeout-minutes'.
  - fork-scan             : a job 'if' contains
                            'head.repo.full_name == github.repository'.
  - action-pin            : a step 'uses' matches the ACTION regex but the ref
                            is not a full 40-hex commit SHA.
  - checkout-credentials  : a step 'uses' starts with 'actions/checkout@' and
                            'with'.persist-credentials is not exactly False.
  - workflow-name         : 'name' is not a str.

Self-test:
  python harnesses/security/ci_workflow_hardening_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402

# ACTION uses-string regex, ported from the source control_audit.py.
ACTION = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/@]+)(?:/[^@]+)?@(?P<ref>.+)$")
# A pinned action ref must be a full 40-character commit SHA.
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Finding:
    code: str
    message: str


@dataclass(frozen=True)
class WorkflowCase:
    """A single audit fixture.

    The workflow is stored as a JSON string and parsed with json.loads inside
    build_workflow(), so the dataclass stays frozen and hashable (a dict field
    would be unhashable). expected_findings is the set of finding codes the
    real auditor must produce; should_pass is True only for the fully-hardened
    fixture.
    """

    name: str
    workflow_json: str
    expected_findings: tuple[str, ...]
    should_pass: bool
    note: str

    def build_workflow(self) -> dict[str, Any]:
        return json.loads(self.workflow_json)


@dataclass(frozen=True)
class AuditResult:
    case: WorkflowCase
    codes: tuple[str, ...]
    ok: bool


def _events(data: dict[str, Any]) -> list[str]:
    """Return the list of trigger event names.

    Handles the PyYAML quirk: the YAML key 'on' may be decoded as the boolean
    key True. Both data['on'] and data[True] are inspected. The 'on' value can
    be a string, a list, or a dict keyed by event name.
    """
    events: list[str] = []
    for key in ("on", True):
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str):
            events.append(value)
        elif isinstance(value, (list, dict)):
            events.extend(str(item) for item in value)
    return events


def _check_concurrency(data: dict[str, Any]) -> bool:
    """Return True if the concurrency block is hardened."""
    concurrency = data.get("concurrency")
    if not isinstance(concurrency, dict):
        return False
    group = concurrency.get("group")
    if not isinstance(group, str) or "github.ref" not in group:
        return False
    cancel = concurrency.get("cancel-in-progress")
    if cancel is True:
        return True
    return bool(isinstance(cancel, str) and cancel.startswith("${{"))


def audit_workflow(data: dict[str, Any]) -> list[Finding]:
    """Deterministic oracle: audit a parsed workflow dict for pwn-request flaws."""
    findings: list[Finding] = []

    # Top-level workflow 'name' must be a str.
    if not isinstance(data.get("name"), str):
        findings.append(Finding("workflow-name", "workflow 'name' must be a string"))

    # Top-level 'permissions' key required.
    if "permissions" not in data:
        findings.append(
            Finding("workflow-permissions", "top-level 'permissions' key is missing")
        )

    # 'concurrency' must scope on github.ref and cancel in-progress runs.
    if not _check_concurrency(data):
        findings.append(
            Finding(
                "workflow-concurrency",
                "'concurrency' must set group containing 'github.ref' and "
                "cancel-in-progress True or a '${{' conditional",
            )
        )

    # The 'on' events must not include pull_request_target.
    if "pull_request_target" in _events(data):
        findings.append(
            Finding(
                "pull-request-target",
                "'on' includes pull_request_target (runs untrusted PR code with "
                "write token)",
            )
        )

    # 'jobs' must be a non-empty dict.
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        findings.append(Finding("workflow-jobs", "'jobs' must be a non-empty mapping"))
        return findings

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            findings.append(
                Finding("workflow-job", f"job '{job_name}' must be a mapping")
            )
            continue

        # Each job must have timeout-minutes.
        if "timeout-minutes" not in job:
            findings.append(
                Finding("job-timeout", f"job '{job_name}' is missing timeout-minutes")
            )

        # A job 'if' guarding on the upstream repo skips fork scanning.
        job_if = job.get("if")
        if isinstance(job_if, str) and "head.repo.full_name == github.repository" in job_if:
            findings.append(
                Finding(
                    "fork-scan",
                    f"job '{job_name}' if-condition skips scanning forked PRs",
                )
            )

        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            with_block = step.get("with")
            with_block = with_block if isinstance(with_block, dict) else {}

            # Fork checkout: with.ref references github.head_ref.
            ref = with_block.get("ref")
            if isinstance(ref, str) and "github.head_ref" in ref:
                findings.append(
                    Finding(
                        "fork-checkout",
                        f"job '{job_name}' checks out untrusted fork ref "
                        "(github.head_ref)",
                    )
                )

            uses = step.get("uses")
            if isinstance(uses, str):
                match = ACTION.match(uses)
                if match and not _SHA40.match(match.group("ref")):
                    findings.append(
                        Finding(
                            "action-pin",
                            f"job '{job_name}' uses '{uses}' pinned to a mutable "
                            "ref, not a 40-hex commit SHA",
                        )
                    )
                if (
                    uses.startswith("actions/checkout@")
                    and with_block.get("persist-credentials") is not False
                ):
                    findings.append(
                        Finding(
                            "checkout-credentials",
                            f"job '{job_name}' checkout leaves persist-credentials "
                            "on (token persisted in .git/config)",
                        )
                    )

    return findings


def audit_workflow_naive(data: dict[str, Any]) -> list[Finding]:
    """INTENTIONALLY BUGGY auditor used to prove the action-pin rule has teeth.

    Identical to audit_workflow except it skips the action-pin SHA check: it
    treats any 'uses' string with an '@' ref as acceptable. The proof test
    shows the real auditor flags an unpinned action while this naive variant
    misses it. Do not use in production; it exists only as a planted bug.
    """
    findings: list[Finding] = []

    if not isinstance(data.get("name"), str):
        findings.append(Finding("workflow-name", "workflow 'name' must be a string"))

    if "permissions" not in data:
        findings.append(
            Finding("workflow-permissions", "top-level 'permissions' key is missing")
        )

    if not _check_concurrency(data):
        findings.append(
            Finding(
                "workflow-concurrency",
                "'concurrency' must set group containing 'github.ref' and "
                "cancel-in-progress True or a '${{' conditional",
            )
        )

    if "pull_request_target" in _events(data):
        findings.append(
            Finding("pull-request-target", "'on' includes pull_request_target")
        )

    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        findings.append(Finding("workflow-jobs", "'jobs' must be a non-empty mapping"))
        return findings

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            findings.append(
                Finding("workflow-job", f"job '{job_name}' must be a mapping")
            )
            continue
        if "timeout-minutes" not in job:
            findings.append(
                Finding("job-timeout", f"job '{job_name}' is missing timeout-minutes")
            )
        job_if = job.get("if")
        if isinstance(job_if, str) and "head.repo.full_name == github.repository" in job_if:
            findings.append(Finding("fork-scan", f"job '{job_name}' skips forked PRs"))

        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            with_block = step.get("with")
            with_block = with_block if isinstance(with_block, dict) else {}
            ref = with_block.get("ref")
            if isinstance(ref, str) and "github.head_ref" in ref:
                findings.append(
                    Finding("fork-checkout", f"job '{job_name}' checks out fork ref")
                )
            uses = step.get("uses")
            # BUG: any '@'-pinned action is accepted; no SHA check at all.
            if (
                isinstance(uses, str)
                and uses.startswith("actions/checkout@")
                and with_block.get("persist-credentials") is not False
            ):
                findings.append(
                    Finding(
                        "checkout-credentials",
                        f"job '{job_name}' checkout persists credentials",
                    )
                )
    return findings


# A full 40-hex commit SHA used by the hardened fixture's pinned actions.
_PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"


# ---------------------------------------------------------------------------
# Corpus: one fully-hardened workflow (zero findings) plus unsafe fixtures
# each isolating a single rule.
# ---------------------------------------------------------------------------

_HARDENED = {
    "name": "ci",
    "on": {"pull_request": {"branches": ["main"]}},
    "permissions": {"contents": "read"},
    "concurrency": {
        "group": "ci-${{ github.ref }}",
        "cancel-in-progress": True,
    },
    "jobs": {
        "build": {
            "runs-on": "ubuntu-latest",
            "timeout-minutes": 15,
            "steps": [
                {
                    "uses": f"actions/checkout@{_PINNED_SHA}",
                    "with": {"persist-credentials": False},
                },
                {
                    "uses": f"actions/setup-python@{_PINNED_SHA}",
                    "with": {"python-version": "3.12"},
                },
            ],
        }
    },
}


def _clone(base: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy via JSON round-trip (fixtures are pure JSON-safe data)."""
    return json.loads(json.dumps(base))


def _mut(**changes: Any) -> dict[str, Any]:
    """Return a copy of the hardened workflow with top-level keys overridden."""
    data = _clone(_HARDENED)
    for key, value in changes.items():
        data[key] = value
    return data


def _hardened_unpinned() -> dict[str, Any]:
    data = _clone(_HARDENED)
    data["jobs"]["build"]["steps"][1]["uses"] = "actions/setup-python@v5"
    return data


def _hardened_pr_target() -> dict[str, Any]:
    data = _clone(_HARDENED)
    data["on"] = {"pull_request_target": {"branches": ["main"]}}
    return data


def _hardened_no_permissions() -> dict[str, Any]:
    data = _clone(_HARDENED)
    del data["permissions"]
    return data


def _hardened_no_timeout() -> dict[str, Any]:
    data = _clone(_HARDENED)
    del data["jobs"]["build"]["timeout-minutes"]
    return data


def _hardened_persist_creds() -> dict[str, Any]:
    data = _clone(_HARDENED)
    data["jobs"]["build"]["steps"][0]["with"]["persist-credentials"] = True
    return data


def _hardened_head_ref() -> dict[str, Any]:
    data = _clone(_HARDENED)
    data["jobs"]["build"]["steps"][0]["with"]["ref"] = "${{ github.head_ref }}"
    return data


def _hardened_fork_scan_if() -> dict[str, Any]:
    data = _clone(_HARDENED)
    data["jobs"]["build"]["if"] = (
        "github.event.pull_request.head.repo.full_name == github.repository"
    )
    return data


def _hardened_no_concurrency() -> dict[str, Any]:
    data = _clone(_HARDENED)
    del data["concurrency"]
    return data


def _hardened_on_as_true_key() -> dict[str, Any]:
    """Reproduce the PyYAML quirk: 'on' decoded as the boolean True key.

    JSON cannot represent a boolean object key, so this fixture is built
    programmatically rather than from a JSON string. It is wired into the
    corpus via the _TrueKeyCase subclass, whose build_workflow() returns this
    dict directly instead of parsing workflow_json (which holds only a marker).
    """
    data = _clone(_HARDENED)
    del data["on"]
    data[True] = {"pull_request_target": {"branches": ["main"]}}
    return data


def _hardened_malformed_job() -> dict[str, Any]:
    data = _clone(_HARDENED)
    data["jobs"] = {"build": "not-a-mapping"}
    return data


def _case(name: str, workflow: dict[str, Any], expected: tuple[str, ...],
          should_pass: bool, note: str) -> WorkflowCase:
    return WorkflowCase(
        name=name,
        workflow_json=json.dumps(workflow),
        expected_findings=expected,
        should_pass=should_pass,
        note=note,
    )


# The True-key fixture cannot survive a JSON round-trip (JSON object keys are
# strings), so it is registered as a builder-backed special case. We embed a
# marker in workflow_json and rebuild it in build_workflow via a subclass-free
# sentinel.
_TRUE_KEY_MARKER = "__ON_AS_TRUE_KEY__"


@dataclass(frozen=True)
class _TrueKeyCase(WorkflowCase):
    def build_workflow(self) -> dict[str, Any]:
        return _hardened_on_as_true_key()


CASES: tuple[WorkflowCase, ...] = (
    _case(
        "hardened", _HARDENED, (), True,
        "fully hardened: pinned SHA, permissions, concurrency, timeout, "
        "persist-credentials False, pull_request",
    ),
    _case(
        "unpinned_action", _hardened_unpinned(), ("action-pin",), False,
        "setup-python pinned to mutable tag v5 instead of a SHA",
    ),
    _case(
        "pull_request_target", _hardened_pr_target(), ("pull-request-target",), False,
        "pull_request_target trigger runs untrusted PR code with write token",
    ),
    _TrueKeyCase(
        "on_as_true_key", _TRUE_KEY_MARKER, ("pull-request-target",), False,
        "PyYAML quirk: 'on' decoded as boolean True key, still pull_request_target",
    ),
    _case(
        "missing_permissions", _hardened_no_permissions(), ("workflow-permissions",),
        False, "no top-level permissions block (defaults to broad token)",
    ),
    _case(
        "missing_timeout", _hardened_no_timeout(), ("job-timeout",), False,
        "job has no timeout-minutes (runaway / resource exhaustion)",
    ),
    _case(
        "persist_credentials", _hardened_persist_creds(), ("checkout-credentials",),
        False, "checkout persists credentials in .git/config",
    ),
    _case(
        "head_ref_checkout", _hardened_head_ref(), ("fork-checkout",), False,
        "checks out untrusted fork head_ref into a privileged context",
    ),
    _case(
        "fork_scan_skip", _hardened_fork_scan_if(), ("fork-scan",), False,
        "if-condition skips scanning forked PRs",
    ),
    _case(
        "missing_concurrency", _hardened_no_concurrency(), ("workflow-concurrency",),
        False, "no concurrency block scoped on github.ref",
    ),
    _case(
        "malformed_job", _hardened_malformed_job(), ("workflow-job",), False,
        "a job entry is not a mapping; the malformed-workflow signal must be reported",
    ),
)


def list_cases() -> list[str]:
    return [case.name for case in CASES]


def run_case(case: WorkflowCase) -> AuditResult:
    findings = audit_workflow(case.build_workflow())
    codes = tuple(finding.code for finding in findings)
    if case.should_pass:  # noqa: SIM108 — else-branch comment documents exact-match rationale
        ok = len(codes) == 0
    else:
        # Exact match: expected codes present AND no unexpected extras, so an
        # over-reporting regression cannot quietly pass an unsafe fixture.
        ok = set(codes) == set(case.expected_findings)
    return AuditResult(case=case, codes=codes, ok=ok)


def run_all() -> list[AuditResult]:
    return [run_case(case) for case in CASES]


# ---------------------------------------------------------------------------
# Teeth: the correct auditor reproduces every fixture's expected finding set;
# the naive auditor (which skips the action-pin SHA check) disagrees on at
# least one case and is therefore caught.
# ---------------------------------------------------------------------------
def _prove(impl: Any) -> bool:
    """True iff `impl` disagrees with the frozen corpus on any case (caught).

    For each WorkflowCase, run the candidate auditor and apply the harness's
    own pass/fail rule: a should_pass fixture must yield zero findings; an
    unsafe fixture must yield EXACTLY its expected finding codes. Any deviation
    (or an exception) means the auditor is caught.
    """
    for case in CASES:
        try:
            codes = {finding.code for finding in impl(case.build_workflow())}
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if case.should_pass:
            if codes:
                return True
        elif codes != set(case.expected_findings):
            return True
    return False


TEETH = Teeth(
    prove=_prove,
    oracle=audit_workflow,
    mutants=(
        Mutant("action_pin_skipped", audit_workflow_naive,
               "naive auditor skips the action-pin SHA check and waves through "
               "an action pinned to a mutable tag"),
    ),
    corpus_size=len(CASES),
    kind="auditor",
    notes="an action pinned to a mutable ref instead of a 40-hex SHA must be flagged",
)


def _run_self_test() -> int:
    results = run_all()
    failures = [result for result in results if not result.ok]
    if failures:
        for result in failures:
            print(
                f"FAIL {result.case.name}: expected findings "
                f"{result.case.expected_findings} "
                f"(should_pass={result.case.should_pass}), got {result.codes}",
                file=sys.stderr,
            )
        return 1

    # Prove the planted bug: the naive auditor must miss the unpinned action.
    unpinned = next(case for case in CASES if case.name == "unpinned_action")
    naive_codes = [f.code for f in audit_workflow_naive(unpinned.build_workflow())]
    real_codes = [f.code for f in audit_workflow(unpinned.build_workflow())]
    if "action-pin" not in real_codes:
        print("FAIL: real auditor did not flag unpinned action", file=sys.stderr)
        return 1
    if "action-pin" in naive_codes:
        print(
            "FAIL: naive auditor unexpectedly flagged the unpinned action "
            "(planted bug missing)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(results)} CI workflow hardening controls passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit GitHub Actions workflows for pwn-request flaws")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_cases()))
        return 0
    if args.json:
        payload = [
            {
                "name": result.case.name,
                "expected_findings": list(result.case.expected_findings),
                "should_pass": result.case.should_pass,
                "codes": list(result.codes),
                "ok": result.ok,
                "note": result.case.note,
            }
            for result in run_all()
        ]
        print(json.dumps(payload, indent=2))
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
