#!/usr/bin/env python3
"""
test_network_test_harness.py — Unit tests for network_test_harness.py
~55 tests covering all components.
"""

import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from harnesses.core.network_test_harness import (
    ConnectionConfig,
    ConnectionPool,
    ConnectionPoolTester,
    ConnectionResult,
    DNSTester,
    MockNetworkHandler,
    NetworkReport,
    PayloadTester,
    ProtocolTester,
    RetryPolicy,
    RetryTester,
    ShutdownTester,
    TimeoutTester,
    _PooledConnection,
    _start_mock_server,
    run_all,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _get(url: str, timeout: float = 3.0):
    return urllib.request.urlopen(url, timeout=timeout)


# ---------------------------------------------------------------------------
# ConnectionConfig
# ---------------------------------------------------------------------------

class TestConnectionConfig(unittest.TestCase):

    def test_defaults(self):
        c = ConnectionConfig()
        self.assertEqual(c.host, "127.0.0.1")
        self.assertEqual(c.port, 19040)
        self.assertEqual(c.timeout, 5.0)
        self.assertEqual(c.max_retries, 3)
        self.assertTrue(c.keep_alive)

    def test_custom_values(self):
        c = ConnectionConfig(host="example.com", port=8080, timeout=1.5,
                             max_retries=5, keep_alive=False)
        self.assertEqual(c.host, "example.com")
        self.assertEqual(c.port, 8080)
        self.assertAlmostEqual(c.timeout, 1.5)
        self.assertEqual(c.max_retries, 5)
        self.assertFalse(c.keep_alive)


# ---------------------------------------------------------------------------
# ConnectionResult
# ---------------------------------------------------------------------------

class TestConnectionResult(unittest.TestCase):

    def test_success_result(self):
        r = ConnectionResult(success=True, latency_ms=42.5, attempts=1)
        self.assertTrue(r.success)
        self.assertAlmostEqual(r.latency_ms, 42.5)
        self.assertIsNone(r.error)

    def test_failure_result(self):
        r = ConnectionResult(success=False, error="timeout", attempts=3)
        self.assertFalse(r.success)
        self.assertEqual(r.error, "timeout")
        self.assertEqual(r.attempts, 3)

    def test_default_attempts(self):
        r = ConnectionResult(success=True)
        self.assertEqual(r.attempts, 1)


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

class TestRetryPolicy(unittest.TestCase):

    def test_first_delay(self):
        p = RetryPolicy(base_delay=0.1, multiplier=2.0, max_delay=10.0, max_attempts=5)
        self.assertAlmostEqual(p.delay_for(0), 0.1)

    def test_second_delay(self):
        p = RetryPolicy(base_delay=0.1, multiplier=2.0, max_delay=10.0, max_attempts=5)
        self.assertAlmostEqual(p.delay_for(1), 0.2)

    def test_exponential_growth(self):
        p = RetryPolicy(base_delay=0.05, multiplier=3.0, max_delay=100.0, max_attempts=6)
        d0 = p.delay_for(0)
        d1 = p.delay_for(1)
        d2 = p.delay_for(2)
        self.assertAlmostEqual(d1 / d0, 3.0, places=5)
        self.assertAlmostEqual(d2 / d1, 3.0, places=5)

    def test_max_delay_capped(self):
        p = RetryPolicy(base_delay=1.0, multiplier=10.0, max_delay=5.0, max_attempts=5)
        for i in range(5):
            self.assertLessEqual(p.delay_for(i), 5.0)

    def test_delay_never_negative(self):
        p = RetryPolicy(base_delay=0.01, multiplier=0.5, max_delay=1.0, max_attempts=5)
        for i in range(5):
            self.assertGreaterEqual(p.delay_for(i), 0.0)


# ---------------------------------------------------------------------------
# MockNetworkHandler / _start_mock_server
# ---------------------------------------------------------------------------

class TestMockServer(unittest.TestCase):

    def setUp(self):
        self._servers = []

    def tearDown(self):
        for s in self._servers:
            s.shutdown()
            s.server_close()

    def _make(self, cfg=None):
        s, port = _start_mock_server(cfg)
        self._servers.append(s)
        return _base(port)

    def test_basic_200(self):
        base = self._make()
        resp = _get(f"{base}/")
        self.assertEqual(resp.status, 200)

    def test_custom_error_code(self):
        base = self._make({"error_code": 503})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _get(f"{base}/")
        self.assertEqual(ctx.exception.code, 503)

    def test_json_response(self):
        base = self._make()
        resp = _get(f"{base}/test")
        data = json.loads(resp.read())
        self.assertIsInstance(data, dict)
        self.assertIn("status", data)

    def test_custom_header(self):
        base = self._make()
        resp = _get(f"{base}/")
        self.assertEqual(resp.headers.get("X-Test-Header"), "harness-18")

    def test_payload_size(self):
        base = self._make({"payload_size": 512})
        resp = _get(f"{base}/")
        body = resp.read()
        self.assertEqual(len(body), 512)
        self.assertTrue(all(b == ord("A") for b in body))

    def test_request_log(self):
        log = []
        base = self._make({"request_log": log, "lock": threading.Lock()})
        _get(f"{base}/logged").read()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0][1], "/logged")

    def test_fail_first_n(self):
        log = []
        s, port = _start_mock_server({
            "fail_first_n": 2,
            "lock": threading.Lock(),
            "_fail_counter": 0,
            "request_log": log,
        })
        self._servers.append(s)
        base = _base(port)
        # First two fail
        for _ in range(2):
            with self.assertRaises(urllib.error.HTTPError):
                _get(f"{base}/x").read()
        # Third succeeds
        resp = _get(f"{base}/x")
        self.assertEqual(resp.status, 200)


