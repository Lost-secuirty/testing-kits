#!/usr/bin/env python3
"""
test_stress_harness.py — Test suite for stress_harness.py
==========================================================
Pure unittest (stdlib). Zero dependencies.

Covers:
  1. MetricsCollector — percentile math, record/flush, final report
  2. Scenarios — weighted task expansion, body template resolution
  3. HTTP client & config — defaults, _do_http_request against mock server
  4. Integration — full engine run with mock server, metrics validation

Run:
  python test_stress_harness.py
  python -m unittest test_stress_harness -v
"""

import asyncio
import json
import math
import sys
import threading
import time
import unittest
from unittest.mock import patch

# Import the module under test
from harnesses.core.stress_harness import (
    HarnessConfig,
    MetricsCollector,
    MockHandler,
    RequestResult,
    SCENARIOS,
    StressEngine,
    TaskDef,
    WorkloadModel,
    build_parser,
    build_weighted_task_list,
    resolve_body,
    start_mock_server,
    _do_http_request,
)


# ============================================================
# 1. METRICS COLLECTOR TESTS
# ============================================================

class TestPercentile(unittest.TestCase):
    """Test the _percentile static method on MetricsCollector."""

    def test_empty_list_returns_zero(self):
        self.assertEqual(MetricsCollector._percentile([], 50), 0.0)

    def test_single_element(self):
        self.assertEqual(MetricsCollector._percentile([42.0], 50), 42.0)
        self.assertEqual(MetricsCollector._percentile([42.0], 99), 42.0)

    def test_two_elements_median(self):
        result = MetricsCollector._percentile([10.0, 20.0], 50)
        self.assertAlmostEqual(result, 15.0, places=2)

    def test_known_percentiles(self):
        # 1..100 — p50 should be ~50.5, p99 should be ~99.01
        data = [float(i) for i in range(1, 101)]
        p50 = MetricsCollector._percentile(data, 50)
        p99 = MetricsCollector._percentile(data, 99)
        self.assertAlmostEqual(p50, 50.5, places=1)
        self.assertGreater(p99, 98.0)
        self.assertLessEqual(p99, 100.0)

    def test_p0_returns_min(self):
        data = [5.0, 10.0, 15.0, 20.0]
        self.assertAlmostEqual(MetricsCollector._percentile(data, 0), 5.0)

    def test_p100_returns_max(self):
        data = [5.0, 10.0, 15.0, 20.0]
        self.assertAlmostEqual(MetricsCollector._percentile(data, 100), 20.0)

    def test_unsorted_input(self):
        """Percentile should work regardless of input order."""
        data = [50.0, 10.0, 90.0, 30.0, 70.0]
        p50 = MetricsCollector._percentile(data, 50)
        self.assertAlmostEqual(p50, 50.0, places=1)

    def test_p99_9_extreme_tail(self):
        data = [float(i) for i in range(1, 1001)]
        p999 = MetricsCollector._percentile(data, 99.9)
        self.assertGreater(p999, 998.0)


class TestMetricsCollector(unittest.TestCase):
    """Test MetricsCollector record/flush/report lifecycle."""

    def _make_result(self, status=200, latency_ms=25.0, error=None,
                     task_name="test_task", scheduled_offset=0.0):
        now = time.monotonic()
        return RequestResult(
            task_name=task_name,
            method="GET",
            url="http://localhost/test",
            status=status,
            latency_ms=latency_ms,
            scheduled_at=now - (latency_ms / 1000.0) - scheduled_offset,
            sent_at=now - (latency_ms / 1000.0),
            completed_at=now,
            error=error,
        )

    def test_empty_collector(self):
        mc = MetricsCollector()
        mc.start()
        self.assertEqual(mc.total_requests, 0)
        self.assertEqual(mc.total_errors, 0)
        report = mc.final_report()
        self.assertIn("No requests were recorded", report)

    def test_record_success(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result(status=200))
        self.assertEqual(mc.total_requests, 1)
        self.assertEqual(mc.total_success, 1)
        self.assertEqual(mc.total_errors, 0)

    def test_record_http_error(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result(status=500))
        self.assertEqual(mc.total_errors, 1)
        self.assertEqual(mc.total_success, 0)

    def test_record_connection_error(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result(status=0, error="connection_refused"))
        self.assertEqual(mc.total_errors, 1)

    def test_record_4xx_counted_as_error(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result(status=404))
        self.assertEqual(mc.total_errors, 1)

    def test_status_code_distribution(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result(status=200))
        mc.record(self._make_result(status=200))
        mc.record(self._make_result(status=500))
        self.assertEqual(mc.status_counts[200], 2)
        self.assertEqual(mc.status_counts[500], 1)

    def test_flush_interval_clears_buffer(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result())
        mc.record(self._make_result())
        stats1 = mc.flush_interval()
        self.assertEqual(stats1["count"], 2)
        # Second flush should be empty
        stats2 = mc.flush_interval()
        self.assertEqual(stats2["count"], 0)
        # But total_requests should still be 2
        self.assertEqual(mc.total_requests, 2)

    def test_flush_interval_has_expected_keys(self):
        mc = MetricsCollector()
        mc.start()
        mc.record(self._make_result())
        stats = mc.flush_interval()
        for key in ("elapsed_s", "count", "rps", "errors", "error_pct",
                     "p50_ms", "p95_ms", "p99_ms"):
            self.assertIn(key, stats, f"Missing key: {key}")

    def test_final_report_contains_sections(self):
        mc = MetricsCollector()
        mc.start()
        for _ in range(10):
            mc.record(self._make_result(task_name="read"))
        for _ in range(5):
            mc.record(self._make_result(task_name="write"))
        report = mc.final_report()
        self.assertIn("FINAL REPORT", report)
        self.assertIn("Corrected Latency", report)
        self.assertIn("Raw Latency", report)
        self.assertIn("Status Code Distribution", report)
        self.assertIn("Per-Task Breakdown", report)
        self.assertIn("read:", report)
        self.assertIn("write:", report)

    def test_corrected_latency_larger_when_delayed(self):
        """If a request was scheduled 100ms before it was sent,
        corrected latency should be higher than raw latency."""
        r = self._make_result(latency_ms=25.0, scheduled_offset=0.1)
        self.assertGreater(r.corrected_latency_ms, r.latency_ms)


