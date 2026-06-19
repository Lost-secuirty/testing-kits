#!/usr/bin/env python3
"""prove-purity checker — prove that every TEETH ``prove`` is clock/IO/RNG-free.

The TEETH contract says ``prove(impl)`` MUST be pure and deterministic: "seed any
RNG; no network, clock, or filesystem I/O" (``harnesses/_teeth.py``). The swap-check
gate cannot see a violation — a ``prove`` that reads ``time.monotonic()`` still
returns a bool, so ``proof_audit`` stays green. That exact bug shipped once
(Batch 5: ``core/memory``'s teeth path defaulted a timestamp to ``time.monotonic()``)
and was only caught by a human reviewer. This gate makes it machine-checkable.

It is **static** (AST only — no import, no execution): for each non-legacy harness it
finds the MODULE-LEVEL function bound to ``TEETH.prove``, walks that function's
*within-module call graph* (so a clock used by an unrelated mock server is NOT flagged —
only code reachable from ``prove``), and flags any reachable call into a clock / RNG /
network / filesystem API. Both call forms are detected: attribute (``time.monotonic()``)
AND imported aliases (``from time import monotonic; monotonic()``), via an import map.

Soundness over false-confidence: a reachable call that cannot be resolved to a module
function, a known pure builtin, an import, or a callable parameter is treated as
``unanalyzable`` (advisory) — never silently counted as pure. AST cannot follow dynamic
dispatch, so a prove reachable only through an instance method is reported unanalyzable
too. Seeded ``random.Random(<seed>)`` is allowed; the bare global ``random.*`` is not.

Usage:
  python tools/prove_purity_checker.py            # gate every non-legacy harness
  python tools/prove_purity_checker.py --json     # machine-readable findings
  python tools/prove_purity_checker.py --self-test # prove the checker on its fixtures
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FLAVORS = ("core", "security", "ai")  # pharmacy is legacy (older soft gate); excluded

# --- what counts as impure on the proof path -------------------------------------------
# Matching is on a call's dotted form after the leftmost name is resolved through the
# module's import map (so `t.monotonic` with `import time as t`, and the bare `monotonic`
# from `from time import monotonic`, both normalize to "time.monotonic").
_CLOCK_ROOTS = {"time"}                        # any time.* — clock read or sleep
_CLOCK_LEAVES = {"now", "utcnow", "today", "monotonic", "time", "perf_counter"}
_RNG_ROOT = "random"
_RNG_GLOBAL = {                                # the module-level (unseeded) RNG surface
    "random", "randint", "randrange", "choice", "choices", "shuffle",
    "uniform", "sample", "getrandbits", "betavariate", "gauss", "normalvariate",
    "seed",  # reseeding the global RNG is itself a shared-state side effect
}
_NET_ROOTS = {"socket", "urllib", "http", "ssl", "ftplib", "smtplib", "requests", "httpx"}
_NONDET_ROOTS = {"secrets"}                    # secrets.* — any
_OS_IMPURE = {
    "urandom", "getpid", "getenv", "system", "popen", "times", "times_ns",
    "listdir", "scandir", "walk", "stat", "lstat", "remove", "rename", "replace",
    "makedirs", "removedirs", "access", "chmod", "chdir", "getcwd",
    "exists", "isfile", "isdir", "getsize", "getmtime", "getctime",  # os.path.* reads
}
_UUID_IMPURE = {"uuid1", "uuid4"}
_BUILTIN_IO = {"open", "input"}                # bare builtins that touch fs / stdin
_ESCAPE = {"eval", "exec", "compile", "__import__", "breakpoint"}  # dynamic-execution escapes
# Filesystem I/O methods, matched on the LEAF attr regardless of receiver — the receiver is
# usually a pathlib.Path instance (or a Path-returning call) the AST can't type. These names
# are distinctive enough that a false positive is unlikely.
_FS_METHODS = {
    "read_text", "write_text", "read_bytes", "write_bytes", "open",
    "unlink", "mkdir", "rmdir", "touch", "iterdir", "glob", "rglob",
    "exists", "is_file", "is_dir", "is_symlink", "stat", "lstat", "samefile",
}
# Every builtin is deterministic and side-effect-free EXCEPT the two I/O ones, which are
# flagged explicitly above. Deriving from `builtins` also whitelists every exception name
# (ValueError, KeyError, StopIteration, ...) a pure prove legitimately constructs/raises.
_SAFE_BUILTINS = frozenset(dir(builtins)) - _BUILTIN_IO - _ESCAPE


def _dotted(node: ast.AST) -> list[str] | None:
    """Return ['time','monotonic'] for a Name/Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


