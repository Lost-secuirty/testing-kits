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

import argparse
import gc
import os

try:
    import resource  # Unix-only; absent on Windows
except ImportError:
    resource = None
import contextlib
import json
import socket
import sys
import threading
import time
import tracemalloc
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from threading import Event, Thread
from typing import Any

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _rss_bytes() -> int:
    """Return current RSS in bytes.  Uses /proc/self/status on Linux,
    resource module as fallback."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # format: "VmRSS:    12345 kB"
                    parts = line.split()
                    return int(parts[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            k32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = _PMC()
            counters.cb = ctypes.sizeof(_PMC)
            if psapi.GetProcessMemoryInfo(
                k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return int(counters.WorkingSetSize)
        except Exception:
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
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.GetProcessHandleCount.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.GetProcessHandleCount.restype = wintypes.BOOL
            count = wintypes.DWORD(0)
            if k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(count)):
                return int(count.value)
        except Exception:
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
        self._created: dict[str, int] = {}
        self._destroyed: dict[str, int] = {}
        self._weak_refs: list[weakref.ref] = []

    def record_create(self, kind: str, obj: Any | None = None) -> None:
        with self._lock:
            self._created[kind] = self._created.get(kind, 0) + 1
            if obj is not None:
                with contextlib.suppress(TypeError):
                    self._weak_refs.append(weakref.ref(obj))

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

    def report(self) -> dict[str, dict[str, int]]:
        """Return per-kind {created, destroyed, leaked} counts."""
        with self._lock:
            result: dict[str, dict[str, int]] = {}
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

def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Return (slope, intercept, r_squared) for a simple linear regression."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    if ss_xx == 0:
        return 0.0, mean_y, 0.0
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    # R²
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys, strict=False))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_sq = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return slope, intercept, r_sq


def analyze_snapshots(snapshots: list[MemorySnapshot],
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
# TEETH: a FROZEN corpus of (memory snapshot series) -> the exact leak verdict
# a CORRECT leak-regression analyzer MUST return.
#
# The oracle-able core is analyze_snapshots: fit a least-squares line to the
# RSS-vs-iteration series and report LEAKED iff the slope STRICTLY exceeds the
# threshold (bytes/iter). A leak-detection harness only has teeth if it CATCHES
# the two classic ways this verdict goes wrong:
#
#   * threshold_boundary — uses ``slope >= threshold`` instead of
#     ``slope > threshold``, so a series whose slope sits EXACTLY on the
#     threshold (a benign steady-state, not a leak) is wrongly flagged;
#   * peak_minus_min — judges leakiness from the peak-to-trough SPAN
#     (``max - min``) instead of the regression slope, so a noisy-but-flat
#     series (large oscillation, zero net trend) is wrongly flagged as leaking.
#
# An impl is a callable ``analyze(rss_series: tuple[int, ...]) -> bool`` that
# returns the leaked verdict. prove() judges each impl against the corpus's
# FROZEN LITERAL ``expected_leaked`` booleans (hand-derived from the
# slope-vs-threshold contract, NEVER read back from the oracle at runtime), so
# the check is non-circular. prove(impl) is True iff any verdict diverges from
# the frozen literal — i.e. the planted analyzer bug is caught.
#
# Pure + deterministic: a fixed least-squares fit over an injected frozen list
# of integer RSS values — no live RSS, no /proc, no tracemalloc, no clock, no
# RNG, no threads, no network, no filesystem. The frozen threshold below is the
# single shared constant every expectation was computed against.
# ---------------------------------------------------------------------------

# Frozen analysis threshold (bytes/iter) for the teeth corpus. Every
# ``expected_leaked`` literal below was hand-derived against THIS constant.
TEETH_THRESHOLD: float = 1000.0


@dataclass(frozen=True)
class LeakCase:
    """One frozen RSS series with its literal, hand-derived leak verdict."""
    name: str
    rss_series: tuple[int, ...]
    expected_leaked: bool
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND each planted
# mutant gets at least one (the peak_minus_min mutant: two) wrong. Every
# ``expected_leaked`` is derived from the contract "leaked iff slope > 1000
# B/iter", not from any reference impl at runtime.
LEAK_CORPUS: tuple[LeakCase, ...] = (
    # Perfectly flat series: slope 0 -> not a leak. Decoy for both mutants
    # (zero span, sub-threshold slope) — neither can flag it.
    LeakCase("flat", (1_000_000,) * 8, False,
             "constant RSS: slope 0, both impls agree it is not a leak"),
    # Strong monotonic growth at 5000 B/iter -> a real leak both impls catch.
    LeakCase("monotonic_growth",
             tuple(1_000_000 + i * 5000 for i in range(8)), True,
             "5000 B/iter linear growth: an unambiguous leak"),
    # Perfect line whose slope is EXACTLY the 1000 B/iter threshold. The oracle
    # uses strict ``>`` so this benign steady-state is NOT a leak; the
    # threshold_boundary mutant's ``>=`` wrongly flags it. Its 7000 B span also
    # trips the peak_minus_min mutant -> this case catches BOTH mutants.
    LeakCase("boundary_exact",
             tuple(1_000_000 + i * 1000 for i in range(8)), False,
             "slope == threshold exactly: strict > is not a leak; >= wrongly flags"),
    # Noisy-but-flat: oscillates over a 40000 B span but the least-squares slope
    # is exactly 0 (symmetric pattern, equal endpoints) -> not a leak. The
    # peak_minus_min mutant flags it on span alone; the slope-based oracle does
    # not. A second, independent case that catches peak_minus_min.
    LeakCase("noisy_flat",
             (1_000_000, 1_040_000, 1_000_000, 1_040_000,
              1_040_000, 1_000_000, 1_040_000, 1_000_000), False,
             "large oscillation, zero net slope: span-based check wrongly flags"),
    # Small steady growth at 100 B/iter, well under threshold -> not a leak.
    # A decoy whose 700 B span is also under any span threshold, so it stays a
    # true-negative for every impl (guards against a too-eager span mutant).
    LeakCase("small_growth",
             tuple(1_000_000 + i * 100 for i in range(8)), False,
             "100 B/iter: sub-threshold growth, not a leak"),
)


# --- ORACLE: reuse the harness's own correct analyze_snapshots --------------

def oracle_analyze(rss_series: tuple[int, ...]) -> bool:
    """Correct leak verdict, delegating to the harness's own
    ``analyze_snapshots``. Wraps the frozen RSS values in MemorySnapshots and
    returns its ``leaked`` flag against the frozen ``TEETH_THRESHOLD``."""
    snaps = [
        MemorySnapshot(rss_bytes=v, gc_objects=0, fd_count=0, thread_count=1, timestamp=0.0)
        for v in rss_series
    ]
    return analyze_snapshots(snaps, threshold_bytes_per_iter=TEETH_THRESHOLD).leaked


# --- Planted buggy twins (each models a real leak-analyzer defect) -----------

def threshold_boundary(rss_series: tuple[int, ...]) -> bool:
    """BUG: ``slope >= threshold`` instead of ``slope > threshold``.

    Off-by-a-boundary comparison: a series whose slope lands EXACTLY on the
    threshold is a benign steady-state, but this twin reports it as a leak.
    """
    snaps = [
        MemorySnapshot(rss_bytes=v, gc_objects=0, fd_count=0, thread_count=1, timestamp=0.0)
        for v in rss_series
    ]
    if len(snaps) < 2:
        return False
    xs = list(range(len(snaps)))
    ys = [float(s.rss_bytes) for s in snaps]
    slope, _intercept, _r = _linear_regression(xs, ys)
    return slope >= TEETH_THRESHOLD  # BUG: should be strict >


def peak_minus_min(rss_series: tuple[int, ...]) -> bool:
    """BUG: leak verdict from the peak-to-trough SPAN, not the regression slope.

    Models the naive "did memory ever spike?" check: ``max(rss) - min(rss) >
    threshold``. A noisy-but-flat series (large oscillation, zero net trend) has
    a big span yet no leak, so this twin raises a false alarm.
    """
    if not rss_series:
        return False
    return (max(rss_series) - min(rss_series)) > TEETH_THRESHOLD  # BUG: span, not slope


def prove(impl: Callable[[tuple[int, ...]], bool]) -> bool:
    """True iff ``impl`` returns the WRONG leak verdict for any frozen corpus
    case (i.e. the analyzer bug is caught): its boolean diverges from the
    hand-derived literal, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    LEAK_CORPUS, never read from the oracle; a fixed least-squares fit over
    frozen integers, no RNG/clock/threads/network/filesystem. An impl that
    raises on a corpus case counts as caught.
    """
    for case in LEAK_CORPUS:
        try:
            verdict = impl(case.rss_series)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if bool(verdict) != case.expected_leaked:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_analyze,
    mutants=(
        # fragility waiver (DP5): threshold_boundary is INHERENTLY single-load-bearing.
        # A >= vs > boundary defect differs from the correct oracle ONLY on a series whose
        # fitted slope lands EXACTLY on the threshold; every other series yields the same
        # verdict in both. So a 2nd discriminating case cannot exist without a contrived
        # second threshold — the single boundary_exact fixture is the honest, minimal catch.
        Mutant("threshold_boundary", threshold_boundary,
               "uses slope >= threshold instead of strict slope > threshold -> a "
               "series sitting exactly on the threshold is wrongly flagged as a leak"),
        Mutant("peak_minus_min", peak_minus_min,
               "judges leakiness from the peak-to-trough span (max - min) instead of "
               "the regression slope -> a noisy-but-flat series raises a false alarm"),
    ),
    corpus_size=len(LEAK_CORPUS),
    kind="oracle_swap",
    notes="a leak verdict must come from the least-squares slope strictly "
          "exceeding the threshold, not from a boundary-inclusive compare nor "
          "the peak-to-trough span",
)


def list_teeth_scenarios() -> list[str]:
    """Names of the frozen leak-analysis corpus cases (the teeth scenarios)."""
    return [c.name for c in LEAK_CORPUS]


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
        self.snapshots: list[MemorySnapshot] = []

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
    ) -> SoakResult:
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
    snapshots: list[MemorySnapshot]
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
    allocated_buffers: list[bytes] = []
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
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None
        self._ready = Event()

    def start(self) -> MockServer:
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
        if self._server:
            self._server.server_close()
            self._server = None
        self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self) -> MockServer:
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
        self._snapshot1: tracemalloc.Snapshot | None = None

    def start(self) -> None:
        tracemalloc.start(self.nframe)
        self._snapshot1 = tracemalloc.take_snapshot()

    def stop_and_diff(self, top_n: int = 10) -> list[tracemalloc.StatisticDiff]:
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

def http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    """Minimal HTTP/1.1 GET using stdlib.  Returns (status, body).

    Unlike urlopen, does NOT raise on 4xx/5xx — those are returned as
    (status, body) tuples so callers can assert on the status code.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen
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
# Self-test / __main__ — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Exercise the leak-regression analyzer this harness guards and assert the
    teeth: the correct oracle reproduces every frozen leak verdict, the
    ObjectTracker lifecycle balance holds, and the universal swap-check passes
    (oracle clean, every planted mutant caught). Pure + deterministic: no live
    RSS, no mock server, no clock — only the frozen LEAK_CORPUS."""
    report = Report("core/memory")

    # 1. The correct oracle reproduces every frozen leak verdict exactly.
    for case in LEAK_CORPUS:
        report.add(f"oracle_leak:{case.name}", case.expected_leaked,
                   oracle_analyze(case.rss_series), detail=case.note)

    # 2. ObjectTracker lifecycle balance (a created-vs-destroyed invariant the
    #    harness also guards): an unbalanced kind leaks, a balanced one does not.
    tracker = ObjectTracker()
    tracker.record_create("Widget")
    tracker.record_create("Widget")
    tracker.record_destroy("Widget")
    rep = tracker.report()
    report.add("tracker_leaked_count", 1, rep["Widget"]["leaked"],
               detail="2 created - 1 destroyed = 1 leaked")
    report.record("tracker_has_leaks", tracker.has_leaks(),
                  detail="an unbalanced kind must report a leak")

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Memory / soak leak-regression controls and teeth")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print("\n".join(list_teeth_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
