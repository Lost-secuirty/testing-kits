#!/usr/bin/env python3
"""
Diff-aware secret gate test harness.

A unified-diff-aware secret scanner. The NOVEL ORACLE here is diff
direction-awareness: a secret token that appears on a REMOVED ('-') line must
NOT trip the gate, while the same token on an ADDED ('+') line MUST be reported,
and at the correct POST-change line number. Removing a leaked secret is a
remediation, not a violation; the common naive scanner that reads every content
line flags the removal too, producing a false positive that blocks the very
commit that fixes the leak. This harness proves the direction-aware oracle has
teeth by pitting it against an intentionally buggy `scan_diff_naive`.

SCOPE: SECRET TOKENS ONLY. PII detectors (EMAIL / SSN / PHONE / CREDIT) are
intentionally OMITTED — those are owned by
harnesses/security/pii_redaction_test_harness.py, and re-porting them here would
be a pure duplicate.

Provenance: the diff parser and the secret regexes are ported from
tools/scan_staged.py (originally portfolio/Journal-and-findings/scan_staged.py).
This harness operates on a PROVIDED unified-diff STRING rather than shelling out
to `git diff`, so the direction-awareness oracle is deterministic and testable.
The diff text supplied to scan_diff() is expected to look like
`git diff --unified=0 --no-color` output. The vendored PII regexes from the
source are deliberately not ported (see SCOPE).

Fixture secret strings are built by CONCATENATION (e.g. "AKIA" + "IOSFOD...") so
this harness file does not trip its own / CI's secret gate.

Self-test:
  python harnesses/security/diff_secret_gate_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
from dataclasses import dataclass
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402

# ----------------------------------------------------------------------------
# Secret patterns — ported verbatim from tools/scan_staged.py (SECRETS ONLY).
# PII regexes from the source are intentionally omitted; see module docstring.
# ----------------------------------------------------------------------------
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS_ACCESS_KEY_ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{59,}\b")),
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "GENERIC_SECRET_ASSIGNMENT",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|passwd|password|access[_-]?key)\b"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{16,}"
        ),
    ),
)

# Escape hatch: a line containing this literal marker yields no hits.
_ALLOWLIST_MARKER = "allowlist secret"


def scan_line(line: str) -> list[str]:
    """Return secret-token labels found on one line of content.

    A line containing the literal escape-hatch marker yields no hits. Only
    secret tokens are detected — PII is out of scope (see module docstring).
    """
    if _ALLOWLIST_MARKER in line:  # intentional escape hatch
        return []
    hits: list[str] = []
    for name, rx in SECRET_PATTERNS:
        if rx.search(line):
            hits.append(name)
    return hits


# ----------------------------------------------------------------------------
# THE ORACLE: direction-aware unified-diff scanner.
# Ported faithfully from tools/scan_staged.py::_added_lines, but operating on a
# provided diff STRING instead of subprocess git output.
#   - track current path from '+++ b/<path>' lines
#   - on '@@ ... +N ...' set newno=N
#   - a '+' line (not '+++') yields (path, newno, text-after-+), then newno+=1
#   - a line NOT starting with '-' advances newno
#   - removed ('-') lines are ignored and do NOT advance newno
# ----------------------------------------------------------------------------
def iter_added_lines(diff_text: str):
    """Yield (path, new_lineno, text) for each ADDED line in a unified diff.

    Removed ('-') lines are skipped and do not advance the post-change line
    counter; this is what makes the gate direction-aware.
    """
    path: str | None = None
    newno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+++ "):
            path = None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            newno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            yield path, newno, line[1:]
            newno += 1
        elif not line.startswith("-"):
            newno += 1


def scan_diff(diff_text: str) -> list[tuple[str | None, int, str]]:
    """Direction-aware scan. Return (path, lineno, label) for secrets on ADDED
    lines only. Secrets on removed lines are never reported."""
    findings: list[tuple[str | None, int, str]] = []
    for path, no, text in iter_added_lines(diff_text):
        for label in scan_line(text):
            findings.append((path, no, label))
    return findings


# ----------------------------------------------------------------------------
# INTENTIONAL BUGGY IMPLEMENTATION.
# Scans EVERY content line regardless of +/- (the common bug): it flags secrets
# on removed lines too, so it blocks the commit that REMOVES a leaked secret.
# The proof test shows scan_diff (real) passes a removed-secret-only diff while
# this naive version flags it.
# ----------------------------------------------------------------------------
def scan_diff_naive(diff_text: str) -> list[tuple[str | None, int, str]]:
    """BUGGY: scans both '+' and '-' content lines, ignoring diff direction."""
    findings: list[tuple[str | None, int, str]] = []
    path: str | None = None
    lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") or line.startswith("-"):
            content = line[1:]
            for label in scan_line(content):
                findings.append((path, lineno, label))
            lineno += 1
        else:
            lineno += 1
    return findings


# ----------------------------------------------------------------------------
# Fixtures. Secret strings are built by concatenation so this file does not
# trip its own / CI secret gate.
# ----------------------------------------------------------------------------
_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
_GHP = "ghp_" + ("a" * 36)
_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
_SLACK = "xoxb-" + "1234567890-abcdefXYZ"
_GKEY = "AIza" + ("b" * 35)
_GENERIC = "api_key" + " = " + "'" + ("A" * 20) + "'"


@dataclass(frozen=True)
class DiffCase:
    """A unified-diff fixture and the secret findings the direction-aware
    oracle is expected to report (path, lineno, label tuples)."""

    name: str
    diff: str
    expected: tuple[tuple[str | None, int, str], ...]
    note: str


CASES: tuple[DiffCase, ...] = (
    # CENTERPIECE: same secret on a removed line AND an added line. Only the
    # added occurrence is reported, at its correct post-change line number.
    DiffCase(
        name="rotate_secret_remove_and_add",
        diff=(
            "diff --git a/config.py b/config.py\n"
            "--- a/config.py\n"
            "+++ b/config.py\n"
            "@@ -10 +10 @@\n"
            "-aws_key = '" + _AWS + "'\n"
            "+aws_key = '" + _AWS + "'\n"
        ),
        # The '-' line at old-10 is ignored and does not advance the counter;
        # the '+' line is the new content at line 10.
        expected=(("config.py", 10, "AWS_ACCESS_KEY_ID"),),
        note="same secret removed then re-added; only the added one is flagged",
    ),
    # Removed-only: a pure remediation diff. Direction-aware oracle: 0 findings.
    DiffCase(
        name="removed_secret_only",
        diff=(
            "diff --git a/secrets.env b/secrets.env\n"
            "--- a/secrets.env\n"
            "+++ b/secrets.env\n"
            "@@ -3 +0,0 @@\n"
            "-GH_TOKEN=" + _GHP + "\n"
        ),
        expected=(),
        note="secret only ever appears on a removed line — not a violation",
    ),
    # Added secret with correct post-change line number after a context-free
    # hunk that starts partway down the file.
    DiffCase(
        name="added_secret_line_number",
        diff=(
            "diff --git a/app/keys.py b/app/keys.py\n"
            "--- a/app/keys.py\n"
            "+++ b/app/keys.py\n"
            "@@ -0,0 +42 @@\n"
            "+slack = '" + _SLACK + "'\n"
        ),
        expected=(("app/keys.py", 42, "SLACK_TOKEN"),),
        note="added secret reported at the post-change line number from the hunk",
    ),
    # Multiple added secrets across consecutive added lines: line numbers must
    # increment per added line.
    DiffCase(
        name="multiple_added_secrets",
        diff=(
            "diff --git a/creds.txt b/creds.txt\n"
            "--- a/creds.txt\n"
            "+++ b/creds.txt\n"
            "@@ -0,0 +5,3 @@\n"
            "+" + _PEM + "\n"
            "+google = " + _GKEY + "\n"
            "+" + _GENERIC + "\n"
        ),
        expected=(
            ("creds.txt", 5, "PRIVATE_KEY_BLOCK"),
            ("creds.txt", 6, "GOOGLE_API_KEY"),
            ("creds.txt", 7, "GENERIC_SECRET_ASSIGNMENT"),
        ),
        note="three added secrets on consecutive lines; counter advances per line",
    ),
    # Removed line between added lines must NOT advance the post-change counter.
    DiffCase(
        name="removed_between_added_no_advance",
        diff=(
            "diff --git a/mix.py b/mix.py\n"
            "--- a/mix.py\n"
            "+++ b/mix.py\n"
            "@@ -8,1 +8,2 @@\n"
            "+first = 1\n"
            "-old = 2\n"
            "+aws = '" + _AWS + "'\n"
        ),
        # +first -> line 8; the removed line is ignored (no advance);
        # +secret -> line 9.
        expected=(("mix.py", 9, "AWS_ACCESS_KEY_ID"),),
        note="removed line does not bump the post-change line number",
    ),
    # Escape hatch on an added secret line: no hits.
    DiffCase(
        name="allowlisted_added_secret",
        diff=(
            "diff --git a/sample.py b/sample.py\n"
            "--- a/sample.py\n"
            "+++ b/sample.py\n"
            "@@ -0,0 +1 @@\n"
            "+demo = '" + _AWS + "'  # allowlist secret\n"
        ),
        expected=(),
        note="allowlist-secret marker suppresses the finding on an added line",
    ),
    # Clean added lines: a keyword with no value, a CI ref. No findings.
    DiffCase(
        name="clean_added_lines",
        diff=(
            "diff --git a/ci.yml b/ci.yml\n"
            "--- a/ci.yml\n"
            "+++ b/ci.yml\n"
            "@@ -0,0 +1,3 @@\n"
            "+# this line mentions an api_key in passing\n"
            "+token: ${{ secrets.GITHUB_TOKEN }}\n"
            "+plain prose with no secret at all\n"
        ),
        expected=(),
        note="bare keyword and CI secret-ref are not literal secrets",
    ),
    # Multi-file diff: path tracking switches on the second '+++ b/' header.
    DiffCase(
        name="multi_file_path_tracking",
        diff=(
            "diff --git a/one.py b/one.py\n"
            "--- a/one.py\n"
            "+++ b/one.py\n"
            "@@ -0,0 +1 @@\n"
            "+a = '" + _GHP + "'\n"
            "diff --git a/two.py b/two.py\n"
            "--- a/two.py\n"
            "+++ b/two.py\n"
            "@@ -0,0 +2 @@\n"
            "+b = '" + _SLACK + "'\n"
        ),
        expected=(
            ("one.py", 1, "GITHUB_TOKEN"),
            ("two.py", 2, "SLACK_TOKEN"),
        ),
        note="each file's findings carry the correct path",
    ),
)


def list_cases() -> list[str]:
    return [case.name for case in CASES]


@dataclass(frozen=True)
class DiffResult:
    case: DiffCase
    findings: tuple[tuple[str | None, int, str], ...]
    ok: bool


def run_case(case: DiffCase) -> DiffResult:
    findings = tuple(scan_diff(case.diff))
    return DiffResult(case=case, findings=findings, ok=findings == case.expected)


def run_all() -> list[DiffResult]:
    return [run_case(case) for case in CASES]


# ----------------------------------------------------------------------------
# Teeth: the direction-aware oracle reproduces every fixture's expected finding
# set; the naive scanner disagrees on at least one (it flags removed secrets).
# ----------------------------------------------------------------------------
def _prove(impl) -> bool:
    """True iff `impl` disagrees with the frozen CASES corpus on any case.

    Each case's diff is scanned and the produced findings compared to the
    case's expected finding set; any mismatch (or an exception) counts as the
    implementation being caught.
    """
    for case in CASES:
        try:
            if tuple(impl(case.diff)) != case.expected:
                return True
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["scan_diff"]

TEETH = Teeth(
    prove=_prove,
    oracle=scan_diff,
    mutants=(
        Mutant("direction_blind", scan_diff_naive,
               "naive scanner flags secrets on removed lines, blocking remediation diffs"),
    ),
    corpus_size=len(CASES),
    kind="auditor",
    notes="a secret on a removed ('-') line must not be reported as a finding",
)


def _run_self_test() -> int:
    results = run_all()
    failures = [r for r in results if not r.ok]
    if failures:
        for r in failures:
            print(
                f"FAIL {r.case.name}: expected {r.case.expected}, got {r.findings}",
                file=sys.stderr,
            )
        return 1

    # The direction-awareness oracle must have teeth: prove the naive scanner
    # disagrees on the removed-secret-only case (it false-positives there).
    removed_only = next(c for c in CASES if c.name == "removed_secret_only")
    real = scan_diff(removed_only.diff)
    naive = scan_diff_naive(removed_only.diff)
    if real:
        print("FAIL: real scanner flagged a removed-only secret", file=sys.stderr)
        return 1
    if not naive:
        print(
            "FAIL: naive scanner did not flag the removed secret — proof is toothless",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(results)} diff secret-gate scenarios passed "
        f"(direction-awareness confirmed: real=0, naive={len(naive)} on removed-only)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run diff-aware secret-gate scenarios")
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
                "name": r.case.name,
                "expected": [list(t) for t in r.case.expected],
                "findings": [list(t) for t in r.findings],
                "ok": r.ok,
                "note": r.case.note,
            }
            for r in run_all()
        ]
        print(json.dumps(payload, indent=2))
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