def _collect_imports(tree: ast.Module) -> dict[str, str]:
    """Map each locally-bound import name to its dotted origin.

    `import time`           -> {'time': 'time'}
    `import os.path as p`    -> {'p': 'os.path'}
    `from time import monotonic`        -> {'monotonic': 'time.monotonic'}
    `from time import monotonic as mono`-> {'mono': 'time.monotonic'}

    Collected module-wide (ast.walk), so a function-LOCAL impure import (e.g. a clock imported
    inside prove itself) is still caught. The rare cost is that an alias bound only in another
    scope is treated as global — an over-approximation that biases toward flagging, never toward
    missing, which is the safe direction for a purity gate.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                if a.name != "*":
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _classify(parts: list[str]) -> str | None:
    """Classify a resolved dotted call (e.g. ['time','monotonic']) as impure, else None."""
    root, leaf = parts[0], parts[-1]
    if root in _CLOCK_ROOTS:
        return f"{'.'.join(parts)}() — clock/sleep"
    if leaf in _CLOCK_LEAVES and ("datetime" in parts or "date" in parts):
        return f"{'.'.join(parts)}() — wall-clock datetime"
    if root == _RNG_ROOT and leaf in _RNG_GLOBAL:
        return f"{'.'.join(parts)}() — unseeded global RNG (use random.Random(<seed>))"
    if root in _NET_ROOTS:
        return f"{'.'.join(parts)}() — network I/O"
    if root in _NONDET_ROOTS:
        return f"{'.'.join(parts)}() — nondeterministic ({root})"
    if root == "os" and leaf in _OS_IMPURE:
        return f"{'.'.join(parts)}() — os nondeterminism/IO"
    if root == "uuid" and leaf in _UUID_IMPURE:
        return f"{'.'.join(parts)}() — random UUID"
    return None


def _resolve(parts: list[str], aliases: dict[str, str]) -> list[str]:
    """Rewrite the leftmost name through the import map: ['t','monotonic'] -> ['time','monotonic']
    given `import time as t`, and ['monotonic'] -> ['time','monotonic'] given a from-import."""
    head = aliases.get(parts[0])
    if head is None:
        return parts
    return head.split(".") + parts[1:]


class _Calls(ast.NodeVisitor):
    """Collect every Call's func node within one function body (descends into comprehensions
    and nested expressions, but not into nested def/lambda bodies — those have their own scope)."""

    def __init__(self) -> None:
        self.funcs: list[ast.AST] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.funcs.append(node.func)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # don't recurse into nested defs; the graph walk handles module-level ones

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)  # a lambda shares the enclosing scope's calls


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


def _prove_target(tree: ast.Module) -> ast.AST | str | None:
    """Find the MODULE-LEVEL function bound to ``TEETH.prove`` (or a lambda)."""
    prove_ref: ast.AST | None = None
    for node in tree.body:  # module scope only
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
        for node in tree.body:  # resolve against module-level defs only
            if isinstance(node, ast.FunctionDef) and node.name == prove_ref.id:
                return node
        return prove_ref.id  # bound to a non-module-level name (e.g. a method) — unanalyzable
    return "<expr>"          # an attribute/partial/etc. — unanalyzable


def check_harness(path: Path) -> dict:
    """Return {'status': OK|IMPURE|UNANALYZABLE|NO_TEETH, 'findings':[...]} for one harness."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _collect_imports(tree)
    # Helper-resolution map: every FunctionDef (incl. nested closures like `inner`/`cents`),
    # but a module-level def always wins a name collision so a same-named nested function in
    # an unrelated mock server can never cause a false IMPURE on the proof path.
    nested = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    module_fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    defs_map = {**nested, **module_fns}
    module_classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    target = _prove_target(tree)
    if target is None:
        return {"status": "NO_TEETH", "findings": []}
    if isinstance(target, str):
        return {"status": "UNANALYZABLE", "findings": [f"prove not a module-level def ({target})"]}

    findings: list[str] = []
    unresolved: set[str] = set()
    seen: set[str] = set()
    params: set[str] = set()
    queue: list[ast.AST] = [target]
    while queue:
        fn = queue.pop()
        params |= _params(fn)
        collector = _Calls()
        for stmt in (fn.body if isinstance(fn, ast.FunctionDef) else [fn.body]):
            collector.visit(stmt)
        for func in collector.funcs:
            line = getattr(func, "lineno", "?")
            where = fn.name if isinstance(fn, ast.FunctionDef) else "<lambda>"
            label: str | None = None
            if isinstance(func, ast.Attribute):
                parts = _dotted(func)                       # None if receiver isn't a Name chain
                if parts:
                    label = _classify(_resolve(parts, aliases))
                if label is None and func.attr in _FS_METHODS:
                    label = f".{func.attr}() — filesystem I/O"  # catches Path(x).read_text()
                # else a pure external/method (json.dumps, a method on a corpus object) — ok
            elif isinstance(func, ast.Name):
                name = func.id
                resolved = _resolve([name], aliases)        # imported alias -> dotted origin
                if len(resolved) > 1:
                    label = _classify(resolved)             # e.g. `from time import monotonic`
                if label is None:
                    if name in _ESCAPE:
                        label = f"{name}() — dynamic code execution (escapes static analysis)"
                    elif name in _BUILTIN_IO:
                        label = f"builtin {name}() — I/O"
                    elif name in defs_map and name not in seen:
                        seen.add(name)
                        queue.append(defs_map[name])
                        continue
                    elif (name in defs_map or name in params or name in _SAFE_BUILTINS
                          or name in module_classes or name in aliases):
                        continue  # resolved-safe: local fn (queued), param, builtin, class ctor, import
                    else:
                        unresolved.add(name)
                        continue
            else:
                # func is a Call/Subscript/etc. — a dynamic target AST can't name. Never silently
                # pure: report it so the harness reads UNANALYZABLE, not OK.
                unresolved.add(f"<dynamic call @ line {line}>")
                continue
            if label:
                findings.append(f"{path.name}:{line} in {where}(): {label}")

    if findings:
        return {"status": "IMPURE", "findings": sorted(set(findings))}
    if unresolved:
        return {"status": "UNANALYZABLE",
                "findings": [f"unresolved reachable call(s): {', '.join(sorted(unresolved))}"]}
    return {"status": "OK", "findings": []}


