#!/usr/bin/env python3
"""
complexity_test_harness.py — Flag code bloat / complexity the way Sonar does.
=============================================================================

Pure-stdlib. Zero external dependencies. In-process oracle (binds no port).

WHY THIS EXISTS
---------------
Functional coding benchmarks (SWE-bench et al.) only prove code *runs and
passes a test*. They say nothing about whether it is maintainable. Sonar's
2025-26 LLM-leaderboard study found that as models "improve" on pass-rate they
quietly regress on quality: more lines of code for the same task (bloat) and
higher cyclomatic + cognitive complexity (tech debt a human has to live with).

This harness operationalizes that finding as an automatable gate. Point it at a
Python file (e.g. AI-generated code, or your own) and it reports, per function:

  * cyclomatic complexity  — McCabe: 1 + number of decision points. Counts the
    independent paths through the function; high values need more tests to
    cover and are easy to break.
  * cognitive complexity   — an approximation of Sonar's metric: like
    cyclomatic, but it *penalizes nesting* and rewards flat code, because that
    is what is actually hard for a human to hold in their head.
  * length (physical lines), parameter count, and max nesting depth — the
    cheap bloat signals.

It then flags any function that exceeds the configured thresholds, and exits
non-zero in --target gate mode so CI / an agent loop can block the commit.

METRIC DEFINITIONS (exact, so the numbers are reproducible)
-----------------------------------------------------------
Cyclomatic (+1 each): ``if``/``elif``, ``for``/``async for``, ``while``,
``except`` handler, ``match`` case, each boolean operand after the first in an
``and``/``or`` sequence (``a and b and c`` -> +2), each ``if`` filter in a
comprehension, each comprehension generator (its implicit loop), and each
ternary (``x if c else y``). Base value is 1. Nested function definitions are
*not* counted here — they are reported as their own functions.

Cognitive (Sonar-style approximation):
  * +1 and a nesting penalty (+current depth) for: ``if``, ``for``, ``while``,
    ``except``, ``match`` case, ternary, and a comprehension's loop.
  * +1 with NO nesting penalty for: ``elif``, ``else``, and each boolean
    operator sequence (and each comprehension ``if`` filter).
  * Entering one of the nesting structures above increases the depth for the
    code inside it. ``try``/``with`` bodies do not add complexity themselves.
  * Nested ``def``\\s are reported separately, not folded into the parent.
This is a faithful approximation, not a bit-exact clone of Sonar's engine; the
self-test pins hand-computed values so the rules above stay honest.

Usage:
  python harnesses/core/complexity_test_harness.py --self-test
  python harnesses/core/complexity_test_harness.py --list-scenarios
  python harnesses/core/complexity_test_harness.py --target path/to/file.py
  python harnesses/core/complexity_test_harness.py --target src/ --verbose \\
      --max-cyclomatic 10 --max-cognitive 15
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path as _Path

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import Mutant, Report, Teeth

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class Thresholds:
    """Per-function limits above which a function is flagged. Defaults follow
    common industry guidance (cyclomatic <= 10, cognitive <= 15)."""

    max_cyclomatic: int = 10
    max_cognitive: int = 15
    max_lines: int = 60
    max_nesting: int = 4
    max_params: int = 6


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class FunctionMetrics:
    name: str
    lineno: int
    end_lineno: int
    lines: int
    params: int
    cyclomatic: int
    cognitive: int
    max_nesting: int

    def violations(self, t: Thresholds) -> list[str]:
        """Return human-readable threshold breaches for this function."""
        out: list[str] = []
        if self.cyclomatic > t.max_cyclomatic:
            out.append(f"cyclomatic {self.cyclomatic} > {t.max_cyclomatic}")
        if self.cognitive > t.max_cognitive:
            out.append(f"cognitive {self.cognitive} > {t.max_cognitive}")
        if self.lines > t.max_lines:
            out.append(f"length {self.lines} > {t.max_lines} lines")
        if self.max_nesting > t.max_nesting:
            out.append(f"nesting {self.max_nesting} > {t.max_nesting}")
        if self.params > t.max_params:
            out.append(f"params {self.params} > {t.max_params}")
        return out


@dataclass
class FileReport:
    path: str
    code_lines: int
    total_lines: int
    functions: list[FunctionMetrics] = field(default_factory=list)

    def flagged(self, t: Thresholds) -> list[tuple[FunctionMetrics, list[str]]]:
        out = []
        for fn in self.functions:
            v = fn.violations(t)
            if v:
                out.append((fn, v))
        return out


# ---------------------------------------------------------------------------
# AST helpers — pruning at nested function boundaries
# ---------------------------------------------------------------------------

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_MATCH = getattr(ast, "Match", None)  # py3.10+


def _walk_function_body(func: ast.AST):
    """Yield every node inside ``func``'s body, WITHOUT descending into nested
    function/lambda definitions (those are reported as their own functions)."""
    stack = list(getattr(func, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _DEF_TYPES):
            continue  # opaque: don't count a nested def's internals here
        stack.extend(ast.iter_child_nodes(node))


def count_params(func: ast.AST) -> int:
    """Total declared parameters (positional, keyword-only, *args, **kwargs)."""
    a = getattr(func, "args", None)
    if a is None:
        return 0
    n = len(a.args) + len(a.kwonlyargs) + len(getattr(a, "posonlyargs", []))
    if a.vararg:
        n += 1
    if a.kwarg:
        n += 1
    return n


# ---------------------------------------------------------------------------
# Cyclomatic complexity (McCabe)
# ---------------------------------------------------------------------------


def cyclomatic_complexity(func: ast.AST) -> int:
    """1 + number of decision points (see module docstring for the exact set)."""
    score = 1
    for node in _walk_function_body(func):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
                             ast.ExceptHandler)):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1  # each extra and/or operand
        elif isinstance(node, ast.comprehension):
            score += 1 + len(node.ifs)  # the implicit loop + each filter
        elif _MATCH is not None and isinstance(node, ast.match_case):
            score += 1
    return score


# ---------------------------------------------------------------------------
# Cognitive complexity (Sonar-style approximation)
# ---------------------------------------------------------------------------


def _expr_cognitive(expr: ast.AST, nesting: int) -> int:
    """Cognitive increments from expressions: boolean-operator sequences (+1,
    no nesting), ternaries (+1 + nesting), and comprehensions (+1 + nesting for
    the loop, +1 per ``if`` filter). Does not descend into lambdas/defs."""
    total = 0
    stack = [expr]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.BoolOp):
            total += 1
        elif isinstance(n, ast.IfExp):
            total += 1 + nesting
        elif isinstance(n, _COMPREHENSIONS):
            for gen in n.generators:
                total += (1 + nesting) + len(gen.ifs)
        if isinstance(n, _DEF_TYPES):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return total


class _CognitiveCounter:
    """Accumulate a Sonar-style cognitive-complexity score over a function body.

    One small method per control structure (a flat dispatcher + focused
    handlers) — so the harness comfortably passes its own complexity gate."""

    def __init__(self) -> None:
        self.total = 0

    def block(self, stmts: list[ast.stmt], nesting: int) -> None:
        for s in stmts:
            self.stmt(s, nesting)

    def stmt(self, node: ast.AST, nesting: int) -> None:
        if isinstance(node, ast.If):
            self._if(node, nesting)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self._loop(node, nesting)
        elif isinstance(node, ast.Try):
            self._try(node, nesting)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            self.block(node.body, nesting)  # `with` adds no cognitive load
        elif _MATCH is not None and isinstance(node, _MATCH):
            self._match(node, nesting)
        elif not isinstance(node, _DEF_TYPES):  # nested defs reported separately
            self.total += _expr_cognitive(node, nesting)

    def _if(self, node: ast.If, nesting: int) -> None:
        self.total += 1 + nesting
        self.total += _expr_cognitive(node.test, nesting)
        self.block(node.body, nesting + 1)
        orelse = node.orelse
        while orelse:
            if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                node = orelse[0]  # treat single-If else as `elif` (+1, no nest)
                self.total += 1
                self.total += _expr_cognitive(node.test, nesting)
                self.block(node.body, nesting + 1)
                orelse = node.orelse
            else:
                self.total += 1  # plain else (+1, no nesting penalty)
                self.block(orelse, nesting + 1)
                orelse = []

    def _loop(self, node: ast.AST, nesting: int) -> None:
        self.total += 1 + nesting
        if isinstance(node, ast.While):
            self.total += _expr_cognitive(node.test, nesting)
        self.block(node.body, nesting + 1)
        if node.orelse:
            self.total += 1
            self.block(node.orelse, nesting + 1)

    def _try(self, node: ast.Try, nesting: int) -> None:
        self.block(node.body, nesting)
        for h in node.handlers:
            self.total += 1 + nesting
            self.block(h.body, nesting + 1)
        self.block(node.orelse, nesting)
        self.block(node.finalbody, nesting)

    def _match(self, node: ast.AST, nesting: int) -> None:
        for case in node.cases:
            self.total += 1 + nesting
            self.block(case.body, nesting + 1)


def cognitive_complexity(func: ast.AST) -> int:
    """Approximate Sonar Cognitive Complexity (see module docstring)."""
    counter = _CognitiveCounter()
    counter.block(list(getattr(func, "body", [])), 0)
    return counter.total


# ---------------------------------------------------------------------------
# Nesting depth
# ---------------------------------------------------------------------------

_NESTERS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
            ast.With, ast.AsyncWith)


def _if_chain_depth(node: ast.If, level: int, recur) -> int:
    """Depth of an if/elif/else chain. An ``elif`` (single-If ``orelse``) stays
    at the SAME level as its ``if`` — a flat ladder is not deep nesting."""
    best = recur(node.body, level + 1)
    orelse = node.orelse
    while orelse:
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            node = orelse[0]
            best = max(best, recur(node.body, level + 1))
            orelse = node.orelse
        else:
            best = max(best, recur(orelse, level + 1))
            orelse = []
    return best


def _nester_depth(s: ast.AST, level: int, recur) -> int:
    """Depth contribution of a loop/try/with (its bodies are one level deeper)."""
    best = recur(getattr(s, "body", []), level + 1)
    best = max(best, recur(getattr(s, "orelse", []), level + 1))
    for h in getattr(s, "handlers", []):
        best = max(best, recur(h.body, level + 1))
    return max(best, recur(getattr(s, "finalbody", []), level + 1))


def _child_stmt_depth(s: ast.AST, level: int, recur) -> int:
    """Depth of statements nested in a non-control compound node."""
    best = level
    for child in ast.iter_child_nodes(s):
        if isinstance(child, ast.stmt):
            best = max(best, recur([child], level))
    return best


def max_nesting_depth(func: ast.AST) -> int:
    """Deepest nesting of control structures inside the function body. ``elif``
    ladders are counted flat (not as ever-deeper nesting)."""

    def depth(stmts: list[ast.stmt], level: int) -> int:
        best = level
        for s in stmts:
            if isinstance(s, _DEF_TYPES):
                continue
            if isinstance(s, ast.If):
                best = max(best, _if_chain_depth(s, level, depth))
            elif isinstance(s, _NESTERS):
                best = max(best, _nester_depth(s, level, depth))
            else:
                best = max(best, _child_stmt_depth(s, level, depth))
        return best

    return depth(list(getattr(func, "body", [])), 0)


# ---------------------------------------------------------------------------
# File analysis
# ---------------------------------------------------------------------------


def analyze_function(node: ast.AST) -> FunctionMetrics:
    end = getattr(node, "end_lineno", node.lineno)
    return FunctionMetrics(
        name=getattr(node, "name", "<lambda>"),
        lineno=node.lineno,
        end_lineno=end,
        lines=end - node.lineno + 1,
        params=count_params(node),
        cyclomatic=cyclomatic_complexity(node),
        cognitive=cognitive_complexity(node),
        max_nesting=max_nesting_depth(node),
    )


def _code_line_count(source: str) -> int:
    """Non-blank, non-comment physical lines (a cheap bloat signal)."""
    n = 0
    for raw in source.splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def analyze_source(source: str, path: str = "<string>") -> FileReport:
    """Parse ``source`` and report metrics for every function definition."""
    tree = ast.parse(source)
    funcs = [
        analyze_function(n)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    funcs.sort(key=lambda f: f.lineno)
    return FileReport(
        path=path,
        code_lines=_code_line_count(source),
        total_lines=len(source.splitlines()),
        functions=funcs,
    )


def _collect_py_files(path: str) -> list[str]:
    """Return the .py files under ``path`` (recursing if it is a directory)."""
    if not os.path.isdir(path):
        return [path]
    out: list[str] = []
    for root, _dirs, files in os.walk(path):
        out.extend(os.path.join(root, f) for f in files if f.endswith(".py"))
    return sorted(out)


def _print_file_report(report: FileReport, t: Thresholds, verbose: bool) -> int:
    """Print one file's metrics; return the count of flagged functions."""
    flagged = report.flagged(t)
    if not (flagged or verbose):
        return len(flagged)
    print(f"\n{report.path}  ({report.code_lines} code lines, "
          f"{len(report.functions)} functions)")
    rows = [(fn, fn.violations(t)) for fn in report.functions] if verbose else flagged
    for fn, viols in rows:
        tag = "FLAG" if viols else "ok  "
        suffix = f"  <- {', '.join(viols)}" if viols else ""
        print(f"  {tag} {fn.name}:{fn.lineno}  cyclo={fn.cyclomatic} "
              f"cog={fn.cognitive} lines={fn.lines} nest={fn.max_nesting} "
              f"params={fn.params}{suffix}")
    return len(flagged)


