#!/usr/bin/env python3
"""prove-circularity checker — prove that no TEETH ``prove`` calls its own oracle at runtime.

The non-circularity discipline is the load-bearing one: ``prove(impl)`` must judge ``impl``
against a FROZEN LITERAL corpus, NOT by calling the oracle at runtime to recompute the
expected answer. A circular prove (``return impl(x) != oracle(x)``) satisfies the swap-check
gate anyway — ``prove(oracle)`` is False and ``prove(mutant)`` is True — so ``proof_audit``
cannot catch it. The only existing guard is a human reading every prove() plus a per-harness
flip-a-literal test. This makes the structural half machine-checkable.

It is **static** (AST only): for each non-legacy harness it finds the function bound to
``TEETH.prove`` and the NAME bound to ``TEETH.oracle``, walks prove's within-module call
graph (module-level + nested helpers, a module-level def winning a name collision), and flags
the harness CIRCULAR if any reachable call targets the oracle by name. The gate's worth is
mostly forward-looking — it is the structural tripwire for new harnesses (#78+) so a circular
prove can never be introduced silently.

Limits (documented): the oracle bound to an inline ``lambda`` has no name to call, so such a
harness is reported ``no-named-oracle`` (advisory — circularity-by-name is N/A; the human
flip-a-literal test still covers it). AST cannot follow dynamic dispatch, so an oracle reached
only through an instance method or a passed-in callable is not detected.

Usage:
  python tools/prove_circularity_checker.py            # gate every non-legacy harness
  python tools/prove_circularity_checker.py --json
  python tools/prove_circularity_checker.py --self-test # prove the checker on its fixtures
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


def _dotted(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


class _Calls(ast.NodeVisitor):
    """Collect every Call's func node within one function body, not recursing into nested defs."""

    def __init__(self) -> None:
        self.funcs: list[ast.AST] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.funcs.append(node.func)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)


def _teeth_kwarg(tree: ast.Module, name: str) -> ast.AST | None:
    """Return the AST node passed as ``name=`` to the module-level ``TEETH = Teeth(...)`` call."""
    found: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TEETH" for t in node.targets
        ) and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if kw.arg == name:
                    found = kw.value
    return found


def _prove_target(tree: ast.Module) -> ast.AST | str | None:
    prove_ref = _teeth_kwarg(tree, "prove")
    if prove_ref is None:
        return None
    if isinstance(prove_ref, ast.Lambda):
        return prove_ref
    if isinstance(prove_ref, ast.Name):
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == prove_ref.id:
                return node
        return prove_ref.id
    return "<expr>"


def _oracle_ref(tree: ast.Module) -> tuple[str, object] | None:
    """How the oracle is bound: ('name','oracle_fn') for a bare function, ('attr',['Cls','m'])
    for a method. A circular prove must call THIS exact reference — matching only a leaf name
    would conflate e.g. the oracle `Propagator.inject` with a prove PARAMETER also named
    `inject` (the impl under test). None when the oracle is an inline lambda (no name to call).
    """
    ref = _teeth_kwarg(tree, "oracle")
    if isinstance(ref, ast.Name):
        return ("name", ref.id)
    if isinstance(ref, ast.Attribute):
        parts = _dotted(ref)
        return ("attr", parts) if parts else None
    return None  # lambda or expression — no name to call


