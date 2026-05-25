"""
Tests for the Property-Based Test Harness (Harness 11 of 36).

57 tests covering:
- Generator correctness
- Property and PropertyRunner
- PropertySuite and PropertyReport
- Shrinker for all types
- Precondition filtering
- HTTP mock server endpoints
- Invariant properties (reverse, sort, concat, commutative, etc.)
"""

import json
import random
import time
import unittest
import urllib.error
import urllib.request
from typing import Any

from property_test_harness import (
    CounterExample,
    MockPropertyHandler,
    MockPropertyServer,
    Property,
    PropertyReport,
    PropertyRunner,
    PropertySuite,
    Shrinker,
    _complexity,
    forall,
    gen_bool,
    gen_dict,
    gen_float,
    gen_int,
    gen_list,
    gen_none,
    gen_one_of,
    gen_positive_int,
    gen_string,
    gen_tuple,
    is_simpler,
    run_suite_and_report,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


# ============================================================
# 1. Generator tests
# ============================================================

class TestGenInt(unittest.TestCase):
    def test_gen_int_in_range(self):
        rng = _rng()
        g = gen_int(-10, 10)
        for _ in range(200):
            v = g(rng)
            self.assertIsInstance(v, int)
            self.assertGreaterEqual(v, -10)
            self.assertLessEqual(v, 10)

    def test_gen_int_default_range(self):
        rng = _rng()
        g = gen_int()
        values = [g(rng) for _ in range(500)]
        self.assertTrue(any(v < 0 for v in values))
        self.assertTrue(any(v > 0 for v in values))

    def test_gen_int_single_value(self):
        rng = _rng()
        g = gen_int(7, 7)
        for _ in range(10):
            self.assertEqual(g(rng), 7)


class TestGenFloat(unittest.TestCase):
    def test_gen_float_type(self):
        rng = _rng()
        g = gen_float(-1.0, 1.0)
        for _ in range(100):
            v = g(rng)
            self.assertIsInstance(v, float)
            self.assertGreaterEqual(v, -1.0)
            self.assertLessEqual(v, 1.0)

    def test_gen_float_spread(self):
        rng = _rng()
        g = gen_float(0.0, 1000.0)
        values = [g(rng) for _ in range(200)]
        self.assertGreater(max(values), 500.0)


class TestGenString(unittest.TestCase):
    def test_gen_string_length(self):
        rng = _rng()
        g = gen_string(3, 8)
        for _ in range(100):
            s = g(rng)
            self.assertIsInstance(s, str)
            self.assertGreaterEqual(len(s), 3)
            self.assertLessEqual(len(s), 8)

    def test_gen_string_empty_allowed(self):
        rng = _rng()
        g = gen_string(0, 5)
        values = [g(rng) for _ in range(200)]
        self.assertTrue(any(len(v) == 0 for v in values))

    def test_gen_string_custom_alphabet(self):
        rng = _rng()
        g = gen_string(5, 5, alphabet="abc")
        for _ in range(50):
            s = g(rng)
            self.assertTrue(all(c in "abc" for c in s))


class TestGenList(unittest.TestCase):
    def test_gen_list_length(self):
        rng = _rng()
        g = gen_list(gen_int(), 2, 5)
        for _ in range(100):
            lst = g(rng)
            self.assertIsInstance(lst, list)
            self.assertGreaterEqual(len(lst), 2)
            self.assertLessEqual(len(lst), 5)

    def test_gen_list_element_types(self):
        rng = _rng()
        g = gen_list(gen_string())
        for _ in range(50):
            lst = g(rng)
            for elem in lst:
                self.assertIsInstance(elem, str)


class TestGenTuple(unittest.TestCase):
    def test_gen_tuple_structure(self):
        rng = _rng()
        g = gen_tuple(gen_int(), gen_string(), gen_float())
        for _ in range(50):
            t = g(rng)
            self.assertIsInstance(t, tuple)
            self.assertEqual(len(t), 3)
            self.assertIsInstance(t[0], int)
            self.assertIsInstance(t[1], str)
            self.assertIsInstance(t[2], float)


class TestGenDict(unittest.TestCase):
    def test_gen_dict_structure(self):
        rng = _rng()
        g = gen_dict(gen_string(1, 5), gen_int(), 1, 4)
        for _ in range(50):
            d = g(rng)
            self.assertIsInstance(d, dict)
            self.assertGreaterEqual(len(d), 1)
            self.assertLessEqual(len(d), 4)

    def test_gen_dict_value_types(self):
        rng = _rng()
        g = gen_dict(gen_string(1, 3), gen_int(), 2, 2)
        for _ in range(30):
            d = g(rng)
            for v in d.values():
                self.assertIsInstance(v, int)


class TestGenOneOf(unittest.TestCase):
    def test_gen_one_of_uses_all(self):
        rng = _rng()
        g = gen_one_of(gen_int(0, 0), gen_int(1, 1), gen_int(2, 2))
        values = set(g(rng) for _ in range(300))
        self.assertEqual(values, {0, 1, 2})

    def test_gen_one_of_single(self):
        rng = _rng()
        g = gen_one_of(gen_int(5, 5))
        for _ in range(10):
            self.assertEqual(g(rng), 5)


# ============================================================
# 2. Shrinker tests
# ============================================================

class TestShrinkerInt(unittest.TestCase):
    def test_shrink_int_positive(self):
        s = Shrinker()
        # Anything > 3 fails; shrink should find 4 (smallest value > 3)
        result = s.shrink(50, lambda v: isinstance(v, int) and v > 3)
        self.assertGreater(result, 3)
        self.assertLessEqual(result, 50)

    def test_shrink_int_negative(self):
        s = Shrinker()
        result = s.shrink(-30, lambda v: isinstance(v, int) and v < 0)
        self.assertLess(result, 0)
        self.assertGreaterEqual(result, -30)

    def test_shrink_int_zero_is_minimal(self):
        s = Shrinker()
        # predicate: any int passes (never fails) => shrink returns original
        original = 42
        result = s.shrink(original, lambda v: True)
        # If predicate always True, shrinker tries 0 first → 0
        self.assertEqual(result, 0)

    def test_shrink_int_to_zero(self):
        s = Shrinker()
        result = s.shrink(100, lambda v: True)
        self.assertEqual(result, 0)


class TestShrinkerFloat(unittest.TestCase):
    def test_shrink_float_towards_zero(self):
        s = Shrinker()
        result = s.shrink(999.9, lambda v: True)
        self.assertAlmostEqual(result, 0.0, places=5)

    def test_shrink_float_nonzero_constraint(self):
        s = Shrinker()
        result = s.shrink(100.0, lambda v: abs(v) > 1.0)
        self.assertGreater(abs(result), 1.0)
        self.assertLessEqual(abs(result), 100.0)


class TestShrinkerString(unittest.TestCase):
    def test_shrink_string_to_empty(self):
        s = Shrinker()
        result = s.shrink("hello world", lambda v: True)
        self.assertEqual(result, "")

    def test_shrink_string_length_constraint(self):
        s = Shrinker()
        result = s.shrink("abcdefgh", lambda v: len(v) >= 3)
        self.assertGreaterEqual(len(result), 3)
        self.assertLessEqual(len(result), 8)

    def test_shrink_string_simpler_chars(self):
        s = Shrinker()
        # Must contain at least one char but we want simpler chars
        result = s.shrink("ZXY", lambda v: len(v) >= 1)
        self.assertGreaterEqual(len(result), 1)
        # Should simplify toward 'a' or '0' or ' '
        self.assertLessEqual(len(result), 3)


class TestShrinkerList(unittest.TestCase):
    def test_shrink_list_to_empty(self):
        s = Shrinker()
        result = s.shrink([1, 2, 3, 4, 5], lambda v: True)
        self.assertEqual(result, [])

    def test_shrink_list_length_constraint(self):
        s = Shrinker()
        result = s.shrink([10, 20, 30, 40, 50], lambda v: len(v) >= 2)
        self.assertGreaterEqual(len(result), 2)
        self.assertLessEqual(len(result), 5)

    def test_shrink_list_elements(self):
        s = Shrinker()
        # List must have sum > 5
        result = s.shrink([10, 20, 30], lambda v: sum(v) > 5)
        self.assertGreater(sum(result), 5)


class TestShrinkerTuple(unittest.TestCase):
    def test_shrink_tuple_values(self):
        s = Shrinker()
        result = s.shrink((100, 200), lambda v: True)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        # Both should be 0
        self.assertEqual(result, (0, 0))

    def test_shrink_tuple_constraint(self):
        s = Shrinker()
        result = s.shrink((50, 50), lambda v: v[0] + v[1] > 10)
        self.assertGreater(result[0] + result[1], 10)


class TestShrinkerDict(unittest.TestCase):
    def test_shrink_dict_removes_keys(self):
        s = Shrinker()
        result = s.shrink({"a": 1, "b": 2, "c": 3}, lambda v: True)
        # Empty dict is simplest
        self.assertEqual(result, {})

    def test_shrink_dict_constraint(self):
        s = Shrinker()
        result = s.shrink({"x": 100, "y": 200}, lambda v: len(v) >= 1)
        self.assertGreaterEqual(len(result), 1)


# ============================================================
# 3. Property tests
# ============================================================

class TestProperty(unittest.TestCase):
    def test_property_passes_when_invariant_holds(self):
        prop = Property(
            gen_list(gen_int()),
            lambda lst: list(reversed(list(reversed(lst)))) == lst,
            name="reverse_twice",
        )
        result = prop.check(num_examples=200, seed=99)
        self.assertIsNone(result)

    def test_property_fails_when_invariant_violated(self):
        # Always-false predicate
        prop = Property(
            gen_int(),
            lambda v: False,
            name="always_false",
        )
        result = prop.check(num_examples=10, seed=1)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, CounterExample)

    def test_property_precondition_filters(self):
        # Only check even numbers; predicate: even number / 2 is integer
        prop = Property(
            gen_int(-50, 50),
            lambda v: v % 2 == 0,
            precondition=lambda v: v % 2 == 0,
            name="even_div_2",
        )
        result = prop.check(num_examples=100, seed=42)
        self.assertIsNone(result)

    def test_property_counterexample_is_shrunk(self):
        # Fails when value > 10; original may be large, shrunk should be ≤ original
        prop = Property(
            gen_int(11, 100),
            lambda v: v <= 10,
            name="fails_large",
        )
        result = prop.check(num_examples=20, seed=7)
        self.assertIsNotNone(result)
        # Shrunk should be simpler than original
        self.assertLessEqual(abs(result.shrunk), abs(result.original))

    def test_property_exception_counts_as_failure(self):
        def bad_predicate(v):
            if v == 0:
                raise ValueError("zero!")
            return True

        prop = Property(
            gen_int(0, 0),  # always 0
            bad_predicate,
            name="exception_prop",
        )
        result = prop.check(num_examples=5, seed=3)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.exception)


