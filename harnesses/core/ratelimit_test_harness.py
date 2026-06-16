"""
Rate Limiting / Throttling Test Harness (Harness 28 of 36)

Pure stdlib, zero external dependencies.
Provides injectable FakeClock, four rate-limiting algorithms,
per-key buckets, stats, reporting, and a mock HTTP server.
"""

from __future__ import annotations

import dataclasses
import http.server
import json
import math
import threading
import time
import urllib.request
from collections import deque

# ---------------------------------------------------------------------------
# FakeClock
# ---------------------------------------------------------------------------

class FakeClock:
    """Injectable clock for deterministic testing."""

    def __init__(self, start: float = 0.0):
        self._time = start

    def now(self) -> float:
        """Return current fake time in seconds."""
        return self._time

    def advance(self, seconds: float) -> None:
        """Advance the clock by *seconds*."""
        if seconds < 0:
            raise ValueError("Cannot advance clock backwards")
        self._time += seconds


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RateLimitDecision:
    """Result of a rate-limit check."""
    allowed: bool
    remaining: int          # tokens/slots remaining after this decision
    retry_after: float      # seconds until a denied request would be allowed


@dataclasses.dataclass
class LimiterStats:
    """Aggregate statistics for a limiter."""
    requests: int = 0
    allowed: int = 0
    denied: int = 0
    current_tokens: float = 0.0


@dataclasses.dataclass
class RateLimitReport:
    """Aggregate report across multiple limiters or a test run."""
    total_requests: int = 0
    total_allowed: int = 0
    total_denied: int = 0
    algorithm: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Token Bucket
# ---------------------------------------------------------------------------

class TokenBucket:
    """
    Classic token-bucket rate limiter.

    capacity    – maximum burst size (tokens).
    refill_rate – tokens added per second.
    clock       – injectable FakeClock (or real clock if None).
    """

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        clock: FakeClock | None = None,
    ):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._clock = clock
        self._tokens: float = float(capacity)
        self._last_refill: float = self._now()
        self._lock = threading.Lock()
        self._stats = LimiterStats(current_tokens=self._tokens)

    # ------------------------------------------------------------------
    def _now(self) -> float:
        return self._clock.now() if self._clock else time.monotonic()

    def _refill(self) -> None:
        now = self._now()
        elapsed = now - self._last_refill
        if elapsed > 0:
            gained = elapsed * self.refill_rate
            self._tokens = min(self.capacity, self._tokens + gained)
            self._last_refill = now

    def allow(self, n: int = 1) -> RateLimitDecision:
        with self._lock:
            self._refill()
            self._stats.requests += 1
            if self._tokens >= n:
                self._tokens -= n
                self._stats.allowed += 1
                self._stats.current_tokens = self._tokens
                return RateLimitDecision(
                    allowed=True,
                    remaining=int(self._tokens),
                    retry_after=0.0,
                )
            else:
                self._stats.denied += 1
                self._stats.current_tokens = self._tokens
                deficit = n - self._tokens
                retry_after = deficit / self.refill_rate
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                )

    def stats(self) -> LimiterStats:
        with self._lock:
            self._refill()
            return dataclasses.replace(
                self._stats, current_tokens=self._tokens
            )


# ---------------------------------------------------------------------------
# Leaky Bucket
# ---------------------------------------------------------------------------

class LeakyBucket:
    """
    Leaky-bucket rate limiter.

    Requests fill the bucket; it drains at drain_rate tokens/sec.
    If the bucket is full, new requests are denied.
    """

    def __init__(
        self,
        capacity: float,
        drain_rate: float,
        clock: FakeClock | None = None,
    ):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if drain_rate <= 0:
            raise ValueError("drain_rate must be positive")
        self.capacity = capacity
        self.drain_rate = drain_rate
        self._clock = clock
        self._level: float = 0.0          # current water level
        self._last_drain: float = self._now()
        self._lock = threading.Lock()
        self._stats = LimiterStats()

    def _now(self) -> float:
        return self._clock.now() if self._clock else time.monotonic()

    def _drain(self) -> None:
        now = self._now()
        elapsed = now - self._last_drain
        if elapsed > 0:
            drained = elapsed * self.drain_rate
            self._level = max(0.0, self._level - drained)
            self._last_drain = now

    def allow(self, n: int = 1) -> RateLimitDecision:
        with self._lock:
            self._drain()
            self._stats.requests += 1
            if self._level + n <= self.capacity:
                self._level += n
                self._stats.allowed += 1
                remaining = int(self.capacity - self._level)
                self._stats.current_tokens = self.capacity - self._level
                return RateLimitDecision(
                    allowed=True,
                    remaining=remaining,
                    retry_after=0.0,
                )
            else:
                self._stats.denied += 1
                self._stats.current_tokens = self.capacity - self._level
                overflow = (self._level + n) - self.capacity
                retry_after = overflow / self.drain_rate
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                )

    def stats(self) -> LimiterStats:
        with self._lock:
            self._drain()
            return dataclasses.replace(
                self._stats,
                current_tokens=self.capacity - self._level,
            )


# ---------------------------------------------------------------------------
# Fixed Window
# ---------------------------------------------------------------------------

