#!/usr/bin/env python3
"""Shared harness discovery and self-test helpers."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclasses.dataclass(frozen=True)
class HarnessRecord:
    root: Path
    path: Path
    category: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.category}/{self.name}"

    @property
    def rel_path(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    @property
    def paired_test(self) -> Path:
        return self.root / "tests" / self.category / f"test_{self.path.stem}.py"

    @property
    def proof_test(self) -> Path:
        return self.root / "tests" / self.category / f"test_{self.name}_proof.py"


def short_name(path: Path) -> str:
    name = path.stem
    if name.endswith("_test_harness"):
        return name[: -len("_test_harness")]
    if name == "stress_harness":
        return "stress"
    if name.endswith("_harness"):
        return name[: -len("_harness")]
    return name


def discover_harnesses(root: Path = REPO_ROOT) -> list[HarnessRecord]:
    """Return every real harness module under harnesses/*/*.py."""
    root = Path(root)
    records: list[HarnessRecord] = []
    for path in sorted((root / "harnesses").glob("*/*.py")):
        if path.name == "__init__.py":
            continue
        try:
            category = path.relative_to(root).parts[1]
        except IndexError:
            continue
        records.append(HarnessRecord(
            root=root,
            path=path,
            category=category,
            name=short_name(path),
        ))
    return records


NO_SELF_TEST_MARKERS = (
    "unrecognized arguments: --self-test",
    "invalid literal for int() with base 10: '--self-test'",
    "No such file or directory: '--self-test'",
    "argument: invalid choice: '--self-test'",
)


def run_self_test(path: Path, root: Path = REPO_ROOT, timeout_s: float = 90.0) -> tuple[str, float, str]:
    """Run one harness with --self-test and return (status, duration_s, tail)."""
    start = time.perf_counter()
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        proc = subprocess.run(
            [sys.executable, str(path), "--self-test"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - start
        return "TIME", duration, f"TIMEOUT after {timeout_s}s"

    duration = time.perf_counter() - start
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and any(marker in combined for marker in NO_SELF_TEST_MARKERS):
        tail = combined.strip().splitlines()[-1] if combined.strip() else ""
        return "SKIP", duration, tail[:200]

    output = combined.strip().splitlines()
    tail = " | ".join(output[-2:]) if output else ""
    return ("OK" if proc.returncode == 0 else "FAIL"), duration, tail[:200]
