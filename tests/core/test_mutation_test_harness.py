"""
Tests for mutation_test_harness.py  (47 tests)
"""

import json
import time
import unittest
import urllib.request
from urllib.error import HTTPError

from harnesses.core.mutation_test_harness import (
    Mutant,
    MutationHTTPServer,
    MutationOperator,
    MutationReport,
    MutationResult,
    MutationRunner,
    Mutator,
    SourceMutator,
    make_exec_test,
    sandbox_exec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def _http_post(url: str, data: dict) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        return json.loads(exc.read())


# ---------------------------------------------------------------------------
# 1. MutationResult enum
# ---------------------------------------------------------------------------

class TestMutationResultEnum(unittest.TestCase):

    def test_enum_members_exist(self):
        for name in ('KILLED', 'SURVIVED', 'ERROR', 'TIMEOUT'):
            self.assertIn(name, MutationResult.__members__)

    def test_enum_values_are_strings(self):
        for member in MutationResult:
            self.assertIsInstance(member.value, str)

    def test_killed_value(self):
        self.assertEqual(MutationResult.KILLED.value, 'KILLED')

    def test_survived_value(self):
        self.assertEqual(MutationResult.SURVIVED.value, 'SURVIVED')


# ---------------------------------------------------------------------------
# 2. MutationOperator enum
# ---------------------------------------------------------------------------

class TestMutationOperatorEnum(unittest.TestCase):

    def test_six_operators(self):
        self.assertEqual(len(MutationOperator), 6)

    def test_operator_names(self):
        names = {op.name for op in MutationOperator}
        expected = {
            'ARITHMETIC_SWAP', 'COMPARISON_SWAP', 'CONSTANT_SWAP',
            'BOOLEAN_SWAP', 'RETURN_SWAP', 'CONDITION_NEGATION',
        }
        self.assertEqual(names, expected)


# ---------------------------------------------------------------------------
# 3. Mutator — arithmetic_swap
# ---------------------------------------------------------------------------

class TestMutatorArithmeticSwap(unittest.TestCase):

    def setUp(self):
        self.m = Mutator()

    def test_plus_to_minus(self):
        src = "x = a + b"
        mutants = self.m.arithmetic_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('a - b' in s for s in sources),
                        f"No '+' -> '-' mutant found. Got: {sources}")

    def test_minus_to_plus(self):
        src = "x = a - b"
        mutants = self.m.arithmetic_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('a + b' in s for s in sources))

    def test_multiply_to_divide(self):
        src = "x = a * b"
        mutants = self.m.arithmetic_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('a / b' in s for s in sources))

    def test_no_mutation_in_string(self):
        src = 'x = "a + b"'
        mutants = self.m.arithmetic_swap(src)
        # All mutations should leave the string literal intact
        for ms, _ in mutants:
            # The string content should not be changed
            self.assertIn('"a + b"', ms)

    def test_returns_list_of_tuples(self):
        src = "x = 1 + 2"
        result = self.m.arithmetic_swap(src)
        self.assertIsInstance(result, list)
        for item in result:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)

    def test_description_contains_operator(self):
        src = "x = a + b"
        mutants = self.m.arithmetic_swap(src)
        descs = [d for _, d in mutants]
        self.assertTrue(any('+' in d and '-' in d for d in descs))


# ---------------------------------------------------------------------------
# 4. Mutator — comparison_swap
# ---------------------------------------------------------------------------

class TestMutatorComparisonSwap(unittest.TestCase):

    def setUp(self):
        self.m = Mutator()

    def test_eq_to_neq(self):
        src = "if x == y: pass"
        mutants = self.m.comparison_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('x != y' in s for s in sources))

    def test_lt_to_gt(self):
        src = "if x < y: pass"
        mutants = self.m.comparison_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('x > y' in s for s in sources))

    def test_lte_to_gte(self):
        src = "if x <= y: pass"
        mutants = self.m.comparison_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('x >= y' in s for s in sources))

    def test_neq_to_eq(self):
        src = "if x != y: pass"
        mutants = self.m.comparison_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('x == y' in s for s in sources))


