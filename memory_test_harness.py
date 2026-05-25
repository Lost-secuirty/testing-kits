"""
Memory / Soak Test Harness (Harness 8 of 36)

Detects memory leaks and resource exhaustion via:
- RSS monitoring using /proc/self/status (Linux) with resource module fallback
- GC object count tracking
- Linear regression on memory growth
- File descriptor usage tracking
- Thread count monitoring
- Object lifecycle tracking (create/destroy balance)
- GC pressure measurement

Zero external dependencies — pure Python stdlib.
"""

from __future__ import annotations

import gc
import math
import os
import resource
import socket
import sys
import threading
import time
import tracemalloc
import weakref
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple
import json
import io


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _rss_bytes() -> int:
    """Return current RSS in bytes.  Uses /proc/self/status on Linux,
    resource module as fallback."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # format: "VmRSS:    12345 kB"
                    parts = line.split()
                    return int(parts[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    # Fallback: resource module (returns KB on Linux, bytes on macOS)
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = usage.ru_maxrss
        if sys.platform == "linux":
            return rss * 1024
        return rss
    except Exception:
        return 0


def _fd_count() -> int:
    """Count open file descriptors for the current process."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    # Fallback: iterate up to soft limit
    try:
        soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        count = 0
        for fd in range(soft):
            try:
                os.fstat(fd)
                count += 1
            except OSError:
                pass
        return count
    except Exception:
        return 0


def _gc_object_count() -> int:
    """Return total number of tracked objects via gc."""
    return sum(len(gc.get_objects(gen)) for gen in range(3))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MemorySnapshot:
    """Point-in-time snapshot of process memory metrics."""
    rss_bytes: int
    gc_objects: int
    fd_count: int
    thread_count: int
    timestamp: float = field(default_factory=time.monotonic)

    def __repr__(self) -> str:
        return (
            f"MemorySnapshot(rss={self.rss_bytes // 1024}KB, "
            f"gc_objects={self.gc_objects}, "
            f"fds={self.fd_count}, "
            f"threads={self.thread_count}, "
            f"t={self.timestamp:.3f})"
        )


@dataclass
class LeakReport:
    """Result of a leak-detection analysis run."""
    leaked: bool
    slope_bytes_per_iter: float
    r_squared: float
    snapshots_analyzed: int
    threshold_bytes_per_iter: float
    details: str = ""

    @property
    def summary(self) -> str:
        status = "LEAK DETECTED" if self.leaked else "OK"
        return (
            f"[{status}] slope={self.slope_bytes_per_iter:.1f} B/iter "
            f"r²={self.r_squared:.3f} "
            f"threshold={self.threshold_bytes_per_iter:.1f} B/iter "
            f"(n={self.snapshots_analyzed})"
        )


