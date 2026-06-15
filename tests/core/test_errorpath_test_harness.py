"""
Tests for Error-Path / Negative Coverage Test Harness (Harness 26 of 36)
~107 tests, pure stdlib.
"""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import fields as dc_fields

from harnesses.core.errorpath_test_harness import (
    BoundaryTester,
    BranchResult,
    CoverageProbe,
    ErrorPathReport,
    ErrorPathServer,
    ExceptionPathTester,
    MockErrorPathHandler,
    NegativeCaseResult,
    NullHandlingTester,
    ResourceCleanupTester,
    TimeoutTester,
    find_free_port,
)

# ---------------------------------------------------------------------------
# Helper functions used by multiple tests
# ---------------------------------------------------------------------------

def _divide(x):
    """Raises ZeroDivisionError when x == 0."""
    return 10 / x


def _must_be_positive(x):
    """Raises ValueError when x <= 0."""
    if x is None:
        raise TypeError("x cannot be None")
    if x <= 0:
        raise ValueError(f"value must be positive, got {x}")
    return x


def _concat(a, b):
    """Concatenate two strings."""
    return str(a) + str(b)


def _add_items(lst, item):
    """Appends item to list; raises TypeError if lst is not a list."""
    if not isinstance(lst, list):
        raise TypeError("lst must be a list")
    lst.append(item)
    return lst


def _parse_int(s):
    """Parse string to int; raises ValueError on bad input."""
    if s is None:
        raise TypeError("s cannot be None")
    if not isinstance(s, str):
        raise TypeError("s must be a string")
    return int(s)


def _slow_function(duration=5.0):
    """Sleeps for duration seconds - used to test timeouts."""
    time.sleep(duration)
    return "done"


def _fast_function():
    """Returns immediately."""
    return "fast"


def _resource_user_good(counters, should_fail=False):
    """Properly acquires and releases a resource."""
    counters["acquired"] += 1
    try:
        if should_fail:
            raise RuntimeError("simulated failure")
        return "ok"
    finally:
        counters["released"] += 1


def _resource_user_leaking(counters, should_fail=False):
    """Leaks resource - no finally block."""
    counters["acquired"] += 1
    if should_fail:
        raise RuntimeError("simulated failure")
    counters["released"] += 1
    return "ok"


# ---------------------------------------------------------------------------
# 1. BranchResult dataclass tests
# ---------------------------------------------------------------------------

class TestBranchResult(unittest.TestCase):

    def test_fields_exist(self):
        names = {f.name for f in dc_fields(BranchResult)}
        self.assertIn("label", names)
        self.assertIn("hit", names)
        self.assertIn("call_count", names)

    def test_create_hit(self):
        br = BranchResult(label="branch_a", hit=True, call_count=3)
        self.assertEqual(br.label, "branch_a")
        self.assertTrue(br.hit)
        self.assertEqual(br.call_count, 3)

    def test_create_not_hit(self):
        br = BranchResult(label="branch_b", hit=False, call_count=0)
        self.assertFalse(br.hit)
        self.assertEqual(br.call_count, 0)

    def test_equality(self):
        br1 = BranchResult(label="x", hit=True, call_count=1)
        br2 = BranchResult(label="x", hit=True, call_count=1)
        self.assertEqual(br1, br2)

    def test_inequality(self):
        br1 = BranchResult(label="x", hit=True, call_count=1)
        br2 = BranchResult(label="x", hit=False, call_count=0)
        self.assertNotEqual(br1, br2)


# ---------------------------------------------------------------------------
# 2. NegativeCaseResult dataclass tests
# ---------------------------------------------------------------------------

class TestNegativeCaseResult(unittest.TestCase):

    def test_fields_exist(self):
        names = {f.name for f in dc_fields(NegativeCaseResult)}
        self.assertIn("input", names)
        self.assertIn("expected_behavior", names)
        self.assertIn("actual_behavior", names)
        self.assertIn("passed", names)

    def test_create_passed(self):
        ncr = NegativeCaseResult(
            input=-1,
            expected_behavior="raises ValueError",
            actual_behavior="raised ValueError",
            passed=True,
        )
        self.assertTrue(ncr.passed)
        self.assertEqual(ncr.input, -1)

    def test_create_failed(self):
        ncr = NegativeCaseResult(
            input=0,
            expected_behavior="raises ValueError",
            actual_behavior="returned 0",
            passed=False,
        )
        self.assertFalse(ncr.passed)