# ============================================================
# 4. PropertyRunner tests
# ============================================================

class TestPropertyRunner(unittest.TestCase):
    def test_runner_returns_none_on_all_pass(self):
        runner = PropertyRunner()
        prop = Property(gen_int(), lambda v: isinstance(v, int))
        result = runner.run_property(prop, num_examples=50, seed=0)
        self.assertIsNone(result)

    def test_runner_returns_counterexample_on_fail(self):
        runner = PropertyRunner()
        prop = Property(gen_int(1, 100), lambda v: v < 0)
        result = runner.run_property(prop, num_examples=10)
        self.assertIsNotNone(result)

    def test_runner_shrinks_counterexample(self):
        runner = PropertyRunner()
        prop = Property(gen_int(50, 200), lambda v: v < 50)
        result = runner.run_property(prop, num_examples=10, seed=5)
        self.assertIsNotNone(result)
        # Shrunk should be simpler/smaller than original
        self.assertLessEqual(abs(result.shrunk), abs(result.original))

    def test_runner_uses_seed_deterministically(self):
        runner = PropertyRunner()
        prop = Property(gen_int(), lambda v: v != 42)
        r1 = runner.run_property(prop, num_examples=200, seed=999)
        r2 = runner.run_property(prop, num_examples=200, seed=999)
        # Same seed → same result
        if r1 is None:
            self.assertIsNone(r2)
        else:
            self.assertEqual(r1.original, r2.original)


