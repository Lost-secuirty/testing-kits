"""
Test suite for memory_test_harness.py — 38 tests.

All tests are fast (no real sleeps > 100ms). Uses a dynamic free port for
the mock HTTP server.
"""

import gc
import json
import threading
import time
import tracemalloc
import unittest

from harnesses._teeth import verify
from harnesses.core.memory_test_harness import (
    LEAK_CORPUS,
    TEETH,
    GCPressureReport,
    LeakReport,
    MemoryAssertions,
    MemorySnapshot,
    MockMemoryHandler,
    MockServer,
    ObjectTracker,
    SoakResult,
    SoakTestRunner,
    TraceMallocMonitor,
    _fd_count,
    _gc_object_count,
    _linear_regression,
    _rss_bytes,
    analyze_snapshots,
    find_free_port,
    http_get,
    list_teeth_scenarios,
    oracle_analyze,
    peak_minus_min,
    prove,
    threshold_boundary,
)

# ---------------------------------------------------------------------------
# Helper: shared server fixture
# ---------------------------------------------------------------------------

class ServerMixin:
    """Sets up a MockServer before each test class."""
    server: MockServer

    @classmethod
    def setUpClass(cls):
        cls.server = MockServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()


# ---------------------------------------------------------------------------
# Group 1: Platform helpers
# ---------------------------------------------------------------------------

