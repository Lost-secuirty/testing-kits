#!/usr/bin/env python3
"""dead-expression checker — flag bare side-effect-free expression statements.

A statement that is *only* an expression of a pure-by-shape kind — a comparison, an
arithmetic/boolean/unary op, a name, an attribute, or a subscript — computes a value and
throws it away. It is almost always a bug: a forgotten ``assert``/assignment, a leftover
line after a refactor, or an operator typo. The repo shipped exactly this twice — a
``sum(...) + m.start()`` line in ``core/mutation`` that computed and discarded a number, and
a since-removed one in ``ai/llm_eval`` (see ``docs/LEARNINGS.md`` and the retro backlog). The
TEETH / proof / purity / circularity gates all stay green on such a line: it changes no
behaviour, it just sits there. This gate makes the dead line itself machine-visible.

It is **static** (AST only — no import, no execution). For each non-legacy harness it walks
the whole module and flags any ``ast.Expr`` statement whose value is one of:

    Name | Attribute | Compare | BinOp | BoolOp | UnaryOp | Subscript | Tuple(*)

A bare ``Tuple`` is flagged only when every element is itself side-effect-free by shape, which
catches the assert-tuple / trailing-comma footgun (``x == 1, "msg"`` or ``foo,`` — a bare tuple
is always truthy/discarded) while leaving an intentional ``(do_a(), do_b())`` alone.

Deliberately **excluded** (never flagged), because they are either legitimate or may carry a
side effect the AST cannot rule out: string/number/`...` constants (docstrings, ellipsis,
sentinel literals), ``Call`` / ``Await`` / ``Yield`` / ``YieldFrom`` (may mutate state), and a
walrus ``NamedExpr`` (binds a name). Attribute and Subscript *can* trip a property or
``__getitem__`` with a side effect, so this gate is **advisory** (it reports, it never blocks a
merge) — but in this harness corpus a bare ``obj.attr`` or ``d[k]`` is overwhelmingly dead code,
and one such Subscript (a no-op ``self._inflight[key]`` in ``core/cache``) was a real leftover.

This is a focused standard-library gate, not a substitute for a full linter: ``ruff``/bugbear
``B018`` is not a required check in this repo (``ruff`` is not the PR gate), so the dead-line
discipline lives here in the proof machinery, dependency-free and tuned to the harnesses.

Usage:
  python tools/dead_expr_checker.py            # scan every non-legacy harness (exit 1 on finds)
  python tools/dead_expr_checker.py --json     # machine-readable findings
  python tools/dead_expr_checker.py --self-test # prove the checker on its fixtures
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

# Expression kinds that, standing alone as a statement, compute a value and discard it with no
# side effect of their own. A bare statement of one of these is dead code. Call/Await/Yield/
# YieldFrom/NamedExpr and every Constant (docstrings, ``...``, literals) are intentionally NOT
# here — they are either side-effecting or legitimate.
_DEAD_VALUE_TYPES = (
    ast.Name,
    ast.Attribute,
    ast.Compare,
    ast.BinOp,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Subscript,
)
# A value is side-effect-free "by shape" if evaluating it runs no user code that could mutate
# state: the dead kinds above plus any Constant. Used to judge a bare tuple — `x == 1, "msg"` and
# `foo,` (the assert-tuple / trailing-comma footgun) are dead, but `(do_a(), do_b())` is not.
_PURE_SHAPE = (*_DEAD_VALUE_TYPES, ast.Constant)


def _dead_kind(value: ast.AST) -> str | None:
    """Name the dead-expression kind for a bare statement value, or None if it may have an effect."""
    if isinstance(value, _DEAD_VALUE_TYPES):
        return type(value).__name__
    if isinstance(value, ast.Tuple) and value.elts and all(isinstance(e, _PURE_SHAPE) for e in value.elts):
        return "Tuple"
    return None


def _snippet(node: ast.AST) -> str:
    """Source-like one-liner for the discarded expression, truncated for the report."""
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse is total for parsed trees on 3.9+
        return f"<{type(node).__name__}>"
    text = " ".join(text.split())
    return text if len(text) <= 80 else text[:77] + "..."


def check_harness(path: Path) -> dict:
    """Return {'status': OK|DEAD_EXPR, 'findings': [...]} for one harness file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    flagged: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr):
            kind = _dead_kind(node.value)
            if kind is None:
                continue
            flagged.append((
                node.lineno,
                node.col_offset,
                f"{path.name}:{node.lineno}: dead expression `{_snippet(node.value)}` "
                f"({kind}) — value computed and discarded",
            ))
    flagged.sort()
    findings = [msg for _, _, msg in flagged]
    return {"status": "DEAD_EXPR" if findings else "OK", "findings": findings}


