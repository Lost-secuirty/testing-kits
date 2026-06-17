#!/usr/bin/env python3
"""file_guard.py — a content-addressed freeze over the repo's gate machinery.

Ported in spirit from the Codex slot repo's ``scripts/file-guard.mjs``: a sha256
baseline over the EXECUTABLE files that enforce this repo's rules — the secret
scanner, the teeth + proof gate, the control auditor, and the gate-canary. It makes
"the audit system may not loosen itself" a HARD tripwire, not a norm.

  --check (default)  hash every protected file, compare to the committed baseline
    (.fileguard.json), and exit 1 on ANY content change / removal / unbaselined add.
    It works on the WORKING TREE, not a git range, so it bites even when a diff-based
    gate's base ref is wrong (the vacuous-green hole gate_canary.py also guards).
  --update (alias --snapshot)  rewrite the baseline. The whole point is that the bump
    then lands IN THE DIFF, where a reviewer sees that the safety machinery changed.

Tamper-EVIDENT, not tamper-proof: anyone may change a guarded file AND re-snapshot in
the same commit. The guard only forces that pair into the reviewed diff — detection,
not prevention. Prose contracts (AGENTS.md, SECURITY.md) stay under the softer
control-audit / secret-scan checks on purpose; this freezes the code that ENFORCES
the rules, not the rules themselves.

Pure standard library. Honors --root (default: the repo root) so a caller can drive
it against a throwaway temp tree.

Exit: 0 clean, 1 drift, 2 missing/corrupt baseline (a NAMED failure, never a bare
crash — honouring the same anti-vacuous-green thesis the guard enforces).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = ".fileguard.json"

# The protected set: the executable machinery that enforces the repo's rules. An
# explicit file that goes missing is a violation (REMOVED), not a silent drop.
PROTECTED_FILES = [
    ".github/control-policy.json",
    ".githooks/pre-commit",
    "harnesses/_teeth.py",
    "tools/control_audit.py",
    "tools/file_guard.py",
    "tools/gate_canary.py",
    "tools/harness_registry.py",
    "tools/proof_audit.py",
    "tools/scan_staged.py",
    "tools/teeth_check.py",
]
# Globs expand to whatever currently matches, so adding/removing a member is itself a
# drift event until the baseline is bumped. (None today — the gate set is explicit.)
PROTECTED_GLOBS: list[str] = []


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected(root: Path) -> set[str]:
    found = set(PROTECTED_FILES)
    for pattern in PROTECTED_GLOBS:
        for match in glob.glob(pattern, root_dir=str(root), recursive=True):
            found.add(Path(match).as_posix())
    return found


def update(root: Path, manifest: Path) -> int:
    """Rewrite the baseline. Refuse (exit 1) if any explicit protected file is missing."""
    files: dict[str, str] = {}
    missing: list[str] = []
    for rel in sorted(_expected(root)):
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        files[rel] = _sha256(path)
    if missing:
        print("file-guard: refusing to snapshot - protected files are missing:", file=sys.stderr)
        for rel in missing:
            print(f"  - {rel}", file=sys.stderr)
        return 1
    manifest.write_text(json.dumps({"version": 1, "files": files}, indent=2) + "\n",
                        encoding="utf-8")
    print(f"file-guard: baseline written ({len(files)} protected files).")
    return 0


def check(root: Path, manifest: Path) -> int:
    """Compare the working tree to the baseline. 0 clean / 1 drift / 2 no-or-corrupt baseline."""
    if not manifest.is_file():
        print(f"file-guard: no baseline at {manifest}. Run: make guard-update", file=sys.stderr)
        return 2
    try:
        baseline = json.loads(manifest.read_text(encoding="utf-8")).get("files", {})
    except (ValueError, OSError) as err:
        # A corrupt baseline is exactly the tampered/inert case the guard exists for —
        # fail with a NAMED error (exit 2), not an uncaught crash.
        print(f"file-guard: baseline {manifest} is unreadable or corrupt ({err}). "
              "Re-create it with: make guard-update", file=sys.stderr)
        return 2
    drift: list[str] = []
    for rel in sorted(set(baseline) | _expected(root)):
        path = root / rel
        if not path.is_file():
            drift.append(f"REMOVED      {rel}  (protected file is gone)")
            continue
        now = _sha256(path)
        if rel not in baseline:
            drift.append(f"UNBASELINED  {rel}  (present but absent from the baseline)")
        elif now != baseline[rel]:
            drift.append(f"MODIFIED     {rel}  ({baseline[rel][:12]}... -> {now[:12]}...)")
    if not drift:
        print(f"file-guard: OK - {len(baseline)} protected files match the baseline.")
        return 0
    print("file-guard: DRIFT - the repo gate machinery changed since the baseline:\n",
          file=sys.stderr)
    for line in drift:
        print(f"  {line}", file=sys.stderr)
    print("\nIf this change is intentional and reviewed, re-baseline so the bump shows in the\n"
          "diff:  make guard-update   (then commit .fileguard.json alongside the change).\n"
          "If you did NOT expect this, a gate may have been weakened - STOP and review.",
          file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Content-addressed freeze over gate machinery.")
    parser.add_argument("--update", "--snapshot", action="store_true", dest="update",
                        help="rewrite the baseline (the bump lands in the reviewed diff)")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repo root to scan")
    parser.add_argument("--manifest", default="",
                        help="baseline path (default: <root>/.fileguard.json)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    manifest = Path(args.manifest).resolve() if args.manifest else root / MANIFEST_NAME
    return update(root, manifest) if args.update else check(root, manifest)


if __name__ == "__main__":
    raise SystemExit(main())
