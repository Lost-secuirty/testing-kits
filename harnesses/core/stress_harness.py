#!/usr/bin/env python3
"""
stress_harness.py — Code-Only Stress Test Harness (2026)
=========================================================
Pure-Python (ZERO dependencies) stress testing engine implementing:
  - Open workload model (constant arrival rate) to prevent coordinated omission
  - Weighted task scenarios (read-heavy vs write-heavy)
  - HdrHistogram-style percentile metrics (p50, p95, p99, p99.9)
  - Live console reporting with throughput and error tracking
  - CLI-driven configuration
  - Built-in mock server for self-testing

Uses only stdlib: asyncio, http.server, urllib. No pip install needed.

Based on research from: "Architecting High-Performance Code-Only Test Harnesses"

Usage:
  python stress_harness.py --url http://localhost:8080 --rate 100 --duration 30
  python stress_harness.py --self-test
  python stress_harness.py --list-scenarios
  python stress_harness.py --help

Author: Scott (codeing testing harnesses project)
"""

import argparse
import asyncio
import contextlib
import json
import math
import random
import signal
import statistics
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path as _Path
from typing import Any
from urllib.parse import urlparse

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import Mutant, Report, Teeth

# ============================================================
# CONFIGURATION & DATA CLASSES
# ============================================================

class WorkloadModel(Enum):
    OPEN = "open"       # constant arrival rate — prevents coordinated omission
    CLOSED = "closed"   # VU-paced — susceptible to coordinated omission


@dataclass
class HarnessConfig:
    """All tunable knobs for a stress run."""
    target_url: str = "http://localhost:8080"
    rate: int = 100                    # requests per second (open model)
    duration: int = 30                 # seconds
    max_vus: int = 500                 # max concurrent virtual users
    timeout: float = 10.0             # per-request timeout (seconds)
    ramp_up: int = 0                  # ramp-up seconds (0 = instant full rate)
    scenario: str = "default"         # scenario name to run
    auth_token: str = ""              # bearer token for auth headers
    report_interval: float = 2.0      # live stats every N seconds
    workload_model: WorkloadModel = WorkloadModel.OPEN
    verbose: bool = False             # debug logging


@dataclass
class RequestResult:
    """Outcome of a single HTTP request."""
    task_name: str
    method: str
    url: str
    status: int
    latency_ms: float         # wall-clock latency
    scheduled_at: float        # when the request SHOULD have been sent
    sent_at: float             # when the request WAS actually sent
    completed_at: float
    error: str | None = None

    @property
    def corrected_latency_ms(self) -> float:
        """Latency from *scheduled* send time — defeats coordinated omission."""
        return (self.completed_at - self.scheduled_at) * 1000.0


# ============================================================
# METRICS COLLECTOR
# ============================================================