# ============================================================
# 5. PropertySuite / PropertyReport tests
# ============================================================

class TestPropertySuite(unittest.TestCase):
    def test_suite_run_all_all_pass(self):
        suite = PropertySuite("test_suite")
        suite.add(Property(gen_int(), lambda v: isinstance(v, int)), "int_type")
        suite.add(Property(gen_string(), lambda s: isinstance(s, str)), "str_type")
        report = suite.run_all(num_examples=50, seed=10)
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.failed, 0)
        self.assertTrue(report.all_passed)

    def test_suite_run_all_with_failure(self):
        suite = PropertySuite("test_suite")
        suite.add(Property(gen_int(), lambda v: isinstance(v, int)), "always_pass")
        suite.add(Property(gen_int(), lambda v: False), "always_fail")
        report = suite.run_all(num_examples=10, seed=0)
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 1)
        self.assertFalse(report.all_passed)

    def test_suite_counterexamples_populated(self):
        suite = PropertySuite("test_suite")
        suite.add(Property(gen_int(1, 5), lambda v: v == 0), "never_zero")
        report = suite.run_all(num_examples=5, seed=1)
        self.assertEqual(len(report.counterexamples), 1)
        name, ce = report.counterexamples[0]
        self.assertEqual(name, "never_zero")
        self.assertIsInstance(ce, CounterExample)

    def test_suite_multiple_failures(self):
        suite = PropertySuite("multi")
        for i in range(3):
            suite.add(Property(gen_int(), lambda v: False), f"fail_{i}")
        report = suite.run_all(num_examples=5)
        self.assertEqual(report.failed, 3)
        self.assertEqual(len(report.counterexamples), 3)

    def test_suite_property_convenience_method(self):
        suite = PropertySuite("conv")
        suite.property(gen_int(), lambda v: isinstance(v, int), name="int_check")
        report = suite.run_all(num_examples=30, seed=42)
        self.assertEqual(report.passed, 1)


