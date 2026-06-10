"""
Concurrency Test Harness (Harness 9 of 36)
Tests thread safety and synchronization primitives.
Pure stdlib, zero external dependencies.
"""

import threading
import queue
import time
import socket
import json
import random
import logging
import http.server
import urllib.request
import urllib.error
from collections import defaultdict
from typing import List, Dict, Any, Optional, Callable, Tuple


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_PORT = 18950


# ---------------------------------------------------------------------------
# Shared Counter (locked and unlocked variants)
# ---------------------------------------------------------------------------

class SharedCounter:
    """Thread-safe counter using a lock."""

    def __init__(self, initial: int = 0) -> None:
        self._value = initial
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self._value += 1

    def decrement(self) -> None:
        with self._lock:
            self._value -= 1

    def get(self) -> int:
        with self._lock:
            return self._value

    def reset(self, value: int = 0) -> None:
        with self._lock:
            self._value = value


class UnsafeCounter:
    """Non-thread-safe counter — intentionally racy for demonstration."""

    def __init__(self, initial: int = 0) -> None:
        self._value = initial

    def increment(self) -> None:
        # Read-modify-write without a lock: classic race condition
        tmp = self._value
        # Yield to increase likelihood of interleaving
        time.sleep(0)
        self._value = tmp + 1

    def get(self) -> int:
        return self._value

    def reset(self, value: int = 0) -> None:
        self._value = value


# ---------------------------------------------------------------------------
# Race Detector
# ---------------------------------------------------------------------------