# ---------------------------------------------------------------------------
# 3. ErrorPathReport dataclass tests
# ---------------------------------------------------------------------------

class TestErrorPathReport(unittest.TestCase):

    def test_default_empty(self):
        r = ErrorPathReport()
        self.assertEqual(r.total_tests, 0)
        self.assertEqual(r.passed_tests, 0)

    def test_total_counts_all_lists(self):
        r = ErrorPathReport(
            branch_results=[BranchResult("a", True, 1)],
            negative_results=[NegativeCaseResult(1, "e", "a", True)],
            exception_results=[{"passed": True}],
            null_results=[{"passed": False}],
            boundary_results=[{"passed": True}],
            timeout_results=[{"passed": True}],
            cleanup_results=[{"passed": False}],
        )
        self.assertEqual(r.total_tests, 7)

    def test_passed_counts_correctly(self):
        r = ErrorPathReport(
            branch_results=[BranchResult("a", True, 1), BranchResult("b", False, 0)],
            negative_results=[NegativeCaseResult(1, "e", "a", True)],
            exception_results=[{"passed": True}, {"passed": False}],
        )
        # branch_results: 1 hit (a), 1 not hit (b) → 1 passed
        # negative_results: 1 passed
        # exception_results: 1 passed, 1 failed → 1 passed
        self.assertEqual(r.passed_tests, 3)

    def test_separate_lists_independent(self):
        r1 = ErrorPathReport()
        r2 = ErrorPathReport()
        r1.branch_results.append(BranchResult("x", True, 1))
        self.assertEqual(len(r2.branch_results), 0)


# ---------------------------------------------------------------------------
# 4. CoverageProbe tests
# ---------------------------------------------------------------------------

class TestCoverageProbe(unittest.TestCase):

    def setUp(self):
        self.probe = CoverageProbe()

    def test_probe_and_hit(self):
        self.probe.probe("branch_a")
        self.assertTrue(self.probe.hit("branch_a"))

    def test_unhit_returns_false(self):
        self.assertFalse(self.probe.hit("branch_z"))

    def test_never_hit_empty_when_all_hit(self):
        self.probe.register(["a", "b"])
        self.probe.probe("a")
        self.probe.probe("b")
        self.assertEqual(self.probe.never_hit(), [])

    def test_never_hit_returns_unprobed(self):
        self.probe.register(["a", "b", "c"])
        self.probe.probe("a")
        missing = self.probe.never_hit()
        self.assertIn("b", missing)
        self.assertIn("c", missing)
        self.assertNotIn("a", missing)

    def test_register_preregisters_with_zero_count(self):
        self.probe.register(["x", "y"])
        self.assertEqual(self.probe.call_count("x"), 0)
        self.assertFalse(self.probe.hit("x"))

    def test_probe_increments_count(self):
        self.probe.probe("branch")
        self.probe.probe("branch")
        self.probe.probe("branch")
        self.assertEqual(self.probe.call_count("branch"), 3)

    def test_call_count_zero_for_unknown(self):
        self.assertEqual(self.probe.call_count("unknown"), 0)

    def test_all_labels_includes_registered(self):
        self.probe.register(["r1", "r2"])
        self.probe.probe("p1")
        labels = self.probe.all_labels()
        self.assertIn("r1", labels)
        self.assertIn("r2", labels)
        self.assertIn("p1", labels)

    def test_reset_clears_counts_keeps_labels(self):
        self.probe.register(["a", "b"])
        self.probe.probe("a")
        self.probe.reset()
        self.assertEqual(self.probe.call_count("a"), 0)
        self.assertIn("a", self.probe.all_labels())

    def test_reset_all_clears_everything(self):
        self.probe.register(["a"])
        self.probe.probe("b")
        self.probe.reset_all()
        self.assertEqual(self.probe.all_labels(), [])

    def test_get_branch_results_returns_all(self):
        self.probe.register(["a", "b"])
        self.probe.probe("a")
        results = self.probe.get_branch_results()
        labels = {r.label for r in results}
        self.assertIn("a", labels)
        self.assertIn("b", labels)

    def test_get_branch_results_hit_status(self):
        self.probe.register(["hit_me", "miss_me"])
        self.probe.probe("hit_me")
        results = {r.label: r for r in self.probe.get_branch_results()}
        self.assertTrue(results["hit_me"].hit)
        self.assertFalse(results["miss_me"].hit)

    def test_multiple_probes_same_label(self):
        for _ in range(5):
            self.probe.probe("repeated")
        self.assertEqual(self.probe.call_count("repeated"), 5)
        self.assertTrue(self.probe.hit("repeated"))

    def test_never_hit_with_no_registrations(self):
        # Nothing registered, nothing probed
        self.assertEqual(self.probe.never_hit(), [])

    def test_probe_auto_registers(self):
        self.probe.probe("auto")
        self.assertIn("auto", self.probe.all_labels())


