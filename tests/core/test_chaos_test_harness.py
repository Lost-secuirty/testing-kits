"""
Unit tests for chaos_test_harness.py  (Harness 7 of 36)

46 tests covering:
  - CircuitBreaker state machine
  - FaultInjector (latency, error, timeout, corruption)
  - retry_with_backoff
  - ResilienceMetrics
  - MockChaosHandler / HTTP server
  - ResilienceTestRunner
  - FallbackRegistry / graceful degradation
  - Recovery / cooldown behaviour
"""

import contextlib
import json
import time
import unittest
from unittest.mock import MagicMock

from harnesses._teeth import verify
from harnesses.core.chaos_test_harness import (
    BREAKER_CORPUS,
    TEETH,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    FallbackRegistry,
    FaultInjector,
    FaultType,
    ResilienceMetrics,
    ResilienceTestRunner,
    http_get,
    http_get_json,
    is_transient,
    list_scenarios,
    oracle_run,
    prove,
    retry_with_backoff,
    serves_while_open,
    start_mock_server,
    trips_one_late,
)

# ---------------------------------------------------------------------------
# Shared server fixture
# ---------------------------------------------------------------------------

def setUpModule():
    global _server, _port, _base_url
    _server, _port, _base_url = start_mock_server()


def tearDownModule():
    _server.shutdown()
    _server.server_close()


def _url(path: str) -> str:
    return f"{_base_url}{path}"


# ---------------------------------------------------------------------------
# 1. CircuitBreaker – CLOSED state
# ---------------------------------------------------------------------------

class TestCircuitBreakerClosed(unittest.TestCase):

    def _make_cb(self, threshold=3):
        return CircuitBreaker(failure_threshold=threshold, open_duration=60.0)

    def test_initial_state_is_closed(self):
        cb = self._make_cb()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_successful_call_returns_value(self):
        cb = self._make_cb()
        result = cb.call(lambda: 42)
        self.assertEqual(result, 42)

    def test_closed_state_after_success(self):
        cb = self._make_cb()
        cb.call(lambda: "hello")
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_failure_increments_count(self):
        cb = self._make_cb(threshold=5)
        with contextlib.suppress(ConnectionError):
            cb.call(lambda: (_ for _ in ()).throw(ConnectionError("fail")))
        self.assertEqual(cb.failure_count, 1)

    def test_failure_below_threshold_stays_closed(self):
        cb = self._make_cb(threshold=3)
        for _ in range(2):
            with contextlib.suppress(ConnectionError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_success_resets_failure_count(self):
        cb = self._make_cb(threshold=3)
        with contextlib.suppress(ConnectionError):
            cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        cb.call(lambda: "ok")  # success resets
        self.assertEqual(cb.failure_count, 0)

    def test_non_transient_exception_still_counted(self):
        cb = self._make_cb(threshold=2)
        with contextlib.suppress(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(cb.failure_count, 1)


# ---------------------------------------------------------------------------
# 2. CircuitBreaker – OPEN state
# ---------------------------------------------------------------------------

class TestCircuitBreakerOpen(unittest.TestCase):

    def _tripped_cb(self, threshold=2, open_duration=60.0):
        cb = CircuitBreaker(failure_threshold=threshold, open_duration=open_duration)
        for _ in range(threshold):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError("fail")))
        return cb

    def test_transitions_to_open_after_threshold(self):
        cb = self._tripped_cb(threshold=2)
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_open_circuit_raises_circuit_open_error(self):
        cb = self._tripped_cb(threshold=2)
        with self.assertRaises(CircuitOpenError):
            cb.call(lambda: "should not reach")

    def test_open_circuit_does_not_call_fn(self):
        cb = self._tripped_cb(threshold=2)
        fn = MagicMock()
        with contextlib.suppress(CircuitOpenError):
            cb.call(fn)
        fn.assert_not_called()

    def test_fallback_returned_when_open(self):
        cb = CircuitBreaker(failure_threshold=2, open_duration=60.0, fallback="default")
        for _ in range(2):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        # Manually use a runner that handles fallback
        runner = ResilienceTestRunner(circuit_breaker=cb)
        result = runner.run(lambda: "live", use_fault_injector=False)
        self.assertEqual(result, "default")

    def test_reset_closes_circuit(self):
        cb = self._tripped_cb(threshold=2)
        self.assertEqual(cb.state, CircuitState.OPEN)
        cb.reset()
        self.assertEqual(cb.state, CircuitState.CLOSED)


