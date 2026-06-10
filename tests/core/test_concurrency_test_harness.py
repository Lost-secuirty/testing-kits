"""
Unit tests for concurrency_test_harness.py
Harness 9 of 36 — thread safety and synchronization.
Pure stdlib, zero external dependencies.
All tests are deterministic (use locked/safe variants; avoid flaky unlocked tests).
"""

import threading
import time
import unittest
import urllib.request
import json

from harnesses.core.concurrency_test_harness import (
    SharedCounter,
    UnsafeCounter,
    RaceDetector,
    DeadlockDetector,
    AtomicityChecker,
    ConcurrentListTest,
    ConcurrentDictTest,
    ProducerConsumerTest,
    BarrierTest,
    CountdownLatch,
    CountdownLatchTest,
    ReadWriteLock,
    ReadWriteLockTest,
    SimpleThreadPool,
    ThreadPoolTest,
    SemaphoreTest,
    EventSignalingTest,
    ConditionVariableTest,
    MockServer,
    ConcurrentHTTPTest,
    ConcurrencyTestHarness,
)


# ---------------------------------------------------------------------------
# SharedCounter tests
# ---------------------------------------------------------------------------

class TestSharedCounter(unittest.TestCase):

    def test_initial_value(self):
        c = SharedCounter(0)
        self.assertEqual(c.get(), 0)

    def test_initial_value_custom(self):
        c = SharedCounter(42)
        self.assertEqual(c.get(), 42)

    def test_increment(self):
        c = SharedCounter(0)
        c.increment()
        self.assertEqual(c.get(), 1)

    def test_decrement(self):
        c = SharedCounter(5)
        c.decrement()
        self.assertEqual(c.get(), 4)

    def test_reset(self):
        c = SharedCounter(100)
        c.reset(0)
        self.assertEqual(c.get(), 0)

    def test_concurrent_increments(self):
        """Locked counter must reach exact expected value."""
        c = SharedCounter(0)
        n_threads = 10
        increments = 100
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for _ in range(increments):
                c.increment()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(c.get(), n_threads * increments)

    def test_concurrent_decrements(self):
        expected = 1000
        c = SharedCounter(expected)
        n_threads = 10
        decrements = 100
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for _ in range(decrements):
                c.decrement()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(c.get(), 0)


# ---------------------------------------------------------------------------
# RaceDetector tests
# ---------------------------------------------------------------------------

class TestRaceDetector(unittest.TestCase):

    def test_locked_counter_no_race(self):
        rd = RaceDetector(n_threads=5, increments_per_thread=50)
        result = rd.run_with_locked()
        self.assertEqual(result["actual"], result["expected"])
        self.assertFalse(result["race_detected"])

    def test_locked_counter_result_keys(self):
        rd = RaceDetector(n_threads=3, increments_per_thread=10)
        result = rd.run_with_locked()
        self.assertIn("counter_type", result)
        self.assertIn("expected", result)
        self.assertIn("actual", result)
        self.assertIn("race_detected", result)
        self.assertEqual(result["counter_type"], "locked")

    def test_unlocked_result_keys(self):
        rd = RaceDetector(n_threads=3, increments_per_thread=10)
        result = rd.run_with_unlocked()
        self.assertIn("counter_type", result)
        self.assertEqual(result["counter_type"], "unlocked")

    def test_expected_calculation(self):
        rd = RaceDetector(n_threads=4, increments_per_thread=25)
        self.assertEqual(rd.expected, 100)


# ---------------------------------------------------------------------------
# DeadlockDetector tests
# ---------------------------------------------------------------------------

class TestDeadlockDetector(unittest.TestCase):

    def test_deadlock_detected(self):
        dd = DeadlockDetector(timeout=0.5)
        result = dd.detect()
        self.assertIn("deadlock_detected", result)
        # With conflicting lock ordering, at least one thread should fail to
        # acquire the second lock within the timeout
        self.assertTrue(result["deadlock_detected"])

    def test_safe_ordering_no_deadlock(self):
        dd = DeadlockDetector(timeout=1.0)
        result = dd.detect_safe()
        self.assertFalse(result["deadlock_detected"])
        self.assertTrue(result["all_completed"])

    def test_result_keys_present(self):
        dd = DeadlockDetector(timeout=0.3)
        result = dd.detect()
        for key in ("deadlock_detected", "thread_a_acquired_b", "thread_b_acquired_a", "timeout_used"):
            self.assertIn(key, result)


