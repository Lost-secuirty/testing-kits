"""
Caching Correctness Test Harness (Harness 27 of 36)

Tests stale/wrong data silent-failure surface.
Mock HTTP server on dynamic port (default 19130).
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import socket

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Clock abstraction
# ---------------------------------------------------------------------------

class RealClock:
    """Default clock using real time."""

    def now(self) -> float:
        return time.monotonic()


class FakeClock:
    """Injectable fake clock for deterministic TTL tests (no real sleeps)."""

    def __init__(self, start: float = 0.0) -> None:
        self._time = start

    def now(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds

    def set(self, t: float) -> None:
        self._time = t


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CacheEntry:
    """A single cache entry."""
    value: Any
    expires_at: float | None  # None means no expiry
    created_at: float


@dataclasses.dataclass
class CacheStats:
    """Aggregate cache statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total


# ---------------------------------------------------------------------------
# Cache (correct implementation)
# ---------------------------------------------------------------------------

class Cache:
    """
    Thread-safe LRU cache with per-entry TTL.

    - injectable clock (default: RealClock)
    - max_size=0 means unlimited
    - LRU recency updated on both get and set
    """

    def __init__(
        self,
        max_size: int = 0,
        default_ttl: float | None = None,
        clock: Any | None = None,
    ) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._clock = clock if clock is not None else RealClock()
        self._store: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.expires_at is None:
            return False
        return self._clock.now() >= entry.expires_at

    def _evict_lru(self) -> None:
        """Remove the least-recently-used entry."""
        if self._store:
            self._store.popitem(last=False)
            self.stats.evictions += 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            if self._is_expired(entry):
                del self._store[key]
                self.stats.misses += 1
                return None
            # Move to end (most-recently-used)
            self._store.move_to_end(key)
            self.stats.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            effective_ttl = ttl if ttl is not None else self._default_ttl
            now = self._clock.now()
            expires_at = (now + effective_ttl) if effective_ttl is not None else None
            entry = CacheEntry(value=value, expires_at=expires_at, created_at=now)

            if key in self._store:
                # Update existing entry and move to end
                self._store[key] = entry
                self._store.move_to_end(key)
            else:
                self._store[key] = entry
                self._store.move_to_end(key)
                # Evict if over capacity
                if self._max_size > 0:
                    while len(self._store) > self._max_size:
                        self._evict_lru()

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            return not self._is_expired(entry)


# ---------------------------------------------------------------------------
# BuggyCache — skips invalidation on write (stale-after-write)
# ---------------------------------------------------------------------------

class BuggyCache:
    """
    Intentionally broken cache: set() does NOT overwrite existing entries.
    This means stale data is returned after a write — proves harness catches it.
    """

    def __init__(
        self,
        max_size: int = 0,
        default_ttl: float | None = None,
        clock: Any | None = None,
    ) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._clock = clock if clock is not None else RealClock()
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.expires_at is None:
            return False
        return self._clock.now() >= entry.expires_at

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            if self._is_expired(entry):
                del self._store[key]
                self.stats.misses += 1
                return None
            self.stats.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            # BUG: skip write if key already exists — stale-after-write
            if key in self._store:
                return
            effective_ttl = ttl if ttl is not None else self._default_ttl
            now = self._clock.now()
            expires_at = (now + effective_ttl) if effective_ttl is not None else None
            self._store[key] = CacheEntry(value=value, expires_at=expires_at, created_at=now)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# StaleTTLCache — ignores expiry on read (serves stale data past its TTL)
# ---------------------------------------------------------------------------

class StaleTTLCache:
    """
    Intentionally broken cache: get() never checks expiry, so an entry whose TTL
    has elapsed is still returned as a live hit. Models the common production bug
    where a cache layer stores an expiry timestamp but forgets to enforce it on
    read — serving stale data indefinitely.
    """

    def __init__(
        self,
        max_size: int = 0,
        default_ttl: float | None = None,
        clock: Any | None = None,
    ) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._clock = clock if clock is not None else RealClock()
        self._store: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            # BUG: no expiry check — expired entries are served as live hits.
            self._store.move_to_end(key)
            self.stats.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            effective_ttl = ttl if ttl is not None else self._default_ttl
            now = self._clock.now()
            expires_at = (now + effective_ttl) if effective_ttl is not None else None
            entry = CacheEntry(value=value, expires_at=expires_at, created_at=now)
            self._store[key] = entry
            self._store.move_to_end(key)
            if self._max_size > 0:
                while len(self._store) > self._max_size:
                    self._store.popitem(last=False)
                    self.stats.evictions += 1

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# NoEvictCache — never evicts under capacity pressure (unbounded growth)
# ---------------------------------------------------------------------------

