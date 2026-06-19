#!/usr/bin/env python3
"""mutant-fragility checker — enforce the corpus-cardinality FLOOR that lets a
harness catch each mutant on more than one case.

A mutant is "single-load-bearing" (fragile) when exactly ONE frozen corpus case
catches it: mis-edit that one fixture and the mutant silently stops being caught
while every other gate stays green. The 2B hardening pass added a second
documented discriminating case to each such mutant so it is killed by >=2
independent cases. This gate is the forward tripwire that keeps the door shut.

What it can and cannot check, stated honestly:

  * The exact property — "every mutant is killed by >=2 distinct cases" — is a
    RUNTIME fact (it depends on how ``prove`` judges each corpus element against
    each mutant) and is not statically decidable. Each harness's ``--self-test``
    and the 2B leave-one-out analysis cover that per harness.
  * What IS statically checkable, and what this gate enforces, is the NECESSARY
    precondition: the corpus a harness judges its mutants against must hold >=2
    cases. A corpus of one case cannot possibly catch any mutant on two — every
    mutant is single-load-bearing by construction.

So this gate resolves each required harness's declared ``corpus_size`` to an
integer where it can (``len(<module-level display>)``, a bare int literal, or one
hop through a module constant) and:

  * **OK** — the judged corpus holds >=2 cases;
  * **FRAGILE** — it holds <2 and the harness is not exempt (the tripwire: a new
    harness whose whole corpus is one case is caught before merge);
  * **EXEMPT** — it holds <2 but is an inherently single-scenario harness listed
    in ``FRAGILITY_EXEMPT`` with a reason (``core/iot`` judges one aggregate
    fingerprint, not a list of per-case rows);
  * **UNANALYZABLE** (advisory) — ``corpus_size`` is a dynamic expression with no
    statically countable collection (honest corpora the AST cannot tie down).

Per-mutant inherent single-case defects (e.g. ``core/memory`` ``threshold_boundary``,
a ``>=`` vs ``>`` boundary that can only differ at exactly slope==threshold) are
documented as a fragility waiver in the harness itself, next to the mutant.

This gate is ADVISORY (DP3): its paired test asserts the CHECKER bites on fixtures
and analyses every harness without raising — it does NOT assert repo cleanliness,
so it never red-locks main. Only ``FRAGILE`` or a STALE exempt entry makes a manual
``make fragility`` run exit non-zero.

Usage:
  python tools/fragility_checker.py            # report every non-legacy harness
  python tools/fragility_checker.py --json
  python tools/fragility_checker.py --self-test # prove the checker on its fixtures
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FLAVORS = ("core", "security", "ai")  # pharmacy is legacy (older soft gate); excluded

# The corpus-cardinality floor: every judged corpus must hold at least this many cases.
MIN_CORPUS = 2

# Harnesses whose judged corpus is inherently a single scenario, with the reason. An
# entry here is a reviewed waiver, not a silent skip: the gate verifies the harness
# still exists AND still reads <2 (a stale waiver — harness gone, or grown to >=2 —
# fails, so the exemption cannot outlive the condition that justified it).
FRAGILITY_EXEMPT: dict[str, str] = {
    "harnesses/core/iot_telemetry_test_harness.py":
        "single aggregate-fingerprint scenario by design (corpus_size=1): prove compares "
        "one rolled-up AggResult to AGG_EXPECTED, not a list of per-case rows, so the "
        "mutants are judged against one rich fingerprint rather than N discriminating cases",
}


def _teeth_kwarg(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        # accept both ``TEETH = Teeth(...)`` and the annotated ``TEETH: Teeth = Teeth(...)``
        # — an AnnAssign-blind check would silently skip an annotated harness (the same
        # blind spot fixed in _module_assigns; keep the two consistent).
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Call):
            continue
        is_teeth = (
            (isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "TEETH" for t in node.targets))
            or (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name) and node.target.id == "TEETH")
        )
        if is_teeth:
            for kw in node.value.keywords:
                if kw.arg == name:
                    return kw.value
    return None


def _module_assigns(tree: ast.Module) -> dict[str, ast.AST]:
    """Map each module-level ``NAME = <expr>`` to its value node (last assignment wins).

    Handles both plain ``NAME = ...`` and annotated ``NAME: tuple[...] = ...`` — most
    corpora are declared with a type annotation (an ``ast.AnnAssign``), so missing those
    would read nearly every harness as UNANALYZABLE.
    """
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = node.value
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and node.value is not None):
            out[node.target.id] = node.value
    return out


def _display_len(node: ast.AST) -> int | None:
    """Number of elements if ``node`` is a list/tuple/set/dict DISPLAY literal, else None."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return len(node.elts)
    if isinstance(node, ast.Dict):
        return len(node.keys)
    return None


def _corpus_count(size: ast.AST, assigns: dict[str, ast.AST]) -> int | None:
    """Resolve a ``corpus_size`` expression to an integer element count, or None when it
    is not statically countable.

    Handles ``corpus_size=<int literal>``, ``corpus_size=len(<display or named display>)``,
    and one hop through a module constant (``N = len(CORPUS); corpus_size=N``). Anything
    else — ``sum(...)``, a comprehension, a runtime call, concatenation — is None, the
    honest "advisory, cannot anchor" signal.
    """
    if isinstance(size, ast.Name) and size.id in assigns:
        size = assigns[size.id]  # one hop: corpus_size=_N where _N = len(CORPUS)
    if isinstance(size, ast.Constant) and isinstance(size.value, bool):
        return None
    if isinstance(size, ast.Constant) and isinstance(size.value, int):
        return size.value
    if (isinstance(size, ast.Call) and isinstance(size.func, ast.Name)
            and size.func.id == "len" and len(size.args) == 1):
        arg = size.args[0]
        direct = _display_len(arg)
        if direct is not None:
            return direct
        if isinstance(arg, ast.Name) and arg.id in assigns:
            return _display_len(assigns[arg.id])
    return None