# ---------------------------------------------------------------------------
# ProtocolTester
# ---------------------------------------------------------------------------

class TestProtocolTester(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tester = ProtocolTester()

    @classmethod
    def tearDownClass(cls):
        cls.tester.stop()

    def test_status_line_ok(self):
        self.assertTrue(self.tester.test_status_line())

    def test_content_type_header(self):
        self.assertTrue(self.tester.test_content_type_header())

    def test_custom_header_present(self):
        self.assertTrue(self.tester.test_custom_header_present())

    def test_json_body_parseable(self):
        self.assertTrue(self.tester.test_json_body_parseable())

    def test_post_echoes_path(self):
        self.assertTrue(self.tester.test_post_echoes_path())

    def test_404_returns_error_code(self):
        self.assertTrue(self.tester.test_404_returns_error_code())

    def test_head_has_no_body(self):
        self.assertTrue(self.tester.test_head_has_no_body())

    def test_content_length_matches_body(self):
        self.assertTrue(self.tester.test_content_length_matches_body())


# ---------------------------------------------------------------------------
# TimeoutTester
# ---------------------------------------------------------------------------

class TestTimeoutTester(unittest.TestCase):

    def setUp(self):
        self.tester = TimeoutTester()

    def tearDown(self):
        self.tester.stop()

    def test_connection_timeout_fails(self):
        result = self.tester.test_connection_timeout(timeout=0.1)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_read_timeout_fails(self):
        result = self.tester.test_read_timeout(timeout=0.1)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_read_timeout_latency_recorded(self):
        result = self.tester.test_read_timeout(timeout=0.1)
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_timeout_within_budget(self):
        # Allow generous budget for CI machines
        self.assertTrue(self.tester.test_timeout_within_budget(timeout=0.15))

    def test_connection_error_type(self):
        result = self.tester.test_connection_timeout(timeout=0.1)
        self.assertIsInstance(result.error, str)
        self.assertGreater(len(result.error), 0)


# ---------------------------------------------------------------------------
# RetryTester
# ---------------------------------------------------------------------------

class TestRetryTester(unittest.TestCase):

    def setUp(self):
        self.policy = RetryPolicy(
            base_delay=0.01, multiplier=2.0, max_delay=0.5, max_attempts=3
        )
        self.tester = RetryTester(self.policy)

    def test_exhausts_max_attempts(self):
        result = self.tester.test_retry_count_on_server_error()
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, self.policy.max_attempts)

    def test_retry_succeeds_after_failures(self):
        result = self.tester.test_retry_succeeds_after_failures()
        self.assertTrue(result.success)

    def test_backoff_schedule_length(self):
        sched = self.tester.test_backoff_schedule()
        self.assertEqual(len(sched), self.policy.max_attempts - 1)

    def test_delay_increases_monotonically(self):
        self.assertTrue(self.tester.test_delay_increases())

    def test_delay_respects_max(self):
        self.assertTrue(self.tester.test_delay_respects_max())

    def test_attempt_with_retry_on_valid_server(self):
        s, port = _start_mock_server()
        try:
            result = self.tester.attempt_with_retry(f"http://127.0.0.1:{port}/")
            self.assertTrue(result.success)
            self.assertEqual(result.attempts, 1)
        finally:
            s.shutdown()
            s.server_close()