# ============================================================
# 6. Invariant property tests (actual mathematical properties)
# ============================================================

class TestInvariantProperties(unittest.TestCase):
    def test_reverse_twice(self):
        result = forall(
            gen_list(gen_int()),
            lambda lst: list(reversed(list(reversed(lst)))) == lst,
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_sort_idempotent(self):
        result = forall(
            gen_list(gen_int()),
            lambda lst: sorted(sorted(lst)) == sorted(lst),
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_sort_length_preserved(self):
        result = forall(
            gen_list(gen_int()),
            lambda lst: len(sorted(lst)) == len(lst),
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_string_concat_length(self):
        result = forall(
            gen_tuple(gen_string(), gen_string()),
            lambda t: len(t[0] + t[1]) == len(t[0]) + len(t[1]),
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_addition_commutative(self):
        result = forall(
            gen_tuple(gen_int(), gen_int()),
            lambda t: t[0] + t[1] == t[1] + t[0],
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_addition_associative(self):
        result = forall(
            gen_tuple(gen_int(), gen_int(), gen_int()),
            lambda t: (t[0] + t[1]) + t[2] == t[0] + (t[1] + t[2]),
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_multiplication_commutative(self):
        result = forall(
            gen_tuple(gen_int(), gen_int()),
            lambda t: t[0] * t[1] == t[1] * t[0],
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_string_reverse_twice(self):
        result = forall(
            gen_string(),
            lambda s: s[::-1][::-1] == s,
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_abs_non_negative(self):
        result = forall(
            gen_int(-1000, 1000),
            lambda v: abs(v) >= 0,
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_list_append_length(self):
        result = forall(
            gen_tuple(gen_list(gen_int()), gen_int()),
            lambda t: len(t[0] + [t[1]]) == len(t[0]) + 1,
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_set_from_list_subset(self):
        result = forall(
            gen_list(gen_int(-5, 5)),
            lambda lst: all(v in lst for v in set(lst)),
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_min_leq_max(self):
        result = forall(
            gen_list(gen_int(), min_len=1, max_len=10),
            lambda lst: min(lst) <= max(lst),
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_sorted_first_leq_last(self):
        result = forall(
            gen_list(gen_int(), min_len=1, max_len=10),
            lambda lst: sorted(lst)[0] <= sorted(lst)[-1],
            num_examples=200,
            seed=42,
        )
        self.assertIsNone(result)

    def test_precondition_positive_sqrt(self):
        import math

        result = forall(
            gen_float(0.0, 1e6),
            lambda v: math.sqrt(v) >= 0,
            precondition=lambda v: v >= 0,
            num_examples=100,
            seed=42,
        )
        self.assertIsNone(result)

    def test_precondition_nonzero_division(self):
        # Integer division: a // b gives quotient q such that q * b + r == a, 0 <= r < abs(b)
        # In Python: a == (a // b) * b + (a % b), and a % b >= 0 always
        result = forall(
            gen_tuple(gen_int(), gen_int()),
            lambda t: t[0] == (t[0] // t[1]) * t[1] + (t[0] % t[1]),
            precondition=lambda t: t[1] != 0,
            num_examples=100,
            seed=42,
        )
        self.assertIsNone(result)


# ============================================================
# 7. Shrinking produces smaller/simpler values
# ============================================================

class TestShrinkingProducesSmaller(unittest.TestCase):
    def test_shrunk_int_smaller_abs(self):
        prop = Property(gen_int(20, 100), lambda v: v < 20)
        ce = prop.check(num_examples=30, seed=1)
        self.assertIsNotNone(ce)
        self.assertLessEqual(abs(ce.shrunk), abs(ce.original))

    def test_shrunk_list_shorter_or_equal(self):
        prop = Property(gen_list(gen_int(), min_len=3), lambda lst: len(lst) < 3)
        ce = prop.check(num_examples=30, seed=1)
        self.assertIsNotNone(ce)
        self.assertLessEqual(len(ce.shrunk), len(ce.original))

    def test_shrunk_string_shorter_or_equal(self):
        prop = Property(gen_string(min_len=3), lambda s: len(s) < 3)
        ce = prop.check(num_examples=30, seed=1)
        self.assertIsNotNone(ce)
        self.assertLessEqual(len(ce.shrunk), len(ce.original))

    def test_complexity_of_shrunk_leq_original(self):
        prop = Property(gen_list(gen_int(10, 50), min_len=2), lambda lst: False)
        ce = prop.check(num_examples=10, seed=2)
        self.assertIsNotNone(ce)
        self.assertLessEqual(_complexity(ce.shrunk), _complexity(ce.original))


# ============================================================
# 8. HTTP Mock Server tests
# ============================================================

class TestMockPropertyServer(unittest.TestCase):
    def setUp(self):
        self.server = MockPropertyServer()
        self.server.start()
        self.base_url = self.server.base_url

    def tearDown(self):
        self.server.stop()

    def _get(self, path: str):
        url = self.base_url + path
        resp = urllib.request.urlopen(url)
        return json.loads(resp.read())

    def _post(self, path: str, data: Any = None):
        url = self.base_url + path
        body = json.dumps(data).encode() if data is not None else b""
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())

    def test_status_endpoint(self):
        data = self._get("/status")
        self.assertEqual(data["status"], "ok")

    def test_results_initially_empty(self):
        data = self._get("/results")
        self.assertEqual(data["results"], [])

    def test_run_known_property(self):
        data = self._post("/run_property", {"property": "reverse_twice", "num_examples": 20})
        self.assertEqual(data["property"], "reverse_twice")
        self.assertTrue(data["passed"])

    def test_run_unknown_property(self):
        data = self._post("/run_property", {"property": "does_not_exist"})
        self.assertIn("error", data)
        self.assertIn("available", data)

    def test_run_sort_idempotent(self):
        data = self._post("/run_property", {"property": "sort_idempotent", "num_examples": 30})
        self.assertTrue(data["passed"])

    def test_run_addition_commutative(self):
        data = self._post("/run_property", {"property": "addition_commutative", "num_examples": 30})
        self.assertTrue(data["passed"])

    def test_run_string_concat_len(self):
        data = self._post("/run_property", {"property": "string_concat_len", "num_examples": 30})
        self.assertTrue(data["passed"])

    def test_results_accumulate(self):
        self._post("/run_property", {"property": "reverse_twice", "num_examples": 10})
        self._post("/run_property", {"property": "sort_idempotent", "num_examples": 10})
        data = self._get("/results")
        self.assertEqual(len(data["results"]), 2)

    def test_reset_clears_results(self):
        self._post("/run_property", {"property": "reverse_twice", "num_examples": 5})
        self._post("/reset")
        data = self._get("/results")
        self.assertEqual(data["results"], [])

    def test_reset_returns_ok(self):
        data = self._post("/reset")
        self.assertEqual(data["status"], "reset")

    def test_404_on_unknown_get(self):
        url = self.base_url + "/nonexistent"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url)
        self.assertEqual(ctx.exception.code, 404)

    def test_status_results_count(self):
        self._post("/run_property", {"property": "reverse_twice", "num_examples": 5})
        data = self._get("/status")
        self.assertGreaterEqual(data["results_count"], 1)


# ============================================================
# 9. Utility helpers
# ============================================================

class TestUtilityHelpers(unittest.TestCase):
    def test_is_simpler_smaller_int(self):
        self.assertTrue(is_simpler(1, 100))

    def test_is_simpler_shorter_string(self):
        self.assertTrue(is_simpler("a", "abcde"))

    def test_is_simpler_empty_list(self):
        self.assertTrue(is_simpler([], [1, 2, 3]))

    def test_run_suite_and_report_all_pass(self):
        props = {
            "reverse": Property(gen_list(gen_int()), lambda lst: lst[::-1][::-1] == lst),
            "type": Property(gen_int(), lambda v: isinstance(v, int)),
        }
        report = run_suite_and_report(props, num_examples=50, seed=1)
        self.assertTrue(report.all_passed)

    def test_forall_returns_none_on_success(self):
        result = forall(gen_int(), lambda v: isinstance(v, int), num_examples=50, seed=1)
        self.assertIsNone(result)

    def test_forall_returns_counterexample_on_failure(self):
        result = forall(gen_int(), lambda v: False, num_examples=5, seed=1)
        self.assertIsNotNone(result)

    def test_gen_bool_values(self):
        rng = _rng()
        g = gen_bool()
        values = [g(rng) for _ in range(100)]
        self.assertIn(True, values)
        self.assertIn(False, values)

    def test_gen_none_always_none(self):
        rng = _rng()
        g = gen_none()
        for _ in range(10):
            self.assertIsNone(g(rng))

    def test_gen_positive_int(self):
        rng = _rng()
        g = gen_positive_int(50)
        for _ in range(100):
            v = g(rng)
            self.assertGreater(v, 0)
            self.assertLessEqual(v, 50)

    def test_property_report_repr(self):
        r = PropertyReport(passed=3, failed=1)
        self.assertIn("passed=3", repr(r))
        self.assertIn("failed=1", repr(r))


if __name__ == "__main__":
    unittest.main(verbosity=2)
