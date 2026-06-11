#!/usr/bin/env python3
"""Staged secret/PII gate — blocks commits (and CI) that introduce secrets or a
path from the personal/Drive tier, and WARNS (non-blocking) on PII.

Usage:
  python3 tools/scan_staged.py --staged          # scan staged diff (pre-commit)
  python3 tools/scan_staged.py --ci --base REF   # scan REF...HEAD (CI)
  python3 tools/scan_staged.py --self-test       # built-in cases, exit 0/1

Exit code: 0 = no blocking findings, 1 = a block (commit/CI should fail), 2 = usage.

Severity:
  BLOCK  secrets (API keys, tokens, private keys) and personal/Drive-tier paths
         (PERSONAL_JOURNAL*, private/). These must never reach git.
  WARN   PII (EMAIL / SSN / CREDIT+Luhn / PHONE). Surfaced but NOT blocking, by
         decision — personal data isn't expected in these repos, and hard-blocking
         it caused false positives. Promote PII to BLOCK later by removing the
         kinds from _PII_KINDS below (one line).

The PII detectors (EMAIL / SSN / CREDIT+Luhn / PHONE) are VENDORED verbatim from
testing-kits/harnesses/security/pii_redaction_test_harness.py so each repo stays
self-contained and zero-dependency. DOB / bare-date detection from that harness is
intentionally OMITTED: dated frontmatter (date: YYYY-MM-DD) is legitimate and
everywhere in these repos, so it would flag every commit.

Escape hatch: a line containing the marker  allowlist secret  is skipped.
One-off bypass for an intentional commit:  git commit --no-verify  (use sparingly).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# --- VENDORED from pii_redaction_test_harness.py (provenance: testing-kits) ---
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_RE = re.compile(r"\b(?:\d[ -]?){15}\d\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\d)"
)


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — vendored from the PII harness."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# --- ADDED: secret patterns the PII harness does not cover (these BLOCK) ---
_SECRET_RES = [
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
]

# PII kinds are WARN-only (see module docstring). Everything else BLOCKs.
_PII_KINDS = {"EMAIL", "SSN", "CREDIT_CARD", "PHONE"}

_PERSONAL_PATH_RE = re.compile(r"(^|/)(PERSONAL_JOURNAL[^/]*$|private/)")


def scan_line(line: str) -> list[str]:
    """Return finding-type labels for one added line of content."""
    if "allowlist secret" in line:  # intentional escape hatch
        return []
    hits: list[str] = []
    if _EMAIL_RE.search(line):
        hits.append("EMAIL")
    if _SSN_RE.search(line):
        hits.append("SSN")
    for m in _CREDIT_RE.finditer(line):
        digits = re.sub(r"[ -]", "", m.group())
        if len(digits) == 16 and _luhn_ok(digits):
            hits.append("CREDIT_CARD")
            break
    if _PHONE_RE.search(line):
        hits.append("PHONE")
    for name, rx in _SECRET_RES:
        if rx.search(line):
            hits.append(name)
    return hits


def _git(args: list[str]) -> str:
    out = subprocess.run(["git"] + args, capture_output=True, text=True, check=False)
    return out.stdout


def _changed_paths(diff_args: list[str]) -> list[str]:
    return [p for p in _git(["diff", "--name-only"] + diff_args).splitlines() if p]


def _added_lines(diff_args: list[str]):
    """Yield (path, new_lineno, text) for each ADDED line in the diff."""
    diff = _git(["diff", "--unified=0", "--no-color"] + diff_args)
    path = None
    newno = 0
    for line in diff.splitlines():
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


def _fmt(rows) -> None:
    for path, no, kind in rows:
        loc = f"{path}:{no}" if no else path
        print(f"  {kind:<34} {loc}")


def _scan(diff_args: list[str]) -> int:
    blocks, warns = [], []
    for p in _changed_paths(diff_args):
        if _PERSONAL_PATH_RE.search(p):
            blocks.append((p, 0, "PERSONAL/PRIVATE PATH (Drive-tier only)"))
    for path, no, text in _added_lines(diff_args):
        for hit in scan_line(text):
            (warns if hit in _PII_KINDS else blocks).append((path or "?", no, hit))

    if warns:
        print("WARNING: possible PII in this change (not blocking):")
        _fmt(warns)
        print()
    if not blocks:
        if warns:
            print("PII warnings only — commit allowed. Keep personal data in the Drive vault.")
        return 0
    print("BLOCKED: possible secret / personal-tier content in this change.\n")
    _fmt(blocks)
    print(
        "\nThe raw value is not printed. Remove it (secrets never belong in git; personal\n"
        "data belongs in the Drive vault). For an intentional, reviewed line add the marker\n"
        "'allowlist secret', or bypass once with 'git commit --no-verify'."
    )
    return 1


def _run_self_test() -> int:
    # Fixtures assembled from parts so this file does not trip its own gate.
    aws = "AKIA" + "IOSFODNN7EXAMPLE"
    ghp = "ghp_" + ("a" * 36)
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----"
    slack = "xoxb-" + "1234567890-abcdefXYZ"
    gkey = "AIza" + ("b" * 35)
    generic = "api_key" + " = " + "'" + ("A" * 20) + "'"
    must_block = [aws, ghp, pem, slack, gkey, generic]

    ssn = "123" + "-45-" + "6789"
    card = " ".join(["4242"] * 4)  # passes Luhn
    email = "alice" + "@" + "example.com"
    must_warn = [ssn, card, email]

    must_clean = [
        "Beverly Hills 90210",                       # ZIP, not PII
        "order #" + "1234567812345678",              # 16-digit, fails Luhn
        "date: 2026-06-02  project: testing-kits",
        "see arXiv 2310.13548 and 2401.04088",       # arXiv ids
        "standup at 3pm to discuss the build",       # plain prose
        "this line mentions an api_key in passing",  # keyword, no value
        "uses ${{ secrets.GITHUB_TOKEN }} in CI",    # CI ref, not a literal token
    ]

    fails = 0
    for s in must_block:
        labels = scan_line(s)
        if not any(l not in _PII_KINDS for l in labels):
            fails += 1
            print("  FAIL: expected secret block was not detected")
    for s in must_warn:
        labels = scan_line(s)
        if not labels or any(l not in _PII_KINDS for l in labels):
            fails += 1
            print("  FAIL: expected PII warning was not detected")
    for s in must_clean:
        labels = scan_line(s)
        if labels:
            fails += 1
            print("  FAIL: clean fixture produced a false positive")
    if fails:
        print(f"self-test: {fails} failure(s)")
        return 1
    print(
        f"self-test: OK ({len(must_block)} block, {len(must_warn)} warn, "
        f"{len(must_clean)} clean)"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Secret/PII staged-content gate.")
    p.add_argument("--self-test", action="store_true", help="run built-in cases")
    p.add_argument("--staged", action="store_true", help="scan the staged diff")
    p.add_argument("--ci", action="store_true", help="scan base...HEAD (needs --base)")
    p.add_argument("--base", default="", help="base ref for --ci")
    a = p.parse_args()

    if a.self_test:
        return _run_self_test()
    if a.ci:
        if not a.base:
            print("--ci requires --base REF", file=sys.stderr)
            return 2
        return _scan([f"{a.base}...HEAD"])
    return _scan(["--cached"])  # default: staged


if __name__ == "__main__":
    sys.exit(main())