# ---------------------------------------------------------------------------
# AtomicityChecker tests
# ---------------------------------------------------------------------------

class TestAtomicityChecker(unittest.TestCase):

    def test_atomic_increment_correct(self):
        ac = AtomicityChecker(n_threads=5, ops_per_thread=20)
        result = ac.check_atomic()
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["actual"], result["expected"])

    def test_atomic_result_keys(self):
        ac = AtomicityChecker(n_threads=4, ops_per_thread=10)
        result = ac.check_atomic()
        for key in ("operation", "expected", "actual", "is_correct"):
            self.assertIn(key, result)

    def test_non_atomic_result_keys(self):
        ac = AtomicityChecker(n_threads=4, ops_per_thread=10)
        result = ac.check_non_atomic()
        for key in ("operation", "expected", "actual", "is_correct", "lost_updates"):
            self.assertIn(key, result)


# ---------------------------------------------------------------------------
# ConcurrentListTest
# ---------------------------------------------------------------------------

class TestConcurrentListTest(unittest.TestCase):

    def test_correct_length(self):
        cl = ConcurrentListTest(n_threads=5, items_per_thread=20)
        result = cl.run()
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["actual_length"], result["expected_length"])

    def test_unique_items(self):
        cl = ConcurrentListTest(n_threads=5, items_per_thread=20)
        result = cl.run()
        self.assertTrue(result["items_unique"])

    def test_result_keys(self):
        cl = ConcurrentListTest(n_threads=3, items_per_thread=10)
        result = cl.run()
        for key in ("expected_length", "actual_length", "is_correct", "items_unique"):
            self.assertIn(key, result)


# ---------------------------------------------------------------------------
# ConcurrentDictTest
# ---------------------------------------------------------------------------

class TestConcurrentDictTest(unittest.TestCase):

    def test_correct_key_count(self):
        cd = ConcurrentDictTest(n_threads=5, items_per_thread=20)
        result = cd.run()
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["actual_keys"], result["expected_keys"])

    def test_counter_accumulation_correct(self):
        cd = ConcurrentDictTest(n_threads=5, items_per_thread=20)
        result = cd.run_counter_accumulation()
        self.assertTrue(result["is_correct"])
        self.assertEqual(result["actual_total"], result["expected_total"])

    def test_counter_accumulation_buckets(self):
        cd = ConcurrentDictTest(n_threads=10, items_per_thread=10)
        result = cd.run_counter_accumulation()
        # 10 threads % 5 = 5 unique buckets
        self.assertEqual(result["bucket_count"], 5)


# ---------------------------------------------------------------------------
# ProducerConsumerTest
# ---------------------------------------------------------------------------

class TestProducerConsumerTest(unittest.TestCase):

    def test_all_consumed(self):
        pc = ProducerConsumerTest(n_producers=3, n_consumers=3, items_per_producer=15)
        result = pc.run()
        self.assertTrue(result["all_consumed"])

    def test_no_duplicates(self):
        pc = ProducerConsumerTest(n_producers=3, n_consumers=3, items_per_producer=15)
        result = pc.run()
        self.assertTrue(result["no_duplicates"])

    def test_counts_match(self):
        pc = ProducerConsumerTest(n_producers=2, n_consumers=2, items_per_producer=10)
        result = pc.run()
        self.assertEqual(result["total_produced"], result["total_consumed"])

    def test_result_keys(self):
        pc = ProducerConsumerTest(n_producers=2, n_consumers=2, items_per_producer=5)
        result = pc.run()
        for key in ("total_produced", "total_consumed", "unique_items", "all_consumed", "no_duplicates"):
            self.assertIn(key, result)


# ---------------------------------------------------------------------------
# BarrierTest
# ---------------------------------------------------------------------------

class TestBarrierTest(unittest.TestCase):

    def test_all_threads_complete_phase1(self):
        bt = BarrierTest(n_threads=6)
        result = bt.run()
        self.assertTrue(result["all_completed_phase1"])

    def test_all_threads_complete_phase2(self):
        bt = BarrierTest(n_threads=6)
        result = bt.run()
        self.assertTrue(result["all_completed_phase2"])

    def test_no_errors(self):
        bt = BarrierTest(n_threads=4)
        result = bt.run()
        self.assertEqual(result["errors"], [])

    def test_barrier_action_runs_once(self):
        bt = BarrierTest(n_threads=4)
        result = bt.run_with_action()
        self.assertTrue(result["action_ran_once"])
        self.assertEqual(result["completions"], 4)