# ---------------------------------------------------------------------------
# 3. CircuitBreaker – HALF_OPEN state
# ---------------------------------------------------------------------------

class TestCircuitBreakerHalfOpen(unittest.TestCase):

    def _open_cb(self, open_duration=0.05):
        cb = CircuitBreaker(failure_threshold=2, open_duration=open_duration)
        for _ in range(2):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        return cb

    def test_transitions_to_half_open_after_cooldown(self):
        cb = self._open_cb(open_duration=0.05)
        time.sleep(0.1)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_probe_success_closes_circuit(self):
        cb = self._open_cb(open_duration=0.05)
        time.sleep(0.1)
        cb.call(lambda: "probe ok")
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_half_open_probe_failure_reopens_circuit(self):
        cb = self._open_cb(open_duration=0.05)
        time.sleep(0.1)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        with contextlib.suppress(ConnectionError, CircuitOpenError):
            cb.call(lambda: (_ for _ in ()).throw(ConnectionError("probe fail")))
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_full_cycle_closed_open_half_open_closed(self):
        cb = CircuitBreaker(failure_threshold=2, open_duration=0.05)
        self.assertEqual(cb.state, CircuitState.CLOSED)

        for _ in range(2):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        self.assertEqual(cb.state, CircuitState.OPEN)

        time.sleep(0.1)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        cb.call(lambda: "recovery")
        self.assertEqual(cb.state, CircuitState.CLOSED)


# ---------------------------------------------------------------------------
# 4. FaultInjector
# ---------------------------------------------------------------------------

class TestFaultInjector(unittest.TestCase):

    def test_no_fault_passes_through(self):
        fi = FaultInjector()
        result = fi.wrap(lambda: 99)()
        self.assertEqual(result, 99)

    def test_fault_type_default_none(self):
        fi = FaultInjector()
        self.assertEqual(fi.fault_type, FaultType.NONE)
        self.assertFalse(fi.enabled)

    def test_inject_error_raises_runtime_error(self):
        fi = FaultInjector().inject_error("boom")
        with self.assertRaises(RuntimeError) as ctx:
            fi.wrap(lambda: None)()
        self.assertIn("boom", str(ctx.exception))

    def test_inject_latency_adds_delay(self):
        fi = FaultInjector().inject_latency(100)  # 100 ms
        t0 = time.monotonic()
        fi.wrap(lambda: None)()
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.assertGreater(elapsed_ms, 80)

    def test_inject_latency_still_returns_value(self):
        fi = FaultInjector().inject_latency(10)
        result = fi.wrap(lambda: "hello")()
        self.assertEqual(result, "hello")

    def test_inject_corruption_modifies_output(self):
        fi = FaultInjector().inject_corruption(lambda v: "CORRUPTED")
        result = fi.wrap(lambda: "original")()
        self.assertEqual(result, "CORRUPTED")

    def test_inject_corruption_default_reverses_string(self):
        fi = FaultInjector().inject_corruption()
        result = fi.wrap(lambda: "abc")()
        self.assertEqual(result, "cba")

    def test_disable_stops_injection(self):
        fi = FaultInjector().inject_error("should not raise")
        fi.disable()
        result = fi.wrap(lambda: "ok")()
        self.assertEqual(result, "ok")
        self.assertFalse(fi.enabled)

    def test_fault_type_latency_set_correctly(self):
        fi = FaultInjector().inject_latency(50)
        self.assertEqual(fi.fault_type, FaultType.LATENCY)

    def test_fault_type_error_set_correctly(self):
        fi = FaultInjector().inject_error()
        self.assertEqual(fi.fault_type, FaultType.ERROR)

    def test_call_interface(self):
        fi = FaultInjector()
        result = fi(lambda x: x * 2, 5)
        self.assertEqual(result, 10)