# ============================================================
# 2. SCENARIO & TEMPLATE TESTS
# ============================================================

class TestBuildWeightedTaskList(unittest.TestCase):
    """Test weighted task expansion."""

    def test_single_task_weight_1(self):
        tasks = [TaskDef(name="a", method="GET", path="/", weight=1)]
        expanded = build_weighted_task_list(tasks)
        self.assertEqual(len(expanded), 1)

    def test_weight_distribution(self):
        tasks = [
            TaskDef(name="read", method="GET", path="/", weight=5),
            TaskDef(name="write", method="POST", path="/api", weight=1),
        ]
        expanded = build_weighted_task_list(tasks)
        self.assertEqual(len(expanded), 6)
        reads = [t for t in expanded if t.name == "read"]
        writes = [t for t in expanded if t.name == "write"]
        self.assertEqual(len(reads), 5)
        self.assertEqual(len(writes), 1)

    def test_all_scenarios_expand_correctly(self):
        """Every built-in scenario should expand to sum of weights."""
        for name, tasks in SCENARIOS.items():
            expanded = build_weighted_task_list(tasks)
            expected = sum(t.weight for t in tasks)
            self.assertEqual(len(expanded), expected,
                             f"Scenario '{name}' expansion mismatch")

    def test_empty_task_list(self):
        expanded = build_weighted_task_list([])
        self.assertEqual(expanded, [])


class TestResolveBody(unittest.TestCase):
    """Test body template resolution."""

    def test_none_body(self):
        self.assertIsNone(resolve_body(None, 1))

    def test_timestamp_replaced(self):
        body = {"ts": "{{TIMESTAMP}}"}
        result = resolve_body(body, 1)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertNotEqual(parsed["ts"], "{{TIMESTAMP}}")
        # Should be ISO format
        self.assertIn("T", parsed["ts"])

    def test_seq_replaced(self):
        body = {"id": "item_{{SEQ}}"}
        result = resolve_body(body, 42)
        parsed = json.loads(result)
        self.assertEqual(parsed["id"], "item_42")

    def test_both_placeholders(self):
        body = {"seq": "{{SEQ}}", "ts": "{{TIMESTAMP}}"}
        result = resolve_body(body, 99)
        parsed = json.loads(result)
        self.assertEqual(parsed["seq"], "99")
        self.assertIn("T", parsed["ts"])

    def test_no_placeholders_passthrough(self):
        body = {"static": "value", "num": 123}
        result = resolve_body(body, 1)
        parsed = json.loads(result)
        self.assertEqual(parsed["static"], "value")
        self.assertEqual(parsed["num"], 123)

    def test_returns_bytes(self):
        body = {"x": 1}
        result = resolve_body(body, 1)
        self.assertIsInstance(result, bytes)