class MetricsCollector:
    """
    Thread-safe metrics collection with percentile tracking.
    Uses corrected latency (from scheduled time) to prevent
    coordinated omission from hiding tail latency.
    """

    def __init__(self):
        self.results: list[RequestResult] = []
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._interval_results: list[RequestResult] = []
        self.total_requests = 0
        self.total_errors = 0
        self.total_success = 0
        self.status_counts: dict[int, int] = defaultdict(int)

    def start(self):
        self._start_time = time.monotonic()

    def record(self, result: RequestResult):
        with self._lock:
            self.results.append(result)
            self._interval_results.append(result)
            self.total_requests += 1
            self.status_counts[result.status] += 1
            if result.error or result.status >= 400:
                self.total_errors += 1
            else:
                self.total_success += 1

    def flush_interval(self) -> dict[str, Any]:
        """Drain interval buffer and return stats for live reporting."""
        with self._lock:
            batch = self._interval_results[:]
            self._interval_results.clear()

        if not batch:
            return {"count": 0}

        latencies = [r.corrected_latency_ms for r in batch]
        errors = sum(1 for r in batch if r.error or r.status >= 400)
        elapsed = time.monotonic() - self._start_time

        return {
            "elapsed_s": round(elapsed, 1),
            "count": len(batch),
            "rps": round(self.total_requests / max(elapsed, 0.001), 1),
            "errors": errors,
            "error_pct": round(errors / len(batch) * 100, 1) if batch else 0,
            "p50_ms": round(self._percentile(latencies, 50), 2),
            "p95_ms": round(self._percentile(latencies, 95), 2),
            "p99_ms": round(self._percentile(latencies, 99), 2),
        }

    def final_report(self) -> str:
        """Generate the final summary after the run completes."""
        if not self.results:
            return "No requests were recorded."

        elapsed = time.monotonic() - self._start_time
        latencies = [r.corrected_latency_ms for r in self.results]
        raw_latencies = [r.latency_ms for r in self.results]

        lines = [
            "",
            "=" * 66,
            "  STRESS TEST  —  FINAL REPORT",
            "=" * 66,
            f"  Duration:          {elapsed:.1f}s",
            f"  Total Requests:    {self.total_requests:,}",
            f"  Successful:        {self.total_success:,}",
            f"  Failed:            {self.total_errors:,}",
            f"  Error Rate:        {self.total_errors / max(self.total_requests, 1) * 100:.2f}%",
            f"  Throughput:        {self.total_requests / max(elapsed, 0.001):.1f} req/s",
            "",
            "  — Corrected Latency (from scheduled time) —",
            f"    Min:     {min(latencies):.2f} ms",
            f"    Mean:    {statistics.mean(latencies):.2f} ms",
            f"    Median:  {self._percentile(latencies, 50):.2f} ms",
            f"    p90:     {self._percentile(latencies, 90):.2f} ms",
            f"    p95:     {self._percentile(latencies, 95):.2f} ms",
            f"    p99:     {self._percentile(latencies, 99):.2f} ms",
            f"    p99.9:   {self._percentile(latencies, 99.9):.2f} ms",
            f"    Max:     {max(latencies):.2f} ms",
            "",
            "  — Raw Latency (actual network round-trip) —",
            f"    Min:     {min(raw_latencies):.2f} ms",
            f"    Mean:    {statistics.mean(raw_latencies):.2f} ms",
            f"    p95:     {self._percentile(raw_latencies, 95):.2f} ms",
            f"    p99:     {self._percentile(raw_latencies, 99):.2f} ms",
            f"    Max:     {max(raw_latencies):.2f} ms",
            "",
            "  — Status Code Distribution —",
        ]

        for code in sorted(self.status_counts.keys()):
            count = self.status_counts[code]
            lines.append(f"    {code}: {count:,}  ({count / self.total_requests * 100:.1f}%)")

        # Task breakdown
        task_groups: dict[str, list[float]] = defaultdict(list)
        for r in self.results:
            task_groups[r.task_name].append(r.corrected_latency_ms)

        if len(task_groups) > 1:
            lines.append("")
            lines.append("  — Per-Task Breakdown —")
            for name, lats in sorted(task_groups.items()):
                lines.append(
                    f"    {name}: {len(lats):,} reqs | "
                    f"p50={self._percentile(lats, 50):.1f}ms | "
                    f"p99={self._percentile(lats, 99):.1f}ms"
                )

        lines.append("=" * 66)
        return "\n".join(lines)

    @staticmethod
    def _percentile(data: list[float], pct: float) -> float:
        """Compute percentile without numpy — linear interpolation."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n == 1:
            return sorted_data[0]
        k = (pct / 100.0) * (n - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


# ============================================================
# TASK SCENARIOS
# ============================================================

@dataclass
class TaskDef:
    """A single weighted task in a scenario."""
    name: str
    method: str
    path: str
    weight: int = 1
    body: dict | None = None
    headers: dict | None = None


SCENARIOS: dict[str, list[TaskDef]] = {
    "default": [
        TaskDef(name="health_check", method="GET", path="/", weight=5),
        TaskDef(name="post_data", method="POST", path="/api/data",
                weight=1, body={"payload": "stress_test", "ts": "{{TIMESTAMP}}"}),
    ],
    "read_heavy": [
        TaskDef(name="read_index", method="GET", path="/", weight=8),
        TaskDef(name="read_api", method="GET", path="/api/status", weight=4),
        TaskDef(name="write_event", method="POST", path="/api/events",
                weight=1, body={"event": "ping"}),
    ],
    "write_heavy": [
        TaskDef(name="post_ingest", method="POST", path="/api/ingest",
                weight=5, body={"data": "bulk_payload", "seq": "{{SEQ}}"}),
        TaskDef(name="put_update", method="PUT", path="/api/update",
                weight=3, body={"status": "processing"}),
        TaskDef(name="get_health", method="GET", path="/", weight=1),
    ],
    "api_crud": [
        TaskDef(name="create", method="POST", path="/api/items",
                weight=2, body={"name": "item_{{SEQ}}"}),
        TaskDef(name="read", method="GET", path="/api/items", weight=5),
        TaskDef(name="update", method="PUT", path="/api/items/1",
                weight=2, body={"name": "updated_{{SEQ}}"}),
        TaskDef(name="delete", method="DELETE", path="/api/items/1", weight=1),
    ],
}


def build_weighted_task_list(tasks: list[TaskDef]) -> list[TaskDef]:
    """Expand tasks by weight into a flat list for round-robin selection."""
    expanded = []
    for t in tasks:
        expanded.extend([t] * t.weight)
    return expanded


def resolve_body(body: dict | None, seq: int) -> bytes | None:
    """Replace template placeholders in request bodies."""
    if body is None:
        return None
    raw = json.dumps(body)
    raw = raw.replace("{{TIMESTAMP}}", datetime.now(timezone.utc).isoformat())
    raw = raw.replace("{{SEQ}}", str(seq))
    return raw.encode("utf-8")


# ============================================================
# TEETH: frozen stress-metric corpus + planted analyzer defects
# ============================================================

REQUESTS_ONE = "requests:1"


@dataclass(frozen=True)
class StressMetricCase:
    """One frozen stress observation with hand-authored metric events."""

    name: str
    results: tuple[RequestResult, ...] = ()
    tasks: tuple[TaskDef, ...] = ()
    expected_events: tuple[str, ...] = ()


def _result(
    *,
    task_name: str,
    status: int,
    latency_ms: float,
    scheduled_at: float,
    sent_at: float,
    completed_at: float,
    error: str | None = None,
) -> RequestResult:
    return RequestResult(
        task_name=task_name,
        method="GET",
        url="https://example.invalid/",
        status=status,
        latency_ms=latency_ms,
        scheduled_at=scheduled_at,
        sent_at=sent_at,
        completed_at=completed_at,
        error=error,
    )


STRESS_METRIC_CORPUS: tuple[StressMetricCase, ...] = (
    StressMetricCase(
        name="corrected_latency_includes_scheduler_lag",
        results=(
            _result(
                task_name="read",
                status=200,
                latency_ms=50.0,
                scheduled_at=0.00,
                sent_at=0.20,
                completed_at=0.25,
            ),
        ),
        expected_events=(
            REQUESTS_ONE,
            "errors:0",
            "max_corrected_ms:250.0",
            "p95_corrected_ms:250.0",
        ),
    ),
    StressMetricCase(
        name="http_status_counts_as_error",
        results=(
            _result(
                task_name="write",
                status=500,
                latency_ms=20.0,
                scheduled_at=0.00,
                sent_at=0.00,
                completed_at=0.02,
            ),
        ),
        expected_events=(
            REQUESTS_ONE,
            "errors:1",
            "max_corrected_ms:20.0",
            "p95_corrected_ms:20.0",
        ),
    ),
    StressMetricCase(
        name="connection_error_counts_as_error",
        results=(
            _result(
                task_name="read",
                status=0,
                latency_ms=5.0,
                scheduled_at=0.00,
                sent_at=0.00,
                completed_at=0.005,
                error="connection_refused",
            ),
        ),
        expected_events=(
            REQUESTS_ONE,
            "errors:1",
            "max_corrected_ms:5.0",
            "p95_corrected_ms:5.0",
        ),
    ),
    StressMetricCase(
        name="weighted_scenario_expansion",
        tasks=(
            TaskDef(name="read", method="GET", path="/", weight=3),
            TaskDef(name="write", method="POST", path="/api", weight=1),
        ),
        expected_events=(
            "weighted_total:4",
            "task:read:3",
            "task:write:1",
        ),
    ),
)


def oracle_stress_audit(case: StressMetricCase) -> tuple[str, ...]:
    """Correct pure analyzer for stress-metric observations."""
    if case.tasks:
        expanded = build_weighted_task_list(list(case.tasks))
        events = [f"weighted_total:{len(expanded)}"]
        counts: dict[str, int] = defaultdict(int)
        for task in expanded:
            counts[task.name] += 1
        events.extend(f"task:{name}:{counts[name]}" for name in sorted(counts))
        return tuple(events)

    latencies = [r.corrected_latency_ms for r in case.results]
    errors = sum(1 for r in case.results if r.error or r.status >= 400)
    return (
        f"requests:{len(case.results)}",
        f"errors:{errors}",
        f"max_corrected_ms:{max(latencies) if latencies else 0.0:.1f}",
        f"p95_corrected_ms:{MetricsCollector._percentile(latencies, 95):.1f}",
    )


def raw_latency_auditor(case: StressMetricCase) -> tuple[str, ...]:
    """BUG: uses raw request duration and misses scheduler lag."""
    if case.tasks:
        return oracle_stress_audit(case)
    latencies = [r.latency_ms for r in case.results]
    errors = sum(1 for r in case.results if r.error or r.status >= 400)
    return (
        f"requests:{len(case.results)}",
        f"errors:{errors}",
        f"max_corrected_ms:{max(latencies) if latencies else 0.0:.1f}",
        f"p95_corrected_ms:{MetricsCollector._percentile(latencies, 95):.1f}",
    )


def status_only_error_auditor(case: StressMetricCase) -> tuple[str, ...]:
    """BUG: counts HTTP failures but misses transport errors."""
    if case.tasks:
        return oracle_stress_audit(case)
    latencies = [r.corrected_latency_ms for r in case.results]
    errors = sum(1 for r in case.results if r.status >= 400)
    return (
        f"requests:{len(case.results)}",
        f"errors:{errors}",
        f"max_corrected_ms:{max(latencies) if latencies else 0.0:.1f}",
        f"p95_corrected_ms:{MetricsCollector._percentile(latencies, 95):.1f}",
    )


def equal_weight_auditor(case: StressMetricCase) -> tuple[str, ...]:
    """BUG: treats every task as weight=1, distorting load mix."""
    if not case.tasks:
        return oracle_stress_audit(case)
    events = [f"weighted_total:{len(case.tasks)}"]
    events.extend(f"task:{task.name}:1" for task in sorted(case.tasks, key=lambda t: t.name))
    return tuple(events)


def prove(impl: Callable[[StressMetricCase], tuple[str, ...]]) -> bool:
    """True iff the analyzer diverges from any frozen stress metric case."""
    for case in STRESS_METRIC_CORPUS:
        try:
            if tuple(impl(case)) != case.expected_events:
                return True
        except Exception:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_stress_audit,
    mutants=(
        Mutant("raw_latency_auditor", raw_latency_auditor,
               "misses coordinated omission by ignoring scheduled send time"),
        Mutant("status_only_error_auditor", status_only_error_auditor,
               "misses transport-level failures with status 0"),
        Mutant("equal_weight_auditor", equal_weight_auditor,
               "ignores weighted scenario expansion"),
    ),
    corpus_size=len(STRESS_METRIC_CORPUS),
    kind="oracle_swap",
    notes="Frozen stress metric and workload-shape corpus.",
)


# ============================================================
# HTTP CLIENT — stdlib-based, runs in thread pool
# ============================================================

def _do_http_request(
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str | None]:
    """
    Perform a single HTTP request using http.client (stdlib).
    Uses persistent connections (keep-alive) for connection pooling.
    Returns (status_code, error_string_or_None).
    Runs in a thread pool so the event loop stays free.
    """
    import http.client

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read(1024)  # drain body
        status = resp.status
        conn.close()
        return status, None
    except TimeoutError:
        return 0, "timeout"
    except ConnectionRefusedError:
        return 0, "connection_refused"
    except OSError as e:
        return 0, f"connection_error: {e}"
    except Exception as e:
        return 0, f"unexpected: {type(e).__name__}: {e}"


# ============================================================
# THE ENGINE — open workload constant-arrival-rate dispatcher
# ============================================================

class StressEngine:
    """
    Core stress test engine.

    Open workload model: dispatches requests at a constant arrival rate
    regardless of server response times. This prevents coordinated
    omission — the #1 measurement flaw in stress testing.

    Uses a ThreadPoolExecutor to run blocking HTTP calls concurrently
    while the asyncio event loop manages scheduling precision.
    """

    def __init__(self, config: HarnessConfig, metrics: MetricsCollector):
        self.config = config
        self.metrics = metrics
        self._stop = False
        self._active_vus = 0
        self._max_vu_reached = 0
        self._seq = 0
        self._dropped = 0

        if config.scenario not in SCENARIOS:
            available = ", ".join(SCENARIOS.keys())
            raise ValueError(
                f"Unknown scenario '{config.scenario}'. Available: {available}"
            )
        self._task_pool = build_weighted_task_list(SCENARIOS[config.scenario])

    def stop(self):
        self._stop = True

    async def _execute_request(
        self,
        pool: ThreadPoolExecutor,
        task: TaskDef,
        scheduled_at: float,
        seq: int,
    ):
        """Execute a single HTTP request via thread pool and record metrics."""
        self._active_vus += 1
        self._max_vu_reached = max(self._max_vu_reached, self._active_vus)

        url = self.config.target_url.rstrip("/") + task.path
        body = resolve_body(task.body, seq)
        headers: dict[str, str] = {}
        if task.headers:
            headers.update(task.headers)
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        if body:
            headers.setdefault("Content-Type", "application/json")

        sent_at = time.monotonic()

        loop = asyncio.get_running_loop()
        status, error_msg = await loop.run_in_executor(
            pool,
            _do_http_request,
            task.method,
            url,
            body,
            headers,
            self.config.timeout,
        )

        completed_at = time.monotonic()
        self._active_vus -= 1

        result = RequestResult(
            task_name=task.name,
            method=task.method,
            url=url,
            status=status,
            latency_ms=(completed_at - sent_at) * 1000.0,
            scheduled_at=scheduled_at,
            sent_at=sent_at,
            completed_at=completed_at,
            error=error_msg,
        )
        self.metrics.record(result)

        if self.config.verbose and error_msg:
            print(f"  [DBG] {task.name} -> {error_msg}", file=sys.stderr)

    async def _reporter(self):
        """Print live stats to console at regular intervals."""
        while not self._stop:
            await asyncio.sleep(self.config.report_interval)
            stats = self.metrics.flush_interval()
            if stats["count"] == 0:
                continue
            print(
                f"  [{stats['elapsed_s']:>6.1f}s] "
                f"reqs={self.metrics.total_requests:>7,} | "
                f"rps={stats['rps']:>7.1f} | "
                f"errs={stats['errors']:>4} ({stats['error_pct']:.1f}%) | "
                f"p50={stats['p50_ms']:>8.1f}ms | "
                f"p95={stats['p95_ms']:>8.1f}ms | "
                f"p99={stats['p99_ms']:>8.1f}ms | "
                f"VUs={self._active_vus:>4}/{self._max_vu_reached}"
            )

    async def run(self):
        """
        Main execution loop — constant arrival rate dispatcher.

        Sleeps until the NEXT scheduled dispatch time, fires a request
        into the thread pool. If the VU pool is exhausted, the request
        is dropped and counted as a miss.
        """
        cfg = self.config
        print(f"\n{'=' * 66}")
        print("  STRESS HARNESS — starting run")
        print(f"{'=' * 66}")
        print(f"  Target:     {cfg.target_url}")
        print(f"  Rate:       {cfg.rate} req/s")
        print(f"  Duration:   {cfg.duration}s")
        print(f"  Max VUs:    {cfg.max_vus}")
        print(f"  Scenario:   {cfg.scenario}")
        print(f"  Workload:   {cfg.workload_model.value} (coordinated omission: "
              f"{'PREVENTED' if cfg.workload_model == WorkloadModel.OPEN else 'SUSCEPTIBLE'})")
        if cfg.ramp_up:
            print(f"  Ramp-up:    {cfg.ramp_up}s")
        print(f"{'=' * 66}\n")

        pool = ThreadPoolExecutor(max_workers=cfg.max_vus)
        self.metrics.start()

        reporter_task = asyncio.create_task(self._reporter())

        start = time.monotonic()
        request_idx = 0
        pending: set[asyncio.Task] = set()

        try:
            while not self._stop:
                elapsed = time.monotonic() - start
                if elapsed >= cfg.duration:
                    break

                # Compute current rate (with optional ramp-up)
                if cfg.ramp_up > 0 and elapsed < cfg.ramp_up:
                    max(1, int(cfg.rate * (elapsed / cfg.ramp_up)))
                else:
                    pass

                # Schedule time for THIS request
                scheduled_at = start + (request_idx / cfg.rate)

                # Sleep until scheduled dispatch time
                sleep_for = scheduled_at - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

                # Re-check stop after sleep
                if self._stop:
                    break

                # Check VU capacity
                if self._active_vus >= cfg.max_vus:
                    self._dropped += 1
                    if cfg.verbose:
                        print(f"  [DBG] VU pool exhausted ({self._active_vus}/{cfg.max_vus}), "
                              f"dropping request #{request_idx}", file=sys.stderr)
                    request_idx += 1
                    continue

                # Pick task from weighted pool (round-robin)
                task_def = self._task_pool[request_idx % len(self._task_pool)]
                self._seq += 1

                # Fire and forget — open workload model
                t = asyncio.create_task(
                    self._execute_request(pool, task_def, scheduled_at, self._seq)
                )
                pending.add(t)
                t.add_done_callback(pending.discard)

                request_idx += 1

        except asyncio.CancelledError:
            pass
        finally:
            # Drain in-flight requests
            if pending:
                print(f"\n  Draining {len(pending)} in-flight requests...")
                done, still_pending = await asyncio.wait(
                    pending, timeout=cfg.timeout + 2
                )
                for t in still_pending:
                    t.cancel()

            self._stop = True
            reporter_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reporter_task

            pool.shutdown(wait=False)

        # Final report
        print(self.metrics.final_report())

        if self._dropped > 0:
            print(f"\n  WARNING: {self._dropped:,} requests dropped (VU pool exhausted)")
            print(f"    Increase --max-vus beyond {cfg.max_vus} to sustain {cfg.rate} req/s")

        print(f"\n  Peak concurrent VUs: {self._max_vu_reached}")
        print()


# ============================================================
# BUILT-IN MOCK SERVER — stdlib http.server (runs in a thread)
# ============================================================

class MockHandler(BaseHTTPRequestHandler):
    """
    Tiny HTTP handler for debugging the harness.
    Responds to any method/path with 200 + JSON.
    Injects random 1-50ms latency to test metrics.
    """

    def do_ANY(self):
        delay = random.uniform(0.001, 0.05)
        time.sleep(delay)
        body = json.dumps({
            "status": "ok",
            "method": self.command,
            "path": self.path,
            "latency_injected_ms": round(delay * 1000, 2),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Route all methods to do_ANY
    do_GET = do_ANY
    do_POST = do_ANY
    do_PUT = do_ANY
    do_DELETE = do_ANY
    do_PATCH = do_ANY

    def log_message(self, format, *args):
        """Suppress default request logging to keep console clean."""
        pass


def start_mock_server(port: int = 8080) -> ThreadingHTTPServer:
    """Start threaded mock server in a daemon thread. Returns the server instance."""
    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    server.daemon_threads = True  # clean up handler threads on shutdown
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  Mock server running on http://127.0.0.1:{port} (threaded)")
    return server


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stress_harness",
        description="Code-only Python stress test harness with open workload model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run against local server at 100 req/s for 30s
  python stress_harness.py --url http://localhost:8080 --rate 100 --duration 30

  # Read-heavy scenario with ramp-up
  python stress_harness.py --url http://localhost:8080 --rate 500 --duration 60 \\
      --scenario read_heavy --ramp-up 10

  # Start the built-in mock server only
  python stress_harness.py --mock-server

  # Run mock server + stress test together (self-test)
  python stress_harness.py --self-test

  # List available scenarios
  python stress_harness.py --list-scenarios
        """,
    )

    p.add_argument("--url", default="http://localhost:8080",
                    help="Target base URL (default: http://localhost:8080)")
    p.add_argument("--rate", type=int, default=100,
                    help="Requests per second — constant arrival rate (default: 100)")
    p.add_argument("--duration", type=int, default=30,
                    help="Test duration in seconds (default: 30)")
    p.add_argument("--max-vus", type=int, default=500,
                    help="Maximum concurrent virtual users (default: 500)")
    p.add_argument("--timeout", type=float, default=10.0,
                    help="Per-request timeout in seconds (default: 10.0)")
    p.add_argument("--ramp-up", type=int, default=0,
                    help="Ramp-up period in seconds (default: 0 = instant)")
    p.add_argument("--scenario", default="default",
                    choices=list(SCENARIOS.keys()),
                    help="Scenario to run (default: default)")
    p.add_argument("--auth-token", default="",
                    help="Bearer token for Authorization header")
    p.add_argument("--report-interval", type=float, default=2.0,
                    help="Live reporting interval in seconds (default: 2.0)")
    p.add_argument("--verbose", "-v", action="store_true",
                    help="Enable debug logging")
    p.add_argument("--mock-server", action="store_true",
                    help="Start the built-in mock server only (no stress test)")
    p.add_argument("--mock-port", type=int, default=8080,
                    help="Port for mock server (default: 8080)")
    p.add_argument("--self-test", action="store_true",
                    help="Run mock server + stress test together")
    p.add_argument("--json", action="store_true",
                    help="Run self-test and emit the structured Report as JSON")
    p.add_argument("--list-scenarios", action="store_true",
                    help="List available scenarios and exit")

    return p