# ---------------------------------------------------------------------------
# 5. Mutator — constant_swap
# ---------------------------------------------------------------------------

class TestMutatorConstantSwap(unittest.TestCase):

    def setUp(self):
        self.m = Mutator()

    def test_true_to_false(self):
        src = "x = True"
        mutants = self.m.constant_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('False' in s for s in sources))

    def test_false_to_true(self):
        src = "x = False"
        mutants = self.m.constant_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('True' in s for s in sources))

    def test_zero_to_one(self):
        src = "x = 0"
        mutants = self.m.constant_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any(s.strip().endswith('= 1') or '= 1' in s for s in sources))

    def test_one_to_zero(self):
        src = "x = 1"
        mutants = self.m.constant_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('= 0' in s for s in sources))


# ---------------------------------------------------------------------------
# 6. Mutator — boolean_swap
# ---------------------------------------------------------------------------

class TestMutatorBooleanSwap(unittest.TestCase):

    def setUp(self):
        self.m = Mutator()

    def test_and_to_or(self):
        src = "if a and b: pass"
        mutants = self.m.boolean_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('a or b' in s for s in sources))

    def test_or_to_and(self):
        src = "if a or b: pass"
        mutants = self.m.boolean_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('a and b' in s for s in sources))

    def test_no_false_positive_in_word(self):
        # 'android' should NOT be mutated
        src = "android = True"
        mutants = self.m.boolean_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertFalse(any('orroid' in s for s in sources))


# ---------------------------------------------------------------------------
# 7. Mutator — return_swap
# ---------------------------------------------------------------------------

class TestMutatorReturnSwap(unittest.TestCase):

    def setUp(self):
        self.m = Mutator()

    def test_return_true_to_false(self):
        src = "def f():\n    return True\n"
        mutants = self.m.return_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('return False' in s for s in sources))

    def test_return_false_to_true(self):
        src = "def f():\n    return False\n"
        mutants = self.m.return_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('return True' in s for s in sources))

    def test_return_zero_to_one(self):
        src = "def f():\n    return 0\n"
        mutants = self.m.return_swap(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('return 1' in s for s in sources))

    def test_description_mentions_swap(self):
        src = "def f():\n    return True\n"
        mutants = self.m.return_swap(src)
        descs = [d for _, d in mutants]
        self.assertTrue(any('return_swap' in d for d in descs))


# ---------------------------------------------------------------------------
# 8. Mutator — condition_negation
# ---------------------------------------------------------------------------

class TestMutatorConditionNegation(unittest.TestCase):

    def setUp(self):
        self.m = Mutator()

    def test_if_negation(self):
        src = "if x:\n    pass\n"
        mutants = self.m.condition_negation(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('not' in s for s in sources))

    def test_while_negation(self):
        src = "while running:\n    pass\n"
        mutants = self.m.condition_negation(src)
        sources = [ms for ms, _ in mutants]
        self.assertTrue(any('not' in s for s in sources))

    def test_double_negation_removed(self):
        src = "if not x:\n    pass\n"
        mutants = self.m.condition_negation(src)
        sources = [ms for ms, _ in mutants]
        # Should produce "if x:" (removing the not)
        self.assertTrue(any(
            'if x:' in s or ('if ' in s and 'not' not in s)
            for s in sources
        ))


# ---------------------------------------------------------------------------
# 9. SourceMutator
# ---------------------------------------------------------------------------