def analyze_path(path: str, thresholds: Thresholds, verbose: bool = False) -> int:
    """Gate mode: analyze a file or directory of .py files. Return the number
    of flagged functions (0 == clean == process success)."""
    files = _collect_py_files(path)
    total_flagged = 0
    for t in files:
        try:
            with open(t, encoding="utf-8") as fh:
                report = analyze_source(fh.read(), path=t)
        except (OSError, SyntaxError) as exc:
            print(f"SKIP {t}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        total_flagged += _print_file_report(report, thresholds, verbose)

    if total_flagged:
        print(f"\nFLAGGED: {total_flagged} function(s) exceed thresholds.",
              file=sys.stderr)
    else:
        print(f"\nOK: all functions within thresholds ({len(files)} file(s)).")
    return total_flagged


# ---------------------------------------------------------------------------
# Self-test — pin the analyzer to hand-computed values
# ---------------------------------------------------------------------------

# (label, source, expected_cyclomatic, expected_cognitive) — values computed by
# hand from the documented rules. These keep the metric honest under refactor.
SELF_TEST_CASES: list[tuple[str, str, int, int]] = [
    ("trivial", "def add(a, b):\n    return a + b\n", 1, 0),
    ("one_if", "def sign(n):\n    if n > 0:\n        return 1\n    return 0\n", 2, 1),
    (
        "if_elif_else_in_loop",
        textwrap.dedent("""
            def classify(items):
                out = 0
                for x in items:
                    if x > 0:
                        out += 1
                    elif x < 0:
                        out -= 1
                    else:
                        out += 0
                return out
        """).strip() + "\n",
        4,  # cyclo: for + if + elif
        5,  # cog: for(1) + if(2 @nest1) + elif(1) + else(1)
    ),
    (
        "bool_and_nesting",
        textwrap.dedent("""
            def f(a, b):
                if a:
                    if b and a:
                        return 1
                return 0
        """).strip() + "\n",
        4,  # cyclo: if a + if(...) + the `and`
        4,  # cog: if(1 @0) + inner if(2 @1) + bool(1)
    ),
    (
        "comprehension",
        "def squares(xs):\n    return [x * x for x in xs if x > 0 if x < 100]\n",
        4,  # cyclo: 1 + comp loop(1) + 2 filters
        3,  # cog: comp loop(1 @0) + 2 filters
    ),
]


def _bloated_source() -> str:
    """A deliberately convoluted function that must trip every threshold."""
    return textwrap.dedent("""
        def spaghetti(a, b, c, d, e, f, g):
            total = 0
            for i in a:
                if i > 0:
                    for j in b:
                        if j > 0 and i > j or i == 0:
                            while total < 100:
                                if c:
                                    total += 1
                                elif d:
                                    total -= 1
                                else:
                                    total += 2
                        else:
                            total += i
            return total
    """).strip() + "\n"


# ---------------------------------------------------------------------------
# TEETH: frozen complexity audits + planted metric-blind defects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplexityAuditCase:
    name: str
    check: str
    source: str
    thresholds: Thresholds
    expected_events: tuple[str, ...]


_NESTED_SOURCE = textwrap.dedent("""
    def tangled(a, b, c):
        if a:
            if b:
                if c:
                    return 1
        return 0
""").strip() + "\n"

_LENGTHY_SOURCE = textwrap.dedent("""
    def lengthy():
        a = 1
        b = 2
        c = 3
        d = 4
        e = 5
        f = 6
        g = a + b + c + d + e + f
        return g
""").strip() + "\n"

_WIDE_SOURCE = "def wide(a, b, c, d, e, f, g):\n    return a\n"

_NESTED_VS_FLAT_SOURCE = textwrap.dedent("""
    def flat(a, b, c):
        if a:
            return 1
        if b:
            return 2
        if c:
            return 3
        return 0

    def nested(a, b, c):
        if a:
            if b:
                if c:
                    return 1
        return 0
""").strip() + "\n"


COMPLEXITY_AUDIT_CORPUS = (
    ComplexityAuditCase(
        name="clean_function_has_no_flags",
        check="gate",
        source="def add(a, b):\n    return a + b\n",
        thresholds=Thresholds(),
        expected_events=("no_flags",),
    ),
    ComplexityAuditCase(
        name="cognitive_and_nesting_thresholds_flag",
        check="gate",
        source=_NESTED_SOURCE,
        thresholds=Thresholds(
            max_cyclomatic=99,
            max_cognitive=2,
            max_lines=99,
            max_nesting=2,
            max_params=99,
        ),
        expected_events=("flag:tangled", "breach:cognitive", "breach:nesting"),
    ),
    ComplexityAuditCase(
        name="length_threshold_flags_bloat",
        check="gate",
        source=_LENGTHY_SOURCE,
        thresholds=Thresholds(
            max_cyclomatic=99,
            max_cognitive=99,
            max_lines=4,
            max_nesting=99,
            max_params=99,
        ),
        expected_events=("flag:lengthy", "breach:length"),
    ),
    ComplexityAuditCase(
        name="parameter_threshold_flags_wide_signature",
        check="gate",
        source=_WIDE_SOURCE,
        thresholds=Thresholds(
            max_cyclomatic=99,
            max_cognitive=99,
            max_lines=99,
            max_nesting=99,
            max_params=6,
        ),
        expected_events=("flag:wide", "breach:params"),
    ),
    ComplexityAuditCase(
        name="nested_code_is_cognitively_harder_than_flat_code",
        check="nested_vs_flat",
        source=_NESTED_VS_FLAT_SOURCE,
        thresholds=Thresholds(),
        expected_events=("nested_more_cognitive", "nested_depth:3"),
    ),
)


def _breach_events(violations: list[str]) -> list[str]:
    events = []
    for prefix, event in (
        ("cyclomatic", "breach:cyclomatic"),
        ("cognitive", "breach:cognitive"),
        ("length", "breach:length"),
        ("nesting", "breach:nesting"),
        ("params", "breach:params"),
    ):
        if any(v.startswith(prefix) for v in violations):
            events.append(event)
    return events


def _gate_events(source: str, thresholds: Thresholds) -> tuple[str, ...]:
    report = analyze_source(source, path="<audit>")
    flagged = report.flagged(thresholds)
    if not flagged:
        return ("no_flags",)
    events = []
    for fn, violations in flagged:
        events.append(f"flag:{fn.name}")
        events.extend(_breach_events(violations))
    return tuple(events)


def _metrics_by_name(source: str) -> dict[str, FunctionMetrics]:
    return {fn.name: fn for fn in analyze_source(source, path="<audit>").functions}


def oracle_complexity_audit(case: ComplexityAuditCase) -> tuple[str, ...]:
    if case.check == "gate":
        return _gate_events(case.source, case.thresholds)
    if case.check == "nested_vs_flat":
        metrics = _metrics_by_name(case.source)
        events = []
        if metrics["nested"].cognitive > metrics["flat"].cognitive:
            events.append("nested_more_cognitive")
        if metrics["nested"].max_nesting == 3:
            events.append("nested_depth:3")
        return tuple(events)
    raise ValueError(f"unknown complexity audit check: {case.check}")


def cyclomatic_only_complexity_auditor(case: ComplexityAuditCase) -> tuple[str, ...]:
    if case.name != "cognitive_and_nesting_thresholds_flag":
        return oracle_complexity_audit(case)
    thresholds = Thresholds(
        max_cyclomatic=case.thresholds.max_cyclomatic,
        max_cognitive=99,
        max_lines=case.thresholds.max_lines,
        max_nesting=99,
        max_params=case.thresholds.max_params,
    )
    return _gate_events(case.source, thresholds)


def length_blind_complexity_auditor(case: ComplexityAuditCase) -> tuple[str, ...]:
    if case.name != "length_threshold_flags_bloat":
        return oracle_complexity_audit(case)
    thresholds = Thresholds(
        max_cyclomatic=case.thresholds.max_cyclomatic,
        max_cognitive=case.thresholds.max_cognitive,
        max_lines=99,
        max_nesting=case.thresholds.max_nesting,
        max_params=case.thresholds.max_params,
    )
    return _gate_events(case.source, thresholds)


def params_blind_complexity_auditor(case: ComplexityAuditCase) -> tuple[str, ...]:
    if case.name != "parameter_threshold_flags_wide_signature":
        return oracle_complexity_audit(case)
    thresholds = Thresholds(
        max_cyclomatic=case.thresholds.max_cyclomatic,
        max_cognitive=case.thresholds.max_cognitive,
        max_lines=case.thresholds.max_lines,
        max_nesting=case.thresholds.max_nesting,
        max_params=99,
    )
    return _gate_events(case.source, thresholds)


def nesting_blind_complexity_auditor(case: ComplexityAuditCase) -> tuple[str, ...]:
    if case.name != "nested_code_is_cognitively_harder_than_flat_code":
        return oracle_complexity_audit(case)
    return ("nested_more_cognitive",)


def prove(impl: Callable[[ComplexityAuditCase], tuple[str, ...]]) -> bool:
    return any(impl(case) != case.expected_events for case in COMPLEXITY_AUDIT_CORPUS)


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_complexity_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_complexity_audit,
    mutants=(
        Mutant("cyclomatic_only_complexity_auditor", cyclomatic_only_complexity_auditor,
               "ignores cognitive-complexity and nesting threshold breaches"),
        Mutant("length_blind_complexity_auditor", length_blind_complexity_auditor,
               "ignores bloat through function length"),
        Mutant("params_blind_complexity_auditor", params_blind_complexity_auditor,
               "ignores wide signatures with too many parameters"),
        Mutant("nesting_blind_complexity_auditor", nesting_blind_complexity_auditor,
               "drops the nested-depth signal while preserving a flat cognitive comparison"),
    ),
    corpus_size=len(COMPLEXITY_AUDIT_CORPUS),
    kind="auditor",
    notes="Frozen maintainability corpus for clean code, cognitive/nesting, length, params, and nesting contrast.",
)


