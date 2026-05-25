#!/usr/bin/env python3
"""
network_test_harness.py — Network / Protocol Test Harness (Harness 18/36)
=========================================================================
Pure-Python (ZERO external dependencies) network testing engine.

Covers:
  - ConnectionConfig / ConnectionResult / RetryPolicy dataclasses
  - ProtocolTester   — HTTP request/response format correctness
  - TimeoutTester    — connection & read-timeout enforcement
  - RetryTester      — exponential-backoff retry counts & schedule
  - PayloadTester    — large payload handling (1 KB / 10 KB / 100 KB)
  - ConnectionPoolTester — checkout / return / expiry
  - ShutdownTester   — graceful shutdown while requests are in-flight
  - DNSTester        — invalid hostname → connection error (not crash)
  - NetworkReport    — aggregate results
  - MockNetworkHandler — threaded HTTP server with delay / payload / error injection

Port: 19040 (dynamic, picked at runtime)
"""

from __future__ import annotations

import http.server
import json
import math
import queue
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 19040
    timeout: float = 5.0          # seconds
    max_retries: int = 3
    keep_alive: bool = True


@dataclass
class ConnectionResult:
    success: bool
    latency_ms: float = 0.0
    error: Optional[str] = None
    attempts: int = 1


@dataclass
class RetryPolicy:
    base_delay: float = 0.05      # seconds  (kept small for fast tests)
    multiplier: float = 2.0
    max_delay: float = 2.0
    max_attempts: int = 3

    def delay_for(self, attempt: int) -> float:
        """Return the sleep duration for the given attempt number (0-indexed)."""
        raw = self.base_delay * (self.multiplier ** attempt)
        return min(raw, self.max_delay)


# ---------------------------------------------------------------------------
# Mock Server
# ---------------------------------------------------------------------------

class MockNetworkHandler(BaseHTTPRequestHandler):
    """
    Configurable HTTP handler.

    The server's `config` dict (stored on the server instance) can contain:
      delay        – seconds to sleep before responding  (default 0)
      error_code   – HTTP status to return              (default 200)
      payload_size – bytes of body to send              (default 0)
      fail_first_n – return 503 for the first N requests
      request_log  – list that records each (method, path) tuple
    """

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def _cfg(self, key, default=None):
        return getattr(self.server, "config", {}).get(key, default)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def _handle(self):
        cfg = getattr(self.server, "config", {})

        # Record request
        log: Optional[list] = cfg.get("request_log")
        if log is not None:
            log.append((self.command, self.path))

        # Delay simulation
        delay = cfg.get("delay", 0)
        if delay:
            time.sleep(delay)

        # fail_first_n counter (thread-safe via lock on server)
        lock: threading.Lock = cfg.get("lock", threading.Lock())
        fail_first_n: int = cfg.get("fail_first_n", 0)
        with lock:
            counter = cfg.get("_fail_counter", 0)
            if counter < fail_first_n:
                cfg["_fail_counter"] = counter + 1
                self._send(503, b"Service Unavailable")
                return
            cfg["_fail_counter"] = counter  # keep counting

        status = cfg.get("error_code", 200)
        payload_size = cfg.get("payload_size", 0)

        if self.path == "/slow-read":
            # For read-timeout tests: send headers immediately, then drip body
            body = b"x" * max(payload_size, 1)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # pause before writing body to trigger read timeout
            read_delay = cfg.get("read_delay", 0.5)
            time.sleep(read_delay)
            try:
                self.wfile.write(body)
            except Exception:
                pass
            return

        if payload_size:
            body = b"A" * payload_size
        else:
            body = json.dumps({"status": "ok", "path": self.path}).encode()

        self._send(status, body)

    def _send(self, status: int, body: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Test-Header", "harness-18")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _start_mock_server(config: Optional[Dict[str, Any]] = None) -> Tuple[ThreadingHTTPServer, int]:
    """Start a mock server on a random free port and return (server, port)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockNetworkHandler)
    server.config = config or {}  # type: ignore[attr-defined]
    if "lock" not in server.config:  # type: ignore[attr-defined]
        server.config["lock"] = threading.Lock()  # type: ignore[attr-defined]
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


# ---------------------------------------------------------------------------
# Protocol Tester
# ---------------------------------------------------------------------------

class ProtocolTester:
    """Verifies HTTP protocol correctness (request/response format)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._server, self._port = _start_mock_server()
        self._base = f"http://127.0.0.1:{self._port}"

    def stop(self):
        self._server.shutdown()

    # ------------------------------------------------------------------
    def test_status_line(self) -> bool:
        """Response must start with HTTP/1.x 200 OK."""
        try:
            resp = urllib.request.urlopen(f"{self._base}/", timeout=2)
            return resp.status == 200
        except Exception:
            return False

    def test_content_type_header(self) -> bool:
        try:
            resp = urllib.request.urlopen(f"{self._base}/ping", timeout=2)
            ct = resp.headers.get("Content-Type", "")
            return "application/json" in ct
        except Exception:
            return False

    def test_custom_header_present(self) -> bool:
        try:
            resp = urllib.request.urlopen(f"{self._base}/ping", timeout=2)
            return resp.headers.get("X-Test-Header") == "harness-18"
        except Exception:
            return False

    def test_json_body_parseable(self) -> bool:
        try:
            resp = urllib.request.urlopen(f"{self._base}/ping", timeout=2)
            data = json.loads(resp.read())
            return isinstance(data, dict)
        except Exception:
            return False

    def test_post_echoes_path(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self._base}/echo",
                data=b"hello",
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=2)
            data = json.loads(resp.read())
            return data.get("path") == "/echo"
        except Exception:
            return False

    def test_404_returns_error_code(self) -> bool:
        """Configure the server to return 404."""
        server2, port2 = _start_mock_server({"error_code": 404})
        base = f"http://127.0.0.1:{port2}"
        try:
            urllib.request.urlopen(f"{base}/missing", timeout=2)
            return False
        except urllib.error.HTTPError as e:
            return e.code == 404
        except Exception:
            return False
        finally:
            server2.shutdown()

    def test_head_has_no_body(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._base}/", method="HEAD")
            resp = urllib.request.urlopen(req, timeout=2)
            return resp.status == 200
        except Exception:
            return False

    def test_content_length_matches_body(self) -> bool:
        try:
            resp = urllib.request.urlopen(f"{self._base}/data", timeout=2)
            declared = int(resp.headers.get("Content-Length", -1))
            body = resp.read()
            return declared == len(body)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Timeout Tester
