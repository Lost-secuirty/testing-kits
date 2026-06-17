#!/usr/bin/env python3
"""MoE-style PR auditor — runs deterministic expert lenses and emits a learnings report.

Triggered by ``.github/workflows/moe-audit.yml`` when a PR is moved out of draft
(``pull_request: ready_for_review``); posts/updates a single PR comment. The "MoE"
is a panel of independent deterministic lenses (no LLM, no external API key — fits
this public, pure-stdlib repo), each emitting findings that are synthesized into a
learnings report:

* **teeth**       — proof-audit scopes; harnesses changed in this PR that are still
                    ``pending`` (promote them?); any ``required`` failing / teeth broken.
* **vacuous**     — changed harnesses whose ``--self-test`` is a no-op (no argparse;
                    exits 0 on a bogus flag) — "fails loud" is not actually wired.
* **purity**      — changed ``harnesses/`` code importing anything outside the stdlib
                    (the zero-runtime-dependency rule). Uses ``sys.stdlib_module_names``.
* **controls**    — repo control audit (``tools/control_audit.py``) still passes.
* **scope**       — diff shape; campaign docs updated when harnesses changed.

Core lenses are pure stdlib. Posting uses only ``GITHUB_TOKEN`` via urllib.

Usage:
  python tools/moe_audit.py --base origin/main                 # print report
  python tools/moe_audit.py --base origin/main --out report.md # write report
  python tools/moe_audit.py --report report.md --post --pr 33  # post/update comment
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKER = "<!-- moe-audit-report -->"
LEGACY_CATEGORIES = {"pharmacy"}

# Modules that are first-party (not third-party) for the stdlib-purity lens.
FIRST_PARTY = {"harnesses", "tools", "tests"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], timeout: float = 300.0) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def changed_files(base: str) -> list[str]:
    rc, out = _run(["git", "diff", "--name-only", f"{base}...HEAD"])
    if rc != 0:  # fall back to a plain diff against base
        rc, out = _run(["git", "diff", "--name-only", base])
    return [line.strip() for line in out.splitlines() if line.strip()]


def changed_harnesses(changed: list[str]) -> list[str]:
    out = []
    for f in changed:
        p = f.replace("\\", "/")
        parts = p.split("/")
        if len(parts) == 3 and parts[0] == "harnesses" and p.endswith(".py") \
                and not parts[2].startswith("_"):
            out.append(p)
    return out


class Lens:
    def __init__(self, key: str):
        self.key = key
        self.status = "ok"          # ok | info | warn | fail
        self.lines: list[str] = []

    def add(self, line: str, *, level: str = "info") -> None:
        order = {"ok": 0, "info": 1, "warn": 2, "fail": 3}
        if order[level if level in order else "info"] > order[self.status]:
            self.status = level if level in order else self.status
        self.lines.append(line)


# --------------------------------------------------------------------------- #
# lenses
# --------------------------------------------------------------------------- #
def lens_teeth(changed_h: list[str]) -> Lens:
    lens = Lens("teeth")
    rc, out = _run([sys.executable, "tools/proof_audit.py", "--json"])
    try:
        data = json.loads(out[out.index("{"):])  # tolerate any stray prefix
    except (ValueError, IndexError):
        lens.add("could not parse proof_audit output", level="warn")
        return lens
    s = data.get("summary", {})
    lens.add(f"scopes: **{s.get('required_ok', 0)}/{s.get('required', 0)} required** "
             f"teeth-verified, {s.get('pending', 0)} pending, {s.get('legacy', 0)} legacy, "
             f"**{s.get('fail', 0)} failing**.")
    rows = {r["key"]: r for r in data.get("per_harness", [])}
    if s.get("fail", 0):
        lens.add("required/legacy harnesses FAILING the gate:", level="fail")
        for r in data.get("per_harness", []):
            if not r.get("ok"):
                lens.add(f"  - `{r['key']}` ({r['scope']}): {'; '.join(r.get('failures', []))}",
                         level="fail")
    # changed harnesses still pending -> promotion candidates
    changed_keys = []
    for hp in changed_h:
        parts = hp.split("/")
        name = parts[2][:-len("_test_harness.py")] if parts[2].endswith("_test_harness.py") \
            else (parts[2][:-len(".py")].replace("_harness", ""))
        changed_keys.append(f"{parts[1]}/{name}")
    pend = [k for k in changed_keys if rows.get(k, {}).get("scope") == "pending"]
    newly_req = [k for k in changed_keys if rows.get(k, {}).get("scope") == "required"]
    if newly_req:
        lens.add("changed harnesses now **required** (teeth wired): "
                 + ", ".join(f"`{k}`" for k in sorted(set(newly_req))))
    if pend:
        lens.add("changed harnesses still **pending** (touched but no verified TEETH — "
                 "promote or note why):", level="warn")
        for k in sorted(set(pend)):
            lens.add(f"  - `{k}`", level="warn")
    return lens


def lens_vacuous(changed_h: list[str]) -> Lens:
    lens = Lens("vacuous")
    flagged = []
    for hp in changed_h:
        rc, _out = _run([sys.executable, hp, "--moe-bogus-flag-xyzzy"], timeout=60)
        # A real argparse rejects an unknown flag (exit 2). Exit 0 => no CLI parsing,
        # so --self-test is silently ignored (a no-op that cannot fail loud).
        if rc == 0:
            flagged.append(hp)
    if flagged:
        lens.add("changed harnesses with a **no-op `--self-test`** (accept any flag, "
                 "exit 0 — cannot fail loud). Add a real argparse + Report:", level="warn")
        for f in flagged:
            lens.add(f"  - `{f}`", level="warn")
    else:
        lens.add("no changed harness has a no-op self-test.")
    return lens


def _third_party_imports(py_path: Path) -> list[str]:
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                continue
            mods = [(node.module or "").split(".")[0]]
        else:
            continue
        for m in mods:
            if m and m not in stdlib and m not in FIRST_PARTY and m != "__future__":
                bad.append(m)
    return sorted(set(bad))


def lens_purity(changed_h: list[str]) -> Lens:
    lens = Lens("purity")
    offenders = {}
    for hp in changed_h:
        bad = _third_party_imports(REPO_ROOT / hp)
        if bad:
            offenders[hp] = bad
    if offenders:
        lens.add("**third-party imports in harness code** (harnesses must be pure "
                 "stdlib):", level="fail")
        for hp, mods in offenders.items():
            lens.add(f"  - `{hp}`: {', '.join(mods)}", level="fail")
    else:
        lens.add(f"all {len(changed_h)} changed harness file(s) are pure stdlib.")
    return lens


def lens_controls() -> Lens:
    lens = Lens("controls")
    rc, out = _run([sys.executable, "tools/control_audit.py"], timeout=120)
    dep_missing = any(m in out for m in
                      ("ModuleNotFoundError", "ImportError", "No module named"))
    if rc == 0:
        lens.add("repository control audit passes.")
    elif rc == 127 or dep_missing:
        # The authoritative gate is the required "Instruction and control audit"
        # check; here it just couldn't run (e.g. PyYAML absent). Advisory only.
        lens.add("control audit not runnable in this job (missing dependency); see "
                 "the required 'Instruction and control audit' check.", level="info")
    else:
        # Advisory warning, not a blocker — the required check is the real gate.
        lens.add(f"control audit reported issues (rc={rc}); confirm via the required "
                 "'Instruction and control audit' check.", level="warn")
    return lens


def lens_scope(base: str, changed: list[str], changed_h: list[str]) -> Lens:
    lens = Lens("scope")
    rc, stat = _run(["git", "diff", "--shortstat", f"{base}...HEAD"])
    lens.add(f"diff: {stat.strip() or 'n/a'} across {len(changed)} file(s); "
             f"{len(changed_h)} harness file(s) touched.")
    docs_touched = any(d in changed for d in (
        "docs/UPGRADE_CAMPAIGN.md", "docs/LEARNINGS.md", "HARNESS_INVENTORY.md"))
    if changed_h and not docs_touched:
        lens.add("harnesses changed but no campaign doc (UPGRADE_CAMPAIGN/LEARNINGS/"
                 "INVENTORY) updated — record the decision/learning.", level="warn")
    return lens


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
_ICON = {"ok": "🟢", "info": "🔵", "warn": "🟡", "fail": "🔴"}


def build_report(base: str) -> tuple[str, str]:
    changed = changed_files(base)
    changed_h = changed_harnesses(changed)
    lenses = [
        lens_teeth(changed_h),
        lens_vacuous(changed_h),
        lens_purity(changed_h),
        lens_controls(),
        lens_scope(base, changed, changed_h),
    ]
    worst = "ok"
    order = {"ok": 0, "info": 1, "warn": 2, "fail": 3}
    for ln in lenses:
        if order[ln.status] > order[worst]:
            worst = ln.status
    verdict = {"ok": "clean", "info": "clean", "warn": "review advised",
               "fail": "blockers found"}[worst]

    overall = (f"_Auto-generated when this PR left draft. Deterministic expert "
               f"panel · overall: **{_ICON[worst]} {verdict}**._")
    out = [MARKER, "## MoE learnings report", overall, ""]
    for ln in lenses:
        out.append(f"### {_ICON[ln.status]} {ln.key}")
        out.extend(ln.lines or ["(nothing to report)"])
        out.append("")
    out.append("---")
    out.append("Lenses are deterministic (no LLM). The teeth gate is the source of "
               "truth; warnings are learnings to record, not merge blockers. See "
               "`docs/UPGRADE_CAMPAIGN.md`.")
    return "\n".join(out), worst


# --------------------------------------------------------------------------- #
# GitHub comment post/update (GITHUB_TOKEN via urllib; stdlib only)
# --------------------------------------------------------------------------- #
def _api(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https GitHub API URL: {url!r}")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 (https-only enforced above; controlled GitHub API URL)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "moe-audit")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (https-only enforced above; controlled GitHub API URL)
        body = resp.read().decode()
    return json.loads(body) if body else {}


def post_comment(repo: str, pr: int, token: str, body: str) -> str:
    base = f"https://api.github.com/repos/{repo}"
    existing_id = None
    page = 1
    while True:
        comments = _api("GET", f"{base}/issues/{pr}/comments?per_page=100&page={page}", token)
        if not comments:
            break
        for c in comments:
            if (c.get("body") or "").startswith(MARKER):
                existing_id = c["id"]
                break
        if existing_id or len(comments) < 100:
            break
        page += 1
    if existing_id:
        _api("PATCH", f"{base}/issues/comments/{existing_id}", token, {"body": body})
        return f"updated comment {existing_id}"
    _api("POST", f"{base}/issues/{pr}/comments", token, {"body": body})
    return "created new comment"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="origin/main", help="base ref for the diff")
    p.add_argument("--out", help="write the report to this file")
    p.add_argument("--report", help="read a prebuilt report from this file (with --post)")
    p.add_argument("--post", action="store_true", help="post/update the PR comment")
    p.add_argument("--pr", type=int, help="PR number (with --post)")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                   help="owner/repo (defaults to $GITHUB_REPOSITORY)")
    args = p.parse_args(argv)

    # emojis in the report crash the legacy Windows console (cp1252)
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.report:
        body = Path(args.report).read_text(encoding="utf-8")
    else:
        body = build_report(args.base)[0]
        if args.out:
            Path(args.out).write_text(body + "\n", encoding="utf-8")
        else:
            print(body)

    if args.post:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token or not args.repo or not args.pr:
            print("--post requires GITHUB_TOKEN, --repo, and --pr", file=sys.stderr)
            return 2
        try:
            print(post_comment(args.repo, args.pr, token, body))
        except urllib.error.URLError as exc:
            # Advisory tool: a failed/forbidden POST (e.g. a read-only GITHUB_TOKEN
            # on a fork PR -> 403) must not turn the advisory check red.
            print(f"post failed (advisory, ignoring): {exc}", file=sys.stderr)
    # Advisory tool: never fail the build on findings.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
