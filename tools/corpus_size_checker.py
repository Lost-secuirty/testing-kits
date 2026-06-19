#!/usr/bin/env python3
"""corpus-size checker — prove that a harness's declared ``corpus_size`` counts the
collection its ``prove`` actually judges, not an unrelated input.

``Teeth.corpus_size`` is the advertised breadth of a proof — "this oracle is judged
against N frozen cases." ``_teeth.verify`` only checks ``corpus_size >= 1``, so a number
that *overstates* the proof sails through every other gate. That shipped once: ``core/iot``
declared ``corpus_size=len(STREAM)`` (19 raw telemetry messages) while ``prove`` makes a
single aggregate comparison — one scenario dressed up as nineteen. This gate makes that
machine-checkable.

A naive "``corpus_size == len(corpus)``" check would be vacuous: **every** harness already
writes ``corpus_size=len(<CORPUS>)``, so the equality holds by construction. The real
question is whether the counted collection is the one the verdict depends on. So this gate is
**static** (AST only) and anchors the count to the proof path:

  * resolve ``corpus_size`` to the collection name(s) it is built from — unwrap ``len(X)`` /
    ``sum(.. for _ in X ..)`` and follow a one-hop module constant (``N = len(X)``);
  * walk ``prove``'s within-module call graph and collect the names it ITERATES (``for``/
    comprehension) or COMPARES against (``==`` / ``!=`` operands), resolving local aliases
    (``rpcs = RPCS``) — but NOT names merely passed as call arguments (``impl(STREAM)`` does
    not anchor ``STREAM``);
  * **OK** if the counted collection is among those anchors; **MISLABELED** if ``prove`` anchors
    on some module-level collection but not this one (iot: counts ``STREAM``, compares
    ``AGG_EXPECTED``); **UNANALYZABLE** (advisory) if the size is a dynamic expression
    (``len(runner._combos(...))``) or ``prove`` has no static anchor — honest corpora the AST
    cannot tie down, never failed.

Only MISLABELED fails the gate. The forward value is the tripwire: a new harness that counts
its input instead of its judged corpus is caught before merge.

Usage:
  python tools/corpus_size_checker.py            # gate every non-legacy harness
  python tools/corpus_size_checker.py --json
  python tools/corpus_size_checker.py --self-test # prove the checker on its fixtures
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

# Names that are callables/builtins, never the corpus collection itself.
_NOT_A_CORPUS = {
    "len", "sum", "int", "float", "abs", "min", "max", "range", "enumerate", "zip",
    "set", "frozenset", "sorted", "tuple", "list", "dict", "map", "filter", "all", "any",
}


def _root_name(node: ast.AST) -> str | None:
    """Root identifier of a Name / Attribute / Subscript chain, else None."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _names(node: ast.AST) -> set[str]:
    """Every identifier referenced inside an expression, minus builtins/callables."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} - _NOT_A_CORPUS


def _passthrough_names(node: ast.AST) -> set[str] | None:
    """Names an assignment RHS simply *forwards* (so the target is an alias of them), or None
    if the RHS computes a new value. ``a = b`` / ``a = b if c else d`` / ``a = b or c`` are
    pass-throughs; ``a = f(b)`` / ``a = b + 1`` are NOT (the result is not an alias of ``b``)."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Constant):
        return set()
    if isinstance(node, ast.IfExp):
        body, orelse = _passthrough_names(node.body), _passthrough_names(node.orelse)
        return None if body is None or orelse is None else body | orelse
    if isinstance(node, ast.BoolOp):
        out: set[str] = set()
        for value in node.values:
            part = _passthrough_names(value)
            if part is None:
                return None
            out |= part
        return out
    return None


def _teeth_kwarg(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TEETH" for t in node.targets
        ) and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if kw.arg == name:
                    return kw.value
    return None


