"""Test suite for complexity_test_harness.

Locks the metric definitions (cyclomatic, cognitive, nesting), the gate
behavior, and the analyzer's handling of real Python constructs."""

import sys
import textwrap
import unittest

from harnesses.core.complexity_test_harness import (
    FileReport,
    FunctionMetrics,
    Thresholds,
    _collect_py_files,
    _run_self_test,
    analyze_path,
    analyze_source,
    cognitive_complexity,
    cyclomatic_complexity,
    list_scenarios,
    max_nesting_depth,
)


def _func(source: str):
    """Parse a single-function snippet and return its FunctionMetrics."""
    return analyze_source(textwrap.dedent(source).strip() + "\n").functions[0]


class TestCyclomatic(unittest.TestCase):
    def test_straight_line_is_one(self):
        self.assertEqual(_func("def f(a):\n    return a").cyclomatic, 1)

    def test_each_if_adds_one(self):
        m = _func("""
            def f(a):
                if a:
                    return 1
                return 0
        """)
        self.assertEqual(m.cyclomatic, 2)

    def test_boolean_operands_count(self):
        # `a and b and c` -> two extra operands -> base 1 + 2 = 3
        self.assertEqual(_func("def f(a, b, c):\n    return a and b and c").cyclomatic, 3)

    def test_comprehension_loop_and_filters(self):
        # 1 (base) + 1 (comp loop) + 2 (filters)
        m = _func("def f(xs):\n    return [x for x in xs if x if x > 0]")
        self.assertEqual(m.cyclomatic, 4)


class TestCognitive(unittest.TestCase):
    def test_flat_code_is_zero(self):
        self.assertEqual(_func("def f(a):\n    return a + 1").cognitive, 0)

    def test_nesting_is_penalized(self):
        flat = _func("""
            def f(a, b):
                if a:
                    return 1
                if b:
                    return 2
        """)
        nested = _func("""
            def f(a, b):
                if a:
                    if b:
                        return 1
        """)
        # two flat ifs: 1 + 1 = 2; nested if-in-if: 1 + (1+1) = 3
        self.assertEqual(flat.cognitive, 2)
        self.assertEqual(nested.cognitive, 3)

    def test_elif_and_else_increment_without_nesting(self):
        m = _func("""
            def f(x):
                if x == 1:
                    return "a"
                elif x == 2:
                    return "b"
                else:
                    return "c"
        """)
        # if(1) + elif(1) + else(1)
        self.assertEqual(m.cognitive, 3)


class TestNesting(unittest.TestCase):
    def test_elif_ladder_is_flat(self):
        # Regression: an elif ladder must NOT be counted as deep nesting even
        # though Python's AST represents `elif` as a nested If.
        m = _func("""
            def f(x):
                if x == 1:
                    return 1
                elif x == 2:
                    return 2
                elif x == 3:
                    return 3
                else:
                    return 0
        """)
        self.assertEqual(m.max_nesting, 1)

    def test_genuine_nesting_counts(self):
        m = _func("""
            def f(a):
                if a:
                    for x in a:
                        while x:
                            if x > 1:
                                return x
        """)
        self.assertEqual(m.max_nesting, 4)


class TestParamsAndLines(unittest.TestCase):
    def test_param_count(self):
        m = _func("def f(a, b, c=1, *args, k=2, **kw):\n    return a")
        self.assertEqual(m.params, 6)  # a, b, c, args, k, kw

    def test_line_span(self):
        m = _func("def f():\n    x = 1\n    return x")
        self.assertEqual(m.lines, 3)


class TestThresholdsAndGate(unittest.TestCase):
    def test_clean_function_has_no_violations(self):
        m = _func("def f(a):\n    return a + 1")
        self.assertEqual(m.violations(Thresholds()), [])

    def test_violations_report_each_breach(self):
        m = FunctionMetrics(
            name="x", lineno=1, end_lineno=99, lines=99, params=9,
            cyclomatic=20, cognitive=40, max_nesting=8,
        )
        viols = m.violations(Thresholds())
        self.assertEqual(len(viols), 5)  # all five limits breached

    def test_filereport_flagged_filters(self):
        report = analyze_source("def f(a):\n    return a\n")
        self.assertEqual(report.flagged(Thresholds()), [])
        self.assertIsInstance(report, FileReport)


class TestAnalyzeSource(unittest.TestCase):
    def test_finds_all_functions_including_nested(self):
        report = analyze_source(textwrap.dedent("""
            def outer():
                def inner():
                    return 1
                return inner
        """).strip() + "\n")
        names = {f.name for f in report.functions}
        self.assertEqual(names, {"outer", "inner"})

    def test_code_line_count_skips_blanks_and_comments(self):
        report = analyze_source("# comment\n\ndef f():\n    return 1\n")
        self.assertEqual(report.code_lines, 2)  # def + return only

    def test_nested_def_not_folded_into_parent(self):
        # outer's own complexity excludes the inner def's body.
        report = analyze_source(textwrap.dedent("""
            def outer(a):
                def inner(b):
                    if b:
                        return 1
                return inner
        """).strip() + "\n")
        outer = next(f for f in report.functions if f.name == "outer")
        self.assertEqual(outer.cyclomatic, 1)  # the inner `if` is not counted


class TestAnalyzePathGate(unittest.TestCase):
    def _write(self, tmp_path, name, body):
        import os
        p = os.path.join(tmp_path, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body).strip() + "\n")
        return p

    def test_clean_file_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "ok.py", "def f(a):\n    return a + 1")
            self.assertEqual(analyze_path(p, Thresholds()), 0)

    def test_bloated_file_is_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "bad.py", """
                def spaghetti(a, b, c, d, e, f, g):
                    for i in a:
                        if i > 0:
                            for j in b:
                                while c:
                                    if d and e or f:
                                        c -= 1
                    return c
            """)
            self.assertGreater(analyze_path(p, Thresholds()), 0)

    def test_collect_py_files_recurses_dir(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sub"))
            self._write(d, "a.py", "def f():\n    return 1")
            self._write(os.path.join(d, "sub"), "b.py", "def g():\n    return 2")
            files = _collect_py_files(d)
            self.assertEqual(len(files), 2)
            self.assertEqual(files, sorted(files))

    def test_single_file_target(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "one.py", "def f():\n    return 1")
            self.assertEqual(_collect_py_files(p), [p])


@unittest.skipUnless(sys.version_info >= (3, 10), "match needs py3.10+")
class TestMatchStatement(unittest.TestCase):
    def test_match_cases_add_complexity(self):
        m = _func("""
            def f(x):
                match x:
                    case 1:
                        return "a"
                    case 2:
                        return "b"
                    case _:
                        return "c"
        """)
        # cyclomatic: 1 + 3 cases; cognitive: 3 cases at nesting 0
        self.assertEqual(m.cyclomatic, 4)
        self.assertEqual(m.cognitive, 3)


class TestSelfTestContract(unittest.TestCase):
    def test_list_scenarios_nonempty(self):
        self.assertGreaterEqual(len(list_scenarios()), 5)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)

    def test_pure_functions_are_importable(self):
        # the metric functions work on raw AST too (used by other harnesses)
        import ast
        tree = ast.parse("def f(a):\n    return a and a")
        fn = tree.body[0]
        self.assertEqual(cyclomatic_complexity(fn), 2)
        self.assertEqual(cognitive_complexity(fn), 1)
        self.assertEqual(max_nesting_depth(fn), 0)


if __name__ == "__main__":
    unittest.main()