class NoEvictCache:
    """
    Intentionally broken cache: respects TTL and overwrites correctly, but ignores
    max_size — it never evicts the least-recently-used entry. Models an LRU bound
    that is silently not enforced, so a 'bounded' cache grows without limit and
    keeps entries that should have been evicted.
    """

    def __init__(
        self,
        max_size: int = 0,
        default_ttl: float | None = None,
        clock: Any | None = None,
    ) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._clock = clock if clock is not None else RealClock()
        self._store: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.expires_at is None:
            return False
        return self._clock.now() >= entry.expires_at

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            if self._is_expired(entry):
                del self._store[key]
                self.stats.misses += 1
                return None
            self._store.move_to_end(key)
            self.stats.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            effective_ttl = ttl if ttl is not None else self._default_ttl
            now = self._clock.now()
            expires_at = (now + effective_ttl) if effective_ttl is not None else None
            entry = CacheEntry(value=value, expires_at=expires_at, created_at=now)
            self._store[key] = entry
            self._store.move_to_end(key)
            # BUG: max_size is never enforced — no eviction ever happens.

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# NamespacedCache — wraps a Cache with a key prefix
# ---------------------------------------------------------------------------

class NamespacedCache:
    """Wraps a Cache instance, prefixing all keys with a namespace."""

    def __init__(self, cache: Cache, namespace: str) -> None:
        self._cache = cache
        self._namespace = namespace

    def _ns_key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> Any | None:
        return self._cache.get(self._ns_key(key))

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self._cache.set(self._ns_key(key), value, ttl=ttl)

    def delete(self, key: str) -> bool:
        return self._cache.delete(self._ns_key(key))

    def clear(self) -> None:
        # Clear only keys belonging to this namespace
        with self._cache._lock:
            to_delete = [k for k in self._cache._store if k.startswith(f"{self._namespace}:")]
            for k in to_delete:
                del self._cache._store[k]


# ---------------------------------------------------------------------------
# SingleFlightCache — thundering-herd prevention
# ---------------------------------------------------------------------------

class NaiveCache:
    """Simple cache with no thundering-herd protection (for comparison)."""

    def __init__(self, clock: Any | None = None) -> None:
        self._store: dict[str, Any] = {}
        self._clock = clock if clock is not None else RealClock()
        self.loader_call_count = 0

    def get_or_load(self, key: str, loader: Callable[[], Any]) -> Any:
        if key in self._store:
            return self._store[key]
        self.loader_call_count += 1
        value = loader()
        self._store[key] = value
        return value

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


class SingleFlightCache:
    """
    Cache that prevents thundering herd on cache miss.

    On cache miss, uses a per-key Lock to ensure only one loader runs;
    all other concurrent requests for the same key wait and get the result.
    """

    def __init__(self, clock: Any | None = None) -> None:
        self._store: dict[str, Any] = {}
        self._clock = clock if clock is not None else RealClock()
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_results: dict[str, Any] = {}
        self._meta_lock = threading.Lock()
        self.loader_call_count = 0

    def get_or_load(self, key: str, loader: Callable[[], Any]) -> Any:
        # Fast path: already cached
        with self._meta_lock:
            if key in self._store:
                return self._store[key]

            # Check if someone else is already loading
            if key in self._inflight:
                event = self._inflight[key]
                # Release meta lock while waiting
        if 'event' in dir() and key not in self._store:
            # Re-check under lock
            with self._meta_lock:
                if key in self._store:
                    return self._store[key]
                event = self._inflight.get(key, None)

            if event is not None:
                event.wait()
                with self._meta_lock:
                    return self._store.get(key)

        # We are the first; set up the inflight event
        event = threading.Event()
        with self._meta_lock:
            if key in self._store:
                return self._store[key]
            if key in self._inflight:
                ev_wait = True
            else:
                self._inflight[key] = event
                ev_wait = False

        if ev_wait:
            self._inflight[key].wait()
            with self._meta_lock:
                return self._store.get(key)

        try:
            self.loader_call_count += 1
            value = loader()
            with self._meta_lock:
                self._store[key] = value
            return value
        finally:
            with self._meta_lock:
                self._inflight.pop(key, None)
            event.set()

    def invalidate(self, key: str) -> None:
        with self._meta_lock:
            self._store.pop(key, None)


# ---------------------------------------------------------------------------
# SingleFlightCache v2 — cleaner implementation
# ---------------------------------------------------------------------------