def _discover() -> list[Path]:
    out: list[Path] = []
    for flavor in FLAVORS:
        out.extend(sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")))
    return out


def run_gate(as_json: bool = False) -> int:
    buckets: dict[str, list[str]] = {"OK": [], "IMPURE": [], "UNANALYZABLE": [], "NO_TEETH": []}
    records = []
    for path in _discover():
        res = check_harness(path)
        rel = path.relative_to(ROOT).as_posix()
        records.append({"harness": rel, **res})
        buckets[res["status"]].append(rel)
        if not as_json and res["status"] in ("IMPURE", "UNANALYZABLE"):
            tag = "IMPURE      " if res["status"] == "IMPURE" else "UNANALYZABLE"
            print(f"  {tag}  {rel}")
            for f in res["findings"]:
                print(f"      - {f}")
    if as_json:
        print(json.dumps({"records": records,
                          "summary": {k.lower(): len(v) for k, v in buckets.items()}}, indent=2))
    else:
        print(f"\nprove-purity: {len(buckets['OK'])} pure, {len(buckets['IMPURE'])} impure, "
              f"{len(buckets['UNANALYZABLE'])} unanalyzable (advisory), "
              f"{len(buckets['NO_TEETH'])} no-teeth.")
    if buckets["IMPURE"]:
        print("FAIL: a prove() reaches a clock/RNG/network/filesystem call — "
              "prove must judge a frozen corpus deterministically.", file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the checker bites: pure -> OK; an impure prove (via a helper) and an impure prove
    using an IMPORTED-ALIAS clock both -> IMPURE (so neither call form can slip past)."""
    fx = ROOT / "tools" / "_purity_fixtures"
    failures = 0
    expect = {"pure_harness.py": "OK", "impure_harness.py": "IMPURE",
              "aliased_harness.py": "IMPURE", "escape_io_harness.py": "IMPURE"}
    for name, want in expect.items():
        got = check_harness(fx / name)["status"]
        if got != want:
            failures += 1
            print(f"FAIL: {name} read {got}, expected {want}", file=sys.stderr)
    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (pure passes; dotted/aliased clock, eval-escape, and pathlib I/O all detected)")
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
