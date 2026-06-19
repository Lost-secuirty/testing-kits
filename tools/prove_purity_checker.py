#!/usr/bin/env python3
"""prove-purity checker — prove that every TEETH ``prove`` is clock/IO/RNG-free.

The TEETH contract says ``prove(impl)`` MUST be pure and deterministic: "seed any
RNG; no network, clock, or filesystem I/O" (``harnesses/_teeth.py``). The swap-check
gate cannot see a violation — a ``prove`` that reads ``time.monotonic()`` still
returns a bool, so ``proof_audit`` stays green. That exact bug shipped once
(Batch 5: ``core/memory``'s teeth path defaulted a timestamp to ``time.monotonic()``)
and was only caught by a human reviewer. This gate makes it machine-checkable.

It is **static** (AST only — no import, no execution): for each non-legacy harness it
finds the function bound to ``TEETH.prove``, walks that function's *within-module call
graph* (so a clock used by an unrelated mock server is NOT flagged — only code reachable
from ``prove``), and flags any reachable call into a clock / RNG / network / filesystem
API. Scoping to the call graph is the whole point: it pinpoints impurity on the proof
path without drowning in the harness's legitimate I/O elsewhere.

Known limits (documented, not hidden): AST cannot follow dynamic dispatch, so a helper
reached only through an instance method (``self.f()``) or a passed-in callable is not
traversed; such a ``prove`` is reported ``unanalyzable`` (advisory), never silently
passed. Seeded ``random.Random(<seed>)`` is allowed; the bare global ``random.*`` API
is not.

Usage:
  python tools/prove_purity_checker.py            # gate every non-legacy harness
  python tools/prove_purity_checker.py --json     # machine-readable findings
  python tools/prove_purity_checker.py --self-test # prove the checker on its fixtures
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

# --- what counts as impure on the proof path -------------------------------------------
# A reachable call is impure if its dotted target matches one of these. Matching is on the
# call's dotted form (e.g. "time.monotonic", "datetime.datetime.now"): the leftmost Name is
# the root, the final attribute is the leaf.
_CLOCK_ROOTS = {"time"}                       # any time.* — clock read or sleep
_CLOCK_LEAVES = {"now", "utcnow", "today", "monotonic", "time", "perf_counter"}
_RNG_ROOT = "random"
_RNG_GLOBAL = {                               # the module-level (unseeded) RNG surface
    "random", "randint", "randrange", "choice", "choices", "shuffle",
    "uniform", "sample", "getrandbits", "betavariate", "gauss", "normalvariate",
    "seed",  # reseeding the global RNG is itself a shared-state side effect
}
_NET_ROOTS = {"socket", "urllib", "http", "ssl", "ftplib", "smtplib", "requests", "httpx"}
_NONDET_ROOTS = {"secrets"}                   # secrets.* — any
_OS_IMPURE = {"urandom", "getpid", "getenv", "system", "popen", "times", "times_ns"}
_UUID_IMPURE = {"uuid1", "uuid4"}
_BUILTIN_IO = {"open", "input"}               # bare builtins that touch fs / stdin


def _dotted(node: ast.AST) -> str | None:
    """Return 'a.b.c' for a Name/Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _impurity(call: ast.Call) -> str | None:
    """Classify a Call as impure on the proof path, or None if pure. Returns a label."""
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in _BUILTIN_IO:
            return f"builtin {func.id}() — filesystem/stdin I/O"
        return None
    dotted = _dotted(func)
    if not dotted:
        return None
    parts = dotted.split(".")
    root, leaf = parts[0], parts[-1]
    if root in _CLOCK_ROOTS:
        return f"{dotted}() — clock/sleep"
    if leaf in _CLOCK_LEAVES and ("datetime" in parts or "date" in parts):
        return f"{dotted}() — wall-clock datetime"
    if root == _RNG_ROOT and leaf in _RNG_GLOBAL:
        return f"{dotted}() — unseeded global RNG (use random.Random(<seed>))"
    if root in _NET_ROOTS:
        return f"{dotted}() — network I/O"
    if root in _NONDET_ROOTS:
        return f"{dotted}() — nondeterministic ({root})"
    if root == "os" and leaf in _OS_IMPURE:
        return f"{dotted}() — os nondeterminism/IO"
    if root == "uuid" and leaf in _UUID_IMPURE:
        return f"{dotted}() — random UUID"
    return None