@dataclass
class GCPressureReport:
    """Summary of GC collection activity during a soak run."""
    collections_gen0: int
    collections_gen1: int
    collections_gen2: int
    duration_seconds: float

    @property
    def total_collections(self) -> int:
        return self.collections_gen0 + self.collections_gen1 + self.collections_gen2

    @property
    def collections_per_second(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return self.total_collections / self.duration_seconds

    def __repr__(self) -> str:
        return (
            f"GCPressureReport(gen0={self.collections_gen0}, "
            f"gen1={self.collections_gen1}, gen2={self.collections_gen2}, "
            f"total={self.total_collections}, "
            f"rate={self.collections_per_second:.1f}/s)"
        )


# ---------------------------------------------------------------------------
# Object lifecycle tracker
# ---------------------------------------------------------------------------

class ObjectTracker:
    """Track object creation and destruction to detect lifecycle imbalances.

    Usage::

        tracker = ObjectTracker()
        tracker.record_create("Widget")
        tracker.record_destroy("Widget")
        report = tracker.report()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._created: Dict[str, int] = {}
        self._destroyed: Dict[str, int] = {}
        self._weak_refs: List[weakref.ref] = []

    def record_create(self, kind: str, obj: Optional[Any] = None) -> None:
        with self._lock:
            self._created[kind] = self._created.get(kind, 0) + 1
            if obj is not None:
                try:
                    self._weak_refs.append(weakref.ref(obj))
                except TypeError:
                    pass

    def record_destroy(self, kind: str) -> None:
        with self._lock:
            self._destroyed[kind] = self._destroyed.get(kind, 0) + 1

    def reset(self) -> None:
        with self._lock:
            self._created.clear()
            self._destroyed.clear()
            self._weak_refs.clear()

    def live_weak_refs(self) -> int:
        """Return count of tracked objects still alive."""
        return sum(1 for r in self._weak_refs if r() is not None)

    def report(self) -> Dict[str, Dict[str, int]]:
        """Return per-kind {created, destroyed, leaked} counts."""
        with self._lock:
            result: Dict[str, Dict[str, int]] = {}
            all_kinds = set(self._created) | set(self._destroyed)
            for kind in sorted(all_kinds):
                created = self._created.get(kind, 0)
                destroyed = self._destroyed.get(kind, 0)
                result[kind] = {
                    "created": created,
                    "destroyed": destroyed,
                    "leaked": max(0, created - destroyed),
                }
            return result

    def has_leaks(self) -> bool:
        rep = self.report()
        return any(v["leaked"] > 0 for v in rep.values())


# ---------------------------------------------------------------------------
# Linear regression helpers
# ---------------------------------------------------------------------------

def _linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float, float]:
    """Return (slope, intercept, r_squared) for a simple linear regression."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if ss_xx == 0:
        return 0.0, mean_y, 0.0
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    # R²
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_sq = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return slope, intercept, r_sq


def analyze_snapshots(snapshots: List[MemorySnapshot],
                      threshold_bytes_per_iter: float = 1024.0) -> LeakReport:
    """Perform linear regression on RSS values across snapshots.

    Args:
        snapshots: ordered list of MemorySnapshot objects.
        threshold_bytes_per_iter: minimum slope (bytes/iteration) to declare a leak.

    Returns:
        LeakReport with slope, r², and leaked flag.
    """
    if len(snapshots) < 2:
        return LeakReport(
            leaked=False,
            slope_bytes_per_iter=0.0,
            r_squared=0.0,
            snapshots_analyzed=len(snapshots),
            threshold_bytes_per_iter=threshold_bytes_per_iter,
            details="Not enough snapshots for analysis",
        )
    xs = list(range(len(snapshots)))
    ys = [float(s.rss_bytes) for s in snapshots]
    slope, _intercept, r_sq = _linear_regression(xs, ys)
    leaked = slope > threshold_bytes_per_iter
    details = (
        f"RSS went from {ys[0]/1024:.1f}KB to {ys[-1]/1024:.1f}KB "
        f"over {len(snapshots)} snapshots"
    )
    return LeakReport(
        leaked=leaked,
        slope_bytes_per_iter=slope,
        r_squared=r_sq,
        snapshots_analyzed=len(snapshots),
        threshold_bytes_per_iter=threshold_bytes_per_iter,
        details=details,
    )


# ---------------------------------------------------------------------------
# Soak test runner
# ---------------------------------------------------------------------------

class SoakTestRunner:
    """Run a function repeatedly while monitoring memory metrics.

    Example::

        runner = SoakTestRunner()
        result = runner.run(my_func, iterations=200, snapshot_interval=20)
        print(result.leak_report.summary)
    """

    def __init__(self, threshold_bytes_per_iter: float = 1024.0) -> None:
        self.threshold_bytes_per_iter = threshold_bytes_per_iter
        self.snapshots: List[MemorySnapshot] = []

    def _take_snapshot(self) -> MemorySnapshot:
        gc.collect()
        return MemorySnapshot(
            rss_bytes=_rss_bytes(),
            gc_objects=_gc_object_count(),
            fd_count=_fd_count(),
            thread_count=threading.active_count(),
            timestamp=time.monotonic(),
        )

    def run(
        self,
        fn: Callable[[], Any],
        iterations: int = 100,
        snapshot_interval: int = 10,
    ) -> "SoakResult":
        """Execute *fn* for *iterations* calls, snapshotting every *snapshot_interval* calls.

        Returns a SoakResult with snapshots and leak analysis.
        """
        self.snapshots = []
        gc_counts_before = list(gc.get_count())
        t_start = time.monotonic()

        for i in range(iterations):
            fn()
            if i % snapshot_interval == 0:
                self.snapshots.append(self._take_snapshot())

        # Final snapshot
        self.snapshots.append(self._take_snapshot())
        t_end = time.monotonic()
        gc_counts_after = list(gc.get_count())

        leak_report = analyze_snapshots(
            self.snapshots, self.threshold_bytes_per_iter
        )

        gc_report = GCPressureReport(
            collections_gen0=max(0, gc_counts_after[0] - gc_counts_before[0]),
            collections_gen1=max(0, gc_counts_after[1] - gc_counts_before[1]),
            collections_gen2=max(0, gc_counts_after[2] - gc_counts_before[2]),
            duration_seconds=t_end - t_start,
        )

        return SoakResult(
            snapshots=list(self.snapshots),
            leak_report=leak_report,
            gc_report=gc_report,
            iterations=iterations,
            duration_seconds=t_end - t_start,
        )


@dataclass
class SoakResult:
    """Aggregate result from a SoakTestRunner.run() call."""
    snapshots: List[MemorySnapshot]
    leak_report: LeakReport
    gc_report: GCPressureReport
    iterations: int
    duration_seconds: float

    @property
    def peak_rss_bytes(self) -> int:
        if not self.snapshots:
            return 0
        return max(s.rss_bytes for s in self.snapshots)

    @property
    def min_rss_bytes(self) -> int:
        if not self.snapshots:
            return 0
        return min(s.rss_bytes for s in self.snapshots)

    @property
    def rss_growth_bytes(self) -> int:
        if len(self.snapshots) < 2:
            return 0
        return self.snapshots[-1].rss_bytes - self.snapshots[0].rss_bytes

    def summary(self) -> str:
        return (
            f"SoakResult: iters={self.iterations} "
            f"duration={self.duration_seconds:.3f}s "
            f"peak_rss={self.peak_rss_bytes//1024}KB "
            f"rss_growth={self.rss_growth_bytes//1024}KB\n"
            f"  {self.leak_report.summary}\n"
            f"  GC: {self.gc_report}"
        )


# ---------------------------------------------------------------------------
# Mock HTTP handler (for testing memory under HTTP load)
# ---------------------------------------------------------------------------

class MockMemoryHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler that simulates memory-related endpoints.

    Endpoints:
        GET /status    — returns current MemorySnapshot as JSON
        GET /allocate  — allocates a small buffer and returns its size
        GET /gc        — triggers gc.collect() and returns counts
        GET /echo      — echoes query string back
    """

    # Class-level store so tests can inject state
    allocated_buffers: List[bytes] = []
    _lock: threading.Lock = threading.Lock()

    def log_message(self, fmt: str, *args: Any) -> None:
        """Suppress default access logging."""
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/status":
            snap = MemorySnapshot(
                rss_bytes=_rss_bytes(),
                gc_objects=_gc_object_count(),
                fd_count=_fd_count(),
                thread_count=threading.active_count(),
            )
            self._send_json({
                "rss_bytes": snap.rss_bytes,
                "gc_objects": snap.gc_objects,
                "fd_count": snap.fd_count,
                "thread_count": snap.thread_count,
                "timestamp": snap.timestamp,
            })
        elif path == "/allocate":
            size = 1024  # 1 KB
            buf = b"x" * size
            with MockMemoryHandler._lock:
                MockMemoryHandler.allocated_buffers.append(buf)
            self._send_json({"allocated_bytes": size, "total_buffers": len(MockMemoryHandler.allocated_buffers)})
        elif path == "/gc":
            gc.collect()
            counts = gc.get_count()
            self._send_json({"gen0": counts[0], "gen1": counts[1], "gen2": counts[2]})
        elif path == "/echo":
            qs = self.path[len("/echo"):]
            self._send_json({"echo": qs})
        elif path == "/reset":
            with MockMemoryHandler._lock:
                MockMemoryHandler.allocated_buffers.clear()
            self._send_json({"status": "reset"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._send_json({"received_bytes": len(body)})


# ---------------------------------------------------------------------------
# Mock server lifecycle helpers
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Bind to port 0 to get a free ephemeral port from the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockServer:
    """Manages lifecycle of a MockMemoryHandler HTTP server in a daemon thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port if port != 0 else find_free_port()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[Thread] = None
        self._ready = Event()

    def start(self) -> "MockServer":
        """Start the HTTP server in a background daemon thread."""
        self._server = HTTPServer((self.host, self.port), MockMemoryHandler)
        self._server.timeout = 0.1
        self._thread = Thread(target=self._serve, daemon=True, name="MockMemoryServer")
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self

    def _serve(self) -> None:
        self._ready.set()
        assert self._server is not None
        while not getattr(self._server, "_shutdown_request", False):
            self._server.handle_request()

    def stop(self) -> None:
        if self._server:
            self._server._shutdown_request = True  # type: ignore[attr-defined]
        if self._thread:
            self._thread.join(timeout=1.0)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self) -> "MockServer":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Tracemalloc-based snapshot helpers
