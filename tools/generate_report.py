#!/usr/bin/env python3
"""
Run every harness self-test and optionally write STATUS.md / STATUS.json.

Default mode writes the generated status artifacts. ``--check`` is a no-write
verification path for local and CI use.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

try:
    from tools.harness_registry import REPO_ROOT, discover_harnesses, run_self_test
    from tools.proof_audit import audit_harnesses
except ModuleNotFoundError:  # direct script execution from tools/
    from harness_registry import REPO_ROOT, discover_harnesses, run_self_test
    from proof_audit import audit_harnesses


def write_status(rows: list[dict], total_duration: float, proof_result: dict) -> None:
    by_cat: dict[str, list[dict]] = {}
    for row in rows:
        by_cat.setdefault(row["category"], []).append(row)

    n_total = len(rows)
    n_ok = sum(1 for row in rows if row["status"] == "OK")
    n_fail = sum(1 for row in rows if row["status"] in ("FAIL", "TIME"))
    n_skip = sum(1 for row in rows if row["status"] == "SKIP")
    proof_summary = proof_result["summary"]

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
    lines.append(
        f"**{n_total} harnesses | {header} | proof {proof_summary['header']} | "
        f"self-test {total_duration:.2f}s**"
    )
    lines.append("")
    lines.append("## By category")
    lines.append("")
    lines.append("| Category | Count | Green | Fail | Skip | Time |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for cat in sorted(by_cat):
        items = by_cat[cat]
        count = len(items)
        ok = sum(1 for row in items if row["status"] == "OK")
        fail = sum(1 for row in items if row["status"] in ("FAIL", "TIME"))
        skip = sum(1 for row in items if row["status"] == "SKIP")
        duration = sum(row["duration"] for row in items)
        lines.append(f"| {cat} | {count} | {ok} | {fail} | {skip} | {duration:.2f}s |")

    lines.append("")
    lines.append("## Proof audit")
    lines.append("")
    lines.append(
        f"**{proof_summary['ok']}/{proof_summary['total_harnesses']} harnesses proven "
        f"({proof_summary['header']}).**"
    )
    lines.append("")
    lines.append("| Harness | Paired unittest | Proof source | Self-test |")
    lines.append("|---|---|---|---|")
    proof_by_key = {row["key"]: row for row in proof_result["per_harness"]}
    for row in sorted(rows, key=lambda row: (row["category"], row["name"])):
        key = f"{row['category']}/{row['name']}"
        proof = proof_by_key.get(key, {})
        paired = "yes" if proof.get("paired_test_exists") else "no"
        sources = ", ".join(proof.get("proof_sources", [])) or "-"
        lines.append(f"| {key} | {paired} | {sources} | {row['status']} |")

    lines.append("")
    lines.append("## Per harness")
    lines.append("")
    lines.append("| Harness | Self-test | Duration | Notes |")
    lines.append("|---|---|---:|---|")

    per_harness_list = []
    for row in sorted(rows, key=lambda row: (row["category"], row["name"])):
        notes = row["tail"].replace("|", "\\|") if row["tail"] else ""
        lines.append(
            f"| {row['category']}/{row['name']} | {row['status']} | "
            f"{row['duration']:.2f}s | {notes} |"
        )
        per_harness_list.append({
            "category": row["category"],
            "name": row["name"],
            "status": row["status"],
            "duration": round(row["duration"], 2),
            "tail": row["tail"],
        })
    lines.append("")

    (REPO_ROOT / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_category_data: dict[str, dict] = {}
    for cat in sorted(by_cat):
        items = by_cat[cat]
        by_category_data[cat] = {
            "count": len(items),
            "ok": sum(1 for row in items if row["status"] == "OK"),
            "fail": sum(1 for row in items if row["status"] in ("FAIL", "TIME")),
            "skip": sum(1 for row in items if row["status"] == "SKIP"),
            "duration_s": round(sum(row["duration"] for row in items), 2),
        }

    json_obj = {
        "generated_at": now,
        "summary": {
            "total_harnesses": n_total,
            "ok": n_ok,
            "fail": n_fail,
            "skip": n_skip,
            "header": header,
            "total_duration_s": round(total_duration, 2),
        },
        "proof": proof_result,
        "by_category": by_category_data,
        "per_harness": per_harness_list,
    }
    (REPO_ROOT / "STATUS.json").write_text(json.dumps(json_obj, indent=2) + "\n", encoding="utf-8")


def run_all_selftests(timeout_s: float) -> tuple[list[dict], float]:
    records = discover_harnesses()
    rows: list[dict] = []
    start = time.perf_counter()
    for record in records:
        status, duration, tail = run_self_test(record.path, record.root, timeout_s=timeout_s)
        rows.append({
            "category": record.category,
            "name": record.name,
            "status": status,
            "duration": duration,
            "tail": tail,
        })
        print(f"{status:4s} {record.key:36s} {duration:6.2f}s")
    return rows, time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or check testing-kits status.")
    parser.add_argument("--check", action="store_true",
                        help="run self-tests and proof audit without writing STATUS files")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="per-harness self-test timeout in seconds")
    args = parser.parse_args()

    records = discover_harnesses()
    if not records:
        print("No harnesses found.", file=sys.stderr)
        return 2

    rows, total = run_all_selftests(args.timeout)
    selftest_statuses = {f"{row['category']}/{row['name']}": row["status"] for row in rows}
    proof_result = audit_harnesses(records, selftest_statuses=selftest_statuses)

    n_ok = sum(1 for row in rows if row["status"] == "OK")
    n_fail = sum(1 for row in rows if row["status"] in ("FAIL", "TIME"))
    n_skip = sum(1 for row in rows if row["status"] == "SKIP")
    proof_fail = proof_result["summary"]["fail"]

    if not args.check:
        write_status(rows, total, proof_result)

    write_note = "No STATUS files written." if args.check else "Wrote STATUS.md and STATUS.json."
    print(
        f"\n{n_ok}/{len(rows)} green, {n_fail} failing, {n_skip} no --self-test; "
        f"proof {proof_result['summary']['ok']}/{proof_result['summary']['total_harnesses']} "
        f"({total:.2f}s). {write_note}"
    )
    return 0 if n_fail == 0 and n_skip == 0 and proof_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