class TestSourceMutator(unittest.TestCase):

    def setUp(self):
        self.sm = SourceMutator()

    def test_generate_mutants_returns_list(self):
        src = "x = 1 + 2"
        mutants = self.sm.generate_mutants(src)
        self.assertIsInstance(mutants, list)

    def test_mutants_are_mutant_objects(self):
        src = "x = 1 + 2"
        mutants = self.sm.generate_mutants(src)
        for m in mutants:
            self.assertIsInstance(m, Mutant)

    def test_mutant_has_original_source(self):
        src = "x = 1 + 2"
        mutants = self.sm.generate_mutants(src)
        for m in mutants:
            self.assertEqual(m.original_source, src)

    def test_filter_by_operator(self):
        src = "if x == y: pass"
        all_mutants = self.sm.generate_mutants(src)
        comp_mutants = self.sm.generate_mutants(src, [MutationOperator.COMPARISON_SWAP])
        self.assertTrue(len(comp_mutants) <= len(all_mutants))
        for m in comp_mutants:
            self.assertEqual(m.operator, MutationOperator.COMPARISON_SWAP)

    def test_generate_mutants_for_operator(self):
        src = "if a and b: pass"
        mutants = self.sm.generate_mutants_for_operator(src, MutationOperator.BOOLEAN_SWAP)
        self.assertTrue(len(mutants) > 0)
        for m in mutants:
            self.assertEqual(m.operator, MutationOperator.BOOLEAN_SWAP)

    def test_mutant_id_is_unique(self):
        src = "x = 1 + 2 + 3"
        mutants = self.sm.generate_mutants(src)
        ids = [m.mutant_id for m in mutants]
        self.assertEqual(len(ids), len(set(ids)))


# ---------------------------------------------------------------------------
# 10. MutationRunner — KILLED/SURVIVED classification
# ---------------------------------------------------------------------------

class TestMutationRunnerClassification(unittest.TestCase):

    def _simple_add_source(self):
        return "def add(a, b):\n    return a + b\n"

    def _strict_test(self, mutated_source: str) -> bool:
        """Returns False if add(1,2) != 3 — kills any arithmetic mutant."""
        ns = sandbox_exec(mutated_source)
        return ns['add'](1, 2) == 3

    def _lenient_test(self, mutated_source: str) -> bool:
        """Always returns True — nothing ever kills."""
        return True

    def test_survived_when_test_always_passes(self):
        runner = MutationRunner(self._lenient_test, timeout=5)
        report = runner.run(self._simple_add_source(),
                            [MutationOperator.ARITHMETIC_SWAP])
        self.assertGreater(report.survived, 0)

    def test_killed_when_test_detects_mutation(self):
        runner = MutationRunner(self._strict_test, timeout=5)
        report = runner.run(self._simple_add_source(),
                            [MutationOperator.ARITHMETIC_SWAP])
        self.assertGreater(report.killed, 0)

    def test_error_on_syntax_breaking_mutation(self):
        # Manually inject an ERROR result
        mutant = Mutant(
            original_source="x = 1",
            mutated_source="x = (",    # incomplete, SyntaxError
            operator=MutationOperator.ARITHMETIC_SWAP,
            description="test error mutant",
        )
        runner = MutationRunner(self._lenient_test, timeout=5)
        result = runner._run_one(mutant)
        self.assertEqual(result.result, MutationResult.ERROR)

    def test_timeout_classification(self):
        def slow_test(src: str) -> bool:
            time.sleep(10)
            return True

        mutant = Mutant(
            original_source="x = 1 + 2",
            mutated_source="x = 1 - 2",
            operator=MutationOperator.ARITHMETIC_SWAP,
            description="test timeout mutant",
        )
        runner = MutationRunner(slow_test, timeout=0.2)
        result = runner._run_one(mutant)
        self.assertEqual(result.result, MutationResult.TIMEOUT)


# ---------------------------------------------------------------------------
# 11. MutationReport
# ---------------------------------------------------------------------------