# ---------------------------------------------------------------------------
# 5. ExceptionPathTester tests
# ---------------------------------------------------------------------------

class TestExceptionPathTester(unittest.TestCase):

    def setUp(self):
        self.tester = ExceptionPathTester()

    def test_correct_exception_type(self):
        result = self.tester.test(_divide, 0, ZeroDivisionError)
        self.assertTrue(result["passed"])

    def test_wrong_exception_type_fails(self):
        result = self.tester.test(_divide, 0, ValueError)
        self.assertFalse(result["passed"])

    def test_message_fragment_match(self):
        result = self.tester.test(_must_be_positive, -1, ValueError, "positive")
        self.assertTrue(result["passed"])

    def test_message_fragment_mismatch_fails(self):
        result = self.tester.test(_must_be_positive, -1, ValueError, "NONEXISTENT_TEXT_XYZ")
        self.assertFalse(result["passed"])

    def test_no_exception_fails(self):
        result = self.tester.test(_must_be_positive, 5, ValueError)
        self.assertFalse(result["passed"])
        self.assertIn("No exception", result["error"])

    def test_state_unchanged_check_passes(self):
        state = {"value": 42}

        def bad_mutator(x):
            if x < 0:
                raise ValueError("negative")
            state["value"] = x

        result = self.tester.test(
            bad_mutator, -1, ValueError,
            state_obj=state,
            state_snapshot_fn=lambda s: dict(s)
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["state_unchanged"])

    def test_state_changed_fails(self):
        state = {"value": 0}

        def mutates_then_raises(x):
            state["value"] = x  # mutates before raising
            raise ValueError("oops")

        result = self.tester.test(
            mutates_then_raises, 99, ValueError,
            state_obj=state,
            state_snapshot_fn=lambda s: dict(s)
        )
        self.assertFalse(result["passed"])

    def test_results_accumulate(self):
        self.tester.test(_divide, 0, ZeroDivisionError)
        self.tester.test(_must_be_positive, -1, ValueError)
        self.assertEqual(len(self.tester.results()), 2)

    def test_all_passed_true_when_all_pass(self):
        self.tester.test(_divide, 0, ZeroDivisionError)
        self.assertTrue(self.tester.all_passed())

    def test_all_passed_false_when_any_fail(self):
        self.tester.test(_divide, 0, ZeroDivisionError)
        self.tester.test(_divide, 0, ValueError)  # wrong type
        self.assertFalse(self.tester.all_passed())

    def test_failed_returns_only_failures(self):
        self.tester.test(_divide, 0, ZeroDivisionError)  # pass
        self.tester.test(_divide, 0, ValueError)          # fail
        failed = self.tester.failed()
        self.assertEqual(len(failed), 1)

    def test_tuple_input_unpacked(self):
        def multi_arg(a, b):
            if a < 0 or b < 0:
                raise ValueError("must be non-negative")
            return a + b

        result = self.tester.test(multi_arg, (-1, 5), ValueError)
        self.assertTrue(result["passed"])

    def test_actual_exc_type_recorded(self):
        result = self.tester.test(_divide, 0, ZeroDivisionError)
        self.assertEqual(result["actual_exc_type"], "ZeroDivisionError")

    def test_actual_msg_recorded(self):
        result = self.tester.test(_must_be_positive, -5, ValueError, "positive")
        self.assertIsNotNone(result["actual_msg"])

    def test_empty_message_fragment_always_ok(self):
        result = self.tester.test(_divide, 0, ZeroDivisionError, "")
        self.assertTrue(result["passed"])


# ---------------------------------------------------------------------------
# 6. NullHandlingTester tests
# ---------------------------------------------------------------------------