def _module_assigns(tree: ast.Module) -> dict[str, ast.AST]:
    """Map each module-level ``NAME = <expr>`` to its value node (last assignment wins)."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = node.value
    return out


def _prove_def(tree: ast.Module) -> ast.AST | str | None:
    ref = _teeth_kwarg(tree, "prove")
    if ref is None:
        return None
    if isinstance(ref, ast.Name):
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == ref.id:
                return node
        return ref.id
    return "<expr>"  # lambda / attribute — unanalyzable


def _size_collections(size: ast.AST, assigns: dict[str, ast.AST]) -> tuple[set[str], bool]:
    """Resolve the ``corpus_size`` expression to the collection name(s) it counts.

    Returns ``(names, dynamic)``. ``dynamic`` is True when the size cannot be tied to a plain
    collection name (a bare literal, or ``len()``/``sum()`` over a Call/expr such as
    ``len(runner._combos(...))``) — the honest "I can't anchor this" signal.
    """
    # follow one hop through a module constant: corpus_size=_TEETH_CORPUS_SIZE -> len(...)
    if isinstance(size, ast.Name) and size.id in assigns:
        size = assigns[size.id]

    if isinstance(size, ast.Call) and isinstance(size.func, ast.Name):
        if size.func.id == "len" and size.args:
            arg = size.args[0]
            if isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
                root = _root_name(arg)
                return ({root}, False) if root else (set(), True)
            return (set(), True)  # len(<comprehension/call/...>) — dynamic
        if size.func.id == "sum":
            names: set[str] = set()
            dynamic = False
            for arg in size.args:
                if isinstance(arg, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
                    for gen in arg.generators:
                        root = _root_name(gen.iter)
                        if root and root not in _NOT_A_CORPUS:
                            names.add(root)
                        else:
                            dynamic = True
                else:
                    dynamic = True
            return (names, dynamic and not names)
    return (set(), True)  # bare literal or anything else — not statically anchorable


class _Walk(ast.NodeVisitor):
    """Within one function body, collect call targets (for the BFS), iteration/comparison
    anchor names, and local ``a = b`` aliases. Does not recurse into nested defs."""

    def __init__(self) -> None:
        self.calls: list[ast.AST] = []
        self.anchors: set[str] = set()
        self.aliases: dict[str, set[str]] = {}

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node.func)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.anchors |= _names(node.iter)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.anchors |= _names(node.iter)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.anchors |= _names(node.left)
        for cmp in node.comparators:
            self.anchors |= _names(cmp)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            forwarded = _passthrough_names(node.value)  # None unless RHS is a pure name passthrough
            if forwarded:
                self.aliases.setdefault(node.targets[0].id, set()).update(forwarded - _NOT_A_CORPUS)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)


def _prove_anchors(tree: ast.Module, prove: ast.FunctionDef) -> set[str]:
    """Names ``prove`` (and its module-level helpers) iterate or compare, alias-resolved."""
    module_fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    seen: set[str] = set()
    queue: list[ast.FunctionDef] = [prove]
    raw: set[str] = set()
    aliases: dict[str, set[str]] = {}
    while queue:
        fn = queue.pop()
        walk = _Walk()
        for stmt in fn.body:
            walk.visit(stmt)
        raw |= walk.anchors
        for name, vals in walk.aliases.items():
            aliases.setdefault(name, set()).update(vals)
        for func in walk.calls:
            if isinstance(func, ast.Name) and func.id in module_fns and func.id not in seen:
                seen.add(func.id)
                queue.append(module_fns[func.id])
    # expand anchors through local aliases (rpcs -> {RPCS, rpcs}) to a fixpoint — alias growth is
    # monotonic over a finite name set, so this terminates regardless of chain length.
    resolved = set(raw)
    grew = True
    while grew:
        grew = False
        for name in list(resolved):
            for alias in aliases.get(name, set()):
                if alias not in resolved:
                    resolved.add(alias)
                    grew = True
    return resolved


def check_harness(path: Path) -> dict:
    """Return {'status': OK|MISLABELED|UNANALYZABLE|NO_TEETH, 'findings':[...]}"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    size = _teeth_kwarg(tree, "corpus_size")
    prove = _prove_def(tree)
    if size is None or prove is None:
        return {"status": "NO_TEETH", "findings": []}
    if isinstance(prove, str):
        return {"status": "UNANALYZABLE", "findings": [f"prove not a module-level def ({prove})"]}

    counted, dynamic = _size_collections(size, _module_assigns(tree))
    if dynamic or not counted:
        return {"status": "UNANALYZABLE",
                "findings": ["corpus_size is a dynamic/literal expression with no anchorable "
                             f"collection: {ast.unparse(size)}"]}
    anchors = _prove_anchors(tree, prove)
    if counted & anchors:
        return {"status": "OK", "findings": []}
    if anchors:
        return {"status": "MISLABELED",
                "findings": [f"corpus_size counts {sorted(counted)} but prove() iterates/compares "
                             f"{sorted(anchors)} — the declared size is decoupled from the judged "
                             "corpus (count an input the verdict ignores?)"]}
    return {"status": "UNANALYZABLE",
            "findings": [f"corpus_size counts {sorted(counted)} but prove() has no static "
                         "iteration/comparison anchor (dynamic verdict)"]}


def _discover() -> list[Path]:
    out: list[Path] = []
    for flavor in FLAVORS:
        out.extend(sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")))
    return out


def run_gate(as_json: bool = False) -> int:
    buckets: dict[str, list[str]] = {"OK": [], "MISLABELED": [], "UNANALYZABLE": [], "NO_TEETH": []}
    records = []
    for path in _discover():
        res = check_harness(path)
        rel = path.relative_to(ROOT).as_posix()
        records.append({"harness": rel, **res})
        buckets[res["status"]].append(rel)
        if not as_json and res["status"] in ("MISLABELED", "UNANALYZABLE"):
            print(f"  {res['status']:12} {rel}")
            for finding in res["findings"]:
                print(f"      - {finding}")
    if as_json:
        print(json.dumps({"records": records,
                          "summary": {k.lower(): len(v) for k, v in buckets.items()}}, indent=2))
    else:
        print(f"\ncorpus-size: {len(buckets['OK'])} anchored, {len(buckets['MISLABELED'])} mislabeled, "
              f"{len(buckets['UNANALYZABLE'])} unanalyzable (advisory), {len(buckets['NO_TEETH'])} no-teeth.")
    if buckets["MISLABELED"]:
        print("FAIL: a harness's corpus_size counts a collection its prove() never judges — "
              "count the iterated/compared corpus, not an input.", file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the checker bites: an anchored fixture reads OK; one whose corpus_size counts an
    input the verdict ignores reads MISLABELED; a dynamic-corpus fixture reads UNANALYZABLE."""
    fx = ROOT / "tools" / "_corpus_size_fixtures"
    expect = {"anchored_harness.py": "OK", "mislabeled_harness.py": "MISLABELED",
              "dynamic_harness.py": "UNANALYZABLE"}
    failures = 0
    for name, want in expect.items():
        got = check_harness(fx / name)["status"]
        if got != want:
            failures += 1
            print(f"FAIL: {name} read {got}, expected {want}", file=sys.stderr)
    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (anchored corpus passes; counting an unjudged input is caught; a "
          "dynamic corpus is advisory)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="corpus-size anchor checker")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="prove the checker on its fixtures")
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    if not args.json:
        print("corpus-size — anchoring each declared corpus_size to its prove() call graph:\n")
    return run_gate(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