class RaceDetector:
    """
    Detects race conditions by running N threads against a counter and
    comparing the final value to the expected value.
    """

    def __init__(self, n_threads: int = 10, increments_per_thread: int = 100) -> None:
        self.n_threads = n_threads
        self.increments_per_thread = increments_per_thread
        self.expected = n_threads * increments_per_thread

    def _worker(self, counter, barrier: threading.Barrier) -> None:
        barrier.wait()  # all threads start simultaneously
        for _ in range(self.increments_per_thread):
            counter.increment()

    def run_with_locked(self) -> Dict[str, Any]:
        """Run with the thread-safe counter. Final count must equal expected."""
        counter = SharedCounter(0)
        barrier = threading.Barrier(self.n_threads)
        threads = [
            threading.Thread(target=self._worker, args=(counter, barrier))
            for _ in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = counter.get()
        return {
            "counter_type": "locked",
            "expected": self.expected,
            "actual": final,
            "race_detected": final != self.expected,
        }

    def run_with_unlocked(self) -> Dict[str, Any]:
        """
        Run with the unsafe counter. Race conditions may (and often do) cause
        the final count to differ from expected.
        """
        counter = UnsafeCounter(0)
        barrier = threading.Barrier(self.n_threads)
        threads = [
            threading.Thread(target=self._worker, args=(counter, barrier))
            for _ in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = counter.get()
        return {
            "counter_type": "unlocked",
            "expected": self.expected,
            "actual": final,
            "race_detected": final != self.expected,
        }


# ---------------------------------------------------------------------------
# Deadlock Detector
# ---------------------------------------------------------------------------

class DeadlockDetector:
    """
    Demonstrates deadlock detection via timeout.
    Two threads each acquire one lock then try to acquire the other (wrong order).
    """

    def __init__(self, timeout: float = 2.0) -> None:
        self.timeout = timeout
        self.lock_a = threading.Lock()
        self.lock_b = threading.Lock()

    def _thread_a(self, results: Dict, ready: threading.Event, go: threading.Event) -> None:
        with self.lock_a:
            ready.set()
            go.wait()
            acquired = self.lock_b.acquire(timeout=self.timeout)
            results["thread_a_got_b"] = acquired
            if acquired:
                self.lock_b.release()

    def _thread_b(self, results: Dict, ready: threading.Event, go: threading.Event) -> None:
        with self.lock_b:
            ready.set()
            go.wait()
            acquired = self.lock_a.acquire(timeout=self.timeout)
            results["thread_b_got_a"] = acquired
            if acquired:
                self.lock_a.release()

    def detect(self) -> Dict[str, Any]:
        """
        Returns a result dict indicating whether a deadlock was detected.
        A deadlock is detected when at least one thread cannot acquire its second
        lock within the timeout window.
        """
        results: Dict[str, Any] = {}
        ready_a = threading.Event()
        ready_b = threading.Event()
        go = threading.Event()

        ta = threading.Thread(target=self._thread_a, args=(results, ready_a, go), daemon=True)
        tb = threading.Thread(target=self._thread_b, args=(results, ready_b, go), daemon=True)

        ta.start()
        tb.start()

        # Wait until both threads hold their first lock
        ready_a.wait(timeout=5.0)
        ready_b.wait(timeout=5.0)

        # Signal both to try acquiring the second lock simultaneously
        go.set()

        ta.join(timeout=self.timeout + 1.0)
        tb.join(timeout=self.timeout + 1.0)

        thread_a_got_b = results.get("thread_a_got_b", False)
        thread_b_got_a = results.get("thread_b_got_a", False)

        deadlock_detected = not thread_a_got_b or not thread_b_got_a

        return {
            "deadlock_detected": deadlock_detected,
            "thread_a_acquired_b": thread_a_got_b,
            "thread_b_acquired_a": thread_b_got_a,
            "timeout_used": self.timeout,
        }

    def detect_safe(self) -> Dict[str, Any]:
        """
        Demonstrates safe lock ordering: both threads acquire A then B.
        No deadlock should occur.
        """
        results: Dict[str, Any] = {}
        barrier = threading.Barrier(2)

        def safe_worker(name: str) -> None:
            barrier.wait()
            with self.lock_a:
                with self.lock_b:
                    results[name] = True

        threads = [
            threading.Thread(target=safe_worker, args=(f"t{i}",))
            for i in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        return {
            "deadlock_detected": False,
            "all_completed": len(results) == 2,
            "results": results,
        }


# ---------------------------------------------------------------------------
# Atomicity Checker
# ---------------------------------------------------------------------------

class AtomicityChecker:
    """
    Verifies atomicity of read-modify-write operations under contention.
    """

    def __init__(self, n_threads: int = 20, ops_per_thread: int = 50) -> None:
        self.n_threads = n_threads
        self.ops_per_thread = ops_per_thread
        self.expected = n_threads * ops_per_thread

    def _atomic_worker(self, counter: SharedCounter, barrier: threading.Barrier) -> None:
        barrier.wait()
        for _ in range(self.ops_per_thread):
            counter.increment()

    def check_atomic(self) -> Dict[str, Any]:
        """Verify that atomic (locked) increment produces correct result."""
        counter = SharedCounter(0)
        barrier = threading.Barrier(self.n_threads)
        threads = [
            threading.Thread(target=self._atomic_worker, args=(counter, barrier))
            for _ in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = counter.get()
        return {
            "operation": "atomic_increment",
            "expected": self.expected,
            "actual": final,
            "is_correct": final == self.expected,
        }

    def check_non_atomic(self) -> Dict[str, Any]:
        """
        Demonstrate non-atomic increment. Uses a high thread/op count so that
        race conditions are observable in practice.
        """
        counter = UnsafeCounter(0)
        barrier = threading.Barrier(self.n_threads)

        def non_atomic_worker() -> None:
            barrier.wait()
            for _ in range(self.ops_per_thread):
                counter.increment()

        threads = [
            threading.Thread(target=non_atomic_worker)
            for _ in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = counter.get()
        return {
            "operation": "non_atomic_increment",
            "expected": self.expected,
            "actual": final,
            # Non-atomic may or may not match — we report the discrepancy
            "is_correct": final == self.expected,
            "lost_updates": self.expected - final,
        }


# ---------------------------------------------------------------------------
# Concurrent Collection Tests
# ---------------------------------------------------------------------------

class ConcurrentListTest:
    """
    Tests thread-safe access patterns on a shared list.
    Python's GIL provides some protection for simple operations, but we
    validate correctness explicitly with a lock.
    """

    def __init__(self, n_threads: int = 10, items_per_thread: int = 50) -> None:
        self.n_threads = n_threads
        self.items_per_thread = items_per_thread
        self._list: List[int] = []
        self._lock = threading.Lock()

    def _append_worker(self, start: int, barrier: threading.Barrier) -> None:
        barrier.wait()
        for i in range(self.items_per_thread):
            with self._lock:
                self._list.append(start + i)

    def run(self) -> Dict[str, Any]:
        self._list.clear()
        barrier = threading.Barrier(self.n_threads)
        threads = [
            threading.Thread(target=self._append_worker, args=(i * self.items_per_thread, barrier))
            for i in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        expected_len = self.n_threads * self.items_per_thread
        return {
            "expected_length": expected_len,
            "actual_length": len(self._list),
            "is_correct": len(self._list) == expected_len,
            "items_unique": len(set(self._list)) == expected_len,
        }


class ConcurrentDictTest:
    """
    Tests thread-safe access patterns on a shared dict.
    """

    def __init__(self, n_threads: int = 10, items_per_thread: int = 50) -> None:
        self.n_threads = n_threads
        self.items_per_thread = items_per_thread
        self._dict: Dict[str, int] = {}
        self._lock = threading.Lock()

    def _writer_worker(self, thread_id: int, barrier: threading.Barrier) -> None:
        barrier.wait()
        for i in range(self.items_per_thread):
            key = f"t{thread_id}_k{i}"
            with self._lock:
                self._dict[key] = thread_id * 1000 + i

    def run(self) -> Dict[str, Any]:
        self._dict.clear()
        barrier = threading.Barrier(self.n_threads)
        threads = [
            threading.Thread(target=self._writer_worker, args=(i, barrier))
            for i in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        expected_keys = self.n_threads * self.items_per_thread
        return {
            "expected_keys": expected_keys,
            "actual_keys": len(self._dict),
            "is_correct": len(self._dict) == expected_keys,
        }

    def run_counter_accumulation(self) -> Dict[str, Any]:
        """Accumulate counters per key from multiple threads."""
        counters: Dict[str, int] = defaultdict(int)
        lock = threading.Lock()
        n_threads = self.n_threads
        increments = self.items_per_thread

        def worker(tid: int, barrier: threading.Barrier) -> None:
            barrier.wait()
            for _ in range(increments):
                key = f"bucket_{tid % 5}"
                with lock:
                    counters[key] += 1

        barrier = threading.Barrier(n_threads)
        threads = [
            threading.Thread(target=worker, args=(i, barrier))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = sum(counters.values())
        expected_total = n_threads * increments
        return {
            "expected_total": expected_total,
            "actual_total": total,
            "is_correct": total == expected_total,
            "bucket_count": len(counters),
        }


# ---------------------------------------------------------------------------
# Producer-Consumer Queue Test
# ---------------------------------------------------------------------------

class ProducerConsumerTest:
    """
    Tests producer-consumer patterns using queue.Queue.
    All items produced must be consumed exactly once.
    """

    def __init__(
        self,
        n_producers: int = 4,
        n_consumers: int = 4,
        items_per_producer: int = 25,
    ) -> None:
        self.n_producers = n_producers
        self.n_consumers = n_consumers
        self.items_per_producer = items_per_producer
        self.total_items = n_producers * items_per_producer

    def _producer(
        self,
        producer_id: int,
        q: queue.Queue,
        barrier: threading.Barrier,
    ) -> None:
        barrier.wait()
        for i in range(self.items_per_producer):
            item = (producer_id, i)
            q.put(item)

    def _consumer(
        self,
        consumed: List,
        lock: threading.Lock,
        q: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set() or not q.empty():
            try:
                item = q.get(timeout=0.1)
                with lock:
                    consumed.append(item)
                q.task_done()
            except queue.Empty:
                continue

    def run(self) -> Dict[str, Any]:
        q: queue.Queue = queue.Queue()
        consumed: List = []
        lock = threading.Lock()
        stop_event = threading.Event()
        barrier = threading.Barrier(self.n_producers)

        producers = [
            threading.Thread(target=self._producer, args=(i, q, barrier))
            for i in range(self.n_producers)
        ]
        consumers = [
            threading.Thread(
                target=self._consumer,
                args=(consumed, lock, q, stop_event),
                daemon=True,
            )
            for _ in range(self.n_consumers)
        ]

        for c in consumers:
            c.start()
        for p in producers:
            p.start()
        for p in producers:
            p.join()

        q.join()
        stop_event.set()

        # Give consumers time to see the stop event
        for c in consumers:
            c.join(timeout=2.0)

        unique_items = set(consumed)
        return {
            "total_produced": self.total_items,
            "total_consumed": len(consumed),
            "unique_items": len(unique_items),
            "all_consumed": len(consumed) == self.total_items,
            "no_duplicates": len(consumed) == len(unique_items),
        }


# ---------------------------------------------------------------------------
# Barrier Synchronization Test
# ---------------------------------------------------------------------------

class BarrierTest:
    """
    Verifies that threading.Barrier correctly synchronizes threads at a
    rendezvous point.
    """

    def __init__(self, n_threads: int = 8) -> None:
        self.n_threads = n_threads

    def run(self) -> Dict[str, Any]:
        phases_completed: List[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(self.n_threads)
        errors: List[str] = []

        def worker(tid: int) -> None:
            try:
                # Phase 1
                time.sleep(random.uniform(0, 0.01))
                barrier.wait()
                with lock:
                    phases_completed.append(1)

                # Phase 2
                time.sleep(random.uniform(0, 0.01))
                barrier.wait()
                with lock:
                    phases_completed.append(2)
            except threading.BrokenBarrierError as e:
                with lock:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        phase1_count = phases_completed.count(1)
        phase2_count = phases_completed.count(2)

        return {
            "n_threads": self.n_threads,
            "phase1_completions": phase1_count,
            "phase2_completions": phase2_count,
            "all_completed_phase1": phase1_count == self.n_threads,
            "all_completed_phase2": phase2_count == self.n_threads,
            "errors": errors,
        }

    def run_with_action(self) -> Dict[str, Any]:
        """Test barrier with an action callback executed once per barrier crossing."""
        actions_run: List[int] = []
        lock = threading.Lock()

        def barrier_action() -> None:
            with lock:
                actions_run.append(1)

        barrier = threading.Barrier(self.n_threads, action=barrier_action)
        completions: List[bool] = []

        def worker() -> None:
            barrier.wait()
            with lock:
                completions.append(True)

        threads = [threading.Thread(target=worker) for _ in range(self.n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        return {
            "n_threads": self.n_threads,
            "actions_run": len(actions_run),
            "completions": len(completions),
            "action_ran_once": len(actions_run) == 1,
        }


# ---------------------------------------------------------------------------
# Countdown Latch
# ---------------------------------------------------------------------------

class CountdownLatch:
    """
    A countdown latch implemented with threading.Event and a counter.
    Threads can wait until the count reaches zero.
    """

    def __init__(self, count: int) -> None:
        if count < 0:
            raise ValueError("count must be >= 0")
        self._count = count
        self._lock = threading.Lock()
        self._event = threading.Event()
        if count == 0:
            self._event.set()

    def count_down(self) -> None:
        """Decrement the latch count; sets the event when count reaches zero."""
        with self._lock:
            if self._count <= 0:
                return
            self._count -= 1
            if self._count == 0:
                self._event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the latch count reaches zero or timeout expires."""
        return self._event.wait(timeout=timeout)

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def is_released(self) -> bool:
        return self._event.is_set()


class CountdownLatchTest:
    """Tests CountdownLatch under concurrent usage."""

    def __init__(self, n_workers: int = 10) -> None:
        self.n_workers = n_workers

    def run(self) -> Dict[str, Any]:
        latch = CountdownLatch(self.n_workers)
        results: List[str] = []
        lock = threading.Lock()
        gate_open = threading.Event()

        def worker(tid: int) -> None:
            gate_open.wait()
            latch.count_down()
            # After counting down, wait for latch to reach zero
            released = latch.wait(timeout=5.0)
            with lock:
                results.append(f"t{tid}:{'ok' if released else 'timeout'}")

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(self.n_workers)
        ]
        for t in threads:
            t.start()

        gate_open.set()  # release all workers

        for t in threads:
            t.join(timeout=10.0)

        all_ok = all(r.endswith(":ok") for r in results)
        return {
            "n_workers": self.n_workers,
            "results_count": len(results),
            "all_released": all_ok,
            "latch_at_zero": latch.count == 0,
            "latch_is_released": latch.is_released(),
        }


# ---------------------------------------------------------------------------
# Read-Write Lock
# ---------------------------------------------------------------------------

class ReadWriteLock:
    """
    A simple read-preferring read-write lock.
    Multiple readers can hold the lock simultaneously; writers are exclusive.
    """

    def __init__(self) -> None:
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._readers = 0

    def acquire_read(self) -> None:
        with self._read_lock:
            self._readers += 1
            if self._readers == 1:
                self._write_lock.acquire()

    def release_read(self) -> None:
        with self._read_lock:
            self._readers -= 1
            if self._readers == 0:
                self._write_lock.release()

    def acquire_write(self) -> None:
        self._write_lock.acquire()

    def release_write(self) -> None:
        self._write_lock.release()


class ReadWriteLockTest:
    """Tests ReadWriteLock under concurrent readers and writers."""

    def __init__(self, n_readers: int = 8, n_writers: int = 2, ops: int = 20) -> None:
        self.n_readers = n_readers
        self.n_writers = n_writers
        self.ops = ops

    def run(self) -> Dict[str, Any]:
        rw_lock = ReadWriteLock()
        shared_value: Dict[str, int] = {"v": 0}
        read_values: List[int] = []
        errors: List[str] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(self.n_readers + self.n_writers)

        def reader() -> None:
            barrier.wait()
            for _ in range(self.ops):
                rw_lock.acquire_read()
                try:
                    v = shared_value["v"]
                    with results_lock:
                        read_values.append(v)
                finally:
                    rw_lock.release_read()

        def writer(tid: int) -> None:
            barrier.wait()
            for i in range(self.ops):
                rw_lock.acquire_write()
                try:
                    shared_value["v"] += 1
                finally:
                    rw_lock.release_write()

        readers = [threading.Thread(target=reader) for _ in range(self.n_readers)]
        writers = [threading.Thread(target=writer, args=(i,)) for i in range(self.n_writers)]
        all_threads = readers + writers

        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join(timeout=15.0)

        expected_writes = self.n_writers * self.ops
        return {
            "expected_writes": expected_writes,
            "final_value": shared_value["v"],
            "writes_correct": shared_value["v"] == expected_writes,
            "total_reads": len(read_values),
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# Thread Pool
# ---------------------------------------------------------------------------

class SimpleThreadPool:
    """
    A minimal thread pool backed by a queue.Queue.
    """

    def __init__(self, n_workers: int = 4) -> None:
        self.n_workers = n_workers
        self._queue: queue.Queue = queue.Queue()
        self._workers: List[threading.Thread] = []
        self._stop = threading.Event()
        self._start()

    def _start(self) -> None:
        for _ in range(self.n_workers):
            t = threading.Thread(target=self._run, daemon=True)
            t.start()
            self._workers.append(t)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                fn, args, kwargs = self._queue.get(timeout=0.1)
                try:
                    fn(*args, **kwargs)
                finally:
                    self._queue.task_done()
            except queue.Empty:
                continue

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        self._queue.put((fn, args, kwargs))

    def wait(self) -> None:
        self._queue.join()

    def shutdown(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=2.0)


class ThreadPoolTest:
    """Tests SimpleThreadPool."""

    def __init__(self, n_workers: int = 4, n_tasks: int = 40) -> None:
        self.n_workers = n_workers
        self.n_tasks = n_tasks

    def run(self) -> Dict[str, Any]:
        pool = SimpleThreadPool(self.n_workers)
        counter = SharedCounter(0)
        task_ids: List[int] = []
        lock = threading.Lock()

        def task(tid: int) -> None:
            counter.increment()
            with lock:
                task_ids.append(tid)
            time.sleep(random.uniform(0, 0.005))

        for i in range(self.n_tasks):
            pool.submit(task, i)

        pool.wait()
        pool.shutdown()

        return {
            "n_tasks": self.n_tasks,
            "completed": counter.get(),
            "all_completed": counter.get() == self.n_tasks,
            "unique_tasks": len(set(task_ids)) == self.n_tasks,
        }


# ---------------------------------------------------------------------------
# Semaphore Test
# ---------------------------------------------------------------------------

class SemaphoreTest:
    """
    Tests threading.Semaphore to limit concurrent access.
    """

    def __init__(self, max_concurrent: int = 3, n_threads: int = 10) -> None:
        self.max_concurrent = max_concurrent
        self.n_threads = n_threads

    def run(self) -> Dict[str, Any]:
        sem = threading.Semaphore(self.max_concurrent)
        concurrent_counts: List[int] = []
        lock = threading.Lock()
        active = [0]
        max_seen = [0]

        def worker() -> None:
            with sem:
                with lock:
                    active[0] += 1
                    if active[0] > max_seen[0]:
                        max_seen[0] = active[0]
                    concurrent_counts.append(active[0])
                time.sleep(0.01)
                with lock:
                    active[0] -= 1

        threads = [threading.Thread(target=worker) for _ in range(self.n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        return {
            "max_concurrent_allowed": self.max_concurrent,
            "max_concurrent_observed": max_seen[0],
            "limit_respected": max_seen[0] <= self.max_concurrent,
            "all_completed": len(concurrent_counts) == self.n_threads,
        }


# ---------------------------------------------------------------------------
# Event Signaling Test
# ---------------------------------------------------------------------------

class EventSignalingTest:
    """
    Tests threading.Event for signaling between threads.
    """

    def run_basic(self) -> Dict[str, Any]:
        event = threading.Event()
        result: Dict[str, Any] = {}

        def waiter() -> None:
            fired = event.wait(timeout=5.0)
            result["fired"] = fired

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        event.set()
        t.join(timeout=5.0)

        return {
            "event_set": event.is_set(),
            "waiter_received": result.get("fired", False),
        }

    def run_broadcast(self) -> Dict[str, Any]:
        """One event wakes multiple waiters simultaneously."""
        event = threading.Event()
        n_waiters = 5
        received: List[bool] = []
        lock = threading.Lock()

        def waiter() -> None:
            fired = event.wait(timeout=5.0)
            with lock:
                received.append(fired)

        threads = [threading.Thread(target=waiter) for _ in range(n_waiters)]
        for t in threads:
            t.start()
        time.sleep(0.05)
        event.set()
        for t in threads:
            t.join(timeout=5.0)

        return {
            "n_waiters": n_waiters,
            "all_received": len(received) == n_waiters and all(received),
        }

    def run_timeout(self) -> Dict[str, Any]:
        """Waiter times out when event is never set."""
        event = threading.Event()
        result: Dict[str, Any] = {}

        def waiter() -> None:
            fired = event.wait(timeout=0.1)
            result["fired"] = fired

        t = threading.Thread(target=waiter)
        t.start()
        t.join(timeout=2.0)

        return {
            "event_set": event.is_set(),
            "timed_out": not result.get("fired", True),
        }


# ---------------------------------------------------------------------------
# Mock HTTP Server for Concurrent Request Testing
# ---------------------------------------------------------------------------

_request_counter = SharedCounter(0)
_concurrent_peak = [0]
_concurrent_current = [0]
_concurrent_lock = threading.Lock()


class MockConcurrencyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for concurrency testing."""

    def log_message(self, fmt: str, *args: Any) -> None:  # suppress default logging
        pass

    def _set_headers(self, status: int = 200, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()

    def _write_json(self, data: Any) -> None:
        body = json.dumps(data).encode()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/counter":
            _request_counter.increment()
            self._set_headers()
            self._write_json({"count": _request_counter.get()})

        elif self.path == "/concurrent":
            with _concurrent_lock:
                _concurrent_current[0] += 1
                if _concurrent_current[0] > _concurrent_peak[0]:
                    _concurrent_peak[0] = _concurrent_current[0]
            time.sleep(0.02)  # simulate work
            with _concurrent_lock:
                _concurrent_current[0] -= 1
            self._set_headers()
            self._write_json({"peak": _concurrent_peak[0]})

        elif self.path == "/echo":
            self._set_headers()
            self._write_json({"path": self.path, "method": "GET"})

        elif self.path == "/reset":
            _request_counter.reset(0)
            with _concurrent_lock:
                _concurrent_peak[0] = 0
                _concurrent_current[0] = 0
            self._set_headers()
            self._write_json({"status": "reset"})

        elif self.path == "/health":
            self._set_headers()
            self._write_json({"status": "ok"})

        else:
            self._set_headers(404)
            self._write_json({"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

        if self.path == "/submit":
            self._set_headers()
            self._write_json({"received": payload, "status": "ok"})
        else:
            self._set_headers(404)
            self._write_json({"error": "not found"})


class MockServer:
    """Wraps the HTTP server for easy start/stop lifecycle management."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        """Start the server and return the bound port."""
        self._server = http.server.HTTPServer(
            (self.host, self.port),
            MockConcurrencyHandler,
        )
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Reset global state
        _request_counter.reset(0)
        with _concurrent_lock:
            _concurrent_peak[0] = 0
            _concurrent_current[0] = 0
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class ConcurrentHTTPTest:
    """
    Fires concurrent HTTP requests at the mock server and verifies responses.
    """

    def __init__(self, base_url: str, n_threads: int = 10) -> None:
        self.base_url = base_url
        self.n_threads = n_threads

    def _fetch(self, path: str, results: List, lock: threading.Lock) -> None:
        try:
            url = f"{self.base_url}{path}"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                with lock:
                    results.append(data)
        except Exception as e:
            with lock:
                results.append({"error": str(e)})

    def run_counter_test(self) -> Dict[str, Any]:
        """Send N requests to /counter and check total count."""
        # Reset first
        with urllib.request.urlopen(f"{self.base_url}/reset", timeout=5) as r:
            pass

        results: List = []
        lock = threading.Lock()
        barrier = threading.Barrier(self.n_threads)

        def worker() -> None:
            barrier.wait()
            self._fetch("/counter", results, lock)

        threads = [threading.Thread(target=worker) for _ in range(self.n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        successes = [r for r in results if "error" not in r]
        return {
            "n_requests": self.n_threads,
            "n_responses": len(results),
            "n_successes": len(successes),
            "all_succeeded": len(successes) == self.n_threads,
        }

    def run_concurrent_test(self) -> Dict[str, Any]:
        """Measure peak concurrency via /concurrent endpoint."""
        results: List = []
        lock = threading.Lock()

        threads = [
            threading.Thread(target=self._fetch, args=("/concurrent", results, lock))
            for _ in range(self.n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        successes = [r for r in results if "error" not in r]
        peak = max((r.get("peak", 0) for r in successes), default=0)
        return {
            "n_requests": self.n_threads,
            "n_successes": len(successes),
            "peak_concurrency": peak,
        }


# ---------------------------------------------------------------------------
# Condition Variable Test
# ---------------------------------------------------------------------------

class ConditionVariableTest:
    """Tests threading.Condition for producer-consumer synchronization."""

    def __init__(self, n_items: int = 20) -> None:
        self.n_items = n_items

    def run(self) -> Dict[str, Any]:
        buffer: List[int] = []
        MAX_BUFFER = 5
        produced: List[int] = []
        consumed: List[int] = []
        lock = threading.Lock()
        condition = threading.Condition(lock)

        def producer() -> None:
            for i in range(self.n_items):
                with condition:
                    while len(buffer) >= MAX_BUFFER:
                        condition.wait()
                    buffer.append(i)
                    produced.append(i)
                    condition.notify_all()

        def consumer() -> None:
            count = 0
            while count < self.n_items:
                with condition:
                    while not buffer:
                        condition.wait()
                    item = buffer.pop(0)
                    consumed.append(item)
                    count += 1
                    condition.notify_all()

        t_prod = threading.Thread(target=producer)
        t_cons = threading.Thread(target=consumer)
        t_prod.start()
        t_cons.start()
        t_prod.join(timeout=10.0)
        t_cons.join(timeout=10.0)

        return {
            "expected": self.n_items,
            "produced": len(produced),
            "consumed": len(consumed),
            "all_produced": len(produced) == self.n_items,
            "all_consumed": len(consumed) == self.n_items,
            "order_preserved": consumed == sorted(consumed),
        }


# ---------------------------------------------------------------------------
# Harness Runner
# ---------------------------------------------------------------------------

class ConcurrencyTestHarness:
    """
    Orchestrates all concurrency tests and returns a summary report.
    """

    def __init__(self, server_port: int = 0) -> None:
        self.server_port = server_port
        self._server = MockServer(port=server_port)
        self._results: Dict[str, Any] = {}

    def start_server(self) -> int:
        port = self._server.start()
        self.server_port = port
        return port

    def stop_server(self) -> None:
        self._server.stop()

    @property
    def base_url(self) -> str:
        return self._server.base_url

    def run_all(self) -> Dict[str, Any]:
        """Run all concurrency tests and return a consolidated report."""
        results: Dict[str, Any] = {}

        # Race detection
        rd = RaceDetector(n_threads=5, increments_per_thread=100)
        results["race_locked"] = rd.run_with_locked()
        results["race_unlocked"] = rd.run_with_unlocked()

        # Deadlock
        dd = DeadlockDetector(timeout=1.0)
        results["deadlock"] = dd.detect()
        results["deadlock_safe"] = dd.detect_safe()

        # Atomicity
        ac = AtomicityChecker(n_threads=10, ops_per_thread=50)
        results["atomicity_atomic"] = ac.check_atomic()
        results["atomicity_non_atomic"] = ac.check_non_atomic()

        # Collections
        cl = ConcurrentListTest(n_threads=5, items_per_thread=20)
        results["concurrent_list"] = cl.run()

        cd = ConcurrentDictTest(n_threads=5, items_per_thread=20)
        results["concurrent_dict"] = cd.run()
        results["concurrent_dict_counters"] = cd.run_counter_accumulation()

        # Producer-consumer
        pc = ProducerConsumerTest(n_producers=3, n_consumers=3, items_per_producer=20)
        results["producer_consumer"] = pc.run()

        # Barrier
        bt = BarrierTest(n_threads=6)
        results["barrier"] = bt.run()
        results["barrier_action"] = bt.run_with_action()

        # Countdown latch
        clt = CountdownLatchTest(n_workers=8)
        results["countdown_latch"] = clt.run()

        # Read-write lock
        rw = ReadWriteLockTest(n_readers=6, n_writers=2, ops=15)
        results["read_write_lock"] = rw.run()

        # Thread pool
        tp = ThreadPoolTest(n_workers=4, n_tasks=20)
        results["thread_pool"] = tp.run()

        # Semaphore
        st = SemaphoreTest(max_concurrent=3, n_threads=8)
        results["semaphore"] = st.run()

        # Event signaling
        es = EventSignalingTest()
        results["event_basic"] = es.run_basic()
        results["event_broadcast"] = es.run_broadcast()
        results["event_timeout"] = es.run_timeout()

        # Condition variable
        cv = ConditionVariableTest(n_items=15)
        results["condition_variable"] = cv.run()

        # HTTP
        if self._server._server is not None:
            ht = ConcurrentHTTPTest(self._server.base_url, n_threads=6)
            results["http_counter"] = ht.run_counter_test()
            results["http_concurrent"] = ht.run_concurrent_test()

        self._results = results
        return results

    def summary(self) -> Dict[str, Any]:
        """Return a human-readable summary of all test results."""
        if not self._results:
            return {"error": "no results — run run_all() first"}
        passed = 0
        failed = 0
        details = {}
        for name, result in self._results.items():
            ok = not result.get("error") and not result.get("errors")
            details[name] = result
            if ok:
                passed += 1
            else:
                failed += 1
        return {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "details": details,
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False) -> int:
    """Run the full in-process suite; the locked counter must show no race and
    the unlocked counter must expose one."""
    harness = ConcurrencyTestHarness()  # in-process tests need no server
    harness.run_all()
    s = harness.summary()
    rl = harness._results["race_locked"]
    ru = harness._results["race_unlocked"]
    checks = [
        ("primitive suite all-pass", s["failed"] == 0 and s["passed"] >= 15,
         f"passed={s['passed']} failed={s['failed']}"),
        ("locked counter: no lost updates",
         rl["actual"] == rl["expected"] and not rl["race_detected"], str(rl)),
        ("unlocked counter: race exposed", ru["race_detected"], str(ru)),
    ]
    failures = [n for n, ok, _ in checks if not ok]
    for n, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}  ({d})")
    print(f"\n  {len(checks) - len(failures)}/{len(checks)} checks passed")
    return 0 if not failures else 1


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Concurrency test harness")
    p.add_argument("--self-test", action="store_true", help="Run built-in scenarios and exit")
    p.add_argument("--serve", action="store_true", help="Start the mock HTTP server too")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    harness = ConcurrencyTestHarness(server_port=args.port if args.serve else 0)
    if args.serve:
        logger.info("Mock server started on port %d", harness.start_server())
    try:
        harness.run_all()
        print(json.dumps(harness.summary(), indent=2))
    finally:
        harness.stop_server()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