class TestNullHandlingTester(unittest.TestCase):

    def setUp(self):
        self.tester = NullHandlingTester()

    def test_none_in_first_arg(self):
        results = self.tester.test_function(_concat, ["hello", "world"])
        # _concat(None, "world") calls str(None) + str("world") = "Noneworld" - ok
        self.assertTrue(results[0]["passed"])

    def test_none_raises_type_error_is_ok(self):
        def strict(x, y):
            if x is None or y is None:
                raise TypeError("no Nones allowed")
            return x + y

        results = self.tester.test_function(strict, [1, 2])
        # Both positions should raise TypeError which is allowed
        self.assertTrue(all(r["passed"] for r in results))

    def test_attribute_error_is_failure(self):
        def crashes_on_none(x):
            return x.upper()  # AttributeError when x is None

        results = self.tester.test_function(crashes_on_none, ["hello"])
        # AttributeError is not allowed by default
        self.assertFalse(results[0]["passed"])

    def test_custom_allowed_exc_types(self):
        def attr_err_raiser(x):
            return x.some_method()

        tester = NullHandlingTester(allowed_exc_types=[AttributeError])
        results = tester.test_function(attr_err_raiser, ["anything"])
        self.assertTrue(results[0]["passed"])

    def test_results_accumulate_across_calls(self):
        self.tester.test_function(_concat, ["a", "b"])
        self.tester.test_function(_concat, ["c", "d"])
        # 2 params × 2 calls = 4 results
        self.assertEqual(len(self.tester.results()), 4)

    def test_all_passed_when_all_handle_gracefully(self):
        results = self.tester.test_function(_concat, ["x", "y"])
        self.assertTrue(self.tester.all_passed())

    def test_param_index_recorded(self):
        results = self.tester.test_function(_concat, ["a", "b"])
        indices = [r["param_index"] for r in results]
        self.assertEqual(sorted(indices), [0, 1])

    def test_each_position_tested_independently(self):
        results = self.tester.test_function(_add_items, [[1, 2], "item"])
        # Position 0: None instead of list → TypeError (allowed)
        # Position 1: None is a valid item → appended ok
        self.assertEqual(len(results), 2)

    def test_value_error_is_allowed(self):
        results = self.tester.test_function(_must_be_positive, [5])
        # None raises TypeError, which is allowed
        self.assertTrue(results[0]["passed"])


# ---------------------------------------------------------------------------
# 7. BoundaryTester tests
# ---------------------------------------------------------------------------

class TestBoundaryTester(unittest.TestCase):

    def setUp(self):
        self.tester = BoundaryTester()

    def test_empty_string_raises(self):
        result = self.tester.test(_parse_int, "", label="empty_string")
        self.assertTrue(result["passed"])

    def test_none_raises_type_error(self):
        result = self.tester.test(_parse_int, None, label="none",
                                  allowed_exc_types=[TypeError, ValueError])
        self.assertTrue(result["passed"])

    def test_zero_raises_on_positive_only(self):
        result = self.tester.test(_must_be_positive, 0, label="zero")
        self.assertTrue(result["passed"])

    def test_negative_raises(self):
        result = self.tester.test(_must_be_positive, -1, label="negative")
        self.assertTrue(result["passed"])

    def test_no_raise_fails_when_expect_raises(self):
        result = self.tester.test(_must_be_positive, 5, label="valid",
                                  expect_raises=True)
        self.assertFalse(result["passed"])

    def test_expect_no_raise_passes_when_returns(self):
        result = self.tester.test(_must_be_positive, 5, label="valid",
                                  expect_raises=False)
        self.assertTrue(result["passed"])

    def test_check_return_fn(self):
        result = self.tester.test(
            lambda x: x * 2,
            5,
            label="double",
            expect_raises=False,
            check_return_fn=lambda v: v == 10,
        )
        self.assertTrue(result["passed"])

    def test_check_return_fn_fail(self):
        result = self.tester.test(
            lambda x: x * 2,
            5,
            label="double_wrong",
            expect_raises=False,
            check_return_fn=lambda v: v == 99,
        )
        self.assertFalse(result["passed"])

    def test_expected_return_value(self):
        result = self.tester.test(
            lambda x: x + 1,
            4,
            label="increment",
            expect_raises=False,
            expect_return_value=5,
        )
        self.assertTrue(result["passed"])

    def test_expected_return_value_wrong(self):
        result = self.tester.test(
            lambda x: x + 1,
            4,
            label="increment_wrong",
            expect_raises=False,
            expect_return_value=99,
        )
        self.assertFalse(result["passed"])

    def test_test_all_defaults_runs_multiple(self):
        def safe_handler(x):
            if x is None or (isinstance(x, (int, float)) and x <= 0):
                raise ValueError("bad value")
            if isinstance(x, str) and len(x) == 0:
                raise ValueError("empty string")
            if isinstance(x, (list, dict)) and len(x) == 0:
                raise ValueError("empty container")
            return x

        results = self.tester.test_all_defaults(safe_handler)
        self.assertEqual(len(results), len(BoundaryTester.BOUNDARY_INPUTS))

    def test_results_accumulate(self):
        self.tester.test(_must_be_positive, 0)
        self.tester.test(_must_be_positive, -1)
        self.assertEqual(len(self.tester.results()), 2)

    def test_exc_type_recorded(self):
        result = self.tester.test(_must_be_positive, 0)
        self.assertEqual(result["exc_type"], "ValueError")

    def test_label_recorded(self):
        result = self.tester.test(_must_be_positive, 0, label="my_label")
        self.assertEqual(result["label"], "my_label")

    def test_oversized_input_raises(self):
        def limited(x):
            if isinstance(x, str) and len(x) > 1000:
                raise ValueError("too large")
            return x

        result = self.tester.test(limited, "x" * 10000, label="oversized")
        self.assertTrue(result["passed"])

    def test_all_passed_property(self):
        self.tester.test(_must_be_positive, 0)
        self.tester.test(_must_be_positive, -1)
        self.assertTrue(self.tester.all_passed())

    def test_all_passed_false_when_any_fail(self):
        self.tester.test(_must_be_positive, 0)       # passes
        self.tester.test(_must_be_positive, 5,       # fails (no raise)
                         expect_raises=True)
        self.assertFalse(self.tester.all_passed())