class SingleFlightCacheV2:
    """
    Cleaner single-flight implementation using per-key locks.
    Only one loader executes per key; waiters get the cached result.
    """

    def __init__(self, clock: Any | None = None) -> None:
        self._store: dict[str, Any] = {}
        self._clock = clock if clock is not None else RealClock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self.loader_call_count = 0

    def _get_key_lock(self, key: str) -> threading.Lock:
        with self._meta_lock:
            if key not in self._key_locks:
                self._key_locks[key] = threading.Lock()
            return self._key_locks[key]

    def get_or_load(self, key: str, loader: Callable[[], Any]) -> Any:
        # Fast path (no lock)
        if key in self._store:
            return self._store[key]

        key_lock = self._get_key_lock(key)
        with key_lock:
            # Check again under key lock
            if key in self._store:
                return self._store[key]
            self.loader_call_count += 1
            value = loader()
            with self._meta_lock:
                self._store[key] = value
            return value

    def invalidate(self, key: str) -> None:
        with self._meta_lock:
            self._store.pop(key, None)


# ---------------------------------------------------------------------------
# CacheReport
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""


@dataclasses.dataclass
class CacheReport:
    """Aggregates test results from cache correctness checks."""
    results: dataclasses.field(default_factory=list) = dataclasses.field(default_factory=list)

    def add(self, name: str, passed: bool, message: str = "") -> None:
        self.results.append(TestResult(name=name, passed=passed, message=message))

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        lines = [f"CacheReport: {self.passed}/{self.total} passed"]
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            line = f"  [{status}] {r.name}"
            if r.message:
                line += f": {r.message}"
            lines.append(line)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MockCacheHandler — HTTP server for cache testing
# ---------------------------------------------------------------------------

class MockCacheHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP handler that simulates a cache-backed data source.

    Routes:
      GET  /cache/<key>         → return cached value or 404
      POST /cache/<key>         → set value (body: JSON {"value": ..., "ttl": ...})
      DELETE /cache/<key>       → delete key
      GET  /stats               → return cache stats as JSON
      POST /clear               → clear all cache entries
    """

    # Shared cache instance (set by MockCacheServer)
    _cache: Cache | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        # Suppress default request logging
        pass

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def do_GET(self) -> None:
        if self.path == "/stats":
            stats = self._cache.stats if self._cache else None
            if stats:
                self._send_json(200, {
                    "hits": stats.hits,
                    "misses": stats.misses,
                    "evictions": stats.evictions,
                    "hit_ratio": stats.hit_ratio,
                })
            else:
                self._send_json(503, {"error": "no cache"})
            return

        if self.path.startswith("/cache/"):
            key = self.path[len("/cache/"):]
            value = self._cache.get(key) if self._cache else None
            if value is None:
                self._send_json(404, {"error": "not found"})
            else:
                self._send_json(200, {"key": key, "value": value})
            return

        self._send_json(404, {"error": "unknown route"})

    def do_POST(self) -> None:
        if self.path == "/clear":
            if self._cache:
                self._cache.clear()
            self._send_json(200, {"ok": True})
            return

        if self.path.startswith("/cache/"):
            key = self.path[len("/cache/"):]
            try:
                body = self._read_body()
                data = json.loads(body) if body else {}
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "invalid JSON"})
                return

            value = data.get("value")
            ttl = data.get("ttl")
            if self._cache:
                self._cache.set(key, value, ttl=ttl)
            self._send_json(200, {"ok": True, "key": key})
            return

        self._send_json(404, {"error": "unknown route"})

    def do_DELETE(self) -> None:
        if self.path.startswith("/cache/"):
            key = self.path[len("/cache/"):]
            deleted = self._cache.delete(key) if self._cache else False
            self._send_json(200, {"ok": deleted})
            return

        self._send_json(404, {"error": "unknown route"})


def _wait_until_accepting(host: str, port: int, timeout: float = 3.0) -> None:
    """Block until the listener accepts a connection, or timeout elapses.

    Closes the CI race where serve_forever() has not yet bound/listened by the
    time MockCacheServer.start() returns and a client connects.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError(
        f"mock server at {host}:{port} did not start accepting within {timeout}s"
    )