# ---------------------------------------------------------------------------
# 5. retry_with_backoff
# ---------------------------------------------------------------------------

class TestRetryWithBackoff(unittest.TestCase):

    def test_succeeds_on_first_attempt(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        result = retry_with_backoff(fn, max_attempts=3, base_delay=0.0)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 1)

    def test_retries_on_transient_error(self):
        attempts = []

        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("transient")
            return "done"

        result = retry_with_backoff(fn, max_attempts=3, base_delay=0.0, jitter=False)
        self.assertEqual(result, "done")
        self.assertEqual(len(attempts), 3)

    def test_raises_after_max_attempts(self):
        def fn():
            raise ConnectionError("always fails")

        with self.assertRaises(ConnectionError):
            retry_with_backoff(fn, max_attempts=3, base_delay=0.0, jitter=False)

    def test_does_not_retry_non_transient_errors(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("non-transient")

        with self.assertRaises(ValueError):
            retry_with_backoff(fn, max_attempts=5, base_delay=0.0)
        self.assertEqual(len(calls), 1)

    def test_is_transient_recognises_connection_error(self):
        self.assertTrue(is_transient(ConnectionError()))

    def test_is_transient_recognises_timeout_error(self):
        self.assertTrue(is_transient(TimeoutError()))

    def test_is_transient_rejects_value_error(self):
        self.assertFalse(is_transient(ValueError()))

    def test_custom_retryable_predicate(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("retry me")
            return "ok"

        result = retry_with_backoff(
            fn, max_attempts=3, base_delay=0.0,
            retryable=lambda e: isinstance(e, RuntimeError),
        )
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)


# ---------------------------------------------------------------------------
# 6. ResilienceMetrics
# ---------------------------------------------------------------------------

class TestResilienceMetrics(unittest.TestCase):

    def test_initial_counts_are_zero(self):
        m = ResilienceMetrics()
        self.assertEqual(m.success_count, 0)
        self.assertEqual(m.failure_count, 0)
        self.assertEqual(m.open_count, 0)

    def test_record_success(self):
        m = ResilienceMetrics()
        m.record_success(10.0)
        self.assertEqual(m.success_count, 1)

    def test_record_failure(self):
        m = ResilienceMetrics()
        m.record_failure()
        self.assertEqual(m.failure_count, 1)

    def test_record_open(self):
        m = ResilienceMetrics()
        m.record_open()
        self.assertEqual(m.open_count, 1)

    def test_total_calls(self):
        m = ResilienceMetrics()
        m.record_success()
        m.record_failure()
        m.record_open()
        self.assertEqual(m.total_calls, 3)

    def test_success_rate(self):
        m = ResilienceMetrics()
        m.record_success()
        m.record_success()
        m.record_failure()
        self.assertAlmostEqual(m.success_rate, 2 / 3, places=4)

    def test_success_rate_zero_when_no_calls(self):
        m = ResilienceMetrics()
        self.assertEqual(m.success_rate, 0.0)

    def test_avg_latency(self):
        m = ResilienceMetrics()
        m.record_success(100.0)
        m.record_success(200.0)
        self.assertAlmostEqual(m.avg_latency_ms, 150.0)

    def test_reset_clears_all(self):
        m = ResilienceMetrics()
        m.record_success(5.0)
        m.record_failure(3.0)
        m.reset()
        self.assertEqual(m.success_count, 0)
        self.assertEqual(m.failure_count, 0)
        self.assertEqual(m.total_calls, 0)

    def test_summary_dict_keys(self):
        m = ResilienceMetrics()
        s = m.summary()
        self.assertIn("success", s)
        self.assertIn("failure", s)
        self.assertIn("open", s)
        self.assertIn("total", s)
        self.assertIn("success_rate", s)


# ---------------------------------------------------------------------------
# 7. Mock HTTP Server
# ---------------------------------------------------------------------------

class TestMockChaosServer(unittest.TestCase):

    def test_ok_endpoint_returns_200(self):
        code, _ = http_get(_url("/ok"))
        self.assertEqual(code, 200)

    def test_ok_endpoint_returns_json(self):
        code, data = http_get_json(_url("/ok"))
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ok")

    def test_fail_endpoint_returns_500(self):
        code, _ = http_get(_url("/fail"))
        self.assertEqual(code, 500)

    def test_fail_endpoint_custom_code(self):
        code, _ = http_get(_url("/fail?code=503"))
        self.assertEqual(code, 503)

    def test_slow_endpoint_delays_response(self):
        t0 = time.monotonic()
        http_get(_url("/slow?delay=0.2"))
        elapsed = time.monotonic() - t0
        self.assertGreater(elapsed, 0.15)

    def test_corrupt_endpoint_returns_invalid_json(self):
        code, body = http_get(_url("/corrupt"))
        self.assertEqual(code, 200)
        with self.assertRaises((json.JSONDecodeError, ValueError)):
            json.loads(body)

    def test_scenario_ok(self):
        _server.scenario = "ok"
        code, data = http_get_json(_url("/scenario"))
        self.assertEqual(code, 200)

    def test_scenario_error(self):
        _server.scenario = "error"
        code, _ = http_get(_url("/scenario"))
        self.assertEqual(code, 500)

    def test_unknown_path_returns_404(self):
        code, _ = http_get(_url("/does-not-exist"))
        self.assertEqual(code, 404)

    def test_flaky_endpoint_eventually_succeeds(self):
        # Reset the server's counter by using a fresh sub-server
        srv, p, base = start_mock_server()
        srv.fail_count = 2
        try:
            results = []
            for _ in range(5):
                code, _ = http_get(f"{base}/flaky")
                results.append(code)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertIn(200, results)


# ---------------------------------------------------------------------------
# 8. ResilienceTestRunner
# ---------------------------------------------------------------------------

class TestResilienceTestRunner(unittest.TestCase):

    def test_run_success_recorded_in_metrics(self):
        m = ResilienceMetrics()
        runner = ResilienceTestRunner(metrics=m)
        runner.run(lambda: "ok", use_fault_injector=False)
        self.assertEqual(m.success_count, 1)

    def test_run_failure_recorded_in_metrics(self):
        m = ResilienceMetrics()
        cb = CircuitBreaker(failure_threshold=10)
        runner = ResilienceTestRunner(circuit_breaker=cb, metrics=m)
        with self.assertRaises(ConnectionError):
            runner.run(
                lambda: (_ for _ in ()).throw(ConnectionError()),
                use_fault_injector=False,
            )
        self.assertEqual(m.failure_count, 1)

    def test_run_open_recorded_in_metrics(self):
        m = ResilienceMetrics()
        cb = CircuitBreaker(failure_threshold=1, open_duration=60.0, fallback=None)
        runner = ResilienceTestRunner(circuit_breaker=cb, metrics=m)
        # Trip the circuit
        with contextlib.suppress(ConnectionError, CircuitOpenError):
            runner.run(
                lambda: (_ for _ in ()).throw(ConnectionError()),
                use_fault_injector=False,
            )
        # Next call should be open
        with contextlib.suppress(CircuitOpenError):
            runner.run(lambda: "live", use_fault_injector=False)
        self.assertGreaterEqual(m.open_count, 1)

    def test_run_scenario_returns_list(self):
        runner = ResilienceTestRunner()
        results = runner.run_scenario(
            lambda: "ok", n=5,
            use_circuit_breaker=False,
            use_fault_injector=False,
        )
        self.assertEqual(len(results), 5)

    def test_runner_uses_fault_injector(self):
        fi = FaultInjector().inject_error("injected")
        runner = ResilienceTestRunner(
            circuit_breaker=CircuitBreaker(failure_threshold=10),
            fault_injector=fi,
        )
        with self.assertRaises(RuntimeError):
            runner.run(lambda: "should not run")


# ---------------------------------------------------------------------------
# 9. FallbackRegistry / Graceful Degradation
# ---------------------------------------------------------------------------

class TestFallbackRegistry(unittest.TestCase):

    def test_register_and_get_value(self):
        reg = FallbackRegistry()
        reg.register("svc", "cached_value")
        self.assertEqual(reg.get("svc"), "cached_value")

    def test_get_returns_default_when_missing(self):
        reg = FallbackRegistry()
        self.assertIsNone(reg.get("missing"))

    def test_call_with_fallback_uses_primary_on_success(self):
        reg = FallbackRegistry()
        reg.register("svc", "fallback")
        result = reg.call_with_fallback("svc", lambda: "primary")
        self.assertEqual(result, "primary")

    def test_call_with_fallback_uses_fallback_on_error(self):
        reg = FallbackRegistry()
        reg.register("svc", "fallback_value")
        result = reg.call_with_fallback(
            "svc",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        self.assertEqual(result, "fallback_value")

    def test_call_with_callable_fallback(self):
        reg = FallbackRegistry()
        reg.register("svc", lambda: "computed_fallback")
        result = reg.call_with_fallback(
            "svc",
            lambda: (_ for _ in ()).throw(RuntimeError()),
        )
        self.assertEqual(result, "computed_fallback")

    def test_raises_when_no_fallback_registered(self):
        reg = FallbackRegistry()
        with self.assertRaises(RuntimeError):
            reg.call_with_fallback(
                "unregistered",
                lambda: (_ for _ in ()).throw(RuntimeError("no fallback")),
            )


# ---------------------------------------------------------------------------
# 10. Recovery / Cooldown Integration
# ---------------------------------------------------------------------------

class TestRecoveryBehavior(unittest.TestCase):

    def test_circuit_recovers_after_cooldown(self):
        """Full end-to-end: trip → wait → recover."""
        cb = CircuitBreaker(failure_threshold=2, open_duration=0.1)

        # Trip it
        for _ in range(2):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        self.assertEqual(cb.state, CircuitState.OPEN)

        # Wait for cooldown
        time.sleep(0.15)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # Probe succeeds → closed
        cb.call(lambda: "recovered")
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_metrics_track_full_lifecycle(self):
        m = ResilienceMetrics()
        cb = CircuitBreaker(failure_threshold=2, open_duration=0.1, fallback="fb")
        runner = ResilienceTestRunner(circuit_breaker=cb, metrics=m)

        # Two failures → open
        for _ in range(2):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                runner.run(
                    lambda: (_ for _ in ()).throw(ConnectionError()),
                    use_fault_injector=False,
                )

        # Open call → returns fallback, records open
        result = runner.run(lambda: "live", use_fault_injector=False)
        self.assertEqual(result, "fb")

        # Wait + recover
        time.sleep(0.15)
        runner.run(lambda: "ok", use_fault_injector=False)

        self.assertGreater(m.success_count, 0)
        self.assertGreater(m.failure_count, 0)
        self.assertGreater(m.open_count, 0)

    def test_second_trip_after_half_open_failure(self):
        """Half-open probe failure should re-trip to OPEN."""
        cb = CircuitBreaker(failure_threshold=2, open_duration=0.05)
        for _ in range(2):
            with contextlib.suppress(ConnectionError, CircuitOpenError):
                cb.call(lambda: (_ for _ in ()).throw(ConnectionError()))

        time.sleep(0.08)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        with contextlib.suppress(ConnectionError, CircuitOpenError):
            cb.call(lambda: (_ for _ in ()).throw(ConnectionError("probe fail")))
        self.assertEqual(cb.state, CircuitState.OPEN)


# ---------------------------------------------------------------------------
# 11. Teeth — the frozen circuit-breaker oracle/mutant swap-check
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    """The teeth contract: prove() catches realistic breaker bugs against a
    frozen literal corpus, the correct oracle is clean, and prove() is
    non-circular (it compares to baked-in literals, never to a runtime oracle)."""

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(len(BREAKER_CORPUS), 1)
        self.assertEqual(TEETH.corpus_size, len(BREAKER_CORPUS))

    def test_oracle_is_clean(self):
        # The correct breaker is NOT flagged by its own corpus.
        self.assertIs(prove(oracle_run), False)

    def test_every_mutant_is_caught(self):
        # Each planted defect is flagged against the frozen corpus.
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertIs(prove(mutant.impl), True)

    def test_trips_one_late_caught(self):
        self.assertIs(prove(trips_one_late), True)

    def test_serves_while_open_caught(self):
        self.assertIs(prove(serves_while_open), True)

    def test_verify_reports_full_teeth(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"])
        self.assertTrue(result["teeth_verified"])
        self.assertTrue(result["oracle_clean"])
        self.assertEqual(result["mutants_caught"], len(TEETH.mutants))
        self.assertEqual(result["mutants_uncaught"], [])

    def test_oracle_matches_every_frozen_literal(self):
        # Independent of prove(): the oracle reproduces each hand-computed
        # observation tuple exactly.
        for case in BREAKER_CORPUS:
            with self.subTest(case=case.name):
                got = oracle_run(
                    case.timeline, case.failure_threshold, case.open_duration
                )
                self.assertEqual(tuple(got), case.expected)

    def test_prove_is_non_circular(self):
        """Flipping ONE frozen literal expectation must make prove(oracle) True.

        If prove() derived its expectations by calling oracle() at runtime, a
        corrupted corpus would still pass (vacuously green). Because the
        expectations are baked-in literals, a single flipped tuple is enough to
        flag the otherwise-correct oracle — proving the comparison is against
        the frozen corpus, not a reference impl."""
        import dataclasses

        import harnesses.core.chaos_test_harness as mod

        original = mod.BREAKER_CORPUS
        self.assertIs(prove(oracle_run), False)  # baseline: clean

        # Corrupt the discriminating reject expectation: ('OPEN', True) ->
        # ('OPEN', False) in the 'reject_while_open' case.
        target = original[1]
        self.assertEqual(target.name, "reject_while_open")
        corrupted = dataclasses.replace(
            target, expected=target.expected[:3] + (("OPEN", False),)
        )
        mod.BREAKER_CORPUS = original[:1] + (corrupted,) + original[2:]
        try:
            self.assertIs(prove(mod.oracle_run), True)
        finally:
            mod.BREAKER_CORPUS = original
        # Restored: clean again.
        self.assertIs(prove(oracle_run), False)

    def test_mutant_discriminated_by_intended_case(self):
        """Each mutant is caught by the case designed to expose it (and the
        off-by-one mutant specifically fails the exact-threshold trip case)."""
        def caught_cases(impl):
            return {
                case.name
                for case in BREAKER_CORPUS
                if tuple(
                    impl(case.timeline, case.failure_threshold, case.open_duration)
                )
                != case.expected
            }

        self.assertIn("trip_on_exact_threshold", caught_cases(trips_one_late))
        self.assertIn("reject_while_open", caught_cases(serves_while_open))

    def test_decoy_case_distinguishes_neither_bug(self):
        """The success-reset decoy stays CLOSED for the oracle AND both bugs:
        teeth must come from real transitions, not coincidence on the decoy."""
        decoy = next(c for c in BREAKER_CORPUS if c.name == "success_resets_failure_run")
        for impl in (oracle_run, trips_one_late, serves_while_open):
            with self.subTest(impl=impl.__name__):
                got = impl(decoy.timeline, decoy.failure_threshold, decoy.open_duration)
                self.assertEqual(tuple(got), decoy.expected)

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(list_scenarios(), [c.name for c in BREAKER_CORPUS])


if __name__ == "__main__":
    unittest.main(verbosity=2)
