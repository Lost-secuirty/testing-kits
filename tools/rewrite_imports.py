#!/usr/bin/env python3
"""
rewrite_imports.py — Phase B helper: rewrite test imports for the new layout.

Before reorg, tests import from the flat root:
    from api_test_harness import ApiTestCase

After reorg, tests live at ``tests/<cat>/`` and harnesses at
``harnesses/<cat>/``. Imports must become:
    from harnesses.core.api_test_harness import ApiTestCase

This script scans every test file under ``tests/`` and rewrites
``from <name>_test_harness import ...`` and ``import <name>_test_harness``
to the new categorized form.

Idempotent: running again is a no-op once the rewrite has happened.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESSES = REPO_ROOT / "harnesses"
TESTS = REPO_ROOT / "tests"


def build_module_index() -> dict[str, str]:
    """Map flat-module-name -> dotted-module-name."""
    index: dict[str, str] = {}
    if not HARNESSES.exists():
        return index
    for path in HARNESSES.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(REPO_ROOT).with_suffix("")
        dotted = ".".join(rel.parts)
        flat = path.stem
        index[flat] = dotted
    return index


def rewrite_text(text: str, index: dict[str, str]) -> tuple[str, int]:
    """Rewrite imports in a single file's text. Return (new_text, count)."""
    count = 0

    def replace_from(match: re.Match) -> str:
        nonlocal count
        indent, flat = match.group(1), match.group(2)
        if flat in index:
            count += 1
            return f"{indent}from {index[flat]} import"
        return match.group(0)

    def replace_import(match: re.Match) -> str:
        nonlocal count
        indent, flat = match.group(1), match.group(2)
        if flat in index:
            count += 1
            return f"{indent}import {index[flat]} as {flat}"
        return match.group(0)

    text = re.sub(
        r"^([ \t]*)from\s+([a-zA-Z_][a-zA-Z_0-9]*)\s+import\b",
        replace_from, text, flags=re.M,
    )
    text = re.sub(
        r"^([ \t]*)import\s+([a-zA-Z_][a-zA-Z_0-9]*)\s*$",
        replace_import, text, flags=re.M,
    )
    return text, count


def main() -> int:
    index = build_module_index()
    if not index:
        print("No harnesses/<cat>/ files found. Run Phase B git mv first.", file=sys.stderr)
        return 2

    if not TESTS.exists():
        print(f"No tests/ dir at {TESTS}.", file=sys.stderr)
        return 2

    total_files = 0
    total_rewrites = 0
    for path in sorted(TESTS.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        new_text, n = rewrite_text(text, index)
        if n:
            path.write_text(new_text, encoding="utf-8")
            total_files += 1
            total_rewrites += n
            print(f"  rewrote {n:2d} import(s) in {path.relative_to(REPO_ROOT)}")

    print(f"\nDone. {total_rewrites} imports rewritten across {total_files} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
