#!/usr/bin/env python3
"""
Chaos / Resilience Test Harness (Harness 7 of 36)
=================================================

Tests system resilience under failure conditions. The CircuitBreaker state
machine is the oracle-able core: a deterministic transition function over a
sequence of call outcomes. A mock HTTP server is available under ``main`` only
(never bound at import, never inside ``prove``); the teeth never touch a socket,
a clock, threads, or RNG.

GOLD shape — declares a module-level ``TEETH`` over the in-process breaker
oracle so the hardened gate (``tools/proof_audit.py``) verifies real teeth.

Run:
  python harnesses/core/chaos_test_harness.py --self-test
  python harnesses/core/chaos_test_harness.py --json
  python harnesses/core/chaos_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import http.server
import json
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an open circuit breaker."""
    pass


class CircuitBreaker:
    """
    State machine: CLOSED → OPEN → HALF_OPEN → CLOSED

    - CLOSED: normal operation; failures accumulate against threshold.
    - OPEN: fast-fail for open_duration seconds after failure threshold hit.
    - HALF_OPEN: allow one probe request; success → CLOSED, failure → OPEN.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        open_duration: float = 5.0,
        success_threshold: int = 1,
        fallback: Any = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.open_duration = open_duration
        self.success_threshold = success_threshold
        self.fallback = fallback

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition()
            return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def call(self, fn: Callable, *args, **kwargs) -> Any:
        """
        Execute *fn* through the circuit breaker.

        Raises CircuitOpenError (without calling fn) when the circuit is
        OPEN and the open_duration cooldown has not yet elapsed.
        """
        with self._lock:
            self._maybe_transition()
            current = self._state

        if current == CircuitState.OPEN:
            raise CircuitOpenError("Circuit is OPEN – fast failing")

        if current == CircuitState.HALF_OPEN:
            return self._call_half_open(fn, *args, **kwargs)

        # CLOSED path
        return self._call_closed(fn, *args, **kwargs)

    def reset(self) -> None:
        """Force the circuit back to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_transition(self) -> None:
        """Check whether to transition OPEN → HALF_OPEN (called under lock)."""
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and (time.monotonic() - self._opened_at) >= self.open_duration
        ):
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0

    def _call_closed(self, fn: Callable, *args, **kwargs) -> Any:
        try:
            result = fn(*args, **kwargs)
            # A success in CLOSED resets the failure counter
            with self._lock:
                self._failure_count = 0
            return result
        except Exception:
            with self._lock:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._trip()
            raise

    def _call_half_open(self, fn: Callable, *args, **kwargs) -> Any:
        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._close()
            return result
        except Exception:
            with self._lock:
                self._trip()
            raise

    def _trip(self) -> None:
        """Transition to OPEN (called under lock)."""
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._failure_count = 0
        self._success_count = 0

    def _close(self) -> None:
        """Transition to CLOSED (called under lock)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at = None


# ---------------------------------------------------------------------------
# Fault Injector
# ---------------------------------------------------------------------------

class FaultType(Enum):
    NONE = "none"
    LATENCY = "latency"
    ERROR = "error"
    TIMEOUT = "timeout"
    CORRUPT = "corrupt"


class FaultInjector:
    """
    Wraps a callable and injects configurable faults.

    Faults supported:
    - latency: sleep *latency_ms* ms before delegating to the real call
    - error: raise RuntimeError immediately
    - timeout: sleep long enough to trigger caller's timeout
    - corrupt: call succeeds but returns garbled data
    """

    def __init__(self) -> None:
        self._fault: FaultType = FaultType.NONE
        self._latency_ms: float = 0.0
        self._error_message: str = "Injected fault error"
        self._corrupt_fn: Callable[[Any], Any] | None = None
        self._timeout_seconds: float = 60.0
        self._enabled: bool = False

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def inject_latency(self, ms: float) -> FaultInjector:
        self._fault = FaultType.LATENCY
        self._latency_ms = ms
        self._enabled = True
        return self

    def inject_error(self, message: str = "Injected fault error") -> FaultInjector:
        self._fault = FaultType.ERROR
        self._error_message = message
        self._enabled = True
        return self

    def inject_timeout(self, seconds: float = 60.0) -> FaultInjector:
        self._fault = FaultType.TIMEOUT
        self._timeout_seconds = seconds
        self._enabled = True
        return self

    def inject_corruption(
        self, corrupt_fn: Callable[[Any], Any] | None = None
    ) -> FaultInjector:
        self._fault = FaultType.CORRUPT
        self._corrupt_fn = corrupt_fn or (lambda v: str(v)[::-1])
        self._enabled = True
        return self

    def disable(self) -> FaultInjector:
        self._fault = FaultType.NONE
        self._enabled = False
        return self

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def fault_type(self) -> FaultType:
        return self._fault

    # ------------------------------------------------------------------
    # Core wrap method
    # ------------------------------------------------------------------

    def wrap(self, fn: Callable) -> Callable:
        """Return a new callable that applies the configured fault before/after *fn*."""

        def _wrapped(*args, **kwargs):
            if not self._enabled or self._fault == FaultType.NONE:
                return fn(*args, **kwargs)

            if self._fault == FaultType.LATENCY:
                time.sleep(self._latency_ms / 1000.0)
                return fn(*args, **kwargs)

            if self._fault == FaultType.ERROR:
                raise RuntimeError(self._error_message)

            if self._fault == FaultType.TIMEOUT:
                time.sleep(self._timeout_seconds)
                return fn(*args, **kwargs)

            if self._fault == FaultType.CORRUPT:
                result = fn(*args, **kwargs)
                return self._corrupt_fn(result)

            return fn(*args, **kwargs)

        return _wrapped

    # ------------------------------------------------------------------
    # Convenience: call directly
    # ------------------------------------------------------------------

    def __call__(self, fn: Callable, *args, **kwargs) -> Any:
        return self.wrap(fn)(*args, **kwargs)


# ---------------------------------------------------------------------------
# Retry with Exponential Back-off + Jitter
# ---------------------------------------------------------------------------

TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
    urllib.error.URLError,
)


def is_transient(exc: Exception) -> bool:
    """Return True if *exc* is considered a transient (retryable) error."""
    return isinstance(exc, TRANSIENT_EXCEPTIONS)


def retry_with_backoff(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 30.0,
    jitter: bool = True,
    retryable: Callable[[Exception], bool] = is_transient,
    **kwargs,
) -> Any:
    """
    Call *fn* up to *max_attempts* times with exponential back-off + jitter.

    Only retries when *retryable(exc)* returns True.
    Raises the last exception if all attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not retryable(exc):
                raise
            if attempt < max_attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                if jitter:
                    delay *= random.uniform(0.5, 1.5)
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Resilience Metrics
# ---------------------------------------------------------------------------