class _CallCollector(ast.NodeVisitor):
    """Collect Call nodes and the local-function names called, within one function body."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []
        self.local_calls: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        if isinstance(node.func, ast.Name):
            self.local_calls.add(node.func.id)
        self.generic_visit(node)


def _prove_target(tree: ast.Module) -> ast.AST | str | None:
    """Find the function bound to module-level ``TEETH.prove``.

    Returns the FunctionDef/Lambda to analyze, or a str name we could not resolve, or None
    if no TEETH/prove is present.
    """
    prove_ref: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TEETH" for t in node.targets
        ) and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if kw.arg == "prove":
                    prove_ref = kw.value
    if prove_ref is None:
        return None
    if isinstance(prove_ref, ast.Lambda):
        return prove_ref
    if isinstance(prove_ref, ast.Name):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == prove_ref.id:
                return node
        return prove_ref.id  # named but not a module-level def (e.g. a method) — unanalyzable
    return "<expr>"  # an attribute/partial/etc. — unanalyzable


def check_harness(path: Path) -> dict:
    """Return {'status': OK|IMPURE|UNANALYZABLE|NO_TEETH, 'findings':[...]} for one harness."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    target = _prove_target(tree)
    if target is None:
        return {"status": "NO_TEETH", "findings": []}
    if isinstance(target, str):
        return {"status": "UNANALYZABLE", "findings": [f"prove not a module-level def ({target})"]}

    # BFS the within-module call graph from the prove function/lambda.
    findings: list[str] = []
    seen: set[str] = set()
    bodies: list[ast.AST] = [target]
    while bodies:
        node = bodies.pop()
        collector = _CallCollector()
        collector.visit(node)
        for call in collector.calls:
            label = _impurity(call)
            if label:
                where = getattr(call, "lineno", "?")
                fn = node.name if isinstance(node, ast.FunctionDef) else "<lambda>"
                findings.append(f"{path.name}:{where} in {fn}(): {label}")
        for name in collector.local_calls:
            if name in defs and name not in seen:
                seen.add(name)
                bodies.append(defs[name])
    return {"status": "IMPURE" if findings else "OK", "findings": sorted(set(findings))}


def _discover() -> list[Path]:
    out: list[Path] = []
    for flavor in FLAVORS:
        out.extend(sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")))
    return out


def run_gate(as_json: bool = False) -> int:
    impure, unanalyzable, ok, no_teeth = [], [], [], []
    records = []
    for path in _discover():
        res = check_harness(path)
        rel = path.relative_to(ROOT).as_posix()
        records.append({"harness": rel, **res})
        {"IMPURE": impure, "UNANALYZABLE": unanalyzable, "OK": ok, "NO_TEETH": no_teeth}[
            res["status"]
        ].append(rel)
        if not as_json:
            if res["status"] == "IMPURE":
                print(f"  IMPURE        {rel}")
                for f in res["findings"]:
                    print(f"      - {f}")
            elif res["status"] == "UNANALYZABLE":
                print(f"  UNANALYZABLE  {rel}  ({res['findings'][0]})")
    if as_json:
        print(json.dumps({"records": records,
                          "summary": {"ok": len(ok), "impure": len(impure),
                                      "unanalyzable": len(unanalyzable),
                                      "no_teeth": len(no_teeth)}}, indent=2))
    else:
        print(f"\nprove-purity: {len(ok)} pure, {len(impure)} impure, "
              f"{len(unanalyzable)} unanalyzable (advisory), {len(no_teeth)} no-teeth.")
    if impure:
        print("FAIL: a prove() reaches a clock/RNG/network/filesystem call — "
              "prove must judge a frozen corpus deterministically.", file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the checker bites: an impure fixture MUST read IMPURE, a pure one MUST read OK."""
    fx = ROOT / "tools" / "_purity_fixtures"
    failures = 0
    pure = check_harness(fx / "pure_harness.py")
    if pure["status"] != "OK":
        failures += 1
        print(f"FAIL: pure fixture read {pure['status']} {pure['findings']}, expected OK",
              file=sys.stderr)
    impure = check_harness(fx / "impure_harness.py")
    if impure["status"] != "IMPURE":
        failures += 1
        print(f"FAIL: impure fixture read {impure['status']}, expected IMPURE "
              "(the checker did not detect a clock call reachable from prove)", file=sys.stderr)
    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (pure fixture passes; impure fixture is detected via the call graph)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="prove-purity checker")
    parser.add_argument("--json", action="store_true", help="machine-readable findings")
    parser.add_argument("--self-test", action="store_true", help="prove the checker on its fixtures")
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    if not args.json:
        print("prove-purity — walking each prove() call graph for clock/RNG/IO:\n")
    return run_gate(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