# ---------------------------------------------------------------------------
# 8. TimeoutTester tests
# ---------------------------------------------------------------------------

class TestTimeoutTester(unittest.TestCase):

    def setUp(self):
        self.tester = TimeoutTester()

    def test_slow_function_times_out(self):
        result = self.tester.test_expects_timeout(
            _slow_function, args=(5.0,), timeout_seconds=0.2
        )
        self.assertTrue(result["timed_out"])
        self.assertTrue(result["passed"])

    def test_fast_function_does_not_time_out(self):
        result = self.tester.test(_fast_function, timeout_seconds=2.0)
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["passed"])

    def test_test_expects_timeout_fails_when_fast(self):
        result = self.tester.test_expects_timeout(
            _fast_function, timeout_seconds=2.0
        )
        self.assertFalse(result["timed_out"])
        self.assertFalse(result["passed"])

    def test_no_partial_data_check(self):
        committed = []

        def slow_with_side_effect():
            time.sleep(5.0)
            committed.append("data")

        result = self.tester.test_expects_timeout(
            slow_with_side_effect,
            timeout_seconds=0.2,
            check_no_partial_data=lambda: len(committed) == 0,
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["no_partial_data"])

    def test_partial_data_detected(self):
        committed = []

        def leaky_slow():
            committed.append("partial")
            time.sleep(5.0)

        result = self.tester.test_expects_timeout(
            leaky_slow,
            timeout_seconds=0.3,
            check_no_partial_data=lambda: len(committed) == 0,
        )
        # timed out but partial data was committed
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["no_partial_data"])
        self.assertFalse(result["passed"])

    def test_results_accumulate(self):
        self.tester.test(_fast_function)
        self.tester.test(_fast_function)
        self.assertEqual(len(self.tester.results()), 2)

    def test_all_passed_true(self):
        self.tester.test(_fast_function)
        self.assertTrue(self.tester.all_passed())

    def test_func_name_recorded(self):
        result = self.tester.test(_fast_function)
        self.assertEqual(result["func"], "_fast_function")

    def test_timeout_value_recorded(self):
        result = self.tester.test(_fast_function, timeout_seconds=0.5)
        self.assertEqual(result["timeout"], 0.5)

    def test_exception_in_func_recorded(self):
        def raises():
            raise RuntimeError("boom")

        result = self.tester.test(raises)
        self.assertEqual(result["exc_type"], "RuntimeError")
        self.assertIn("boom", result["exc_msg"])
        self.assertTrue(result["passed"])  # completed (with exc) before timeout


# ---------------------------------------------------------------------------
# 9. ResourceCleanupTester tests
# ---------------------------------------------------------------------------