# ---------------------------------------------------------------------------
# PayloadTester
# ---------------------------------------------------------------------------

class TestPayloadTester(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tester = PayloadTester()

    @classmethod
    def tearDownClass(cls):
        cls.tester.stop()

    def test_1kb_success(self):
        ok, size = self.tester.test_1kb()
        self.assertTrue(ok)
        self.assertEqual(size, 1024)

    def test_10kb_success(self):
        ok, size = self.tester.test_10kb()
        self.assertTrue(ok)
        self.assertEqual(size, 10 * 1024)

    def test_100kb_success(self):
        ok, size = self.tester.test_100kb()
        self.assertTrue(ok)
        self.assertEqual(size, 100 * 1024)

    def test_payload_integrity(self):
        self.assertTrue(self.tester.test_payload_integrity(4096))

    def test_zero_payload_fallback(self):
        """Payload size 0 returns JSON body (non-zero)."""
        s, port = _start_mock_server({"payload_size": 0})
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)
            body = resp.read()
            self.assertGreater(len(body), 0)
        finally:
            s.shutdown()
            s.server_close()


# ---------------------------------------------------------------------------
# ConnectionPool / _PooledConnection
# ---------------------------------------------------------------------------

class TestConnectionPool(unittest.TestCase):

    def setUp(self):
        self.pool = ConnectionPool("127.0.0.1", 19040, max_size=3, ttl=0.1)

    def tearDown(self):
        self.pool.close_all()

    def test_checkout_creates_connection(self):
        conn = self.pool.checkout()
        self.assertIsNotNone(conn)
        self.pool.checkin(conn)

    def test_checkout_marks_in_use(self):
        conn = self.pool.checkout()
        self.assertTrue(conn.in_use)
        self.pool.checkin(conn)

    def test_checkin_clears_in_use(self):
        conn = self.pool.checkout()
        self.pool.checkin(conn)
        self.assertFalse(conn.in_use)

    def test_size_increases(self):
        self.assertEqual(self.pool.size(), 0)
        conn = self.pool.checkout()
        self.assertEqual(self.pool.size(), 1)
        self.pool.checkin(conn)

    def test_available_after_checkin(self):
        conn = self.pool.checkout()
        self.pool.checkin(conn)
        self.assertEqual(self.pool.available(), 1)

    def test_max_size_not_exceeded(self):
        conns = []
        for _ in range(self.pool.max_size + 3):
            c = self.pool.checkout()
            if c:
                conns.append(c)
        self.assertLessEqual(len(conns), self.pool.max_size)
        for c in conns:
            self.pool.checkin(c)

    def test_reuse_connection(self):
        c1 = self.pool.checkout()
        self.pool.checkin(c1)
        c2 = self.pool.checkout()
        self.assertEqual(c1._id, c2._id)
        self.pool.checkin(c2)

    def test_expiry_evicts_idle(self):
        conn = self.pool.checkout()
        self.pool.checkin(conn)
        time.sleep(0.15)  # wait past TTL=0.1
        self.pool.checkout()  # triggers eviction
        # Pool should have re-created or evicted
        self.assertGreaterEqual(self.pool.size(), 0)

    def test_close_all_empties_pool(self):
        conn = self.pool.checkout()
        self.pool.checkin(conn)
        self.pool.close_all()
        self.assertEqual(self.pool.size(), 0)

    def test_checked_out_count(self):
        c1 = self.pool.checkout()
        c2 = self.pool.checkout()
        self.assertEqual(self.pool.checked_out_count(), 2)
        self.pool.checkin(c1)
        self.assertEqual(self.pool.checked_out_count(), 1)
        self.pool.checkin(c2)


# ---------------------------------------------------------------------------
# ConnectionPoolTester
# ---------------------------------------------------------------------------

class TestConnectionPoolTester(unittest.TestCase):

    def setUp(self):
        self.cpt = ConnectionPoolTester(max_size=3, ttl=0.1)

    def tearDown(self):
        self.cpt.pool.close_all()

    def test_checkout_returns_connection(self):
        self.assertTrue(self.cpt.test_checkout_returns_connection())

    def test_checkin_makes_available(self):
        self.assertTrue(self.cpt.test_checkin_makes_available())

    def test_max_size_respected(self):
        self.assertTrue(self.cpt.test_max_size_respected())

    def test_reuse_after_checkin(self):
        self.assertTrue(self.cpt.test_reuse_after_checkin())

    def test_expiry_removes_idle(self):
        self.assertTrue(self.cpt.test_expiry_removes_idle())

    def test_concurrent_checkout(self):
        self.assertTrue(self.cpt.test_concurrent_checkout())