def _discover() -> list[Path]:
    out: list[Path] = []
    for flavor in FLAVORS:
        out.extend(sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")))
    return out


def run_gate(as_json: bool = False) -> int:
    """Scan every non-legacy harness. Exit 1 if any dead expression is found, else 0.

    The exit code makes ``make dead_expr`` a real local gate; the gate is kept *advisory* in CI
    by wiring (no required test asserts repo-wide cleanliness — only the checker's own fixtures).
    """
    buckets: dict[str, list[str]] = {"OK": [], "DEAD_EXPR": []}
    records = []
    for path in _discover():
        res = check_harness(path)
        rel = path.relative_to(ROOT).as_posix()
        records.append({"harness": rel, **res})
        buckets[res["status"]].append(rel)
        if not as_json and res["status"] == "DEAD_EXPR":
            print(f"  DEAD_EXPR  {rel}")
            for finding in res["findings"]:
                print(f"      - {finding}")
    if as_json:
        print(json.dumps({"records": records,
                          "summary": {k.lower(): len(v) for k, v in buckets.items()}}, indent=2))
    else:
        print(f"\ndead-expr: {len(buckets['OK'])} clean, {len(buckets['DEAD_EXPR'])} with dead "
              "expressions.")
    if buckets["DEAD_EXPR"]:
        print("FAIL: a harness has a bare side-effect-free expression statement — assign it, "
              "assert on it, or delete it.", file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the checker bites: a clean fixture reads OK with no findings; a dead fixture is
    flagged once per planted bare expression and never flags its docstring/ellipsis/call lines."""
    fx = ROOT / "tools" / "_dead_expr_fixtures"
    failures = 0

    clean = check_harness(fx / "clean_harness.py")
    if clean["status"] != "OK" or clean["findings"]:
        failures += 1
        print(f"FAIL: clean_harness.py read {clean['status']} with {len(clean['findings'])} "
              "finding(s), expected OK / 0", file=sys.stderr)

    dead = check_harness(fx / "dead_harness.py")
    expected = 8  # one per flagged kind: Name/Attribute/Compare/BinOp/BoolOp/UnaryOp/Subscript/Tuple
    if dead["status"] != "DEAD_EXPR" or len(dead["findings"]) != expected:
        failures += 1
        print(f"FAIL: dead_harness.py read {dead['status']} with {len(dead['findings'])} "
              f"finding(s), expected DEAD_EXPR / {expected}", file=sys.stderr)
        for finding in dead["findings"]:
            print(f"      - {finding}", file=sys.stderr)

    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (clean passes; each bare Name/Attr/Compare/BinOp/BoolOp/UnaryOp/"
          "Subscript and a pure-element Tuple are caught; docstrings, `...`, calls, and a "
          "tuple holding a call are not)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dead-expression checker (advisory)")
    parser.add_argument("--json", action="store_true", help="machine-readable findings")
    parser.add_argument("--self-test", action="store_true", help="prove the checker on its fixtures")
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    if not args.json:
        print("dead-expr — scanning each harness for bare side-effect-free expression statements:\n")
    return run_gate(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
