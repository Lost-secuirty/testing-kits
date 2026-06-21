"""
Rate Limiting / Throttling Test Harness (Harness 28 of 36)

Pure stdlib, zero external dependencies.
Provides injectable FakeClock, four rate-limiting algorithms,
per-key buckets, stats, reporting, and a mock HTTP server.

TEETH: a FROZEN timeline of (advance-clock, allow(n)) operations driven through
a TokenBucket on the injectable FakeClock, with the EXACT (allowed, remaining)
each correct admission must yield baked in as literals. See ``TIMELINE_CORPUS``
and ``prove`` below.

Self-test:
  python harnesses/core/ratelimit_test_harness.py --self-test
  python harnesses/core/ratelimit_test_harness.py --json
  python harnesses/core/ratelimit_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import dataclasses
import http.server
import json
import math
import sys
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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


# ---------------------------------------------------------------------------
# TEETH: a FROZEN operation timeline -> the exact admission outcomes a CORRECT
# token-bucket limiter MUST produce.
#
# A rate-limit harness only has teeth if it CATCHES a limiter that admits one
# request too many at the empty boundary (an off-by-one on the admission test)
# or that lets the bucket overfill past its capacity after an idle period (a
# dropped ``min(capacity, ...)`` cap, which permits a double-burst). The
# contract every correct token bucket of (capacity C, refill R tok/sec) must
# hold over a deterministic timeline:
#
#   * a request for n tokens is ADMITTED iff the bucket currently holds >= n
#     tokens (strict: at exactly n-1 tokens it is DENIED) — admitting at n-1 is
#     the classic off-by-one;
#   * refill is CAPPED at C: after idling far longer than C/R seconds the bucket
#     holds exactly C tokens, never more — an uncapped refill lets a long idle
#     bankroll an oversized burst.
#
# An impl is a callable ``simulate(config, ops) -> tuple[(allowed, remaining)]``
# returning one (allowed, remaining-after) pair per ``("allow", n)`` op (clock
# advances produce no output). prove() judges each impl against the corpus's
# FROZEN LITERAL outcomes (hand-computed from the contract above, NEVER read
# back from the oracle at runtime), so the check is non-circular. prove(impl) is
# True iff any outcome diverges from the frozen literal — i.e. the limiter bug
# is caught.
#
# Pure + deterministic: the timeline drives an injectable FakeClock (no real
# clock/sleep), integer/float arithmetic only, no RNG, no threads, no network,
# no filesystem. The mock HTTP server is excluded — it lives under main() only.
# The two planted mutants model genuine real-world token-bucket defects:
#
#   * refill_off_by_one — admits when tokens >= n-1 (i.e. ``tokens + 1 >= n``)
#     instead of tokens >= n, so it lets ONE extra request through at the empty
#     boundary: the over-admission bug;
#   * uncapped_refill — drops the ``min(capacity, ...)`` clamp, so a long idle
#     overfills the bucket and a later burst exceeds capacity: the boundary /
#     double-burst bug.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BucketConfig:
    """Frozen token-bucket configuration for a timeline case."""
    capacity: int
    refill_rate: float


@dataclasses.dataclass(frozen=True)
class TimelineCase:
    """One frozen timeline case with literal, hand-computed admission outcomes.

    ``ops`` is a tuple of operations:
      * ``("advance", seconds)`` — move the FakeClock forward (no output);
      * ``("allow", n)`` — request ``n`` tokens (produces one outcome).
    ``expected`` is the tuple of ``(allowed, remaining)`` pairs a CORRECT bucket
    yields, one per ``("allow", n)`` op, in order. Every pair is a literal
    constant, never derived from the oracle at runtime.
    """
    name: str
    config: BucketConfig
    ops: tuple[tuple[str, float], ...]
    expected: tuple[tuple[bool, int], ...]
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND at least one
# planted mutant gets each one wrong. Each ``expected`` pair is hand-computed
# from the token-bucket contract (admit iff tokens >= n; refill capped at C).
# Capacity is 5 and refill 1 tok/sec throughout for an easy hand-trace.
_C5R1 = BucketConfig(capacity=5, refill_rate=1.0)

TIMELINE_CORPUS: tuple[TimelineCase, ...] = (
    # Drain to empty, then ask once more with no time elapsed. A correct bucket
    # DENIES the 6th (0 tokens left). refill_off_by_one wrongly ADMITS it
    # (tokens=0 >= n-1=0). uncapped_refill agrees with the oracle here (no idle
    # to overfill), so this case isolates the off-by-one.
    TimelineCase(
        "drain_then_deny",
        _C5R1,
        (("allow", 1), ("allow", 1), ("allow", 1), ("allow", 1), ("allow", 1),
         ("allow", 1)),
        ((True, 4), (True, 3), (True, 2), (True, 1), (True, 0), (False, 0)),
        "6th request at 0 tokens must be denied; off-by-one admits it",
    ),
    # Drain to exactly 2 tokens, then request 3 at once. Correct bucket DENIES
    # (2 < 3). refill_off_by_one wrongly ADMITS (tokens=2 >= n-1=2), a second,
    # independent witness of the off-by-one at a non-zero boundary.
    TimelineCase(
        "n3_at_2_tokens",
        _C5R1,
        (("allow", 1), ("allow", 1), ("allow", 1), ("allow", 3)),
        ((True, 4), (True, 3), (True, 2), (False, 0)),
        "request of 3 against 2 tokens must be denied; off-by-one admits it",
    ),
    # Drain fully, idle 100s (would mint 100 tokens uncapped), then burst 6.
    # Correct refill caps at capacity=5, so the 6th in the post-idle burst is
    # DENIED. uncapped_refill lets the idle overfill to 100 and admits all 6
    # (double-burst). refill_off_by_one ALSO trips here (it admits the 6th at
    # the empty boundary), so this case witnesses both mutants.
    TimelineCase(
        "idle_overfill_cap",
        _C5R1,
        (("allow", 5), ("advance", 100.0),
         ("allow", 1), ("allow", 1), ("allow", 1), ("allow", 1), ("allow", 1),
         ("allow", 1)),
        ((True, 0),
         (True, 4), (True, 3), (True, 2), (True, 1), (True, 0), (False, 0)),
        "refill capped at capacity after long idle; uncapped allows a 6th burst",
    ),
    # Partial refill below 1 token does not admit. Drain fully, advance 0.5s
    # (mints 0.5 tokens), request 1. Correct bucket DENIES (0.5 < 1). A second
    # witness that uncapped_refill is NOT what breaks this (it agrees here), and
    # that the boundary is strict.
    TimelineCase(
        "partial_refill_denies",
        _C5R1,
        (("allow", 5), ("advance", 0.5), ("allow", 1)),
        ((True, 0), (False, 0)),
        "0.5 refilled tokens cannot satisfy a request for 1",
    ),
    # Full refill after a 5s idle restores exactly capacity (not more). Drain
    # fully, idle 5s, then take 5 — all admitted, 6th denied. Catches an
    # uncapped refill that would admit a 6th, and confirms exact-cap behaviour.
    TimelineCase(
        "exact_cap_refill",
        _C5R1,
        (("allow", 5), ("advance", 5.0),
         ("allow", 1), ("allow", 1), ("allow", 1), ("allow", 1), ("allow", 1),
         ("allow", 1)),
        ((True, 0),
         (True, 4), (True, 3), (True, 2), (True, 1), (True, 0), (False, 0)),
        "5s idle refills to exactly capacity=5, not more",
    ),
    # Long idle then two bursts. Drain fully, idle 10s (caps at 5, would mint 10
    # uncapped), take 5 (admitted, empties), then ask 3 (denied — only 0 left).
    # A second, independent witness of the cap: uncapped admits BOTH the 5-burst
    # remaining (5 left) and the following 3, diverging on both allow steps.
    TimelineCase(
        "long_idle_then_burst",
        _C5R1,
        (("allow", 5), ("advance", 10.0), ("allow", 5), ("allow", 3)),
        ((True, 0), (True, 0), (False, 0)),
        "10s idle caps at capacity; uncapped bankrolls an oversized second burst",
    ),
)


# --- ORACLE: drive the harness's own correct TokenBucket on a FakeClock ------

def oracle_simulate(
    config: BucketConfig,
    ops: tuple[tuple[str, float], ...],
) -> tuple[tuple[bool, int], ...]:
    """Correct admission outcomes, delegating to the harness's own
    ``TokenBucket`` on an injectable ``FakeClock``. Returns one
    ``(allowed, remaining)`` pair per ``("allow", n)`` op, in order."""
    clock = FakeClock(0.0)
    bucket = TokenBucket(config.capacity, config.refill_rate, clock=clock)
    out: list[tuple[bool, int]] = []
    for kind, arg in ops:
        if kind == "advance":
            clock.advance(arg)
        elif kind == "allow":
            decision = bucket.allow(int(arg))
            out.append((decision.allowed, decision.remaining))
        else:  # pragma: no cover - corpus is frozen and well-formed
            raise ValueError(f"unknown op: {kind!r}")
    return tuple(out)


# --- Planted buggy twins (each models a real token-bucket defect) ------------

def _simulate_buggy(
    config: BucketConfig,
    ops: tuple[tuple[str, float], ...],
    *,
    off_by_one: bool = False,
    uncapped: bool = False,
) -> tuple[tuple[bool, int], ...]:
    """Standalone integer/float token-bucket simulation with optional planted
    defects. Pure and deterministic — a frozen timeline, no real clock."""
    capacity = float(config.capacity)
    rate = config.refill_rate
    tokens = capacity
    now = 0.0
    last_refill = 0.0
    out: list[tuple[bool, int]] = []
    for kind, arg in ops:
        if kind == "advance":
            now += arg
            continue
        n = int(arg)
        elapsed = now - last_refill
        if elapsed > 0:
            gained = elapsed * rate
            # BUG (uncapped): dropped the min(capacity, .) clamp on refill.
            tokens = tokens + gained if uncapped else min(capacity, tokens + gained)
            last_refill = now
        admit = (tokens + 1 >= n) if off_by_one else (tokens >= n)  # BUG: >= n-1
        if admit:
            tokens -= n
            out.append((True, int(tokens)))
        else:
            out.append((False, 0))
    return tuple(out)


def refill_off_by_one(
    config: BucketConfig,
    ops: tuple[tuple[str, float], ...],
) -> tuple[tuple[bool, int], ...]:
    """BUG: admits when ``tokens + 1 >= n`` (i.e. tokens >= n-1) instead of
    ``tokens >= n``, letting one extra request through at the empty boundary."""
    return _simulate_buggy(config, ops, off_by_one=True)


def uncapped_refill(
    config: BucketConfig,
    ops: tuple[tuple[str, float], ...],
) -> tuple[tuple[bool, int], ...]:
    """BUG: drops the ``min(capacity, ...)`` clamp on refill, so a long idle
    overfills the bucket and a later burst exceeds capacity (double-burst)."""
    return _simulate_buggy(config, ops, uncapped=True)


def prove(
    impl: Callable[[BucketConfig, tuple[tuple[str, float], ...]],
                   tuple[tuple[bool, int], ...]],
) -> bool:
    """True iff ``impl`` produces a WRONG admission outcome for any frozen
    corpus case (i.e. the limiter bug is caught): any ``(allowed, remaining)``
    pair diverges from the hand-computed literal, the count of outcomes differs,
    or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    ``TIMELINE_CORPUS`` (read via the module global so a corrupted literal is
    honoured), never read from the oracle; no RNG/clock/threads/network/
    filesystem. An impl that raises on a corpus case counts as caught.
    """
    for case in TIMELINE_CORPUS:
        try:
            outcomes = impl(case.config, case.ops)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if tuple(outcomes) != case.expected:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_simulate"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_simulate,
    mutants=(
        Mutant("refill_off_by_one", refill_off_by_one,
               "admits when tokens >= n-1 instead of tokens >= n -> lets one "
               "extra request through at the empty/boundary token count"),
        Mutant("uncapped_refill", uncapped_refill,
               "drops the min(capacity, .) refill clamp -> a long idle overfills "
               "the bucket and a later burst exceeds capacity (double-burst)"),
    ),
    corpus_size=len(TIMELINE_CORPUS),
    kind="oracle_swap",
    notes="a correct token bucket admits iff it holds >= n tokens (strict at the "
          "boundary) and caps refill at capacity; the mutants over-admit at the "
          "boundary or overfill after an idle.",
)


def list_scenarios() -> list[str]:
    """Names of the frozen timeline corpus cases (the teeth scenarios)."""
    return [c.name for c in TIMELINE_CORPUS]


def _run_self_test(as_json: bool = False) -> int:
    """Assert the teeth: the oracle reproduces every frozen admission literal,
    each planted limiter defect is caught, and the universal swap-check passes
    (oracle clean, every mutant caught)."""
    report = Report("core/ratelimit")

    # 1. The correct oracle reproduces every frozen admission literal exactly.
    for case in TIMELINE_CORPUS:
        outcomes = oracle_simulate(case.config, case.ops)
        report.add(f"oracle_timeline:{case.name}",
                   [list(p) for p in case.expected],
                   [list(p) for p in outcomes],
                   detail=case.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rate-limiting / throttling test harness (token-bucket teeth)")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