class ResilienceMetrics:
    """Tracks success/failure/open counts across calls."""

    def __init__(self) -> None:
        self.success_count: int = 0
        self.failure_count: int = 0
        self.open_count: int = 0         # times CircuitOpenError was raised
        self.total_latency_ms: float = 0.0
        self._latency_samples: list[float] = []
        self._lock = threading.Lock()

    def record_success(self, latency_ms: float = 0.0) -> None:
        with self._lock:
            self.success_count += 1
            self.total_latency_ms += latency_ms
            self._latency_samples.append(latency_ms)

    def record_failure(self, latency_ms: float = 0.0) -> None:
        with self._lock:
            self.failure_count += 1
            self.total_latency_ms += latency_ms
            self._latency_samples.append(latency_ms)

    def record_open(self) -> None:
        with self._lock:
            self.open_count += 1

    def reset(self) -> None:
        with self._lock:
            self.success_count = 0
            self.failure_count = 0
            self.open_count = 0
            self.total_latency_ms = 0.0
            self._latency_samples.clear()

    @property
    def total_calls(self) -> int:
        return self.success_count + self.failure_count + self.open_count

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.success_count / self.total_calls

    @property
    def avg_latency_ms(self) -> float:
        samples = self._latency_samples
        if not samples:
            return 0.0
        return sum(samples) / len(samples)

    def summary(self) -> dict[str, Any]:
        return {
            "success": self.success_count,
            "failure": self.failure_count,
            "open": self.open_count,
            "total": self.total_calls,
            "success_rate": round(self.success_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# Mock Chaos HTTP Server
# ---------------------------------------------------------------------------

class MockChaosHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP request handler that simulates chaos scenarios.

    Behaviour is driven by query parameters or by the server's current
    *scenario* attribute (set from tests via ``server.scenario``).
    """

    def log_message(self, fmt: str, *args) -> None:  # silence default logging
        pass

    # Routes -----------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")

        if path == "/ok":
            self._send_json(200, {"status": "ok"})

        elif path == "/slow":
            delay = float(self._query_param("delay", "0.5"))
            time.sleep(delay)
            self._send_json(200, {"status": "ok", "delay": delay})

        elif path == "/fail":
            code = int(self._query_param("code", "500"))
            self._send_json(code, {"error": "forced failure"})

        elif path == "/flaky":
            # Fail the first N requests, then succeed
            with self.server._lock:
                self.server.request_count += 1
                count = self.server.request_count
                fail_count = getattr(self.server, "fail_count", 2)
            if count <= fail_count:
                self._send_json(500, {"error": "flaky", "attempt": count})
            else:
                self._send_json(200, {"status": "ok", "attempt": count})

        elif path == "/corrupt":
            self._send_raw(200, "}{not:valid json{{")

        elif path == "/scenario":
            scenario = getattr(self.server, "scenario", "ok")
            self._handle_scenario(scenario)

        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        self._send_json(200, {"echo": body.decode("utf-8", errors="replace")})

    # Scenario handler -------------------------------------------------------

    def _handle_scenario(self, scenario: str) -> None:
        if scenario == "ok":
            self._send_json(200, {"status": "ok", "scenario": scenario})
        elif scenario == "error":
            self._send_json(500, {"error": "scenario error"})
        elif scenario == "slow":
            time.sleep(0.3)
            self._send_json(200, {"status": "ok", "scenario": scenario})
        elif scenario == "timeout":
            time.sleep(10)
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(200, {"status": "ok", "scenario": scenario})

    # Helpers ----------------------------------------------------------------

    def _query_param(self, name: str, default: str = "") -> str:
        if "?" not in self.path:
            return default
        qs = self.path.split("?", 1)[1]
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k == name:
                    return v
        return default

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, code: int, text: str) -> None:
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_mock_server(port: int = 0) -> tuple[http.server.HTTPServer, int, str]:
    """
    Start the mock chaos HTTP server on *port* (0 = OS-assigned free port).

    Returns (server, actual_port, base_url).
    """
    if port == 0:
        port = _find_free_port()

    server = http.server.HTTPServer(("127.0.0.1", port), MockChaosHandler)
    server._lock = threading.Lock()          # type: ignore[attr-defined]
    server.request_count = 0                 # type: ignore[attr-defined]
    server.fail_count = 2                    # type: ignore[attr-defined]
    server.scenario = "ok"                   # type: ignore[attr-defined]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Resilience Test Runner
# ---------------------------------------------------------------------------

class ResilienceTestRunner:
    """
    High-level runner that exercises a callable under various chaos conditions
    and records metrics.
    """

    def __init__(
        self,
        circuit_breaker: CircuitBreaker | None = None,
        fault_injector: FaultInjector | None = None,
        metrics: ResilienceMetrics | None = None,
    ) -> None:
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.fault_injector = fault_injector or FaultInjector()
        self.metrics = metrics or ResilienceMetrics()

    def run(
        self,
        fn: Callable,
        *args,
        use_circuit_breaker: bool = True,
        use_fault_injector: bool = True,
        **kwargs,
    ) -> Any:
        """
        Execute *fn* through the configured circuit breaker and fault injector,
        recording metrics.
        """
        wrapped = (
            self.fault_injector.wrap(fn) if use_fault_injector else fn
        )

        t0 = time.monotonic()
        try:
            if use_circuit_breaker:
                result = self.circuit_breaker.call(wrapped, *args, **kwargs)
            else:
                result = wrapped(*args, **kwargs)
            latency = (time.monotonic() - t0) * 1000
            self.metrics.record_success(latency)
            return result
        except CircuitOpenError:
            self.metrics.record_open()
            if self.circuit_breaker.fallback is not None:
                return self.circuit_breaker.fallback
            raise
        except Exception:
            latency = (time.monotonic() - t0) * 1000
            self.metrics.record_failure(latency)
            raise

    def run_scenario(
        self,
        fn: Callable,
        *args,
        n: int = 10,
        use_circuit_breaker: bool = True,
        use_fault_injector: bool = True,
        ignore_errors: bool = True,
        **kwargs,
    ) -> list[Any]:
        """
        Run *fn* *n* times and return the list of (result_or_exception) values.
        """
        results = []
        for _ in range(n):
            try:
                r = self.run(
                    fn,
                    *args,
                    use_circuit_breaker=use_circuit_breaker,
                    use_fault_injector=use_fault_injector,
                    **kwargs,
                )
                results.append(r)
            except Exception as exc:
                if ignore_errors:
                    results.append(exc)
                else:
                    raise
        return results


# ---------------------------------------------------------------------------
# HTTP helper (used in tests)
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """Return (status_code, body_text) for a GET request."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def http_get_json(url: str, timeout: float = 5.0) -> tuple[int, Any]:
    code, body = http_get(url, timeout=timeout)
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, body


# ---------------------------------------------------------------------------
# Graceful Degradation helpers
# ---------------------------------------------------------------------------

class FallbackRegistry:
    """Maps service names to fallback callables or values."""

    def __init__(self) -> None:
        self._registry: dict[str, Any] = {}

    def register(self, name: str, fallback: Any) -> None:
        self._registry[name] = fallback

    def get(self, name: str, default: Any = None) -> Any:
        return self._registry.get(name, default)

    def call_with_fallback(
        self, name: str, primary: Callable, *args, **kwargs
    ) -> Any:
        try:
            return primary(*args, **kwargs)
        except Exception:
            fb = self._registry.get(name)
            if fb is None:
                raise
            return fb() if callable(fb) else fb


# ---------------------------------------------------------------------------
# TEETH: a FROZEN timeline of circuit-breaker call outcomes -> the EXACT
# (state, was_rejected) a CORRECT breaker MUST produce after each step.
#
# The chaos harness only has teeth if it CATCHES the two defects a real
# resilience engineer ships most often:
#
#   * a threshold OFF-BY-ONE — the breaker trips one failure too late
#     (``> threshold`` instead of ``>= threshold``), so the Nth consecutive
#     failure that should open the circuit leaves it CLOSED and the very next
#     call (which should be fast-rejected) is instead let through; and
#   * a "never rejects while OPEN" bug — the OPEN state is recorded but the
#     guard that fast-fails is dropped, so every call is served straight to the
#     failing dependency, defeating the entire breaker.
#
# An impl is a callable ``run(timeline, failure_threshold, open_duration)``
# returning a tuple of per-step ``(state_name, was_rejected)`` observations,
# where ``timeline`` is a frozen sequence of string events:
#
#   "S"  -> a call whose underlying fn SUCCEEDS
#   "F"  -> a call whose underlying fn FAILS (raises)
#   "W"  -> WAIT: advance the injected step-clock past ``open_duration`` so the
#           breaker is eligible to transition OPEN -> HALF_OPEN on the next call
#           (an explicit cooldown marker; produces NO observation)
#
# DETERMINISM: the real ``CircuitBreaker`` keys its OPEN->HALF_OPEN transition
# off ``time.monotonic()``. prove() drives an equivalent breaker through an
# INJECTED integer step-clock instead — no real clock, no sleep, no threads, no
# RNG, no socket. ``"W"`` is the only thing that advances time, and it advances
# it by exactly one ``open_duration`` so transitions are fully scripted.
#
# prove() judges each impl against the corpus's FROZEN LITERAL observation
# tuples (hand-computed from the state-machine contract, NEVER read back from
# the oracle at runtime), so the check is non-circular. prove(impl) is True iff
# any observation diverges from the frozen literal — i.e. the planted breaker
# bug is caught.
# ---------------------------------------------------------------------------


def _simulate_breaker(
    timeline: tuple[str, ...],
    failure_threshold: int,
    open_duration: int,
    *,
    trip_late: bool = False,
    serve_while_open: bool = False,
) -> tuple[tuple[str, bool], ...]:
    """Deterministic reference circuit-breaker over a scripted timeline.

    Pure: an injected integer step-clock (advanced only by ``"W"``) replaces
    ``time.monotonic()``; no real time, threads, RNG, or I/O. Returns one
    ``(state_after, was_rejected)`` observation per call event ("S"/"F").

    The two flags select the planted bugs so the oracle and both buggy twins
    share one code path (only the defect differs):

    * ``trip_late``       — open at ``failures > threshold`` (off-by-one late)
                            instead of ``failures >= threshold``;
    * ``serve_while_open``— skip the OPEN fast-reject guard, serving the call.
    """
    state = CircuitState.CLOSED
    failure_count = 0
    success_count = 0
    success_threshold = 1
    opened_at: int | None = None
    now = 0

    observations: list[tuple[str, bool]] = []

    for event in timeline:
        if event == "W":
            # Cooldown marker: advance the injected clock one full open_duration.
            now += open_duration
            continue

        # OPEN -> HALF_OPEN once the cooldown has elapsed (clock-driven).
        if (
            state == CircuitState.OPEN
            and opened_at is not None
            and (now - opened_at) >= open_duration
        ):
            state = CircuitState.HALF_OPEN
            success_count = 0

        # Fast-reject guard while OPEN.
        if state == CircuitState.OPEN and not serve_while_open:
            observations.append((state.value, True))
            continue

        succeeded = event == "S"

        if state == CircuitState.HALF_OPEN:
            if succeeded:
                success_count += 1
                if success_count >= success_threshold:
                    state = CircuitState.CLOSED
                    failure_count = 0
                    success_count = 0
                    opened_at = None
            else:
                # A failed probe re-opens immediately.
                state = CircuitState.OPEN
                opened_at = now
                failure_count = 0
                success_count = 0
            observations.append((state.value, False))
            continue

        # CLOSED path (also reached when serve_while_open masks an OPEN state).
        if succeeded:
            failure_count = 0
        else:
            failure_count += 1
            tripped = (
                failure_count > failure_threshold
                if trip_late
                else failure_count >= failure_threshold
            )
            if tripped:
                state = CircuitState.OPEN
                opened_at = now
                failure_count = 0
                success_count = 0
        observations.append((state.value, False))

    return tuple(observations)


@dataclass(frozen=True)
class BreakerCase:
    """One frozen breaker timeline with literal, hand-computed observations."""
    name: str
    timeline: tuple[str, ...]
    failure_threshold: int
    open_duration: int
    expected: tuple[tuple[str, bool], ...]  # (state, was_rejected) per call event
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND at least one
# planted mutant gets each one wrong. Every ``expected`` tuple is hand-computed
# from the state-machine contract — constants, never derived at runtime.
#
# Legend per observation: (state_after_this_call, was_this_call_rejected).
BREAKER_CORPUS: tuple[BreakerCase, ...] = (
    # threshold=3: the 3rd consecutive failure trips the breaker OPEN. The
    # discriminating case for the off-by-one: 'trips_one_late' leaves the 3rd
    # failure CLOSED (and would only open on a 4th).
    BreakerCase(
        "trip_on_exact_threshold",
        ("F", "F", "F"),
        3,
        5,
        (("CLOSED", False), ("CLOSED", False), ("OPEN", False)),
        "exactly failure_threshold consecutive failures must open the circuit",
    ),
    # After tripping OPEN on the 3rd failure, the 4th call (no cooldown) must be
    # fast-REJECTED. This is the discriminating case for 'serves_while_open':
    # the correct breaker rejects (True); the bug serves it (CLOSED, False).
    BreakerCase(
        "reject_while_open",
        ("F", "F", "F", "F"),
        3,
        5,
        (
            ("CLOSED", False),
            ("CLOSED", False),
            ("OPEN", False),
            ("OPEN", True),   # fast-fail: rejected without calling fn
        ),
        "while OPEN and before cooldown, calls are rejected (not served)",
    ),
    # Full happy-path recovery: trip OPEN, WAIT past cooldown -> HALF_OPEN probe
    # succeeds -> CLOSED. Exercises the cooldown marker and the close path.
    BreakerCase(
        "recover_via_half_open",
        ("F", "F", "W", "S", "S"),
        2,
        5,
        (
            ("CLOSED", False),   # 1st failure
            ("OPEN", False),     # 2nd failure trips (threshold=2)
            # "W" advances the clock; no observation
            ("CLOSED", False),   # HALF_OPEN probe succeeds -> CLOSED
            ("CLOSED", False),   # back to normal CLOSED operation
        ),
        "OPEN -> (cooldown) -> HALF_OPEN -> CLOSED on a successful probe",
    ),
    # HALF_OPEN probe FAILS -> straight back to OPEN, and the next call (no new
    # cooldown) is rejected again. Exercises the half-open->open edge.
    BreakerCase(
        "half_open_probe_fails_reopens",
        ("F", "F", "W", "F", "S"),
        2,
        5,
        (
            ("CLOSED", False),   # 1st failure
            ("OPEN", False),     # 2nd failure trips
            # "W" advances the clock; no observation
            ("OPEN", False),     # HALF_OPEN probe fails -> re-OPEN
            ("OPEN", True),      # still OPEN, no cooldown -> rejected
        ),
        "a failed HALF_OPEN probe re-opens the circuit immediately",
    ),
    # Decoy: a success resets the consecutive-failure counter, so two failures
    # split by a success never reach threshold=3. Neither bug can trip or reject
    # here — guards against teeth that fire on coincidence.
    BreakerCase(
        "success_resets_failure_run",
        ("F", "F", "S", "F", "F"),
        3,
        5,
        (
            ("CLOSED", False),
            ("CLOSED", False),
            ("CLOSED", False),   # success resets the run
            ("CLOSED", False),
            ("CLOSED", False),   # only 2 consecutive again -> still CLOSED
        ),
        "decoy: an interleaved success prevents reaching the threshold",
    ),
)


# --- ORACLE: the correct breaker transition function -------------------------

def oracle_run(
    timeline: tuple[str, ...],
    failure_threshold: int,
    open_duration: int,
) -> tuple[tuple[str, bool], ...]:
    """Correct circuit-breaker observations over a scripted timeline."""
    return _simulate_breaker(timeline, failure_threshold, open_duration)


# --- Planted buggy twins (each models a real resilience defect) --------------

def trips_one_late(
    timeline: tuple[str, ...],
    failure_threshold: int,
    open_duration: int,
) -> tuple[tuple[str, bool], ...]:
    """BUG: opens at ``failure_threshold + 1`` consecutive failures.

    Models the classic ``if failures > threshold`` off-by-one (should be
    ``>=``): the Nth failure that ought to open the circuit leaves it CLOSED, so
    one extra call is sent to the failing dependency before the breaker engages.
    """
    return _simulate_breaker(
        timeline, failure_threshold, open_duration, trip_late=True
    )


def serves_while_open(
    timeline: tuple[str, ...],
    failure_threshold: int,
    open_duration: int,
) -> tuple[tuple[str, bool], ...]:
    """BUG: does not fast-reject while OPEN.

    Models a dropped guard: the state is correctly recorded as OPEN, but the
    fast-fail check is missing, so calls are served straight through to the
    failing dependency while open — defeating the breaker's whole purpose.
    """
    return _simulate_breaker(
        timeline, failure_threshold, open_duration, serve_while_open=True
    )


def prove(
    impl: Callable[
        [tuple[str, ...], int, int], tuple[tuple[str, bool], ...]
    ],
) -> bool:
    """True iff ``impl`` produces the WRONG observations for any frozen corpus
    case (i.e. the breaker bug is caught): the per-step ``(state, was_rejected)``
    tuple diverges from the hand-computed literal, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    BREAKER_CORPUS, never read from the oracle; integer arithmetic and an
    injected step-clock only, no RNG/clock/threads/network/filesystem. An impl
    that raises on a corpus case counts as caught.
    """
    for case in BREAKER_CORPUS:
        try:
            got = impl(case.timeline, case.failure_threshold, case.open_duration)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if tuple(got) != case.expected:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_run"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_run,
    mutants=(
        Mutant("trips_one_late", trips_one_late,
               "opens at failure_threshold+1 (a > vs >= off-by-one) -> the Nth "
               "failure that should trip the breaker leaves it CLOSED and one "
               "extra call reaches the failing dependency"),
        Mutant("serves_while_open", serves_while_open,
               "drops the OPEN fast-reject guard -> every call is served to the "
               "failing dependency while the circuit is OPEN, defeating it"),
    ),
    corpus_size=len(BREAKER_CORPUS),
    kind="oracle_swap",
    notes="a circuit breaker must trip OPEN after EXACTLY failure_threshold "
          "consecutive failures and fast-reject calls while OPEN, then recover "
          "OPEN -> HALF_OPEN -> CLOSED via a successful probe after cooldown",
)


def list_scenarios() -> list[str]:
    """Names of the frozen breaker-timeline corpus cases (the teeth scenarios)."""
    return [c.name for c in BREAKER_CORPUS]


# ---------------------------------------------------------------------------
# Self-test — fails loud, reports findings.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Assert the breaker oracle reproduces every frozen observation literal and
    the universal swap-check passes (oracle clean, every planted mutant caught).
    Pure + deterministic: no socket, clock, thread, or RNG is touched."""
    report = Report("core/chaos")

    # 1. The correct oracle reproduces every frozen observation tuple exactly.
    for case in BREAKER_CORPUS:
        got = oracle_run(case.timeline, case.failure_threshold, case.open_duration)
        report.add(
            f"breaker:{case.name}",
            [list(o) for o in case.expected],
            [list(o) for o in got],
            detail=case.note,
        )

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chaos / resilience harness: circuit-breaker transition oracle"
    )
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--demo", action="store_true",
                        help="run the mock HTTP server smoke demo")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0

    if args.demo:
        return _run_demo()

    return _run_self_test(as_json=args.json)


def _run_demo() -> int:
    """Manual smoke test against the in-process mock chaos server.

    The mock HTTP server is bound here under ``main`` ONLY — never at import
    time and never inside ``prove``/the teeth path.
    """
    print("Starting mock chaos server...")
    server, port, base_url = start_mock_server()
    print(f"Server running on {base_url}")

    cb = CircuitBreaker(failure_threshold=3, open_duration=2.0)
    metrics = ResilienceMetrics()
    runner = ResilienceTestRunner(circuit_breaker=cb, metrics=metrics)

    # Healthy calls
    for _ in range(3):
        try:
            code, data = runner.run(http_get_json, f"{base_url}/ok")
            print(f"  ok: {data}")
        except Exception as exc:
            print(f"  error: {exc}")

    print(f"\nMetrics: {metrics.summary()}")
    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