class MockCacheServer:
    """Context manager that starts a MockCacheHandler HTTP server on a dynamic port."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,  # 0 = OS chooses
        cache: Cache | None = None,
    ) -> None:
        self._host = host
        self._requested_port = port
        self._cache = cache if cache is not None else Cache()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def cache(self) -> Cache:
        return self._cache

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("Server not started")
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self.port}"

    def start(self) -> MockCacheServer:
        # Create a subclass with the cache bound
        cache_ref = self._cache

        class BoundHandler(MockCacheHandler):
            _cache = cache_ref

        self._server = HTTPServer((self._host, self._requested_port), BoundHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[0], self._server.server_address[1]
        _wait_until_accepting(host, port)
        return self

    def stop(self) -> None:
        server = self._server
        if server:
            server.shutdown()
            server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> MockCacheServer:
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Teeth: a FROZEN corpus of cache operations with pre-computed expected results.
#
# prove(impl) runs `impl` (a cache *factory*: class or callable returning a fresh
# cache that accepts a `clock=` kwarg) against this scripted corpus under a
# FakeClock and compares each observable get() to the expected value baked into
# the corpus. It is NON-CIRCULAR: expectations are literal constants, never read
# back from the oracle object. prove(impl) is True iff `impl` diverges from any
# expected outcome (i.e. the planted bug is caught).
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CacheOp:
    """One scripted cache operation against a single shared cache instance."""
    name: str
    op: str            # "set" | "get" | "delete" | "advance"
    key: str = ""
    value: Any = None
    ttl: float | None = None
    advance: float = 0.0
    # For "get" ops only: the value the CORRECT cache must return (a literal
    # constant, computed by hand from the cache contract — not from the oracle).
    expected: Any = None


# A cache with max_size=2 and a FakeClock starting at t=0. The script exercises
# overwrite-invalidation, TTL expiry, and LRU eviction — each chosen so that at
# least one planted mutant returns the WRONG value on a "get".
CACHE_CORPUS: tuple[CacheOp, ...] = (
    # --- overwrite must invalidate (catches BuggyCache stale-after-write) ---
    CacheOp("set_initial", "set", key="k", value="v1"),
    CacheOp("get_initial", "get", key="k", expected="v1"),
    CacheOp("overwrite", "set", key="k", value="v2"),
    CacheOp("get_after_overwrite", "get", key="k", expected="v2"),
    # --- TTL must expire on read (catches StaleTTLCache serve-expired) ---
    CacheOp("set_ttl", "set", key="t", value="alive", ttl=10.0),
    CacheOp("get_ttl_before", "get", key="t", expected="alive"),
    CacheOp("advance_past_ttl", "advance", advance=10.0),
    CacheOp("get_ttl_after", "get", key="t", expected=None),
    # --- LRU must evict the least-recently-used (catches NoEvictCache) ---
    # max_size=2. After the TTL key expired and was read (removed), the store is
    # empty. Insert a, b (full), touch a, insert c -> b is the LRU and must go.
    CacheOp("lru_set_a", "set", key="a", value=1),
    CacheOp("lru_set_b", "set", key="b", value=2),
    CacheOp("lru_touch_a", "get", key="a", expected=1),
    CacheOp("lru_set_c", "set", key="c", value=3),
    CacheOp("lru_b_evicted", "get", key="b", expected=None),
    CacheOp("lru_a_kept", "get", key="a", expected=1),
    CacheOp("lru_c_present", "get", key="c", expected=3),
)


def _run_corpus(factory: Callable[..., Any]) -> list[tuple[str, Any, Any]]:
    """Drive `factory()` through CACHE_CORPUS; return (name, expected, actual)
    for every 'get' op. Pure + deterministic: a FakeClock supplies all time."""
    clock = FakeClock(start=0.0)
    cache = factory(max_size=2, clock=clock)
    observed: list[tuple[str, Any, Any]] = []
    for step in CACHE_CORPUS:
        if step.op == "set":
            cache.set(step.key, step.value, ttl=step.ttl)
        elif step.op == "get":
            observed.append((step.name, step.expected, cache.get(step.key)))
        elif step.op == "delete":
            cache.delete(step.key)
        elif step.op == "advance":
            clock.advance(step.advance)
        else:  # pragma: no cover - guards against a malformed corpus
            raise ValueError(f"unknown op: {step.op!r}")
    return observed


def prove(impl: Callable[..., Any]) -> bool:
    """True iff `impl` (a cache factory) diverges from the frozen corpus's
    pre-computed expected results on any get (i.e. the bug is CAUGHT).

    Deterministic and side-effect-free: no real clock, network, or filesystem;
    the only RNG is none. Judges against literal expectations, never the oracle.
    """
    try:
        observed = _run_corpus(impl)
    except Exception:  # noqa: BLE001 — raising while driving the corpus counts as caught
        return True
    return any(actual != expected for _name, expected, actual in observed)


TEETH = Teeth(
    prove=prove,
    oracle=Cache,
    mutants=(
        Mutant("stale_after_write", BuggyCache,
               "set() skips overwrite of an existing key -> serves stale data after a write"),
        Mutant("serves_expired", StaleTTLCache,
               "get() never checks expiry -> serves data past its TTL"),
        Mutant("no_lru_eviction", NoEvictCache,
               "max_size never enforced -> LRU bound silently unbounded"),
    ),
    corpus_size=sum(1 for op in CACHE_CORPUS if op.op == "get"),
    kind="oracle_swap",
    notes="overwrite must invalidate, TTL must expire on read, and the LRU bound must evict",
)


# ---------------------------------------------------------------------------
# Harness runner (standalone)
# ---------------------------------------------------------------------------

def run_harness() -> CacheReport:
    """Run the built-in correctness checks and return a CacheReport."""
    report = CacheReport()
    clock = FakeClock(start=1000.0)

    # --- Basic get/set ---
    c = Cache(clock=clock)
    c.set("k", "v")
    report.add("basic_set_get", c.get("k") == "v")

    # --- Miss returns None ---
    report.add("miss_returns_none", c.get("missing") is None)

    # --- Invalidation on write ---
    c.set("k", "v2")
    report.add("invalidation_on_write", c.get("k") == "v2")

    # --- BuggyCache stale-after-write ---
    bc = BuggyCache(clock=clock)
    bc.set("k", "original")
    bc.set("k", "updated")
    report.add("buggy_cache_stale_after_write", bc.get("k") == "original",
               "BuggyCache intentionally returns stale data")

    # --- TTL just-before expiry ---
    c2 = Cache(clock=clock)
    clock.set(1000.0)
    c2.set("ttl_key", "alive", ttl=10.0)
    clock.advance(9.999)
    report.add("ttl_just_before_expiry", c2.get("ttl_key") == "alive")

    # --- TTL just-after expiry ---
    clock.advance(0.002)
    report.add("ttl_just_after_expiry", c2.get("ttl_key") is None)

    # --- LRU eviction ---
    clock.set(2000.0)
    lru = Cache(max_size=2, clock=clock)
    lru.set("a", 1)
    lru.set("b", 2)
    lru.get("a")  # promote a
    lru.set("c", 3)  # should evict b
    report.add("lru_evicts_least_recently_used",
               lru.get("b") is None and lru.get("a") == 1 and lru.get("c") == 3)

    # --- Delete ---
    c3 = Cache(clock=clock)
    c3.set("x", 42)
    c3.delete("x")
    report.add("delete_removes_key", c3.get("x") is None)

    # --- Clear ---
    c3.set("a", 1)
    c3.set("b", 2)
    c3.clear()
    report.add("clear_empties_cache", c3.get("a") is None and c3.get("b") is None)

    # --- Stats ---
    clock.set(3000.0)
    cs = Cache(clock=clock)
    cs.set("k", "v")
    cs.get("k")
    cs.get("missing")
    report.add("stats_hits", cs.stats.hits == 1)
    report.add("stats_misses", cs.stats.misses == 1)
    report.add("stats_hit_ratio", cs.stats.hit_ratio == 0.5)

    # --- Namespace isolation ---
    base = Cache(clock=clock)
    ns1 = NamespacedCache(base, "ns1")
    ns2 = NamespacedCache(base, "ns2")
    ns1.set("key", "val1")
    ns2.set("key", "val2")
    report.add("namespace_isolation",
               ns1.get("key") == "val1" and ns2.get("key") == "val2")

    # --- SingleFlightCache loader called once ---
    sfc = SingleFlightCacheV2(clock=clock)
    results = []
    barrier = threading.Barrier(5)

    def slow_loader():
        time.sleep(0.05)
        return "computed"

    def worker():
        barrier.wait()
        v = sfc.get_or_load("shared", slow_loader)
        results.append(v)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    report.add("single_flight_all_get_result", all(r == "computed" for r in results))
    report.add("single_flight_loader_called_once", sfc.loader_call_count == 1)

    return report


def list_scenarios() -> list[str]:
    """Names of the frozen corpus operations (the teeth scenarios)."""
    return [op.name for op in CACHE_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/cache")

    # 1. The legacy correctness harness must still pass end to end.
    legacy = run_harness()
    report.record("run_harness_all_pass", legacy.all_passed,
                  detail=f"{legacy.passed}/{legacy.total} legacy checks passed")

    # 2. The correct oracle (Cache) must match every expected corpus outcome.
    for name, expected, actual in _run_corpus(Cache):
        report.add(f"oracle:{name}", expected, actual)

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Caching correctness controls")
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
    raise SystemExit(main())