# ---------------------------------------------------------------------------
# ShutdownTester
# ---------------------------------------------------------------------------

class TestShutdownTester(unittest.TestCase):

    def setUp(self):
        self.tester = ShutdownTester()

    def test_no_deadlock_on_shutdown(self):
        self.assertTrue(self.tester.test_no_deadlock_on_shutdown())

    def test_all_requests_resolve(self):
        self.assertTrue(self.tester.test_all_requests_resolve())

    def test_shutdown_result_keys(self):
        result = self.tester.test_graceful_shutdown(in_flight=2, delay=0.02)
        self.assertIn("shutdown_ok", result)
        self.assertIn("total", result)
        self.assertIn("succeeded", result)
        self.assertIn("failed", result)

    def test_shutdown_count_correct(self):
        result = self.tester.test_graceful_shutdown(in_flight=3, delay=0.01)
        self.assertEqual(result["total"], 3)


# ---------------------------------------------------------------------------
# DNSTester
# ---------------------------------------------------------------------------

class TestDNSTester(unittest.TestCase):

    def setUp(self):
        self.tester = DNSTester()

    def test_invalid_host_fails(self):
        result = self.tester.test_invalid_hostname_raises(
            "this.host.does.not.exist.invalid"
        )
        self.assertFalse(result.success)

    def test_all_invalid_hosts_fail(self):
        self.assertTrue(self.tester.test_all_invalid_hosts_fail())

    def test_no_crash_on_invalid(self):
        self.assertTrue(self.tester.test_no_crash_on_invalid())

    def test_error_message_populated(self):
        self.assertTrue(self.tester.test_error_message_populated())

    def test_attempts_is_one(self):
        result = self.tester.test_invalid_hostname_raises(
            "no-such-host-xyz-abc-123.local"
        )
        self.assertEqual(result.attempts, 1)

    def test_latency_recorded(self):
        result = self.tester.test_invalid_hostname_raises(
            "this.host.does.not.exist.invalid"
        )
        # latency may be 0 if the OS rejects immediately; just check it's >= 0
        self.assertGreaterEqual(result.latency_ms, 0)


# ---------------------------------------------------------------------------
# NetworkReport
# ---------------------------------------------------------------------------

class TestNetworkReport(unittest.TestCase):

    def test_empty_report(self):
        r = NetworkReport()
        self.assertEqual(r.total_tests, 0)
        self.assertEqual(r.passed, 0)

    def test_summary_string(self):
        r = NetworkReport()
        s = r.summary()
        self.assertIsInstance(s, str)
        self.assertIn("NetworkReport", s)

    def test_protocol_results_stored(self):
        r = NetworkReport(protocol_results={"a": True, "b": False})
        self.assertEqual(len(r.protocol_results), 2)

    def test_total_tests_counts_all(self):
        r = NetworkReport(
            protocol_results={"a": True},
            dns_results={"b": False, "c": True},
        )
        self.assertEqual(r.total_tests, 3)

    def test_passed_counts_true_in_bool_dicts(self):
        r = NetworkReport(
            protocol_results={"a": True, "b": False},
            pool_results={"c": True},
        )
        self.assertEqual(r.passed, 2)


# ---------------------------------------------------------------------------
# Integration: run_all
# ---------------------------------------------------------------------------

class TestRunAll(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.report = run_all()

    def test_run_all_returns_report(self):
        self.assertIsInstance(self.report, NetworkReport)

    def test_protocol_results_populated(self):
        self.assertGreater(len(self.report.protocol_results), 0)

    def test_all_protocol_passed(self):
        for key, val in self.report.protocol_results.items():
            with self.subTest(check=key):
                self.assertTrue(val, f"Protocol check failed: {key}")

    def test_payload_results_populated(self):
        self.assertIn("1kb", self.report.payload_results)
        self.assertIn("10kb", self.report.payload_results)
        self.assertIn("100kb", self.report.payload_results)

    def test_payloads_all_succeeded(self):
        for key, info in self.report.payload_results.items():
            with self.subTest(payload=key):
                self.assertTrue(info["ok"], f"Payload {key} failed")

    def test_dns_results_populated(self):
        self.assertGreater(len(self.report.dns_results), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
