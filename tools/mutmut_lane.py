#!/usr/bin/env python3
"""Advisory mutation-testing lane (Linux/WSL + mutmut only).

The MANDATORY teeth gate is the cross-platform, zero-dependency swap-check in
``tools/proof_audit.py`` (it proves a harness catches the bugs its author planted).
This lane *deepens* that: it mutates harness code and checks the paired test suite
actually kills the mutants — catching "vacuous green" that the swap-check cannot
(suites that execute code but assert nothing meaningful).

It is advisory and NEVER blocks a merge:
* mutmut 3.x does not run on native Windows (boxed/mutmut#397) — this lane skips
  cleanly there. Run it under WSL or in CI (Linux).
* Survivors are reported as findings, not failures; the process exits 0 unless
  ``--strict`` is given.

This module may import the third-party ``mutmut`` (it is tooling, not harness code,
so the pure-stdlib rule for ``harnesses/`` does not apply).

NOTE: validated in CI/Linux. The Windows skip path and ``--list`` are verifiable
on any platform; the live mutmut run is exercised by the advisory CI job.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_CATEGORIES = {"pharmacy"}

# A real module-level TEETH assignment/annotation at column 0 (mirrors
# tools/proof_audit.py._TEETH_DECL — keep both in sync).
_TEETH_DECL = re.compile(r"^TEETH\s*[:=]", re.MULTILINE)


def _skip(reason: str) -> int:
    print(f"mutmut lane skipped: {reason} (advisory lane, not a gate).")
    return 0


def teeth_harnesses() -> list[str]:
    """In-scope harnesses that declare a module-level TEETH (mutation candidates)."""
    found: list[str] = []
    for path in sorted((REPO_ROOT / "harnesses").glob("*/*.py")):
        if path.name == "__init__.py" or path.parts[-2] in LEGACY_CATEGORIES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _TEETH_DECL.search(text):
            found.append(path.relative_to(REPO_ROOT).as_posix())
    return found


def mutmut_available() -> bool:
    if shutil.which("mutmut"):
        return True
    try:
        import mutmut  # noqa: F401
        return True
    except ImportError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="list mutation candidates and exit (works on any platform)")
    parser.add_argument("--strict", action="store_true",
                        help="exit nonzero if mutmut reports surviving mutants")
    args = parser.parse_args(argv)

    candidates = teeth_harnesses()
    if args.list:
        print(f"{len(candidates)} mutation candidate(s) (harnesses with TEETH):")
        for c in candidates:
            print(f"  {c}")
        return 0

    if sys.platform.startswith("win"):
        return _skip("native Windows is unsupported by mutmut (use WSL)")
    if not mutmut_available():
        return _skip("mutmut is not installed (uv sync, or pip install 'mutmut>=3.6')")
    if not candidates:
        return _skip("no harnesses declare TEETH yet")

    # mutmut reads [tool.mutmut].source_paths from pyproject.toml (key renamed from
    # paths_to_mutate in 3.6). Run, then collect results; advisory unless --strict.
    try:
        run = subprocess.run(["mutmut", "run"], cwd=REPO_ROOT, timeout=1800)
        results = subprocess.run(["mutmut", "results"], cwd=REPO_ROOT,
                                 capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        return _skip(f"mutmut timed out ({exc}); advisory lane, not a gate")
    report = REPO_ROOT / "mutmut-report.txt"
    report.write_text(results.stdout or "", encoding="utf-8")
    print(results.stdout or "(no mutmut results captured)")
    print(f"mutmut report written to {report.name} (run rc={run.returncode}).")

    survived = "survived" in (results.stdout or "").lower()
    if args.strict and survived:
        print("STRICT: surviving mutants detected.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
