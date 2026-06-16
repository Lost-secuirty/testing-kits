#!/usr/bin/env python3
"""Validate repository engineering controls against .github/control-policy.json."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("control-audit: PyYAML is required (pip install PyYAML==6.0.3)", file=sys.stderr)
    raise SystemExit(2) from None

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / ".github" / "control-policy.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ACTION = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/@]+)(?:/[^@]+)?@(?P<ref>.+)$")


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError("top level must be a mapping")
    return data


def events(data: dict) -> object:
    return data.get("on", data.get(True, {}))


def has_event(data: dict, event: str) -> bool:
    configured = events(data)
    if isinstance(configured, str):
        return configured == event
    if isinstance(configured, list):
        return event in configured
    if isinstance(configured, dict):
        return event in configured
    return False


def main() -> int:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    exceptions = {
        (item["code"], item.get("path", "*")): item["reason"]
        for item in policy.get("exceptions", [])
    }
    failures: list[str] = []
    waived: list[str] = []

    def report(code: str, path: str, message: str) -> None:
        reason = exceptions.get((code, path)) or exceptions.get((code, "*"))
        line = f"{code} {path}: {message}"
        if reason:
            waived.append(f"{line} [waived: {reason}]")
        else:
            failures.append(line)

    for required in policy["required_files"]:
        path = ROOT / required
        if not path.is_file() or path.stat().st_size == 0:
            report("required-file", required, "missing or empty")

    for instruction in policy["instruction_sources"]:
        path = ROOT / instruction
        if not path.is_file() or len(path.read_text(encoding="utf-8").strip()) < 40:
            report("instruction-source", instruction, "must remain a substantive universal instruction source")

    for nested in policy.get("nested_instruction_files", []):
        if not (ROOT / nested).is_file():
            report("nested-instructions", nested, "declared nested instruction file is missing")

    workflows = sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix in {".yml", ".yaml"}
        and not {".git", "node_modules", ".nox", ".venv", "venv"}.intersection(path.parts)
        and path.parent.name == "workflows"
        and path.parent.parent.name == ".github"
    )
    names: set[str] = set()
    for workflow in workflows:
        rel = workflow.relative_to(ROOT).as_posix()
        text = workflow.read_text(encoding="utf-8")
        try:
            data = load_yaml(workflow)
        except Exception as exc:
            report("workflow-yaml", rel, str(exc))
            continue

        name = data.get("name")
        if isinstance(name, str):
            names.add(name)
        else:
            report("workflow-name", rel, "workflow requires a stable name")

        if "permissions" not in data:
            report("workflow-permissions", rel, "top-level permissions are required")
        concurrency = data.get("concurrency")
        if not isinstance(concurrency, dict):
            report("workflow-concurrency", rel, "concurrency must define group and cancellation")
        else:
            group = concurrency.get("group")
            if not isinstance(group, str) or "github.ref" not in group:
                report("workflow-concurrency", rel, "concurrency.group must be scoped by github.ref")
            cancel = concurrency.get("cancel-in-progress")
            conditional_cancel = isinstance(cancel, str) and cancel.strip().startswith("${{")
            if cancel is not True and not conditional_cancel:
                report("workflow-concurrency", rel, "concurrency.cancel-in-progress must be true or conditional")
        if has_event(data, "pull_request_target"):
            report("pull-request-target", rel, "pull_request_target is prohibited")

        if "ref: ${{ github.head_ref }}" in text:
            report("fork-checkout", rel, "pull request jobs must use the read-only event checkout")

        jobs = data.get("jobs", {})
        if not isinstance(jobs, dict) or not jobs:
            report("workflow-jobs", rel, "workflow must define jobs")
            continue

        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                report("workflow-job", rel, f"job {job_id} must be a mapping")
                continue
            if "timeout-minutes" not in job:
                report("job-timeout", rel, f"job {job_id} requires timeout-minutes")
            if "head.repo.full_name == github.repository" in str(job.get("if", "")):
                report("fork-scan", rel, f"job {job_id} must not skip fork pull requests")
            for step in job.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses")
                if not isinstance(uses, str):
                    continue
                match = ACTION.match(uses)
                if match and not FULL_SHA.fullmatch(match.group("ref")):
                    report("action-pin", rel, f"{uses} is not pinned to a full commit SHA")
                if uses.startswith("actions/checkout@"):
                    with_values = step.get("with", {}) or {}
                    if with_values.get("persist-credentials") is not False:
                        report("checkout-credentials", rel, "actions/checkout must set persist-credentials: false")

    for required_workflow in policy["required_workflows"]:
        if required_workflow not in names:
            report("required-workflow", required_workflow, "stable workflow name not found")

    for item in waived:
        print(f"WAIVED {item}")
    if failures:
        for item in failures:
            print(f"ERROR {item}", file=sys.stderr)
        print(f"control-audit: {len(failures)} failure(s), {len(waived)} documented waiver(s)", file=sys.stderr)
        return 1

    print(
        f"control-audit: PASS ({len(workflows)} workflows, "
        f"{len(waived)} documented waiver(s), scanner={policy['scanner_mode']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