def _params(fn: ast.AST) -> set[str]:
    if not isinstance(fn, (ast.FunctionDef, ast.Lambda)):
        return set()
    a = fn.args
    names = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def check_harness(path: Path) -> dict:
    """Return {'status': OK|CIRCULAR|UNANALYZABLE|NO_NAMED_ORACLE|NO_TEETH, 'findings':[...]}"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = _prove_target(tree)
    if target is None:
        return {"status": "NO_TEETH", "findings": []}
    if isinstance(target, str):
        return {"status": "UNANALYZABLE", "findings": [f"prove not a module-level def ({target})"]}
    oracle = _oracle_ref(tree)
    if oracle is None:
        return {"status": "NO_NAMED_ORACLE", "findings": ["oracle is an inline lambda — N/A"]}
    kind, ref = oracle
    label = ref if kind == "name" else ".".join(ref)

    nested = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    module_fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    defs_map = {**nested, **module_fns}

    findings: list[str] = []
    seen: set[str] = set()
    queue: list[ast.AST] = [target]
    while queue:
        fn = queue.pop()
        fn_params = _params(fn)  # scoped to THIS function: a call to its own param is the impl,
        collector = _Calls()     # not the oracle. NOT cumulative — one helper's param must not
        for stmt in (fn.body if isinstance(fn, ast.FunctionDef) else [fn.body]):  # mask detection
            collector.visit(stmt)                                                  # elsewhere.
        for func in collector.funcs:
            parts = _dotted(func)
            if kind == "attr":
                hit = parts == ref                       # must call the exact Cls.method oracle
            else:  # ("name", ref): a bare call to the oracle fn, not a same-named parameter
                hit = isinstance(func, ast.Name) and func.id == ref and ref not in fn_params
            if hit:
                line = getattr(func, "lineno", "?")
                where = fn.name if isinstance(fn, ast.FunctionDef) else "<lambda>"
                findings.append(f"{path.name}:{line} in {where}(): calls the oracle '{label}()' "
                                "at runtime — prove must compare against frozen literals")
            if isinstance(func, ast.Name) and func.id in defs_map and func.id not in seen:
                seen.add(func.id)
                queue.append(defs_map[func.id])
    return {"status": "CIRCULAR" if findings else "OK", "findings": sorted(set(findings))}


def _discover() -> list[Path]:
    out: list[Path] = []
    for flavor in FLAVORS:
        out.extend(sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")))
    return out


def run_gate(as_json: bool = False) -> int:
    buckets: dict[str, list[str]] = {
        "OK": [], "CIRCULAR": [], "UNANALYZABLE": [], "NO_NAMED_ORACLE": [], "NO_TEETH": []}
    records = []
    for path in _discover():
        res = check_harness(path)
        rel = path.relative_to(ROOT).as_posix()
        records.append({"harness": rel, **res})
        buckets[res["status"]].append(rel)
        if not as_json and res["status"] == "CIRCULAR":
            print(f"  CIRCULAR  {rel}")
            for f in res["findings"]:
                print(f"      - {f}")
    if as_json:
        print(json.dumps({"records": records,
                          "summary": {k.lower(): len(v) for k, v in buckets.items()}}, indent=2))
    else:
        print(f"\nprove-circularity: {len(buckets['OK'])} ok, {len(buckets['CIRCULAR'])} circular, "
              f"{len(buckets['UNANALYZABLE'])} unanalyzable, "
              f"{len(buckets['NO_NAMED_ORACLE'])} inline-lambda-oracle (advisory), "
              f"{len(buckets['NO_TEETH'])} no-teeth.")
    if buckets["UNANALYZABLE"] and not as_json:
        for rel in buckets["UNANALYZABLE"]:
            print(f"  UNANALYZABLE  {rel}", file=sys.stderr)
    if buckets["CIRCULAR"] or buckets["UNANALYZABLE"]:
        # UNANALYZABLE blocks too: a prove that can't be statically analyzed (not a module-level
        # def) could hide a circular call, so it must not silently pass the required gate. The
        # fix is to keep prove a module-level function. (An inline-lambda ORACLE stays advisory —
        # it has no name to call, so circularity-by-name is genuinely N/A, not an escape.)
        print("FAIL: a prove() calls its oracle at runtime, or could not be analyzed — prove must "
              "be a module-level function that compares against frozen literals.", file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the checker bites: a non-circular fixture reads OK; a circular one (prove calls the
    oracle through a helper) reads CIRCULAR."""
    fx = ROOT / "tools" / "_circularity_fixtures"
    failures = 0
    for name, want in {"clean_harness.py": "OK", "circular_harness.py": "CIRCULAR"}.items():
        got = check_harness(fx / name)["status"]
        if got != want:
            failures += 1
            print(f"FAIL: {name} read {got}, expected {want}", file=sys.stderr)
    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (clean prove passes; a prove that calls its oracle is caught)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="prove-circularity checker")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="prove the checker on its fixtures")
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    if not args.json:
        print("prove-circularity — walking each prove() call graph for runtime oracle calls:\n")
    return run_gate(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