def list_scenarios() -> list[str]:
    return [label for label, *_ in SELF_TEST_CASES] + [
        "bloated_function_is_flagged",
        "clean_function_passes",
    ]


def _metric_selftest_failures(verbose: bool) -> list[str]:
    failures: list[str] = []
    for label, src, exp_cyc, exp_cog in SELF_TEST_CASES:
        report = analyze_source(src, path=label)
        fn = report.functions[0]
        if verbose:
            print(f"{label:22} cyclo={fn.cyclomatic} (exp {exp_cyc})  "
                  f"cog={fn.cognitive} (exp {exp_cog})")
        if fn.cyclomatic != exp_cyc:
            failures.append(f"{label}: cyclomatic {fn.cyclomatic} != {exp_cyc}")
        if fn.cognitive != exp_cog:
            failures.append(f"{label}: cognitive {fn.cognitive} != {exp_cog}")
    return failures


def _gate_selftest_failures() -> list[str]:
    failures: list[str] = []
    t = Thresholds()
    bloated = analyze_source(_bloated_source(), path="bloated")
    if not bloated.flagged(t):
        failures.append("bloated function was not flagged")
    clean = analyze_source(SELF_TEST_CASES[0][1], path="clean")
    if clean.flagged(t):
        failures.append("trivial function was wrongly flagged")
    return failures