class FixedWindow:
    """
    Fixed-window rate limiter.

    max_requests allowed per window_seconds window.
    Known weakness: at the boundary between two windows, up to 2×
    max_requests can be admitted within a short period.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: FakeClock | None = None,
    ):
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._window_start: float = self._now()
        self._count: int = 0
        self._lock = threading.Lock()
        self._stats = LimiterStats()

    def _now(self) -> float:
        return self._clock.now() if self._clock else time.monotonic()

    def _maybe_reset(self) -> None:
        now = self._now()
        if now - self._window_start >= self.window_seconds:
            self._window_start = now
            self._count = 0

    def allow(self, n: int = 1) -> RateLimitDecision:
        with self._lock:
            self._maybe_reset()
            self._stats.requests += 1
            if self._count + n <= self.max_requests:
                self._count += n
                self._stats.allowed += 1
                remaining = self.max_requests - self._count
                self._stats.current_tokens = float(remaining)
                return RateLimitDecision(
                    allowed=True,
                    remaining=remaining,
                    retry_after=0.0,
                )
            else:
                self._stats.denied += 1
                self._stats.current_tokens = float(
                    self.max_requests - self._count
                )
                now = self._now()
                retry_after = self.window_seconds - (now - self._window_start)
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after=max(0.0, retry_after),
                )

    def stats(self) -> LimiterStats:
        with self._lock:
            self._maybe_reset()
            return dataclasses.replace(
                self._stats,
                current_tokens=float(self.max_requests - self._count),
            )


# ---------------------------------------------------------------------------
# Sliding Window
# ---------------------------------------------------------------------------

class SlidingWindow:
    """
    Sliding-window rate limiter (log-based).

    Keeps a deque of timestamps; requests older than window_seconds are
    expired before each check.  Prevents the fixed-window boundary burst.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: FakeClock | None = None,
    ):
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._timestamps: deque = deque()
        self._lock = threading.Lock()
        self._stats = LimiterStats()

    def _now(self) -> float:
        return self._clock.now() if self._clock else time.monotonic()

    def _expire(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def allow(self, n: int = 1) -> RateLimitDecision:
        with self._lock:
            now = self._now()
            self._expire(now)
            self._stats.requests += 1
            current_count = len(self._timestamps)
            if current_count + n <= self.max_requests:
                for _ in range(n):
                    self._timestamps.append(now)
                self._stats.allowed += 1
                remaining = self.max_requests - len(self._timestamps)
                self._stats.current_tokens = float(remaining)
                return RateLimitDecision(
                    allowed=True,
                    remaining=remaining,
                    retry_after=0.0,
                )
            else:
                self._stats.denied += 1
                self._stats.current_tokens = float(
                    self.max_requests - current_count
                )
                # Oldest timestamp tells us when a slot will free up
                if self._timestamps:
                    retry_after = (
                        self._timestamps[0] + self.window_seconds - now
                    )
                    retry_after = max(0.0, retry_after)
                else:
                    retry_after = 0.0
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                )

    def stats(self) -> LimiterStats:
        with self._lock:
            now = self._now()
            self._expire(now)
            return dataclasses.replace(
                self._stats,
                current_tokens=float(
                    self.max_requests - len(self._timestamps)
                ),
            )


# ---------------------------------------------------------------------------
# Per-Key Token Buckets
# ---------------------------------------------------------------------------

class PerKeyTokenBuckets:
    """
    Per-API-key independent TokenBuckets.

    Each unique key gets its own TokenBucket created on first access.
    """

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        clock: FakeClock | None = None,
    ):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                self.capacity, self.refill_rate, self._clock
            )
        return self._buckets[key]

    def allow(self, key: str, n: int = 1) -> RateLimitDecision:
        with self._lock:
            bucket = self._get_or_create(key)
        return bucket.allow(n)

    def stats(self, key: str) -> LimiterStats | None:
        with self._lock:
            bucket = self._buckets.get(key)
        if bucket is None:
            return None
        return bucket.stats()

    def keys(self):
        with self._lock:
            return list(self._buckets.keys())


# ---------------------------------------------------------------------------
# Mock HTTP Server (429 + Retry-After)
# ---------------------------------------------------------------------------

class MockRateLimitHandler(http.server.BaseHTTPRequestHandler):
    """
    Minimal HTTP handler that enforces a TokenBucket.

    Returns 200 when allowed, 429 with a Retry-After header when denied.
    The limiter instance is stored on the server object as `server.limiter`.
    """

    def log_message(self, fmt, *args):  # suppress default stderr logging
        pass

    def do_GET(self):
        decision: RateLimitDecision = self.server.limiter.allow()
        if decision.allowed:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps({"status": "ok", "remaining": decision.remaining})
            self.wfile.write(body.encode())
        else:
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", str(math.ceil(decision.retry_after)))
            self.end_headers()
            body = json.dumps(
                {
                    "status": "rate_limited",
                    "retry_after": decision.retry_after,
                }
            )
            self.wfile.write(body.encode())

    def do_POST(self):
        self.do_GET()


class RateLimitServer:
    """Thin wrapper that starts/stops the HTTP server in a daemon thread."""

    def __init__(self, limiter, host: str = "127.0.0.1", port: int = 0):
        self._limiter = limiter
        self._host = host
        self._port = port
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Start server; returns the bound port."""
        self._server = http.server.HTTPServer(
            (self._host, self._port), MockRateLimitHandler
        )
        self._server.limiter = self._limiter
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self._port

    def stop(self):
        if self._server:
            server = self._server
            server.shutdown()
            server.server_close()
            if self._thread:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def make_report(algorithm: str, stats: LimiterStats, notes: str = "") -> RateLimitReport:
    """Build a RateLimitReport from a LimiterStats object."""
    return RateLimitReport(
        total_requests=stats.requests,
        total_allowed=stats.allowed,
        total_denied=stats.denied,
        algorithm=algorithm,
        notes=notes,
    )


def http_get(url: str, timeout: float = 5.0):
    """Return (status_code, headers_dict, body_dict) for a GET request."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read())
            headers = dict(resp.headers)
            return resp.status, headers, body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read())
        headers = dict(exc.headers)
        return exc.code, headers, body