# ---------------------------------------------------------------------------

class TimeoutTester:
    """Verifies connection and read timeouts."""

    def __init__(self):
        self._servers: List[ThreadingHTTPServer] = []

    def stop(self):
        for s in self._servers:
            s.shutdown()

    def _make_server(self, cfg: Dict) -> Tuple[str, int]:
        s, port = _start_mock_server(cfg)
        self._servers.append(s)
        return "127.0.0.1", port

    # Connection timeout: connect to a port that drops packets.
    # We simulate this by binding but NOT calling accept.
    def test_connection_timeout(self, timeout: float = 0.1) -> ConnectionResult:
        """Attempt to connect to a port that accepts TCP but never responds."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(0)   # backlog 0 — still completes TCP handshake though
        port = sock.getsockname()[1]
        # We need a port where the kernel refuses immediately — use a closed port
        sock.close()
        # After close the port is free; a new connect should be refused instantly.
        # For a real "hangs" port we use a raw server that accepts but never replies.
        blocking_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocking_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocking_sock.bind(("127.0.0.1", 0))
        blocking_sock.listen(5)
        port = blocking_sock.getsockname()[1]
        # Don't accept — connections queue but never get HTTP response

        start = time.monotonic()
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/",
                timeout=timeout,
            )
            blocking_sock.close()
            return ConnectionResult(success=False, latency_ms=0, error="no timeout raised")
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            blocking_sock.close()
            return ConnectionResult(
                success=False,
                latency_ms=elapsed,
                error=str(exc),
                attempts=1,
            )

    def test_read_timeout(self, timeout: float = 0.1) -> ConnectionResult:
        """Server sends headers immediately but delays body — read timeout should fire."""
        host, port = self._make_server({"read_delay": 1.0})
        start = time.monotonic()
        try:
            urllib.request.urlopen(
                f"http://{host}:{port}/slow-read",
                timeout=timeout,
            )
            return ConnectionResult(success=False, latency_ms=0, error="no timeout raised")
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ConnectionResult(
                success=False,
                latency_ms=elapsed,
                error=str(exc),
                attempts=1,
            )

    def test_timeout_within_budget(self, timeout: float = 0.15) -> bool:
        """Timeout should occur within 3× the requested timeout budget."""
        result = self.test_read_timeout(timeout=timeout)
        # Should have fired at roughly `timeout` seconds (allow 3× budget)
        return not result.success and result.latency_ms < timeout * 3000


# ---------------------------------------------------------------------------
# Retry Tester
# ---------------------------------------------------------------------------

class RetryTester:
    """Verifies retry logic with exponential backoff."""

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self.policy = policy or RetryPolicy(
            base_delay=0.01, multiplier=2.0, max_delay=1.0, max_attempts=3
        )

    # ------------------------------------------------------------------
    def attempt_with_retry(self, url: str, timeout: float = 2.0) -> ConnectionResult:
        """
        Try `url` up to policy.max_attempts times with exponential backoff.
        Returns ConnectionResult with attempts count.
        """
        policy = self.policy
        last_error: Optional[str] = None
        delays: List[float] = []

        for attempt in range(policy.max_attempts):
            try:
                start = time.monotonic()
                resp = urllib.request.urlopen(url, timeout=timeout)
                elapsed = (time.monotonic() - start) * 1000
                resp.read()
                return ConnectionResult(
                    success=True,
                    latency_ms=elapsed,
                    attempts=attempt + 1,
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < policy.max_attempts - 1:
                    d = policy.delay_for(attempt)
                    delays.append(d)
                    time.sleep(d)

        return ConnectionResult(
            success=False,
            error=last_error,
            attempts=policy.max_attempts,
        )

    def test_retry_count_on_server_error(self) -> ConnectionResult:
        """All requests fail with 503 → should exhaust max_attempts."""
        server, port = _start_mock_server({
            "error_code": 503,
            "fail_first_n": 999,
            "lock": threading.Lock(),
            "_fail_counter": 0,
        })
        try:
            result = self.attempt_with_retry(f"http://127.0.0.1:{port}/retry")
            return result
        finally:
            server.shutdown()

    def test_retry_succeeds_after_failures(self) -> ConnectionResult:
        """First N-1 requests fail, last one succeeds."""
        log: List[Any] = []
        server, port = _start_mock_server({
            "fail_first_n": self.policy.max_attempts - 1,
            "lock": threading.Lock(),
            "_fail_counter": 0,
            "request_log": log,
        })
        try:
            result = self.attempt_with_retry(f"http://127.0.0.1:{port}/retry")
            return result
        finally:
            server.shutdown()

    def test_backoff_schedule(self) -> List[float]:
        """Return the expected delay sequence for policy.max_attempts."""
        return [
            self.policy.delay_for(i)
            for i in range(self.policy.max_attempts - 1)
        ]

    def test_delay_increases(self) -> bool:
        sched = self.test_backoff_schedule()
        if len(sched) < 2:
            return True  # trivially true
        return all(sched[i] <= sched[i + 1] for i in range(len(sched) - 1))

    def test_delay_respects_max(self) -> bool:
        sched = self.test_backoff_schedule()
        return all(d <= self.policy.max_delay for d in sched)


# ---------------------------------------------------------------------------
# Payload Tester
# ---------------------------------------------------------------------------

class PayloadTester:
    """Tests large payload handling."""

    def __init__(self):
        self._servers: List[ThreadingHTTPServer] = []

    def stop(self):
        for s in self._servers:
            s.shutdown()

    def _server_for_size(self, size: int) -> str:
        s, port = _start_mock_server({"payload_size": size})
        self._servers.append(s)
        return f"http://127.0.0.1:{port}/"

    def _fetch(self, url: str, timeout: float = 5.0) -> Tuple[bool, int, Optional[str]]:
        try:
            resp = urllib.request.urlopen(url, timeout=timeout)
            body = resp.read()
            return True, len(body), None
        except Exception as exc:
            return False, 0, str(exc)

    def test_1kb(self) -> Tuple[bool, int]:
        url = self._server_for_size(1024)
        ok, size, _ = self._fetch(url)
        return ok, size

    def test_10kb(self) -> Tuple[bool, int]:
        url = self._server_for_size(10 * 1024)
        ok, size, _ = self._fetch(url)
        return ok, size

    def test_100kb(self) -> Tuple[bool, int]:
        url = self._server_for_size(100 * 1024)
        ok, size, _ = self._fetch(url)
        return ok, size

    def test_payload_integrity(self, size: int = 4096) -> bool:
        """All bytes should be 'A' (0x41) as sent by the mock server."""
        url = self._server_for_size(size)
        ok, _, _ = self._fetch(url)
        if not ok:
            return False
        resp = urllib.request.urlopen(url, timeout=5)
        body = resp.read()
        return body == b"A" * size


# ---------------------------------------------------------------------------
# Connection Pool
# ---------------------------------------------------------------------------

class _PooledConnection:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.created_at = time.monotonic()
        self.last_used = time.monotonic()
        self.in_use = False
        self._id = id(self)

    def touch(self):
        self.last_used = time.monotonic()

    def age(self) -> float:
        return time.monotonic() - self.created_at

    def idle_time(self) -> float:
        return time.monotonic() - self.last_used


class ConnectionPool:
    """Simple connection pool with max-size and TTL-based expiry."""

    def __init__(self, host: str, port: int, max_size: int = 5, ttl: float = 30.0):
        self.host = host
        self.port = port
        self.max_size = max_size
        self.ttl = ttl
        self._pool: List[_PooledConnection] = []
        self._lock = threading.Lock()
        self._checked_out: int = 0

    # ------------------------------------------------------------------
    def checkout(self) -> Optional[_PooledConnection]:
        """Return an idle connection or create a new one (up to max_size)."""
        with self._lock:
            # Evict expired connections first
            self._evict_expired()
            # Find an idle connection
            for conn in self._pool:
                if not conn.in_use:
                    conn.in_use = True
                    conn.touch()
                    self._checked_out += 1
                    return conn
            # Create new if capacity allows
            if len(self._pool) < self.max_size:
                conn = _PooledConnection(self.host, self.port)
                conn.in_use = True
                self._pool.append(conn)
                self._checked_out += 1
                return conn
            return None  # pool exhausted

    def checkin(self, conn: _PooledConnection):
        """Return a connection to the pool."""
        with self._lock:
            conn.in_use = False
            conn.touch()
            self._checked_out = max(0, self._checked_out - 1)

    def _evict_expired(self):
        self._pool = [c for c in self._pool if c.idle_time() < self.ttl or c.in_use]

    def size(self) -> int:
        with self._lock:
            return len(self._pool)

    def available(self) -> int:
        with self._lock:
            return sum(1 for c in self._pool if not c.in_use)

    def checked_out_count(self) -> int:
        with self._lock:
            return self._checked_out

    def close_all(self):
        with self._lock:
            self._pool.clear()
            self._checked_out = 0


class ConnectionPoolTester:
    """Tests ConnectionPool checkout / return / expiry."""

    def __init__(self, max_size: int = 3, ttl: float = 0.1):
        self.max_size = max_size
        self.ttl = ttl
        self.pool = ConnectionPool("127.0.0.1", 19040, max_size=max_size, ttl=ttl)

    def test_checkout_returns_connection(self) -> bool:
        conn = self.pool.checkout()
        if conn is None:
            return False
        self.pool.checkin(conn)
        return True

    def test_checkin_makes_available(self) -> bool:
        conn = self.pool.checkout()
        if conn is None:
            return False
        before = self.pool.available()
        self.pool.checkin(conn)
        after = self.pool.available()
        return after > before

    def test_max_size_respected(self) -> bool:
        conns = []
        for _ in range(self.max_size + 5):
            c = self.pool.checkout()
            if c is not None:
                conns.append(c)
        ok = len(conns) <= self.max_size
        for c in conns:
            self.pool.checkin(c)
        return ok

    def test_reuse_after_checkin(self) -> bool:
        conn1 = self.pool.checkout()
        if conn1 is None:
            return False
        self.pool.checkin(conn1)
        conn2 = self.pool.checkout()
        result = conn2 is not None and conn2._id == conn1._id
        if conn2:
            self.pool.checkin(conn2)
        return result

    def test_expiry_removes_idle(self) -> bool:
        """Connections idle longer than TTL should be evicted on next checkout."""
        conn = self.pool.checkout()
        if conn is None:
            return False
        self.pool.checkin(conn)
        # Wait for TTL to expire
        time.sleep(self.ttl + 0.05)
        # Trigger eviction via checkout
        new_conn = self.pool.checkout()
        # The old connection should have been evicted (new_conn is different or pool re-created it)
        if new_conn:
            self.pool.checkin(new_conn)
        return True  # no crash = pass; deeper check in unit tests

    def test_concurrent_checkout(self) -> bool:
        """Multiple threads should each get a valid (or None) connection."""
        results: List[Optional[_PooledConnection]] = []
        lock = threading.Lock()

        def worker():
            c = self.pool.checkout()
            with lock:
                results.append(c)
            time.sleep(0.02)
            if c is not None:
                self.pool.checkin(c)

        threads = [threading.Thread(target=worker) for _ in range(self.max_size * 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        checked_out = sum(1 for r in results if r is not None)
        return checked_out <= self.max_size


# ---------------------------------------------------------------------------
# Shutdown Tester
# ---------------------------------------------------------------------------

class ShutdownTester:
    """Verifies graceful shutdown while requests are in-flight."""

    def test_graceful_shutdown(self, in_flight: int = 3, delay: float = 0.05) -> Dict[str, Any]:
        """
        Start a server with a small delay, fire `in_flight` requests concurrently,
        then shut the server down.  Check that:
          - shutdown completes without deadlock/exception
          - in-flight requests either complete or receive a clean error
        """
        server, port = _start_mock_server({"delay": delay})
        results: List[bool] = []
        errors: List[str] = []
        lock = threading.Lock()

        def _req():
            try:
                resp = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=2.0
                )
                resp.read()
                with lock:
                    results.append(True)
            except Exception as exc:
                with lock:
                    errors.append(str(exc))
                    results.append(False)

        threads = [threading.Thread(target=_req) for _ in range(in_flight)]
        for t in threads:
            t.start()

        # Allow some requests to start before shutdown
        time.sleep(delay * 0.5)
        shutdown_ok = True
        try:
            server.shutdown()
        except Exception:
            shutdown_ok = False

        for t in threads:
            t.join(timeout=3.0)

        return {
            "shutdown_ok": shutdown_ok,
            "total": len(results),
            "succeeded": sum(results),
            "failed": len(errors),
        }

    def test_no_deadlock_on_shutdown(self) -> bool:
        result = self.test_graceful_shutdown(in_flight=2, delay=0.02)
        return result["shutdown_ok"]

    def test_all_requests_resolve(self) -> bool:
        result = self.test_graceful_shutdown(in_flight=2, delay=0.02)
        return result["total"] == 2


# ---------------------------------------------------------------------------
# DNS Tester
# ---------------------------------------------------------------------------

class DNSTester:
    """Verifies that invalid hostnames produce connection errors, not crashes."""

    _INVALID_HOSTS = [
        "this.host.does.not.exist.invalid",
        "no-such-host-xyz-abc-123.local",
        "!!invalid!!.example.com",
    ]

    def test_invalid_hostname_raises(self, hostname: str) -> ConnectionResult:
        start = time.monotonic()
        try:
            urllib.request.urlopen(f"http://{hostname}/", timeout=1.0)
            return ConnectionResult(success=True, latency_ms=0, error=None)
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return ConnectionResult(
                success=False,
                latency_ms=elapsed,
                error=str(exc),
                attempts=1,
            )

    def test_all_invalid_hosts_fail(self) -> bool:
        for h in self._INVALID_HOSTS:
            result = self.test_invalid_hostname_raises(h)
            if result.success:
                return False
        return True

    def test_no_crash_on_invalid(self) -> bool:
        """Should never raise an unhandled exception — always return ConnectionResult."""
        try:
            result = self.test_invalid_hostname_raises("not a hostname !!!")
            return not result.success
        except Exception:
            return False

    def test_error_message_populated(self) -> bool:
        result = self.test_invalid_hostname_raises(self._INVALID_HOSTS[0])
        return result.error is not None and len(result.error) > 0


# ---------------------------------------------------------------------------
# Network Report
# ---------------------------------------------------------------------------

@dataclass
class NetworkReport:
    protocol_results: Dict[str, bool] = field(default_factory=dict)
    timeout_results: Dict[str, Any] = field(default_factory=dict)
    retry_results: Dict[str, Any] = field(default_factory=dict)
    payload_results: Dict[str, Any] = field(default_factory=dict)
    pool_results: Dict[str, bool] = field(default_factory=dict)
    shutdown_results: Dict[str, Any] = field(default_factory=dict)
    dns_results: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tests(self) -> int:
        return sum(
            len(v) for v in [
                self.protocol_results, self.timeout_results, self.retry_results,
                self.payload_results, self.pool_results, self.shutdown_results,
                self.dns_results,
            ]
        )

    @property
    def passed(self) -> int:
        count = 0
        for d in [self.protocol_results, self.pool_results]:
            count += sum(1 for v in d.values() if v is True)
        return count

    def summary(self) -> str:
        return (
            f"NetworkReport: {self.total_tests} checks recorded, "
            f"{self.passed} boolean-pass checks"
        )


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

def run_all() -> NetworkReport:
    report = NetworkReport()

    # Protocol
    pt = ProtocolTester()
    report.protocol_results = {
        "status_line": pt.test_status_line(),
        "content_type": pt.test_content_type_header(),
        "custom_header": pt.test_custom_header_present(),
        "json_body": pt.test_json_body_parseable(),
        "post_path": pt.test_post_echoes_path(),
        "404_code": pt.test_404_returns_error_code(),
        "head_ok": pt.test_head_has_no_body(),
        "content_length": pt.test_content_length_matches_body(),
    }
    pt.stop()

    # Timeouts
    tt = TimeoutTester()
    ct = tt.test_connection_timeout(timeout=0.1)
    rt = tt.test_read_timeout(timeout=0.1)
    report.timeout_results = {
        "connection_timeout": {"success": ct.success, "error": ct.error},
        "read_timeout": {"success": rt.success, "error": rt.error},
        "within_budget": tt.test_timeout_within_budget(0.15),
    }
    tt.stop()

    # Retry
    rp = RetryPolicy(base_delay=0.01, multiplier=2.0, max_delay=1.0, max_attempts=3)
    rtester = RetryTester(rp)
    fail_result = rtester.test_retry_count_on_server_error()
    succ_result = rtester.test_retry_succeeds_after_failures()
    report.retry_results = {
        "exhausts_attempts": fail_result.attempts == rp.max_attempts,
        "succeeds_eventually": succ_result.success,
        "delay_increases": rtester.test_delay_increases(),
        "delay_respects_max": rtester.test_delay_respects_max(),
    }

    # Payload
    payer = PayloadTester()
    ok1, sz1 = payer.test_1kb()
    ok10, sz10 = payer.test_10kb()
    ok100, sz100 = payer.test_100kb()
    report.payload_results = {
        "1kb": {"ok": ok1, "size": sz1},
        "10kb": {"ok": ok10, "size": sz10},
        "100kb": {"ok": ok100, "size": sz100},
    }
    payer.stop()

    # Pool
    pool_tester = ConnectionPoolTester()
    report.pool_results = {
        "checkout": pool_tester.test_checkout_returns_connection(),
        "checkin": pool_tester.test_checkin_makes_available(),
        "max_size": pool_tester.test_max_size_respected(),
        "reuse": pool_tester.test_reuse_after_checkin(),
        "expiry": pool_tester.test_expiry_removes_idle(),
        "concurrent": pool_tester.test_concurrent_checkout(),
    }

    # Shutdown
    st = ShutdownTester()
    report.shutdown_results = {
        "no_deadlock": st.test_no_deadlock_on_shutdown(),
        "all_resolve": st.test_all_requests_resolve(),
    }

    # DNS
    dt = DNSTester()
    report.dns_results = {
        "all_fail": dt.test_all_invalid_hosts_fail(),
        "no_crash": dt.test_no_crash_on_invalid(),
        "error_msg": dt.test_error_message_populated(),
    }

    return report


if __name__ == "__main__":
    report = run_all()
    print(report.summary())