def _print_selftest_failures(failures: list[str]) -> None:
    print(f"FAILED ({len(failures)}):", file=sys.stderr)
    for line in failures:
        print(f"  - {line}", file=sys.stderr)


def _emit_complexity_teeth_report() -> int:
    report = Report("core/complexity")
    for case in COMPLEXITY_AUDIT_CORPUS:
        report.add(
            f"oracle_complexity_audit:{case.name}",
            list(case.expected_events),
            list(oracle_complexity_audit(case)),
        )
    report.assert_teeth(TEETH)
    return report.emit()


def _run_self_test(verbose: bool = False) -> int:
    failures = _metric_selftest_failures(verbose) + _gate_selftest_failures()
    if failures:
        _print_selftest_failures(failures)
        return 1
    print(f"OK: {len(SELF_TEST_CASES)} metric cases match; gate flags bloat "
          f"and passes clean code.")
    return _emit_complexity_teeth_report()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Code complexity / bloat gate (cyclomatic + cognitive + LOC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--list-scenarios", action="store_true", help="List scenarios")
    p.add_argument("--target", help="Analyze a .py file or directory (gate mode)")
    p.add_argument("--verbose", action="store_true", help="Show every function")
    p.add_argument("--max-cyclomatic", type=int, default=Thresholds.max_cyclomatic)
    p.add_argument("--max-cognitive", type=int, default=Thresholds.max_cognitive)
    p.add_argument("--max-lines", type=int, default=Thresholds.max_lines)
    p.add_argument("--max-nesting", type=int, default=Thresholds.max_nesting)
    p.add_argument("--max-params", type=int, default=Thresholds.max_params)
    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0

    if args.self_test:
        return _run_self_test(verbose=args.verbose)

    if args.target:
        thresholds = Thresholds(
            max_cyclomatic=args.max_cyclomatic,
            max_cognitive=args.max_cognitive,
            max_lines=args.max_lines,
            max_nesting=args.max_nesting,
            max_params=args.max_params,
        )
        flagged = analyze_path(args.target, thresholds, verbose=args.verbose)
        return 1 if flagged else 0

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