class TestMutationReport(unittest.TestCase):

    def _make_report(self, killed=3, survived=2, errors=1, timeouts=0):
        mutants = []
        for _i in range(killed):
            m = Mutant("x=1", "x=2", MutationOperator.CONSTANT_SWAP, "k")
            m.result = MutationResult.KILLED
            mutants.append(m)
        for _i in range(survived):
            m = Mutant("x=1", "x=2", MutationOperator.CONSTANT_SWAP, "s")
            m.result = MutationResult.SURVIVED
            mutants.append(m)
        for _i in range(errors):
            m = Mutant("x=1", "x=(", MutationOperator.CONSTANT_SWAP, "e")
            m.result = MutationResult.ERROR
            mutants.append(m)
        for _i in range(timeouts):
            m = Mutant("x=1", "x=2", MutationOperator.CONSTANT_SWAP, "t")
            m.result = MutationResult.TIMEOUT
            mutants.append(m)
        return MutationReport(source="x=1", mutants=mutants)

    def test_total_count(self):
        report = self._make_report(3, 2, 1, 0)
        self.assertEqual(report.total, 6)

    def test_killed_count(self):
        report = self._make_report(3, 2, 1, 0)
        self.assertEqual(report.killed, 3)

    def test_survived_count(self):
        report = self._make_report(3, 2, 1, 0)
        self.assertEqual(report.survived, 2)

    def test_errors_count(self):
        report = self._make_report(3, 2, 1, 0)
        self.assertEqual(report.errors, 1)

    def test_mutation_score_calculation(self):
        # score = killed / (total - errors - timeouts) = 3 / (3+2) = 0.6
        report = self._make_report(3, 2, 1, 0)
        self.assertAlmostEqual(report.mutation_score, 0.6)

    def test_mutation_score_zero_eligible(self):
        report = self._make_report(0, 0, 2, 0)
        self.assertEqual(report.mutation_score, 0.0)

    def test_mutation_score_all_killed(self):
        report = self._make_report(5, 0, 0, 0)
        self.assertAlmostEqual(report.mutation_score, 1.0)

    def test_survived_mutants_list(self):
        report = self._make_report(3, 2, 1, 0)
        self.assertEqual(len(report.survived_mutants), 2)

    def test_killed_mutants_list(self):
        report = self._make_report(3, 2, 1, 0)
        self.assertEqual(len(report.killed_mutants), 3)

    def test_to_dict_keys(self):
        report = self._make_report(2, 1, 0, 0)
        d = report.to_dict()
        for key in ('total', 'killed', 'survived', 'errors', 'mutation_score'):
            self.assertIn(key, d)

    def test_summary_string(self):
        report = self._make_report(3, 2, 1, 0)
        summary = report.summary()
        self.assertIn('60.0%', summary)
        self.assertIn('3 killed', summary)


# ---------------------------------------------------------------------------
# 12. sandbox_exec helper
# ---------------------------------------------------------------------------

class TestSandboxExec(unittest.TestCase):

    def test_returns_namespace(self):
        ns = sandbox_exec("x = 42")
        self.assertEqual(ns['x'], 42)

    def test_function_defined(self):
        ns = sandbox_exec("def greet(n): return 'hi ' + n")
        self.assertEqual(ns['greet']('world'), 'hi world')

    def test_syntax_error_propagated(self):
        with self.assertRaises(SyntaxError):
            sandbox_exec("def f(: pass")

    def test_runtime_error_propagated(self):
        with self.assertRaises(ZeroDivisionError):
            sandbox_exec("x = 1 / 0")


# ---------------------------------------------------------------------------
# 13. make_exec_test helper
# ---------------------------------------------------------------------------

class TestMakeExecTest(unittest.TestCase):

    def test_passing_test_returns_true(self):
        src = "def add(a, b): return a + b"
        fn = make_exec_test("assert add(1, 2) == 3")
        self.assertTrue(fn(src))

    def test_failing_test_returns_false(self):
        # Mutated source: subtract instead of add
        src = "def add(a, b): return a - b"
        fn = make_exec_test("assert add(1, 2) == 3")
        self.assertFalse(fn(src))

    def test_raises_on_runtime_error(self):
        src = "def f(): raise RuntimeError('boom')"
        fn = make_exec_test("f()")
        with self.assertRaises(RuntimeError):
            fn(src)


# ---------------------------------------------------------------------------
# 14. MutationRunner.exec_in_sandbox static method
# ---------------------------------------------------------------------------