def check_harness(path: Path, exempt: dict[str, str] | None = None) -> dict:
    """Return {'status': OK|FRAGILE|EXEMPT|UNANALYZABLE|NO_TEETH, 'count': int|None,
    'findings': [...]}. ``exempt`` defaults to ``FRAGILITY_EXEMPT`` (overridable for tests)."""
    exempt = FRAGILITY_EXEMPT if exempt is None else exempt
    tree = ast.parse(path.read_text(encoding="utf-8"))
    size = _teeth_kwarg(tree, "corpus_size")
    if size is None:
        return {"status": "NO_TEETH", "count": None, "findings": []}
    count = _corpus_count(size, _module_assigns(tree))
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    if count is None:
        return {"status": "UNANALYZABLE", "count": None,
                "findings": [f"corpus_size is not a statically countable collection: "
                             f"{ast.unparse(size)}"]}
    if count >= MIN_CORPUS:
        return {"status": "OK", "count": count, "findings": []}
    if rel in exempt:
        return {"status": "EXEMPT", "count": count,
                "findings": [f"single-case by design: {exempt[rel]}"]}
    return {"status": "FRAGILE", "count": count,
            "findings": [f"corpus_size={count}: the judged corpus holds <{MIN_CORPUS} cases, so "
                         "no mutant can be caught by two independent cases (every mutant is "
                         "single-load-bearing by construction). Add a 2nd discriminating case "
                         "that catches a real documented failure, or add a FRAGILITY_EXEMPT "
                         "entry with a reason if the harness is inherently single-scenario."]}


def _discover() -> list[Path]:
    out: list[Path] = []
    for flavor in FLAVORS:
        out.extend(sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")))
    return out


def _stale_waivers(discovered_rel: set[str]) -> list[str]:
    """Every FRAGILITY_EXEMPT entry must name a discovered harness that still reads <2 —
    a waiver that outlived its harness or its single-case condition is itself a defect."""
    stale: list[str] = []
    for rel in FRAGILITY_EXEMPT:
        if rel not in discovered_rel:
            stale.append(f"{rel} — exempt entry names no discovered harness (stale waiver)")
            continue
        res = check_harness(ROOT / rel)
        if res["count"] is None or res["count"] >= MIN_CORPUS:
            stale.append(f"{rel} — exempt but corpus_size={res['count']} is not <{MIN_CORPUS}; "
                         "drop the waiver")
    return stale


def run_gate(as_json: bool = False) -> int:
    buckets: dict[str, list[str]] = {k: [] for k in
                                     ("OK", "FRAGILE", "EXEMPT", "UNANALYZABLE", "NO_TEETH")}
    records = []
    discovered = _discover()
    for path in discovered:
        res = check_harness(path)
        rel = path.relative_to(ROOT).as_posix()
        records.append({"harness": rel, **res})
        buckets[res["status"]].append(rel)
        if not as_json and res["status"] in ("FRAGILE", "EXEMPT", "UNANALYZABLE"):
            print(f"  {res['status']:12} {rel}  (corpus_size={res['count']})")
            for finding in res["findings"]:
                print(f"      - {finding}")
    stale = _stale_waivers({p.relative_to(ROOT).as_posix() for p in discovered})
    if as_json:
        print(json.dumps({"records": records, "stale_waivers": stale,
                          "summary": {k.lower(): len(v) for k, v in buckets.items()}}, indent=2))
    else:
        if stale:
            print("\nstale FRAGILITY_EXEMPT entries:", file=sys.stderr)
            for s in stale:
                print(f"  - {s}", file=sys.stderr)
        print(f"\nfragility: {len(buckets['OK'])} ok, {len(buckets['FRAGILE'])} fragile, "
              f"{len(buckets['EXEMPT'])} exempt, {len(buckets['UNANALYZABLE'])} unanalyzable "
              f"(advisory), {len(buckets['NO_TEETH'])} no-teeth.")
    if buckets["FRAGILE"] or stale:
        print("FAIL: a required harness judges its mutants against a single-case corpus (or an "
              "exempt waiver is stale) — give the corpus a 2nd discriminating case.",
              file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the checker bites: a >=2-case fixture reads OK; a single-case fixture reads
    FRAGILE; the same single-case fixture under an exempt entry reads EXEMPT; a dynamic
    corpus_size reads UNANALYZABLE."""
    fx = ROOT / "tools" / "_fragility_fixtures"
    single_rel = "tools/_fragility_fixtures/single_case_harness.py"
    cases = [
        ("robust_harness.py", None, "OK"),
        ("single_case_harness.py", None, "FRAGILE"),
        ("single_case_harness.py", {single_rel: "inherently one scenario (test)"}, "EXEMPT"),
        ("dynamic_harness.py", None, "UNANALYZABLE"),
    ]
    failures = 0
    for name, exempt, want in cases:
        got = check_harness(fx / name, exempt)["status"]
        if got != want:
            failures += 1
            tag = "exempt" if exempt else "default"
            print(f"FAIL: {name} ({tag}) read {got}, expected {want}", file=sys.stderr)
    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (>=2 cases passes; a single-case corpus is caught; an exempt "
          "single-case reads EXEMPT; a dynamic corpus is advisory)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mutant-fragility (corpus-cardinality floor) checker")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="prove the checker on its fixtures")
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    if not args.json:
        print("fragility — every judged corpus must hold >=2 cases (or a reasoned waiver):\n")
    return run_gate(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
