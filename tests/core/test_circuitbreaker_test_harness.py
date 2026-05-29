"""test_circuitbreaker_test_harness.py â€” unittest suite for circuitbreaker_test_harness (44)."""

import unittest

from harnesses.core.circuitbreaker_test_harness import (
    CLOSED,
    OPEN,
    HALF_OPEN,
    CircuitBreaker,
    CircuitBreakerOracle,
    CircuitOpenError,
    FakeClock,
    run_all_scenarios,
)


def boom():
    raise RuntimeError("downstream failure")


class TestBasicTransitions(unittest.TestCase):

    def test_starts_closed(self):
        cb = CircuitBreaker(clock=FakeClock())
        self.assertEqual(cb.state, CLOSED)

    def test_trips_open_on_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                cb.call(boom)
        self.assertEqual(cb.state, OPEN)

    def test_below_threshold_stays_closed(self):
        cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cb.call(boom)
        self.assertEqual(cb.state, CLOSED)

    def test_success_resets_counter(self):
        cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cb.call(boom)
        cb.call(lambda: "ok")
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        self.assertEqual(cb.state, CLOSED)

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            CircuitBreaker(failure_threshold=0)
        with self.assertRaises(ValueError):
            CircuitBreaker(success_threshold=0)
        with self.assertRaises(ValueError):
            CircuitBreaker(half_open_max_calls=0)


class TestOpenAndHalfOpen(unittest.TestCase):

    def test_open_rejects(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        with self.assertRaises(CircuitOpenError):
            cb.call(lambda: "ok")

    def test_half_open_after_timeout(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        self.assertEqual(cb.state, OPEN)
        clk.advance(10.0)
        self.assertEqual(cb.state, HALF_OPEN)

    def test_trial_success_closes(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        clk.advance(5.0)
        cb.call(lambda: "ok")
        self.assertEqual(cb.state, CLOSED)

    def test_trial_failure_retrips(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        clk.advance(5.0)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        self.assertEqual(cb.state, OPEN)

    def test_half_open_cap(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0,
                            half_open_max_calls=1, success_threshold=2, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        clk.advance(5.0)
        self.assertTrue(cb.allow())
        self.assertFalse(cb.allow())

    def test_success_threshold_gt_one(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0,
                            half_open_max_calls=3, success_threshold=2, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        clk.advance(5.0)
        cb.call(lambda: "ok")
        self.assertEqual(cb.state, HALF_OPEN)
        cb.call(lambda: "ok")
        self.assertEqual(cb.state, CLOSED)

    def test_open_before_timeout_stays_gated(self):
        clk = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(boom)
        clk.advance(5.0)
        self.assertEqual(cb.state, OPEN)
        self.assertFalse(cb.allow())


class TestOracle(unittest.TestCase):

    def _replay(self, events, **cfg):
        clk = FakeClock()
        cb = CircuitBreaker(clock=clk, **cfg)
        for ev in events:
            if ev[0] == "advance":
                clk.advance(ev[1])
            elif ev[0] == "ok":
                try:
                    cb.call(lambda: "ok")
                except CircuitOpenError:
                    pass
            else:
                try:
                    cb.call(boom)
                except (RuntimeError, CircuitOpenError):
                    pass
        return cb.state

    def test_oracle_matches_mixed_log(self):
        events = [("fail",), ("fail",), ("ok",), ("fail",), ("fail",),
                  ("fail",), ("advance", 30.0), ("ok",)]
        cfg = dict(failure_threshold=3, reset_timeout=30.0,
                   half_open_max_calls=1, success_threshold=1)
        expected = CircuitBreakerOracle.final_state(events, **cfg)
        self.assertEqual(self._replay(events, **cfg), expected)

    def test_oracle_matches_recovery(self):
        events = [("fail",), ("fail",), ("advance", 10.0), ("ok",), ("ok",)]
        cfg = dict(failure_threshold=2, reset_timeout=10.0,
                   half_open_max_calls=2, success_threshold=2)
        expected = CircuitBreakerOracle.final_state(events, **cfg)
        self.assertEqual(self._replay(events, **cfg), expected)

    def test_oracle_no_premature_promote(self):
        events = [("fail",), ("advance", 4.0), ("fail",)]
        cfg = dict(failure_threshold=1, reset_timeout=10.0)
        expected = CircuitBreakerOracle.final_state(events, **cfg)
        self.assertEqual(expected, OPEN)


class TestSelfTest(unittest.TestCase):

    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(run_all_scenarios(verbose=False)), 13)


if __name__ == "__main__":
    unittest.main()