# ---------------------------------------------------------------------------
# CountdownLatch tests
# ---------------------------------------------------------------------------

class TestCountdownLatch(unittest.TestCase):

    def test_initial_count(self):
        latch = CountdownLatch(5)
        self.assertEqual(latch.count, 5)

    def test_not_released_initially(self):
        latch = CountdownLatch(3)
        self.assertFalse(latch.is_released())

    def test_released_at_zero_initial(self):
        latch = CountdownLatch(0)
        self.assertTrue(latch.is_released())

    def test_countdown_to_zero(self):
        latch = CountdownLatch(3)
        latch.count_down()
        latch.count_down()
        latch.count_down()
        self.assertEqual(latch.count, 0)
        self.assertTrue(latch.is_released())

    def test_wait_returns_true(self):
        latch = CountdownLatch(1)
        latch.count_down()
        result = latch.wait(timeout=1.0)
        self.assertTrue(result)

    def test_wait_timeout(self):
        latch = CountdownLatch(1)
        result = latch.wait(timeout=0.05)
        self.assertFalse(result)

    def test_count_does_not_go_below_zero(self):
        latch = CountdownLatch(1)
        latch.count_down()
        latch.count_down()  # extra countdown should be ignored
        self.assertEqual(latch.count, 0)

    def test_invalid_count_raises(self):
        with self.assertRaises(ValueError):
            CountdownLatch(-1)

    def test_concurrent_countdown(self):
        clt = CountdownLatchTest(n_workers=8)
        result = clt.run()
        self.assertTrue(result["all_released"])
        self.assertTrue(result["latch_at_zero"])
        self.assertTrue(result["latch_is_released"])
        self.assertEqual(result["results_count"], result["n_workers"])


# ---------------------------------------------------------------------------
# ReadWriteLock tests
# ---------------------------------------------------------------------------

class TestReadWriteLock(unittest.TestCase):

    def test_multiple_readers(self):
        """Multiple readers can hold the lock concurrently."""
        rw = ReadWriteLock()
        active = [0]
        max_active = [0]
        lock = threading.Lock()
        barrier = threading.Barrier(5)

        def reader():
            barrier.wait()
            rw.acquire_read()
            try:
                with lock:
                    active[0] += 1
                    if active[0] > max_active[0]:
                        max_active[0] = active[0]
                time.sleep(0.02)
                with lock:
                    active[0] -= 1
            finally:
                rw.release_read()

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        self.assertGreater(max_active[0], 1)

    def test_writer_exclusive(self):
        """Writer has exclusive access — no other writer concurrently."""
        rw = ReadWriteLockTest(n_readers=4, n_writers=2, ops=10)
        result = rw.run()
        self.assertTrue(result["writes_correct"])

    def test_reads_observed(self):
        rw = ReadWriteLockTest(n_readers=4, n_writers=2, ops=10)
        result = rw.run()
        self.assertGreater(result["total_reads"], 0)


# ---------------------------------------------------------------------------
# ThreadPool tests
# ---------------------------------------------------------------------------

class TestThreadPool(unittest.TestCase):

    def test_all_tasks_completed(self):
        tp = ThreadPoolTest(n_workers=4, n_tasks=20)
        result = tp.run()
        self.assertTrue(result["all_completed"])

    def test_all_tasks_unique(self):
        tp = ThreadPoolTest(n_workers=4, n_tasks=20)
        result = tp.run()
        self.assertTrue(result["unique_tasks"])

    def test_simple_pool_submit(self):
        pool = SimpleThreadPool(n_workers=2)
        results = []
        lock = threading.Lock()

        def append_task(x):
            with lock:
                results.append(x)

        for i in range(10):
            pool.submit(append_task, i)
        pool.wait()
        pool.shutdown()
        self.assertEqual(len(results), 10)


# ---------------------------------------------------------------------------
# SemaphoreTest
# ---------------------------------------------------------------------------

class TestSemaphoreTest(unittest.TestCase):

    def test_concurrency_limit_respected(self):
        st = SemaphoreTest(max_concurrent=3, n_threads=10)
        result = st.run()
        self.assertTrue(result["limit_respected"])

    def test_all_threads_completed(self):
        st = SemaphoreTest(max_concurrent=3, n_threads=10)
        result = st.run()
        self.assertTrue(result["all_completed"])