# ---------------------------------------------------------------------------

class TraceMallocMonitor:
    """Wrapper around tracemalloc for precise allocation tracking."""

    def __init__(self, nframe: int = 5) -> None:
        self.nframe = nframe
        self._snapshot1: Optional[tracemalloc.Snapshot] = None

    def start(self) -> None:
        tracemalloc.start(self.nframe)
        self._snapshot1 = tracemalloc.take_snapshot()

    def stop_and_diff(self, top_n: int = 10) -> List[tracemalloc.StatisticDiff]:
        """Return top N allocation diffs since start()."""
        if self._snapshot1 is None:
            return []
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        stats = snap2.compare_to(self._snapshot1, "lineno")
        return stats[:top_n]

    def current_size(self) -> int:
        """Return current allocated size in bytes (while running)."""
        if not tracemalloc.is_tracing():
            return 0
        snap = tracemalloc.take_snapshot()
        return sum(stat.size for stat in snap.statistics("lineno"))


# ---------------------------------------------------------------------------
# Convenience assertion helpers
# ---------------------------------------------------------------------------

class MemoryAssertions:
    """Mixin / helper with assertion methods for memory tests."""

    @staticmethod
    def assert_no_leak(
        fn: Callable[[], Any],
        iterations: int = 50,
        snapshot_interval: int = 5,
        threshold_bytes_per_iter: float = 4096.0,
    ) -> SoakResult:
        """Run fn for iterations and assert no linear memory growth.

        Raises AssertionError if a leak is detected.
        """
        runner = SoakTestRunner(threshold_bytes_per_iter=threshold_bytes_per_iter)
        result = runner.run(fn, iterations=iterations, snapshot_interval=snapshot_interval)
        if result.leak_report.leaked:
            raise AssertionError(
                f"Memory leak detected: {result.leak_report.summary}\n"
                f"{result.summary()}"
            )
        return result

    @staticmethod
    def assert_fd_stable(
        fn: Callable[[], Any],
        iterations: int = 50,
        max_fd_growth: int = 5,
    ) -> int:
        """Run fn and assert FD count doesn't grow by more than max_fd_growth."""
        gc.collect()
        fd_before = _fd_count()
        for _ in range(iterations):
            fn()
        gc.collect()
        fd_after = _fd_count()
        growth = fd_after - fd_before
        if growth > max_fd_growth:
            raise AssertionError(
                f"FD leak detected: before={fd_before} after={fd_after} growth={growth} max={max_fd_growth}"
            )
        return growth

    @staticmethod
    def assert_thread_stable(
        fn: Callable[[], Any],
        iterations: int = 20,
        max_thread_growth: int = 2,
    ) -> int:
        """Run fn and assert thread count doesn't grow significantly."""
        before = threading.active_count()
        for _ in range(iterations):
            fn()
        after = threading.active_count()
        growth = after - before
        if growth > max_thread_growth:
            raise AssertionError(
                f"Thread leak detected: before={before} after={after} growth={growth}"
            )
        return growth