class TestRunnerExecInSandbox(unittest.TestCase):

    def test_exec_in_sandbox_returns_namespace(self):
        ns = MutationRunner.exec_in_sandbox("y = 99")
        self.assertEqual(ns['y'], 99)

    def test_exec_in_sandbox_syntax_error(self):
        with self.assertRaises(SyntaxError):
            MutationRunner.exec_in_sandbox("def bad(: pass")


# ---------------------------------------------------------------------------
# 15. MutationHTTPServer
# ---------------------------------------------------------------------------

class TestMutationHTTPServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = MutationHTTPServer(port=0)
        cls.server.start()
        # Give the server a moment to start
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_health_endpoint(self):
        data = _http_get(self.server.url('/health'))
        self.assertEqual(data.get('status'), 'ok')

    def test_operators_endpoint(self):
        data = _http_get(self.server.url('/operators'))
        self.assertIn('operators', data)
        self.assertEqual(len(data['operators']), 6)

    def test_404_unknown_path(self):
        try:
            _http_get(self.server.url('/nonexistent'))
            self.fail("Expected an error")
        except HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_post_mutants_endpoint(self):
        source = "def f(x):\n    return x + 1\n"
        data = _http_post(self.server.url('/mutants'), {'source': source})
        self.assertIn('count', data)
        self.assertGreater(data['count'], 0)
        self.assertIn('mutants', data)

    def test_post_mutants_missing_source(self):
        data = _http_post(self.server.url('/mutants'), {})
        self.assertIn('error', data)

    def test_post_run_endpoint(self):
        source = "def f(x):\n    return x + 1\n"
        data = _http_post(self.server.url('/run'), {'source': source})
        self.assertIn('total', data)
        self.assertIn('mutation_score', data)

    def test_get_report_after_run(self):
        # Run something first to populate the report
        source = "x = True"
        _http_post(self.server.url('/run'), {'source': source})
        data = _http_get(self.server.url('/report'))
        self.assertIn('total', data)

    def test_actual_port_is_nonzero(self):
        self.assertGreater(self.server.actual_port, 0)

    def test_url_method(self):
        url = self.server.url('/health')
        self.assertIn('http://', url)
        self.assertIn('/health', url)


# ---------------------------------------------------------------------------
# 16. End-to-end mutation testing
# ---------------------------------------------------------------------------

class TestEndToEnd(unittest.TestCase):

    def test_add_function_arithmetic_mutants_killed(self):
        """A strict test suite should kill + -> - mutants in add()."""
        source = "def add(a, b):\n    return a + b\n"
        assertions = "assert add(2, 3) == 5\nassert add(0, 0) == 0\n"
        test_fn = make_exec_test(assertions)
        runner = MutationRunner(test_fn, timeout=5)
        report = runner.run(source, [MutationOperator.ARITHMETIC_SWAP])
        # At least some mutants should be killed
        self.assertGreater(report.killed + report.errors, 0)

    def test_bool_function_return_swap_killed(self):
        """A test that checks both True and False should kill return swaps."""
        source = "def is_positive(n):\n    if n > 0:\n        return True\n    return False\n"
        assertions = (
            "assert is_positive(1) == True\n"
            "assert is_positive(-1) == False\n"
        )
        test_fn = make_exec_test(assertions)
        runner = MutationRunner(test_fn, timeout=5)
        report = runner.run(source, [MutationOperator.RETURN_SWAP])
        self.assertGreater(report.killed, 0)

    def test_mutation_score_with_weak_tests(self):
        """A test that never fails → score should be 0.0 (all survived)."""
        source = "def add(a, b):\n    return a + b\n"
        def always_pass(src):
            return True
        runner = MutationRunner(always_pass, timeout=5)
        report = runner.run(source, [MutationOperator.ARITHMETIC_SWAP])
        # Only error mutants cause non-survived
        eligible = report.total - report.errors - report.timeouts
        if eligible > 0:
            self.assertEqual(report.survived, eligible)
            self.assertAlmostEqual(report.mutation_score, 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