# ---------------------------------------------------------------------------
# EventSignalingTest
# ---------------------------------------------------------------------------

class TestEventSignaling(unittest.TestCase):

    def test_basic_event_fires(self):
        es = EventSignalingTest()
        result = es.run_basic()
        self.assertTrue(result["waiter_received"])
        self.assertTrue(result["event_set"])

    def test_broadcast_wakes_all(self):
        es = EventSignalingTest()
        result = es.run_broadcast()
        self.assertTrue(result["all_received"])

    def test_event_timeout(self):
        es = EventSignalingTest()
        result = es.run_timeout()
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["event_set"])


# ---------------------------------------------------------------------------
# ConditionVariableTest
# ---------------------------------------------------------------------------

class TestConditionVariable(unittest.TestCase):

    def test_all_items_produced_and_consumed(self):
        cv = ConditionVariableTest(n_items=15)
        result = cv.run()
        self.assertTrue(result["all_produced"])
        self.assertTrue(result["all_consumed"])

    def test_order_preserved(self):
        cv = ConditionVariableTest(n_items=15)
        result = cv.run()
        self.assertTrue(result["order_preserved"])


# ---------------------------------------------------------------------------
# MockServer and HTTP tests
# ---------------------------------------------------------------------------

class TestMockServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = MockServer(port=0)
        cls.port = cls.server.start()
        cls.base_url = cls.server.base_url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _get(self, path):
        url = f"{self.base_url}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())

    def test_health_endpoint(self):
        data = self._get("/health")
        self.assertEqual(data["status"], "ok")

    def test_reset_endpoint(self):
        data = self._get("/reset")
        self.assertEqual(data["status"], "reset")

    def test_counter_increments(self):
        self._get("/reset")
        d1 = self._get("/counter")
        d2 = self._get("/counter")
        self.assertEqual(d1["count"], 1)
        self.assertEqual(d2["count"], 2)

    def test_echo_endpoint(self):
        data = self._get("/echo")
        self.assertEqual(data["method"], "GET")

    def test_unknown_endpoint_404(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/no_such_path")
        self.assertEqual(ctx.exception.code, 404)

    def test_concurrent_counter_requests(self):
        ht = ConcurrentHTTPTest(self.base_url, n_threads=6)
        result = ht.run_counter_test()
        self.assertTrue(result["all_succeeded"])
        self.assertEqual(result["n_responses"], result["n_requests"])

    def test_concurrent_concurrent_endpoint(self):
        ht = ConcurrentHTTPTest(self.base_url, n_threads=6)
        result = ht.run_concurrent_test()
        self.assertEqual(result["n_successes"], result["n_requests"])
        self.assertGreaterEqual(result["peak_concurrency"], 1)


# ---------------------------------------------------------------------------
# ConcurrencyTestHarness integration test
# ---------------------------------------------------------------------------

class TestConcurrencyTestHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.harness = ConcurrencyTestHarness(server_port=0)
        cls.harness.start_server()
        cls.results = cls.harness.run_all()

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop_server()

    def test_race_locked_no_race(self):
        r = self.results["race_locked"]
        self.assertFalse(r["race_detected"])

    def test_atomicity_atomic_correct(self):
        r = self.results["atomicity_atomic"]
        self.assertTrue(r["is_correct"])

    def test_producer_consumer_all_consumed(self):
        r = self.results["producer_consumer"]
        self.assertTrue(r["all_consumed"])

    def test_barrier_phases_complete(self):
        r = self.results["barrier"]
        self.assertTrue(r["all_completed_phase1"])
        self.assertTrue(r["all_completed_phase2"])

    def test_countdown_latch_released(self):
        r = self.results["countdown_latch"]
        self.assertTrue(r["all_released"])

    def test_semaphore_limit_respected(self):
        r = self.results["semaphore"]
        self.assertTrue(r["limit_respected"])

    def test_read_write_lock_writes_correct(self):
        r = self.results["read_write_lock"]
        self.assertTrue(r["writes_correct"])

    def test_condition_variable_all_consumed(self):
        r = self.results["condition_variable"]
        self.assertTrue(r["all_consumed"])

    def test_http_counter_all_succeeded(self):
        r = self.results.get("http_counter", {})
        self.assertTrue(r.get("all_succeeded", False))

    def test_summary_structure(self):
        summary = self.harness.summary()
        self.assertIn("total", summary)
        self.assertIn("passed", summary)
        self.assertIn("failed", summary)
        self.assertIn("details", summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