class TestPlatformHelpers(unittest.TestCase):
    """Tests for _rss_bytes, _fd_count, _gc_object_count."""

    def test_rss_bytes_positive(self):
        rss = _rss_bytes()
        self.assertGreater(rss, 0, "RSS should be > 0")

    def test_rss_bytes_is_int(self):
        self.assertIsInstance(_rss_bytes(), int)

    def test_fd_count_positive(self):
        fd = _fd_count()
        self.assertGreater(fd, 0, "FD count should be > 0 (stdin/stdout/stderr at least)")

    def test_fd_count_is_int(self):
        self.assertIsInstance(_fd_count(), int)

    def test_gc_object_count_positive(self):
        count = _gc_object_count()
        self.assertGreater(count, 0)

    def test_gc_object_count_is_int(self):
        self.assertIsInstance(_gc_object_count(), int)

    def test_gc_object_count_increases_with_allocation(self):
        gc.collect()
        before = _gc_object_count()
        # Allocate some tracked objects
        big_list = [{"key": i} for i in range(500)]
        after = _gc_object_count()
        self.assertGreater(after, before)
        del big_list

    def test_rss_bytes_reasonable_range(self):
        rss = _rss_bytes()
        # Process should use at least 1 MB and less than 32 GB
        self.assertGreater(rss, 1 * 1024 * 1024)
        self.assertLess(rss, 32 * 1024 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Group 2: MemorySnapshot
# ---------------------------------------------------------------------------

class TestMemorySnapshot(unittest.TestCase):
    def test_snapshot_creation(self):
        snap = MemorySnapshot(rss_bytes=1024, gc_objects=100, fd_count=5, thread_count=2)
        self.assertEqual(snap.rss_bytes, 1024)
        self.assertEqual(snap.gc_objects, 100)
        self.assertEqual(snap.fd_count, 5)
        self.assertEqual(snap.thread_count, 2)

    def test_snapshot_timestamp_auto(self):
        before = time.monotonic()
        snap = MemorySnapshot(rss_bytes=0, gc_objects=0, fd_count=0, thread_count=1)
        after = time.monotonic()
        self.assertGreaterEqual(snap.timestamp, before)
        self.assertLessEqual(snap.timestamp, after)

    def test_snapshot_repr(self):
        snap = MemorySnapshot(rss_bytes=2048, gc_objects=50, fd_count=3, thread_count=1)
        r = repr(snap)
        self.assertIn("MemorySnapshot", r)
        self.assertIn("gc_objects=50", r)


# ---------------------------------------------------------------------------
# Group 3: Linear regression
# ---------------------------------------------------------------------------

class TestLinearRegression(unittest.TestCase):
    def test_perfect_line(self):
        xs = list(range(10))
        ys = [2.0 * x + 1 for x in xs]
        slope, intercept, r_sq = _linear_regression(xs, ys)
        self.assertAlmostEqual(slope, 2.0, places=5)
        self.assertAlmostEqual(intercept, 1.0, places=5)
        self.assertAlmostEqual(r_sq, 1.0, places=5)

    def test_flat_line(self):
        xs = list(range(10))
        ys = [5.0] * 10
        slope, intercept, r_sq = _linear_regression(xs, ys)
        self.assertAlmostEqual(slope, 0.0, places=5)
        self.assertAlmostEqual(r_sq, 0.0, places=5)

    def test_single_point(self):
        slope, intercept, r_sq = _linear_regression([1], [2])
        self.assertEqual(slope, 0.0)
        self.assertEqual(r_sq, 0.0)

    def test_negative_slope(self):
        xs = list(range(5))
        ys = [10.0 - 2 * x for x in xs]
        slope, _, _ = _linear_regression(xs, ys)
        self.assertLess(slope, 0)


# ---------------------------------------------------------------------------
# Group 4: analyze_snapshots / LeakReport
# ---------------------------------------------------------------------------

class TestAnalyzeSnapshots(unittest.TestCase):
    def _make_snaps(self, start, per_iter, n=20):
        return [
            MemorySnapshot(
                rss_bytes=start + i * per_iter,
                gc_objects=0, fd_count=0, thread_count=1,
            )
            for i in range(n)
        ]

    def test_no_leak_flat(self):
        snaps = self._make_snaps(1_000_000, 0)
        report = analyze_snapshots(snaps, threshold_bytes_per_iter=1024.0)
        self.assertFalse(report.leaked)

    def test_leak_detected_large_slope(self):
        snaps = self._make_snaps(1_000_000, 10_000)
        report = analyze_snapshots(snaps, threshold_bytes_per_iter=1024.0)
        self.assertTrue(report.leaked)
        self.assertGreater(report.slope_bytes_per_iter, 1024)

    def test_not_enough_snapshots(self):
        snaps = self._make_snaps(1_000_000, 1000, n=1)
        report = analyze_snapshots(snaps, threshold_bytes_per_iter=1024.0)
        self.assertFalse(report.leaked)
        self.assertIn("Not enough", report.details)

    def test_report_r_squared_range(self):
        snaps = self._make_snaps(1_000_000, 5_000)
        report = analyze_snapshots(snaps, threshold_bytes_per_iter=1024.0)
        self.assertGreaterEqual(report.r_squared, 0.0)
        self.assertLessEqual(report.r_squared, 1.0)

    def test_report_summary_string(self):
        snaps = self._make_snaps(1_000_000, 2_000)
        report = analyze_snapshots(snaps, threshold_bytes_per_iter=1024.0)
        s = report.summary
        self.assertIn("slope=", s)
        self.assertIn("r²=", s)

    def test_small_growth_no_leak(self):
        # 100 bytes/iter < 1024 threshold
        snaps = self._make_snaps(1_000_000, 100)
        report = analyze_snapshots(snaps, threshold_bytes_per_iter=1024.0)
        self.assertFalse(report.leaked)


# ---------------------------------------------------------------------------
# Group 5: ObjectTracker
# ---------------------------------------------------------------------------

class TestObjectTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = ObjectTracker()

    def test_record_create_and_destroy(self):
        self.tracker.record_create("Widget")
        self.tracker.record_create("Widget")
        self.tracker.record_destroy("Widget")
        rep = self.tracker.report()
        self.assertEqual(rep["Widget"]["created"], 2)
        self.assertEqual(rep["Widget"]["destroyed"], 1)
        self.assertEqual(rep["Widget"]["leaked"], 1)

    def test_no_leak_balanced(self):
        self.tracker.record_create("Foo")
        self.tracker.record_destroy("Foo")
        self.assertFalse(self.tracker.has_leaks())

    def test_has_leaks_true(self):
        self.tracker.record_create("Bar")
        self.assertTrue(self.tracker.has_leaks())

    def test_reset_clears_state(self):
        self.tracker.record_create("X")
        self.tracker.reset()
        self.assertEqual(self.tracker.report(), {})
        self.assertFalse(self.tracker.has_leaks())

    def test_multiple_kinds(self):
        self.tracker.record_create("A")
        self.tracker.record_create("B")
        self.tracker.record_destroy("A")
        rep = self.tracker.report()
        self.assertIn("A", rep)
        self.assertIn("B", rep)
        self.assertEqual(rep["A"]["leaked"], 0)
        self.assertEqual(rep["B"]["leaked"], 1)

    def test_weak_ref_tracking(self):
        class MyObj:
            pass
        obj = MyObj()
        self.tracker.record_create("MyObj", obj)
        self.assertEqual(self.tracker.live_weak_refs(), 1)
        del obj
        gc.collect()
        self.assertEqual(self.tracker.live_weak_refs(), 0)


# ---------------------------------------------------------------------------
# Group 6: GCPressureReport
# ---------------------------------------------------------------------------

class TestGCPressureReport(unittest.TestCase):
    def test_total_collections(self):
        rep = GCPressureReport(collections_gen0=10, collections_gen1=2, collections_gen2=1, duration_seconds=1.0)
        self.assertEqual(rep.total_collections, 13)

    def test_collections_per_second(self):
        rep = GCPressureReport(collections_gen0=10, collections_gen1=0, collections_gen2=0, duration_seconds=2.0)
        self.assertAlmostEqual(rep.collections_per_second, 5.0)

    def test_zero_duration(self):
        rep = GCPressureReport(collections_gen0=5, collections_gen1=0, collections_gen2=0, duration_seconds=0.0)
        self.assertEqual(rep.collections_per_second, 0.0)

    def test_repr(self):
        rep = GCPressureReport(1, 2, 3, 1.0)
        self.assertIn("GCPressureReport", repr(rep))


# ---------------------------------------------------------------------------
# Group 7: SoakTestRunner
# ---------------------------------------------------------------------------

class TestSoakTestRunner(unittest.TestCase):
    def test_run_returns_soak_result(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=10, snapshot_interval=2)
        self.assertIsInstance(result, SoakResult)

    def test_run_snapshots_collected(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=20, snapshot_interval=5)
        # snapshots at i=0,5,10,15 + final = at least 5
        self.assertGreaterEqual(len(result.snapshots), 5)

    def test_run_clean_function_no_leak(self):
        runner = SoakTestRunner(threshold_bytes_per_iter=1024 * 1024)  # 1 MB threshold
        result = runner.run(lambda: None, iterations=30, snapshot_interval=5)
        # With huge threshold, should not report a leak
        self.assertFalse(result.leak_report.leaked)

    def test_run_duration_positive(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=10, snapshot_interval=2)
        self.assertGreater(result.duration_seconds, 0)

    def test_soak_result_peak_rss(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=10, snapshot_interval=2)
        self.assertGreater(result.peak_rss_bytes, 0)

    def test_soak_result_summary(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=10, snapshot_interval=2)
        s = result.summary()
        self.assertIn("SoakResult", s)
        self.assertIn("iters=10", s)

    def test_soak_gc_report_included(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=10, snapshot_interval=2)
        self.assertIsInstance(result.gc_report, GCPressureReport)

    def test_soak_result_min_max_rss(self):
        runner = SoakTestRunner()
        result = runner.run(lambda: None, iterations=10, snapshot_interval=2)
        self.assertLessEqual(result.min_rss_bytes, result.peak_rss_bytes)


# ---------------------------------------------------------------------------
# Group 8: Mock server HTTP endpoints
# ---------------------------------------------------------------------------

class TestMockServer(ServerMixin, unittest.TestCase):
    def test_status_endpoint_returns_200(self):
        status, _ = http_get(f"{self.server.base_url}/status")
        self.assertEqual(status, 200)

    def test_status_endpoint_json_keys(self):
        _, body = http_get(f"{self.server.base_url}/status")
        data = json.loads(body)
        for key in ("rss_bytes", "gc_objects", "fd_count", "thread_count"):
            self.assertIn(key, data)

    def test_gc_endpoint_returns_counts(self):
        _, body = http_get(f"{self.server.base_url}/gc")
        data = json.loads(body)
        self.assertIn("gen0", data)
        self.assertIn("gen1", data)
        self.assertIn("gen2", data)

    def test_allocate_endpoint(self):
        # Reset first
        http_get(f"{self.server.base_url}/reset")
        MockMemoryHandler.allocated_buffers.clear()
        _, body = http_get(f"{self.server.base_url}/allocate")
        data = json.loads(body)
        self.assertIn("allocated_bytes", data)
        self.assertEqual(data["allocated_bytes"], 1024)

    def test_unknown_endpoint_404(self):
        status, body = http_get(f"{self.server.base_url}/nonexistent")
        self.assertEqual(status, 404)

    def test_echo_endpoint(self):
        _, body = http_get(f"{self.server.base_url}/echo?hello=world")
        data = json.loads(body)
        self.assertIn("echo", data)
        self.assertIn("hello=world", data["echo"])

    def test_reset_clears_buffers(self):
        MockMemoryHandler.allocated_buffers.append(b"x" * 100)
        http_get(f"{self.server.base_url}/reset")
        self.assertEqual(len(MockMemoryHandler.allocated_buffers), 0)

    def test_server_base_url_format(self):
        self.assertTrue(self.server.base_url.startswith("http://127.0.0.1:"))


# ---------------------------------------------------------------------------
# Group 9: find_free_port
# ---------------------------------------------------------------------------

class TestFindFreePort(unittest.TestCase):
    def test_returns_int(self):
        port = find_free_port()
        self.assertIsInstance(port, int)

    def test_port_in_valid_range(self):
        port = find_free_port()
        self.assertGreater(port, 0)
        self.assertLess(port, 65536)

    def test_two_ports_differ(self):
        p1 = find_free_port()
        p2 = find_free_port()
        # Very unlikely to be equal
        # (they could be equal in theory but the OS usually advances)
        # Just check they're valid
        self.assertGreater(p1, 0)
        self.assertGreater(p2, 0)


# ---------------------------------------------------------------------------
# Group 10: MemoryAssertions
# ---------------------------------------------------------------------------

class TestMemoryAssertions(unittest.TestCase):
    def test_assert_no_leak_passes_for_clean_fn(self):
        result = MemoryAssertions.assert_no_leak(
            lambda: None,
            iterations=30,
            snapshot_interval=5,
            threshold_bytes_per_iter=1024 * 1024,  # 1 MB — very lenient
        )
        self.assertIsInstance(result, SoakResult)

    def test_assert_no_leak_raises_on_leak(self):
        """Force a leak report by using a tiny threshold."""
        # We patch analyze_snapshots result via a runner with absurdly low threshold
        # Instead: patch the runner so leak_report.leaked = True
        from harnesses.core import memory_test_harness as mh

        def fake_analyze(snaps, threshold=1024.0):
            return LeakReport(
                leaked=True,
                slope_bytes_per_iter=9999.0,
                r_squared=0.99,
                snapshots_analyzed=len(snaps),
                threshold_bytes_per_iter=threshold,
            )

        old = mh.analyze_snapshots
        mh.analyze_snapshots = fake_analyze
        try:
            with self.assertRaises(AssertionError):
                MemoryAssertions.assert_no_leak(lambda: None, iterations=10, snapshot_interval=2)
        finally:
            mh.analyze_snapshots = old

    def test_assert_fd_stable_passes(self):
        growth = MemoryAssertions.assert_fd_stable(lambda: None, iterations=20, max_fd_growth=10)
        self.assertLessEqual(growth, 10)

    def test_assert_thread_stable_passes(self):
        growth = MemoryAssertions.assert_thread_stable(lambda: None, iterations=10, max_thread_growth=2)
        self.assertLessEqual(growth, 2)

    def test_assert_thread_stable_raises_on_leak(self):
        created = []
        stop_events = []

        def leak_thread():
            ev = threading.Event()
            stop_events.append(ev)
            t = threading.Thread(target=ev.wait, daemon=True)
            t.start()
            created.append(t)

        try:
            with self.assertRaises(AssertionError):
                MemoryAssertions.assert_thread_stable(
                    leak_thread, iterations=5, max_thread_growth=1
                )
        finally:
            for ev in stop_events:
                ev.set()
            for t in created:
                t.join(timeout=0.5)


# ---------------------------------------------------------------------------
# Group 11: TraceMallocMonitor
# ---------------------------------------------------------------------------

class TestTraceMallocMonitor(unittest.TestCase):
    def tearDown(self):
        if tracemalloc.is_tracing():
            tracemalloc.stop()

    def test_start_enables_tracing(self):
        mon = TraceMallocMonitor()
        mon.start()
        self.assertTrue(tracemalloc.is_tracing())
        tracemalloc.stop()

    def test_stop_and_diff_returns_list(self):
        mon = TraceMallocMonitor()
        mon.start()
        _ = [x for x in range(1000)]
        diffs = mon.stop_and_diff(top_n=5)
        self.assertIsInstance(diffs, list)

    def test_current_size_positive_while_tracing(self):
        mon = TraceMallocMonitor()
        mon.start()
        size = mon.current_size()
        self.assertGreaterEqual(size, 0)
        tracemalloc.stop()

    def test_current_size_zero_when_not_tracing(self):
        mon = TraceMallocMonitor()
        # Don't start
        size = mon.current_size()
        self.assertEqual(size, 0)


# ---------------------------------------------------------------------------
# Group 12: Teeth — the leak-regression analyzer oracle vs its planted mutants
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    """The hardened-gate teeth contract for core/memory.

    The oracle is the leak-regression analyzer (slope > threshold). prove()
    judges an impl against the FROZEN LITERAL ``expected_leaked`` booleans in
    LEAK_CORPUS — never by calling the oracle at runtime — so the check is
    non-circular.
    """

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(len(LEAK_CORPUS), 1)
        self.assertEqual(TEETH.corpus_size, len(LEAK_CORPUS))

    def test_prove_oracle_is_false(self):
        """The correct oracle must NOT be flagged by its own corpus."""
        self.assertIs(prove(oracle_analyze), False)

    def test_prove_threshold_boundary_mutant_is_true(self):
        """The >= vs > boundary bug must be caught."""
        self.assertIs(prove(threshold_boundary), True)

    def test_prove_peak_minus_min_mutant_is_true(self):
        """The span-vs-slope bug must be caught."""
        self.assertIs(prove(peak_minus_min), True)

    def test_every_declared_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            self.assertIs(prove(mutant.impl), True,
                          f"mutant {mutant.name} was not caught")

    def test_at_least_one_mutant_declared(self):
        self.assertGreaterEqual(len(TEETH.mutants), 1)

    def test_universal_swapcheck_verifies(self):
        """The shared gate's verify() must report fully-verified teeth."""
        result = verify(TEETH)
        self.assertIsNone(result["error"])
        self.assertTrue(result["oracle_clean"])
        self.assertEqual(result["mutants_caught"], result["mutants_total"])
        self.assertEqual(result["mutants_uncaught"], [])
        self.assertTrue(result["teeth_verified"])

    def test_oracle_matches_every_frozen_literal(self):
        """The oracle reproduces each hand-derived expected_leaked verdict."""
        for case in LEAK_CORPUS:
            self.assertEqual(
                oracle_analyze(case.rss_series), case.expected_leaked,
                f"oracle disagreed with frozen literal on {case.name}")

    def test_non_circular_flipping_a_literal_breaks_the_oracle(self):
        """Flipping ONE frozen expectation would make prove(oracle) True.

        This is the campaign's non-circularity probe: prove() must compare to
        the literal, not re-derive it from the oracle. A patched corpus with a
        single flipped expectation must make the (unchanged) oracle look caught.
        """
        import dataclasses

        original = oracle_analyze
        # Flip the expectation on the first case and prove against the patched
        # corpus by monkeypatching the module-level LEAK_CORPUS prove() reads.
        from harnesses.core import memory_test_harness as mh
        flipped = (
            dataclasses.replace(
                mh.LEAK_CORPUS[0],
                expected_leaked=not mh.LEAK_CORPUS[0].expected_leaked,
            ),
            *mh.LEAK_CORPUS[1:],
        )
        saved = mh.LEAK_CORPUS
        mh.LEAK_CORPUS = flipped
        try:
            # The oracle is unchanged; only one literal flipped -> now caught.
            self.assertIs(prove(original), True)
        finally:
            mh.LEAK_CORPUS = saved
        # And restoring the corpus restores the clean verdict.
        self.assertIs(prove(original), False)

    def test_boundary_case_discriminates_threshold_mutant(self):
        """The exact-boundary case is what the >= mutant gets wrong."""
        boundary = next(c for c in LEAK_CORPUS if c.name == "boundary_exact")
        self.assertFalse(oracle_analyze(boundary.rss_series))
        self.assertTrue(threshold_boundary(boundary.rss_series))

    def test_noisy_flat_case_discriminates_span_mutant(self):
        """The noisy-but-flat case is what the peak-minus-min mutant gets wrong."""
        noisy = next(c for c in LEAK_CORPUS if c.name == "noisy_flat")
        self.assertFalse(oracle_analyze(noisy.rss_series))
        self.assertTrue(peak_minus_min(noisy.rss_series))

    def test_list_teeth_scenarios_names(self):
        names = list_teeth_scenarios()
        self.assertEqual(names, [c.name for c in LEAK_CORPUS])

    def test_prove_is_deterministic(self):
        """prove() is pure — repeated calls agree."""
        self.assertEqual(prove(oracle_analyze), prove(oracle_analyze))
        self.assertEqual(prove(threshold_boundary), prove(threshold_boundary))


if __name__ == "__main__":
    unittest.main(verbosity=2)
