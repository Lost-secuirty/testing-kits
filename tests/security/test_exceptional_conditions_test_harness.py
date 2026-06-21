"""test_exceptional_conditions_test_harness.py — unittest suite."""

import unittest

from harnesses.security.exceptional_conditions_test_harness import (
    ErrorLeakChecker,
    FailOpenTester,
    ResourceLeakTester,
    SwallowedExceptionScanner,
    list_scenarios,
    run_all_scenarios,
)


def _raise(_):
    raise RuntimeError("boom")


class TestFailOpenTester(unittest.TestCase):
    def setUp(self):
        self.t = FailOpenTester()

    def test_fail_open_flagged(self):
        def guard(x):
            try:
                return _raise(x)
            except Exception:
                return True
        self.assertTrue(self.t.test(guard, "x")[0])

    def test_fail_closed_deny_accepted(self):
        def guard(x):
            try:
                return _raise(x)
            except Exception:
                return False
        self.assertFalse(self.t.test(guard, "x")[0])

    def test_propagating_guard_is_closed(self):
        self.assertFalse(self.t.test(_raise, "x")[0])


class TestSwallowedExceptionScanner(unittest.TestCase):
    def setUp(self):
        self.s = SwallowedExceptionScanner()

    def test_except_pass_flagged(self):
        self.assertTrue(self.s.scan("try:\n    f()\nexcept Exception:\n    pass\n"))

    def test_bare_except_pass_flagged(self):
        self.assertTrue(self.s.scan("try:\n    f()\nexcept:\n    pass\n"))

    def test_failopen_return_true_flagged(self):
        self.assertTrue(self.s.scan("try:\n    f()\nexcept Exception:\n    return True\n"))

    def test_handled_exception_clean(self):
        src = "try:\n    f()\nexcept KeyError as e:\n    log(e)\n    return False\n"
        self.assertEqual(self.s.scan(src), [])

    def test_syntax_error_no_crash(self):
        self.assertEqual(self.s.scan("def ( bad"), [])


class TestErrorLeakChecker(unittest.TestCase):
    def setUp(self):
        self.c = ErrorLeakChecker()

    def test_sanitized_clean(self):
        self.assertFalse(self.c.check('{"error":"internal error","id":"x"}')[0])

    def test_traceback_flagged(self):
        self.assertTrue(self.c.check("Traceback (most recent call last):")[0])

    def test_sql_flagged(self):
        self.assertTrue(self.c.check("error running SELECT * FROM users")[0])

    def test_secret_flagged(self):
        self.assertTrue(self.c.check("key AKIAIOSFODNN7EXAMPLE failed")[0])


class TestResourceLeakTester(unittest.TestCase):
    def setUp(self):
        self.t = ResourceLeakTester()

    def test_released_on_exception(self):
        def body(tracker):
            with self.t.make_resource(tracker):
                raise ValueError("boom")
        self.assertFalse(self.t.leaks(body)[0])

    def test_leak_flagged(self):
        def body(tracker):
            self.t.make_resource(tracker)
            raise ValueError("boom")
        self.assertTrue(self.t.leaks(body)[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 12)


if __name__ == "__main__":
    unittest.main()