class TestResourceCleanupTester(unittest.TestCase):

    def setUp(self):
        self.tester = ResourceCleanupTester()

    def test_good_implementation_passes(self):
        result = self.tester.test_with_counters(_resource_user_good)
        self.assertFalse(result["leaked"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["acquired"], 1)
        self.assertEqual(result["released"], 1)

    def test_leaking_implementation_on_success_passes(self):
        # When no failure, leaking impl also releases → passes
        result = self.tester.test_with_counters(_resource_user_leaking)
        self.assertFalse(result["leaked"])
        self.assertTrue(result["passed"])

    def test_good_implementation_on_failure_passes(self):
        result = self.tester.test_with_counters(
            _resource_user_good, kwargs={"should_fail": True}
        )
        self.assertFalse(result["leaked"])
        self.assertTrue(result["passed"])

    def test_leaking_implementation_on_failure_flagged(self):
        result = self.tester.test_with_counters(
            _resource_user_leaking, kwargs={"should_fail": True}
        )
        self.assertTrue(result["leaked"])
        self.assertFalse(result["passed"])

    def test_acquire_count_recorded(self):
        result = self.tester.test_with_counters(_resource_user_good)
        self.assertEqual(result["acquired"], 1)

    def test_release_count_recorded(self):
        result = self.tester.test_with_counters(_resource_user_good)
        self.assertEqual(result["released"], 1)

    def test_exception_type_recorded_on_failure(self):
        result = self.tester.test_with_counters(
            _resource_user_good, kwargs={"should_fail": True}
        )
        self.assertEqual(result["exc_type"], "RuntimeError")

    def test_results_accumulate(self):
        self.tester.test_with_counters(_resource_user_good)
        self.tester.test_with_counters(_resource_user_good)
        self.assertEqual(len(self.tester.results()), 2)

    def test_all_passed_true_when_no_leaks(self):
        self.tester.test_with_counters(_resource_user_good)
        self.assertTrue(self.tester.all_passed())

    def test_all_passed_false_when_leak(self):
        self.tester.test_with_counters(_resource_user_good)
        self.tester.test_with_counters(
            _resource_user_leaking, kwargs={"should_fail": True}
        )
        self.assertFalse(self.tester.all_passed())

    def test_multiple_acquisitions_all_released(self):
        def multi_acquire(counters):
            for _ in range(3):
                counters["acquired"] += 1
            try:
                pass
            finally:
                for _ in range(3):
                    counters["released"] += 1

        result = self.tester.test_with_counters(multi_acquire)
        self.assertFalse(result["leaked"])
        self.assertEqual(result["acquired"], 3)

    def test_no_acquisition_not_leaked(self):
        def no_resource(counters):
            return "ok"

        result = self.tester.test_with_counters(no_resource)
        # acquired == 0 → not considered a leak
        self.assertFalse(result["leaked"])
        self.assertTrue(result["passed"])


# ---------------------------------------------------------------------------
# 10. MockErrorPathHandler / ErrorPathServer tests
# ---------------------------------------------------------------------------

class TestErrorPathServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = ErrorPathServer()
        cls.server.start()
        cls.base = cls.server.base_url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _get(self, path, timeout=5):
        url = self.base + path
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _post(self, path, data, timeout=5):
        url = self.base + path
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_server_starts(self):
        self.assertIsNotNone(self.server.port)
        self.assertGreater(self.server.port, 0)

    def test_ok_endpoint_returns_200(self):
        status, body = self._get("/ok")
        self.assertEqual(status, 200)

    def test_ok_endpoint_returns_json(self):
        status, body = self._get("/ok")
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")

    def test_error_endpoint_returns_500(self):
        status, body = self._get("/error")
        self.assertEqual(status, 500)

    def test_error_endpoint_has_message(self):
        status, body = self._get("/error")
        data = json.loads(body)
        self.assertIn("message", data)

    def test_notfound_endpoint_returns_404(self):
        status, body = self._get("/notfound")
        self.assertEqual(status, 404)

    def test_badjson_returns_200_invalid_json(self):
        status, body = self._get("/badjson")
        self.assertEqual(status, 200)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(body)

    def test_empty_endpoint_returns_200_empty_body(self):
        status, body = self._get("/empty")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_unknown_path_returns_404(self):
        status, body = self._get("/does_not_exist")
        self.assertEqual(status, 404)

    def test_probe_endpoint_records_hit(self):
        MockErrorPathHandler.reset_probes()
        label = "test_probe_label"
        self._get(f"/probe/{label}")
        hits = MockErrorPathHandler.get_probe_hits()
        self.assertIn(label, hits)
        self.assertEqual(hits[label], 1)

    def test_probe_endpoint_increments_on_multiple_hits(self):
        MockErrorPathHandler.reset_probes()
        label = "multi_probe"
        self._get(f"/probe/{label}")
        self._get(f"/probe/{label}")
        self._get(f"/probe/{label}")
        hits = MockErrorPathHandler.get_probe_hits()
        self.assertEqual(hits[label], 3)

    def test_post_validate_valid_input(self):
        status, body = self._post("/validate", {"value": 5})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["result"], 10)

    def test_post_validate_missing_field(self):
        status, body = self._post("/validate", {"other": 1})
        self.assertEqual(status, 400)

    def test_post_validate_non_integer(self):
        status, body = self._post("/validate", {"value": "hello"})
        self.assertEqual(status, 400)

    def test_post_validate_negative_value(self):
        status, body = self._post("/validate", {"value": -1})
        self.assertEqual(status, 400)

    def test_post_validate_zero_value(self):
        status, body = self._post("/validate", {"value": 0})
        self.assertEqual(status, 400)

    def test_base_url_format(self):
        self.assertTrue(self.server.base_url.startswith("http://127.0.0.1:"))

    def test_context_manager(self):
        with ErrorPathServer() as srv:
            status, body = urllib.request.urlopen(
                srv.base_url + "/ok", timeout=5
            ).read(), None
        # Just verify it exited cleanly

    def test_reset_probes_clears_all(self):
        MockErrorPathHandler._probe_hits["dummy"] = 5
        MockErrorPathHandler.reset_probes()
        self.assertEqual(MockErrorPathHandler.get_probe_hits(), {})

    def test_find_free_port_returns_int(self):
        port = find_free_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)


