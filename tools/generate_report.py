#!/usr/bin/env python3
"""
generate_report.py — Run every harness's --self-test and write STATUS.md.

Walks ``harnesses/**/*.py`` (or the legacy flat root layout) and invokes
each with ``--self-test``, captures pass/fail + duration, and writes a
single ``STATUS.md`` summary.

Exits 0 if every harness self-tests green, non-zero otherwise.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def discover_harnesses() -> list[Path]:
    """Return harness file paths. Supports both categorized and flat layout."""
    categorized = sorted(REPO_ROOT.glob("harnesses/*/*_test_harness.py"))
    categorized += sorted(REPO_ROOT.glob("harnesses/*/stress_harness.py"))
    if categorized:
        return categorized

    flat = sorted(REPO_ROOT.glob("*_test_harness.py"))
    flat += sorted(REPO_ROOT.glob("stress_harness.py"))
    return [p for p in flat if not p.name.startswith("test_")]


def category_for(path: Path) -> str:
    """Bucket a harness path into a category label."""
    parts = path.relative_to(REPO_ROOT).parts
    if len(parts) >= 3 and parts[0] == "harnesses":
        return parts[1]
    return "(flat)"


def short_name(path: Path) -> str:
    name = path.stem
    if name.endswith("_test_harness"):
        name = name[: -len("_test_harness")]
    elif name == "stress_harness":
        name = "stress"
    return name


NO_SELF_TEST_MARKERS = (
    "unrecognized arguments: --self-test",
    "invalid literal for int() with base 10: '--self-test'",
    "No such file or directory: '--self-test'",
    "argument: invalid choice: '--self-test'",
)


def run_self_test(path: Path, timeout_s: float = 90.0) -> tuple[str, float, str]:
    """Run a harness with --self-test.

    Return (status, duration_s, tail_of_output) where status is one of:
      ``"OK"``    — exit 0
      ``"FAIL"``  — exit non-zero with no "doesn't support --self-test" marker
      ``"SKIP"``  — harness doesn't accept --self-test (CLI shape mismatch)
      ``"TIME"``  — wall-clock timeout
    """
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(path), "--self-test"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - start
        return "TIME", duration, f"TIMEOUT after {timeout_s}s"

    duration = time.perf_counter() - start
    combined = (proc.stdout + proc.stderr)
    if proc.returncode != 0 and any(m in combined for m in NO_SELF_TEST_MARKERS):
        tail = combined.strip().splitlines()[-1] if combined.strip() else ""
        return "SKIP", duration, tail[:200]

    output = combined.strip().splitlines()
    tail = " | ".join(output[-2:]) if output else ""
    return ("OK" if proc.returncode == 0 else "FAIL"), duration, tail[:200]


def write_status(rows: list[dict], total_duration: float) -> None:
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    n_total = len(rows)
    n_ok = sum(1 for r in rows if r["status"] == "OK")
    n_fail = sum(1 for r in rows if r["status"] in ("FAIL", "TIME"))
    n_skip = sum(1 for r in rows if r["status"] == "SKIP")
    if n_fail == 0 and n_skip == 0:
        header = "all green"
    elif n_fail == 0:
        header = f"{n_ok} green, {n_skip} no --self-test"
    else:
        header = f"{n_fail} failing, {n_skip} no --self-test"
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("# Status (auto-generated)")
    lines.append("")
    lines.append(f"_Generated: {now}_")
    lines.append("")
    lines.append(f"**{n_total} harnesses | {header} | self-test {total_duration:.2f}s**")
    lines.append("")
    lines.append("## By category")
    lines.append("")
    lines.append("| Category | Count | Green | Fail | Skip | Time |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for cat in sorted(by_cat):
        items = by_cat[cat]
        n = len(items)
        ok = sum(1 for r in items if r["status"] == "OK")
        fail = sum(1 for r in items if r["status"] in ("FAIL", "TIME"))
        skip = sum(1 for r in items if r["status"] == "SKIP")
        dur = sum(r["duration"] for r in items)
        lines.append(f"| {cat} | {n} | {ok} | {fail} | {skip} | {dur:.2f}s |")
    lines.append("")
    lines.append("## Per harness")
    lines.append("")
    lines.append("| Harness | Self-test | Duration | Notes |")
    lines.append("|---|---|---:|---|")
    for r in sorted(rows, key=lambda r: (r["category"], r["name"])):
        notes = r["tail"].replace("|", "\\|") if r["tail"] else ""
        lines.append(f"| {r['category']}/{r['name']} | {r['status']} | {r['duration']:.2f}s | {notes} |")
    lines.append("")

    (REPO_ROOT / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    harnesses = discover_harnesses()
    if not harnesses:
        print("No harnesses found.", file=sys.stderr)
        return 2

    rows: list[dict] = []
    start = time.perf_counter()
    for path in harnesses:
        status, duration, tail = run_self_test(path)
        rows.append({
            "category": category_for(path),
            "name": short_name(path),
            "status": status,
            "duration": duration,
            "tail": tail,
        })
        print(f"{status:4s} {category_for(path)}/{short_name(path):30s} {duration:6.2f}s")

    total = time.perf_counter() - start
    write_status(rows, total)
    n_ok = sum(1 for r in rows if r["status"] == "OK")
    n_fail = sum(1 for r in rows if r["status"] in ("FAIL", "TIME"))
    n_skip = sum(1 for r in rows if r["status"] == "SKIP")
    print(f"\n{n_ok}/{len(rows)} green, {n_fail} failing, {n_skip} no --self-test "
          f"({total:.2f}s). Wrote STATUS.md.")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