class TestScenarioDefinitions(unittest.TestCase):
    """Validate the built-in scenario structures."""

    def test_all_scenarios_have_tasks(self):
        for name, tasks in SCENARIOS.items():
            self.assertGreater(len(tasks), 0, f"Scenario '{name}' is empty")

    def test_all_tasks_have_valid_methods(self):
        valid_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
        for name, tasks in SCENARIOS.items():
            for t in tasks:
                self.assertIn(t.method, valid_methods,
                              f"Scenario '{name}', task '{t.name}': invalid method '{t.method}'")

    def test_all_tasks_have_paths(self):
        for name, tasks in SCENARIOS.items():
            for t in tasks:
                self.assertTrue(t.path.startswith("/"),
                                f"Scenario '{name}', task '{t.name}': path must start with /")

    def test_expected_scenarios_exist(self):
        expected = {"default", "read_heavy", "write_heavy", "api_crud"}
        self.assertEqual(set(SCENARIOS.keys()), expected)

    def test_weights_all_positive(self):
        for name, tasks in SCENARIOS.items():
            for t in tasks:
                self.assertGreater(t.weight, 0,
                                   f"Scenario '{name}', task '{t.name}': weight must be > 0")


# ============================================================
# 3. CONFIG, CLI, AND HTTP CLIENT TESTS
# ============================================================

class TestHarnessConfig(unittest.TestCase):
    """Test HarnessConfig defaults and construction."""

    def test_defaults(self):
        cfg = HarnessConfig()
        self.assertEqual(cfg.target_url, "http://localhost:8080")
        self.assertEqual(cfg.rate, 100)
        self.assertEqual(cfg.duration, 30)
        self.assertEqual(cfg.max_vus, 500)
        self.assertEqual(cfg.timeout, 10.0)
        self.assertEqual(cfg.ramp_up, 0)
        self.assertEqual(cfg.scenario, "default")
        self.assertEqual(cfg.auth_token, "")
        self.assertEqual(cfg.workload_model, WorkloadModel.OPEN)
        self.assertFalse(cfg.verbose)

    def test_custom_config(self):
        cfg = HarnessConfig(
            target_url="http://example.com",
            rate=500,
            duration=60,
            max_vus=1000,
            scenario="read_heavy",
        )
        self.assertEqual(cfg.target_url, "http://example.com")
        self.assertEqual(cfg.rate, 500)
        self.assertEqual(cfg.scenario, "read_heavy")