# ---------------------------------------------------------------------------
# 11. Integration tests
# ---------------------------------------------------------------------------

class TestIntegration(unittest.TestCase):
    """End-to-end integration tests combining multiple harness components."""

    def test_probe_and_branch_result_integration(self):
        probe = CoverageProbe()
        probe.register(["success_path", "error_path", "timeout_path"])

        # Only hit success and error
        probe.probe("success_path")
        probe.probe("success_path")
        probe.probe("error_path")

        results = probe.get_branch_results()
        by_label = {r.label: r for r in results}

        self.assertEqual(by_label["success_path"].call_count, 2)
        self.assertTrue(by_label["success_path"].hit)
        self.assertTrue(by_label["error_path"].hit)
        self.assertFalse(by_label["timeout_path"].hit)
        self.assertIn("timeout_path", probe.never_hit())

    def test_exception_tester_with_probe(self):
        probe = CoverageProbe()
        exc_tester = ExceptionPathTester()

        def probed_divide(x):
            try:
                result = 10 / x
                probe.probe("success")
                return result
            except ZeroDivisionError:
                probe.probe("zero_division_error")
                raise

        # Test with zero
        exc_tester.test(probed_divide, 0, ZeroDivisionError)
        # Test with valid value
        exc_tester.test(probed_divide, 2, ZeroDivisionError)

        self.assertTrue(probe.hit("zero_division_error"))
        # For x=2, no exception - "No exception raised" recorded
        # success path hit once
        self.assertTrue(probe.hit("success"))

    def test_error_path_report_aggregates(self):
        probe = CoverageProbe()
        probe.register(["a", "b"])
        probe.probe("a")

        report = ErrorPathReport(
            branch_results=probe.get_branch_results(),
            negative_results=[
                NegativeCaseResult(-1, "raises", "raised ValueError", True),
                NegativeCaseResult(0, "raises", "no exception", False),
            ],
            exception_results=[{"passed": True}],
        )

        self.assertEqual(report.total_tests, 5)  # 2 branch + 2 neg + 1 exc
        # passed: branch "a" hit=True, branch "b" hit=False → 1
        #         neg: 1 passed → 1
        #         exc: 1 passed → 1
        self.assertEqual(report.passed_tests, 3)

    def test_boundary_and_null_combined(self):
        boundary = BoundaryTester()
        null_tester = NullHandlingTester()

        def validated_func(x):
            if x is None:
                raise TypeError("cannot be None")
            if not isinstance(x, int):
                raise TypeError("must be int")
            if x <= 0:
                raise ValueError("must be positive")
            return x * 2

        # Boundary tests
        boundary.test(validated_func, 0, label="zero")
        boundary.test(validated_func, -1, label="neg")
        boundary.test(validated_func, 5, label="valid",
                      expect_raises=False, expect_return_value=10)

        # Null test
        null_tester.test_function(validated_func, [5])

        self.assertTrue(boundary.all_passed())
        self.assertTrue(null_tester.all_passed())

    def test_resource_cleanup_with_exception_tester(self):
        exc_tester = ExceptionPathTester()
        cleanup_tester = ResourceCleanupTester()

        state = {"value": 0}

        def resource_operation(x, counters):
            counters["acquired"] += 1
            try:
                if x < 0:
                    raise ValueError(f"negative value: {x}")
                state["value"] = x
                return x
            finally:
                counters["released"] += 1

        # Test that exception is raised correctly
        exc_result = exc_tester.test(
            lambda x: resource_operation(x, {"acquired": 0, "released": 0}),
            -1,
            ValueError,
            "negative",
        )
        self.assertTrue(exc_result["passed"])

        # Test resource cleanup
        cleanup_result = cleanup_tester.test_with_counters(
            lambda counters: resource_operation(-1, counters)
        )
        self.assertFalse(cleanup_result["leaked"])

    def test_timeout_with_server(self):
        """Test that the timeout tester works with real network operations."""
        server = ErrorPathServer()
        server.start()
        try:
            import urllib.request

            def slow_request():
                try:
                    urllib.request.urlopen(
                        server.base_url + "/timeout", timeout=0.2
                    )
                except Exception:
                    pass
                time.sleep(5)  # ensure we time out in the test

            tester = TimeoutTester()
            result = tester.test_expects_timeout(slow_request, timeout_seconds=0.5)
            self.assertTrue(result["timed_out"])
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# 12. Edge case / robustness tests
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_coverage_probe_thread_safety(self):
        """Multiple threads probing same label should not corrupt counts."""
        probe = CoverageProbe()
        probe.register(["shared"])

        def prober():
            for _ in range(100):
                probe.probe("shared")

        threads = [threading.Thread(target=prober) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have exactly 1000 probes
        self.assertEqual(probe.call_count("shared"), 1000)

    def test_exception_tester_with_subclass_exception(self):
        """Subclass exceptions should still match parent type check."""

        class CustomError(ValueError):
            pass

        def raises_custom(x):
            raise CustomError(f"custom: {x}")

        tester = ExceptionPathTester()
        # Test with parent type - should pass since CustomError is-a ValueError
        result = tester.test(raises_custom, "bad", ValueError, "custom")
        self.assertTrue(result["passed"])

    def test_boundary_tester_with_custom_exc_whitelist(self):
        def only_accepts_positive_int(x):
            if isinstance(x, str):
                raise RuntimeError("strings not allowed")
            if x is None:
                raise RuntimeError("None not allowed")
            if isinstance(x, (list, dict)) or (isinstance(x, int) and x <= 0):
                raise RuntimeError("bad input")
            return x

        tester = BoundaryTester()
        # RuntimeError is not in default allowed list
        result = tester.test(only_accepts_positive_int, "",
                             allowed_exc_types=[RuntimeError])
        self.assertTrue(result["passed"])

    def test_null_tester_with_zero_args(self):
        """Function with no arguments - nothing to test."""
        tester = NullHandlingTester()
        results = tester.test_function(lambda: 42, [])
        self.assertEqual(results, [])

    def test_report_default_lists_are_independent_instances(self):
        """Each ErrorPathReport should have its own default lists."""
        r1 = ErrorPathReport()
        r2 = ErrorPathReport()
        r1.branch_results.append(BranchResult("x", True, 1))
        self.assertEqual(len(r2.branch_results), 0)

    def test_timeout_tester_with_kwargs(self):
        def func_with_kwargs(multiplier=1):
            time.sleep(0.01 * multiplier)
            return multiplier * 2

        tester = TimeoutTester()
        result = tester.test(func_with_kwargs, kwargs={"multiplier": 1},
                             timeout_seconds=2.0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["return_value"], 2)

    def test_probe_register_idempotent(self):
        """Registering same label twice should not duplicate it."""
        probe = CoverageProbe()
        probe.register(["a", "a", "b"])
        probe.probe("a")
        # "a" should appear only once in never_hit computation
        never = probe.never_hit()
        self.assertNotIn("a", never)
        # "b" should appear once
        b_count = never.count("b")
        self.assertEqual(b_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