# ---------------------------------------------------------------------------
# HTTP client helper (stdlib only)
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: float = 5.0) -> Tuple[int, bytes]:
    """Minimal HTTP/1.1 GET using stdlib.  Returns (status, body).

    Unlike urlopen, does NOT raise on 4xx/5xx — those are returned as
    (status, body) tuples so callers can assert on the status code.
    """
    from urllib.request import urlopen
    from urllib.error import URLError, HTTPError
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except HTTPError as exc:
        # 4xx/5xx: read the body from the error object and return normally
        body = exc.read() if exc.fp is not None else b""
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"HTTP GET {url} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Self-test / __main__
# ---------------------------------------------------------------------------

def _self_test() -> None:
    print("=== Memory / Soak Test Harness self-test ===\n")

    # 1. Basic snapshot
    snap = MemorySnapshot(
        rss_bytes=_rss_bytes(),
        gc_objects=_gc_object_count(),
        fd_count=_fd_count(),
        thread_count=threading.active_count(),
    )
    print(f"Snapshot: {snap}")

    # 2. Soak with clean function (no leak)
    print("\n[1] Soak test — clean function (no leak expected):")
    runner = SoakTestRunner(threshold_bytes_per_iter=8192.0)
    result = runner.run(lambda: None, iterations=50, snapshot_interval=5)
    print(result.summary())

    # 3. ObjectTracker
    print("\n[2] ObjectTracker:")
    tracker = ObjectTracker()
    tracker.record_create("Widget")
    tracker.record_create("Widget")
    tracker.record_destroy("Widget")
    rep = tracker.report()
    print(f"  report={rep}")
    print(f"  has_leaks={tracker.has_leaks()}")

    # 4. Mock server
    print("\n[3] Mock HTTP server:")
    server = MockServer()
    server.start()
    try:
        status, body = http_get(f"{server.base_url}/status")
        print(f"  /status -> HTTP {status}, {len(body)} bytes")
        status2, body2 = http_get(f"{server.base_url}/gc")
        print(f"  /gc     -> HTTP {status2}, {body2.decode()}")
    finally:
        server.stop()

    # 5. Linear regression
    print("\n[4] Linear regression leak detection:")
    snaps_flat = [
        MemorySnapshot(rss_bytes=1_000_000 + i * 100, gc_objects=0, fd_count=0, thread_count=1)
        for i in range(20)
    ]
    report_flat = analyze_snapshots(snaps_flat, threshold_bytes_per_iter=1024.0)
    print(f"  Flat growth (100B/iter): {report_flat.summary}")

    snaps_big = [
        MemorySnapshot(rss_bytes=1_000_000 + i * 50_000, gc_objects=0, fd_count=0, thread_count=1)
        for i in range(20)
    ]
    report_big = analyze_snapshots(snaps_big, threshold_bytes_per_iter=1024.0)
    print(f"  Big leak  (50KB/iter):  {report_big.summary}")

    print("\n=== self-test complete ===")


if __name__ == "__main__":
    _self_test()