def list_scenarios() -> list[str]:
    return [case.name for case in STRESS_METRIC_CORPUS]


def print_workload_scenarios() -> None:
    print("\nAvailable Scenarios:")
    print("-" * 50)
    for name, tasks in SCENARIOS.items():
        total_weight = sum(t.weight for t in tasks)
        print(f"\n  [{name}]")
        for t in tasks:
            pct = t.weight / total_weight * 100
            print(f"    {t.method:>6} {t.path:<25} weight={t.weight} ({pct:.0f}%)")
    print()


# ============================================================
# MAIN
# ============================================================

async def async_main():
    parser = build_parser()
    args = parser.parse_args()

    if args.list_scenarios:
        print_workload_scenarios()
        return

    if args.mock_server:
        server = start_mock_server(args.mock_port)
        print("  Press Ctrl+C to stop\n")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            server.shutdown()
            server.server_close()
        return

    config = HarnessConfig(
        target_url=args.url,
        rate=args.rate,
        duration=args.duration,
        max_vus=args.max_vus,
        timeout=args.timeout,
        ramp_up=args.ramp_up,
        scenario=args.scenario,
        auth_token=args.auth_token,
        report_interval=args.report_interval,
        verbose=args.verbose,
    )

    metrics = MetricsCollector()
    engine = StressEngine(config, metrics)

    # Graceful shutdown on Ctrl+C
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, engine.stop)

    if args.json:
        args.self_test = True

    mock_server = None
    run_context = contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext()
    with run_context:
        if args.self_test:
            print("\n  SELF-TEST MODE: starting mock server + stress test\n")
            config.target_url = f"http://127.0.0.1:{args.mock_port}"
            mock_server = start_mock_server(args.mock_port)
            await asyncio.sleep(0.3)  # let server bind

        try:
            await engine.run()
        finally:
            if mock_server:
                mock_server.shutdown()
                mock_server.server_close()

    if args.self_test:
        report = Report("core/stress")
        report.record(
            "self_test_recorded_requests",
            metrics.total_requests >= 1,
            detail=f"requests={metrics.total_requests}",
        )
        report.add("self_test_errors", 0, metrics.total_errors)
        for case in STRESS_METRIC_CORPUS:
            report.add(
                f"oracle_stress_audit:{case.name}",
                list(case.expected_events),
                list(oracle_stress_audit(case)),
            )
        report.assert_teeth(TEETH)
        raise SystemExit(report.emit(as_json=args.json))


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n  Interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
