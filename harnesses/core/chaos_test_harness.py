"""
Chaos / Resilience Test Harness (Harness 7 of 36)

Tests system resilience under failure conditions using a built-in mock HTTP server.
Pure stdlib, zero external dependencies.
"""

import http.server
import json
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


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
        self._opened_at: Optional[float] = None
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
        except Exception as exc:
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
        self._corrupt_fn: Optional[Callable[[Any], Any]] = None
        self._timeout_seconds: float = 60.0
        self._enabled: bool = False

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def inject_latency(self, ms: float) -> "FaultInjector":
        self._fault = FaultType.LATENCY
        self._latency_ms = ms
        self._enabled = True
        return self

    def inject_error(self, message: str = "Injected fault error") -> "FaultInjector":
        self._fault = FaultType.ERROR
        self._error_message = message
        self._enabled = True
        return self

    def inject_timeout(self, seconds: float = 60.0) -> "FaultInjector":
        self._fault = FaultType.TIMEOUT
        self._timeout_seconds = seconds
        self._enabled = True
        return self

    def inject_corruption(
        self, corrupt_fn: Optional[Callable[[Any], Any]] = None
    ) -> "FaultInjector":
        self._fault = FaultType.CORRUPT
        self._corrupt_fn = corrupt_fn or (lambda v: str(v)[::-1])
        self._enabled = True
        return self

    def disable(self) -> "FaultInjector":
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
    last_exc: Optional[Exception] = None
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
        self._latency_samples: List[float] = []
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

    def summary(self) -> Dict[str, Any]:
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


def start_mock_server(port: int = 0) -> Tuple[http.server.HTTPServer, int, str]:
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
        circuit_breaker: Optional[CircuitBreaker] = None,
        fault_injector: Optional[FaultInjector] = None,
        metrics: Optional[ResilienceMetrics] = None,
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
            latency = (time.monotonic() - t0) * 1000
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
    ) -> List[Any]:
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

def http_get(url: str, timeout: float = 5.0) -> Tuple[int, str]:
    """Return (status_code, body_text) for a GET request."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def http_get_json(url: str, timeout: float = 5.0) -> Tuple[int, Any]:
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
        self._registry: Dict[str, Any] = {}

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
# Demo / manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