class TestCLIParser(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_default_args(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.url, "http://localhost:8080")
        self.assertEqual(args.rate, 100)
        self.assertEqual(args.duration, 30)
        self.assertFalse(args.self_test)
        self.assertFalse(args.verbose)

    def test_custom_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "--url", "http://api.test:9090",
            "--rate", "500",
            "--duration", "60",
            "--scenario", "write_heavy",
            "--ramp-up", "10",
            "--max-vus", "1000",
            "-v",
        ])
        self.assertEqual(args.url, "http://api.test:9090")
        self.assertEqual(args.rate, 500)
        self.assertEqual(args.duration, 60)
        self.assertEqual(args.scenario, "write_heavy")
        self.assertEqual(args.ramp_up, 10)
        self.assertEqual(args.max_vus, 1000)
        self.assertTrue(args.verbose)

    def test_self_test_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--self-test"])
        self.assertTrue(args.self_test)

    def test_invalid_scenario_rejected(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--scenario", "nonexistent"])


class TestStressEngineInit(unittest.TestCase):
    """Test StressEngine construction and validation."""

    def test_invalid_scenario_raises(self):
        cfg = HarnessConfig(scenario="does_not_exist")
        mc = MetricsCollector()
        with self.assertRaises(ValueError) as ctx:
            StressEngine(cfg, mc)
        self.assertIn("does_not_exist", str(ctx.exception))

    def test_valid_scenario_builds_task_pool(self):
        cfg = HarnessConfig(scenario="api_crud")
        mc = MetricsCollector()
        engine = StressEngine(cfg, mc)
        expected_size = sum(t.weight for t in SCENARIOS["api_crud"])
        self.assertEqual(len(engine._task_pool), expected_size)


class TestRequestResult(unittest.TestCase):
    """Test RequestResult corrected latency calculation."""

    def test_corrected_equals_raw_when_no_delay(self):
        """If sent exactly on schedule, corrected == raw."""
        now = time.monotonic()
        r = RequestResult(
            task_name="t", method="GET", url="/",
            status=200, latency_ms=25.0,
            scheduled_at=now - 0.025,
            sent_at=now - 0.025,
            completed_at=now,
        )
        self.assertAlmostEqual(r.corrected_latency_ms, r.latency_ms, places=0)

    def test_corrected_higher_when_queued(self):
        """If request was queued 500ms past schedule, corrected >> raw."""
        now = time.monotonic()
        r = RequestResult(
            task_name="t", method="GET", url="/",
            status=200, latency_ms=25.0,
            scheduled_at=now - 0.525,  # scheduled 525ms ago
            sent_at=now - 0.025,       # sent 25ms ago (500ms late)
            completed_at=now,
        )
        self.assertGreater(r.corrected_latency_ms, 500.0)
        self.assertAlmostEqual(r.latency_ms, 25.0)


# ============================================================
# 4. HTTP CLIENT & INTEGRATION TESTS (require mock server)
# ============================================================

class TestHttpClient(unittest.TestCase):
    """Test _do_http_request against the built-in mock server."""

    @classmethod
    def setUpClass(cls):
        """Start mock server once for all HTTP tests."""
        cls.server = start_mock_server(port=19876)
        time.sleep(0.3)  # let it bind
        cls.base_url = "http://127.0.0.1:19876"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_get_returns_200(self):
        status, err = _do_http_request("GET", f"{self.base_url}/", None, {}, 5.0)
        self.assertEqual(status, 200)
        self.assertIsNone(err)

    def test_post_returns_200(self):
        body = b'{"test": true}'
        headers = {"Content-Type": "application/json"}
        status, err = _do_http_request("POST", f"{self.base_url}/api/data",
                                       body, headers, 5.0)
        self.assertEqual(status, 200)
        self.assertIsNone(err)

    def test_put_returns_200(self):
        status, err = _do_http_request("PUT", f"{self.base_url}/api/update",
                                       b'{"x":1}', {}, 5.0)
        self.assertEqual(status, 200)

    def test_delete_returns_200(self):
        status, err = _do_http_request("DELETE", f"{self.base_url}/api/items/1",
                                       None, {}, 5.0)
        self.assertEqual(status, 200)

    def test_connection_refused(self):
        status, err = _do_http_request("GET", "http://127.0.0.1:1/nope",
                                       None, {}, 2.0)
        self.assertEqual(status, 0)
        self.assertIsNotNone(err)
        self.assertIn("connection", err.lower())

    def test_custom_headers_accepted(self):
        headers = {"Authorization": "Bearer test123", "X-Custom": "value"}
        status, err = _do_http_request("GET", f"{self.base_url}/",
                                       None, headers, 5.0)
        self.assertEqual(status, 200)


class TestIntegrationFullRun(unittest.TestCase):
    """Integration test: run StressEngine against mock server for a short burst."""

    @classmethod
    def setUpClass(cls):
        cls.server = start_mock_server(port=19877)
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_short_run_produces_valid_metrics(self):
        """Run at 30 req/s for 3 seconds — expect ~90 requests, 0 errors."""
        cfg = HarnessConfig(
            target_url="http://127.0.0.1:19877",
            rate=30,
            duration=3,
            max_vus=50,
            scenario="default",
            report_interval=10.0,  # suppress live output during test
            verbose=False,
        )
        metrics = MetricsCollector()
        engine = StressEngine(cfg, metrics)

        asyncio.run(engine.run())

        # Should have roughly 90 requests (30/s * 3s), allow some tolerance
        self.assertGreater(metrics.total_requests, 70,
                           f"Expected ~90 requests, got {metrics.total_requests}")
        self.assertLess(metrics.total_requests, 120,
                        f"Expected ~90 requests, got {metrics.total_requests}")

        # Zero errors against the mock server
        self.assertEqual(metrics.total_errors, 0,
                         f"Expected 0 errors, got {metrics.total_errors}")

        # All should be 200s
        self.assertEqual(metrics.status_counts[200], metrics.total_requests)

        # Latency sanity: mock injects 1-50ms, so p99 should be well under 500ms
        latencies = [r.corrected_latency_ms for r in metrics.results]
        p99 = MetricsCollector._percentile(latencies, 99)
        self.assertLess(p99, 500.0,
                        f"p99 latency {p99:.1f}ms is too high for mock server")

        # Final report should be non-empty
        report = metrics.final_report()
        self.assertIn("FINAL REPORT", report)
        self.assertIn("200:", report)

    def test_ramp_up_works(self):
        """Run with ramp-up — should still complete without errors."""
        cfg = HarnessConfig(
            target_url="http://127.0.0.1:19877",
            rate=50,
            duration=4,
            max_vus=100,
            ramp_up=2,
            scenario="read_heavy",
            report_interval=10.0,
        )
        metrics = MetricsCollector()
        engine = StressEngine(cfg, metrics)

        asyncio.run(engine.run())

        self.assertGreater(metrics.total_requests, 50)
        self.assertEqual(metrics.total_errors, 0)

    def test_multiple_scenarios(self):
        """Verify api_crud scenario produces all 4 task types."""
        cfg = HarnessConfig(
            target_url="http://127.0.0.1:19877",
            rate=40,
            duration=3,
            max_vus=50,
            scenario="api_crud",
            report_interval=10.0,
        )
        metrics = MetricsCollector()
        engine = StressEngine(cfg, metrics)

        asyncio.run(engine.run())

        task_names = {r.task_name for r in metrics.results}
        expected = {"create", "read", "update", "delete"}
        self.assertEqual(task_names, expected,
                         f"Expected tasks {expected}, got {task_names}")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
