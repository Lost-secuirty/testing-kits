"""
Idempotency / Retry-Safety Test Harness (Harness 21 of 36)

Tests retry-safety and idempotency patterns including:
- Atomic check-and-set dedup store
- Key-based deduplication
- Retry convergence
- Concurrent deduplication
- In-progress state handling
- TTL expiration
- Response persistence validation
- Safe HTTP method classification
- Mock HTTP server with idempotency key enforcement
"""

import http.server
import json

# Make the shared teeth contract importable whether run as a module or a script.
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Enums and Data Types
# ---------------------------------------------------------------------------

class IdempotencyState(Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class IdempotencyEntry:
    key: str
    state: IdempotencyState
    response: Any | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    ttl: float | None = None  # seconds; None = never expires

    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl


# ---------------------------------------------------------------------------
# IdempotencyStore: thread-safe atomic check-and-set dedup store
# ---------------------------------------------------------------------------

class IdempotencyStore:
    """
    Atomic check-and-set dedup store.
    Stores: key -> {state: PENDING/COMPLETED/FAILED, response, created_at, ttl}
    Thread-safe via threading.Lock.
    """

    def __init__(self):
        self._store: dict[str, IdempotencyEntry] = {}
        self._lock = threading.Lock()

    def start(self, key: str, ttl: float | None = None) -> bool:
        """
        Atomically check if key exists; if not, insert with PENDING state.
        Returns True if this call "won" the slot (first to register).
        Returns False if key already exists (duplicate request).
        """
        with self._lock:
            if key in self._store and not self._store[key].is_expired():
                return False
            # Either key doesn't exist or it has expired
            self._store[key] = IdempotencyEntry(
                key=key,
                state=IdempotencyState.PENDING,
                ttl=ttl
            )
            return True

    def complete(self, key: str, response: Any) -> None:
        """Mark key as COMPLETED with the given response."""
        with self._lock:
            if key not in self._store:
                raise KeyError(f"Key '{key}' not found in store")
            entry = self._store[key]
            entry.state = IdempotencyState.COMPLETED
            entry.response = response

    def fail(self, key: str, error: str) -> None:
        """Mark key as FAILED with the given error message."""
        with self._lock:
            if key not in self._store:
                raise KeyError(f"Key '{key}' not found in store")
            entry = self._store[key]
            entry.state = IdempotencyState.FAILED
            entry.error = error

    def get(self, key: str) -> IdempotencyEntry | None:
        """Return the entry for key, or None if not found."""
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and entry.is_expired():
                return None
            return entry

    def is_expired(self, key: str) -> bool:
        """Return True if the entry exists and has exceeded its TTL."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            return entry.is_expired()

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        with self._lock:
            expired_keys = [
                k for k, v in self._store.items() if v.is_expired()
            ]
            for k in expired_keys:
                del self._store[k]
            return len(expired_keys)

    def size(self) -> int:
        """Return number of entries currently in the store."""
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._store.clear()


# ---------------------------------------------------------------------------
# StateOnlyStore: buggy store that saves state but NOT the response
# ---------------------------------------------------------------------------

class StateOnlyStore:
    """
    Buggy idempotency store that saves state transitions but NOT the response.
    This demonstrates the failure mode where replayed requests can't return
    the same response as the original request.
    """

    def __init__(self):
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                return False
            self._store[key] = {
                "state": IdempotencyState.PENDING,
                # BUG: response is deliberately NOT stored
            }
            return True

    def complete(self, key: str, response: Any) -> None:
        """Mark complete but intentionally does NOT save the response."""
        with self._lock:
            if key not in self._store:
                raise KeyError(f"Key '{key}' not found")
            # BUG: response is not stored
            self._store[key]["state"] = IdempotencyState.COMPLETED

    def get_response(self, key: str) -> Any | None:
        """Returns None because response was never saved — the bug."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            # BUG: can never return original response
            return entry.get("response", None)

    def get_state(self, key: str) -> IdempotencyState | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            return entry.get("state")


# ---------------------------------------------------------------------------
# KeyDedupTester
# ---------------------------------------------------------------------------

class KeyDedupTester:
    """
    Verifies that a second call with the same idempotency key returns the
    cached response without re-executing the side effect.
    """

    def __init__(self, store: IdempotencyStore | None = None):
        self.store = store or IdempotencyStore()
        self.side_effect_count = 0
        self._lock = threading.Lock()

    def _execute_side_effect(self, payload: Any) -> dict[str, Any]:
        """Simulates a non-idempotent side effect (e.g., charging a card)."""
        with self._lock:
            self.side_effect_count += 1
        return {
            "result": "success",
            "payload": payload,
            "execution_count": self.side_effect_count
        }

    def process_request(self, idempotency_key: str, payload: Any) -> dict[str, Any]:
        """
        Process a request with deduplication.
        If key already exists and is COMPLETED, return cached response.
        If key doesn't exist, execute side effect and cache response.
        """
        existing = self.store.get(idempotency_key)
        if existing is not None:
            if existing.state == IdempotencyState.COMPLETED:
                return {"cached": True, "response": existing.response}
            elif existing.state == IdempotencyState.PENDING:
                return {"cached": False, "status": "PENDING", "response": None}
            elif existing.state == IdempotencyState.FAILED:
                return {"cached": True, "status": "FAILED", "error": existing.error}

        # Attempt to claim the slot
        won = self.store.start(idempotency_key)
        if not won:
            # Another thread claimed it between our get() and start()
            existing = self.store.get(idempotency_key)
            if existing and existing.state == IdempotencyState.COMPLETED:
                return {"cached": True, "response": existing.response}
            return {"cached": False, "status": "PENDING", "response": None}

        try:
            response = self._execute_side_effect(payload)
            self.store.complete(idempotency_key, response)
            return {"cached": False, "response": response}
        except Exception as e:
            self.store.fail(idempotency_key, str(e))
            raise

    def reset(self):
        self.side_effect_count = 0
        self.store.clear()


# ---------------------------------------------------------------------------
# RetryConvergenceTester
# ---------------------------------------------------------------------------

class RetryConvergenceTester:
    """
    Verifies that replaying the same request N times returns identical
    cached response each time (idempotent replay convergence).
    """

    def __init__(self, store: IdempotencyStore | None = None):
        self.store = store or IdempotencyStore()
        self.execution_count = 0

    def execute_once(self, key: str, payload: Any) -> Any:
        """Execute an operation, returning cached result on replay."""
        existing = self.store.get(key)
        if existing and existing.state == IdempotencyState.COMPLETED:
            return existing.response

        if not self.store.start(key):
            existing = self.store.get(key)
            if existing and existing.state == IdempotencyState.COMPLETED:
                return existing.response
            return None

        self.execution_count += 1
        result = {"id": key, "data": payload, "attempt": self.execution_count}
        self.store.complete(key, result)
        return result

    def replay_n_times(self, key: str, payload: Any, n: int) -> list:
        """Replay the same key N times; all responses should be identical."""
        responses = []
        for _ in range(n):
            resp = self.execute_once(key, payload)
            responses.append(resp)
        return responses

    def all_responses_identical(self, responses: list) -> bool:
        """Check that all responses in the list are identical."""
        if not responses:
            return True
        first = json.dumps(responses[0], sort_keys=True)
        return all(json.dumps(r, sort_keys=True) == first for r in responses)


# ---------------------------------------------------------------------------
# ConcurrentDedupTester
# ---------------------------------------------------------------------------

class ConcurrentDedupTester:
    """
    Uses threading.Barrier to fire N threads simultaneously.
    Only one should execute the side effect (exactly-once semantics).
    Others get the cached response.
    """

    def __init__(self, store: IdempotencyStore | None = None):
        self.store = store or IdempotencyStore()
        self.execution_count = 0
        self._exec_lock = threading.Lock()

    def _side_effect(self, payload: Any) -> dict[str, Any]:
        with self._exec_lock:
            self.execution_count += 1
        # Simulate some work
        time.sleep(0.01)
        return {"result": "processed", "payload": payload}

    def run_concurrent(self, key: str, payload: Any, n_threads: int) -> list:
        """
        Fire n_threads simultaneously with the same idempotency key.
        Returns list of (won_slot, response) tuples.
        """
        barrier = threading.Barrier(n_threads)
        results = [None] * n_threads

        def worker(idx):
            barrier.wait()  # All threads start at the same time
            won = self.store.start(key)
            if won:
                try:
                    response = self._side_effect(payload)
                    self.store.complete(key, response)
                    results[idx] = ("executed", response)
                except Exception as e:
                    self.store.fail(key, str(e))
                    results[idx] = ("failed", str(e))
            else:
                # Wait for completion
                for _ in range(50):
                    entry = self.store.get(key)
                    if entry and entry.state == IdempotencyState.COMPLETED:
                        results[idx] = ("cached", entry.response)
                        return
                    time.sleep(0.005)
                results[idx] = ("pending", None)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        return results

    def reset(self):
        self.execution_count = 0
        self.store.clear()


# ---------------------------------------------------------------------------
# InProgressTester
# ---------------------------------------------------------------------------

class InProgressTester:
    """
    Verifies that a second request while the first is still PENDING
    returns 409 Conflict (or a PENDING status) rather than executing again.
    """

    def __init__(self, store: IdempotencyStore | None = None):
        self.store = store or IdempotencyStore()

    def handle_request(self, key: str, payload: Any, execution_fn: Callable) -> dict[str, Any]:
        """
        Returns:
          - 409 dict if key is PENDING
          - cached response if key is COMPLETED
          - executes and caches if key is new
        """
        existing = self.store.get(key)
        if existing is not None:
            if existing.state == IdempotencyState.PENDING:
                return {"status_code": 409, "error": "Request in progress", "state": "PENDING"}
            elif existing.state == IdempotencyState.COMPLETED:
                return {"status_code": 200, "cached": True, "response": existing.response}
            elif existing.state == IdempotencyState.FAILED:
                return {"status_code": 422, "cached": True, "error": existing.error}

        won = self.store.start(key)
        if not won:
            return {"status_code": 409, "error": "Request in progress", "state": "PENDING"}

        try:
            result = execution_fn(payload)
            self.store.complete(key, result)
            return {"status_code": 201, "cached": False, "response": result}
        except Exception as e:
            self.store.fail(key, str(e))
            return {"status_code": 500, "error": str(e)}


# ---------------------------------------------------------------------------
# TTLTester
# ---------------------------------------------------------------------------

class TTLTester:
    """
    Verifies that entries expire after TTL and are cleaned up.
    """

    def __init__(self, store: IdempotencyStore | None = None):
        self.store = store or IdempotencyStore()

    def add_entry_with_ttl(self, key: str, response: Any, ttl: float) -> None:
        """Add a completed entry with a specific TTL."""
        self.store.start(key, ttl=ttl)
        self.store.complete(key, response)

    def is_accessible(self, key: str) -> bool:
        """Check if an entry is still accessible (not expired)."""
        return self.store.get(key) is not None

    def wait_for_expiry(self, key: str, extra_wait: float = 0.05) -> None:
        """Wait until the entry has expired."""
        entry_raw = None
        with self.store._lock:
            entry_raw = self.store._store.get(key)
        if entry_raw and entry_raw.ttl:
            remaining = entry_raw.created_at + entry_raw.ttl - time.time()
            if remaining > 0:
                time.sleep(remaining + extra_wait)

    def cleanup_and_count(self) -> int:
        """Run cleanup and return number of removed entries."""
        return self.store.cleanup_expired()


# ---------------------------------------------------------------------------
# ResponsePersistenceTester
# ---------------------------------------------------------------------------

class ResponsePersistenceTester:
    """
    Proves that StateOnlyStore fails — it cannot return the original response
    on replay, demonstrating the failure mode.
    """

    def __init__(self):
        self.correct_store = IdempotencyStore()
        self.buggy_store = StateOnlyStore()

    def test_correct_store(self, key: str, response: Any) -> Any:
        """Store a response correctly and retrieve it."""
        self.correct_store.start(key)
        self.correct_store.complete(key, response)
        entry = self.correct_store.get(key)
        return entry.response if entry else None

    def test_buggy_store(self, key: str, response: Any) -> Any:
        """Attempt to store and retrieve a response using the buggy store."""
        self.buggy_store.start(key)
        self.buggy_store.complete(key, response)  # Response is NOT saved
        return self.buggy_store.get_response(key)  # Returns None

    def demonstrate_failure(self, key: str, response: Any) -> dict[str, Any]:
        """
        Demonstrates that:
        - Correct store returns original response on replay
        - Buggy store returns None (failure mode)
        """
        correct_result = self.test_correct_store(key + "_correct", response)
        buggy_result = self.test_buggy_store(key + "_buggy", response)

        return {
            "correct_store_result": correct_result,
            "buggy_store_result": buggy_result,
            "correct_store_works": correct_result == response,
            "buggy_store_fails": buggy_result is None,
        }


# ---------------------------------------------------------------------------
# Teeth: the response-persistence invariant.
#
# The core retry-safety invariant is that after start(key)+complete(key, resp),
# a replay must return the ORIGINAL response. IdempotencyStore satisfies this;
# StateOnlyStore is the planted defect that records state but drops the response,
# so a replayed request can never return the cached result.
#
# `prove(store_factory)` is pure and deterministic: it builds a fresh store from
# the supplied zero-arg factory and round-trips each case in a frozen, in-memory
# corpus (no clock/network/filesystem; the store classes' own time.time() default
# is never compared, only the persisted response value). It returns True iff the
# store fails to return the original response on ANY case (i.e. the defect is
# caught). Retrieval is interface-adaptive: the buggy store exposes get_response,
# the correct store exposes get(...).response.
# ---------------------------------------------------------------------------
# Frozen corpus of (key, response) cases the persisted response must survive.
_TEETH_CASES: tuple[tuple[str, Any], ...] = (
    ("teeth-k1", {"amount": 100, "currency": "USD"}),
    ("teeth-k2", {"txn": "abc", "v": 1}),
    ("teeth-k3", "plain-string-response"),
    ("teeth-k4", [1, 2, 3]),
    ("teeth-k5", {"nested": {"ok": True}, "items": [9, 8, 7]}),
)


def _persisted_response(store: Any, key: str) -> Any:
    """Retrieve the response a store kept for ``key``, across both interfaces."""
    if hasattr(store, "get_response"):
        return store.get_response(key)
    entry = store.get(key)
    return entry.response if entry is not None else None


def _prove(store_factory: Callable[[], Any]) -> bool:
    """True iff a fresh store from ``store_factory`` fails to round-trip the
    original response on any frozen case (the response-persistence defect)."""
    for key, response in _TEETH_CASES:
        try:
            store = store_factory()
            store.start(key)
            store.complete(key, response)
            if _persisted_response(store, key) != response:
                return True
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
    return False


TEETH = Teeth(
    prove=_prove,
    oracle=IdempotencyStore,
    mutants=(
        Mutant("response_not_persisted", StateOnlyStore,
               "state-only store records COMPLETED but drops the response, so a "
               "replayed request can never return the cached result"),
    ),
    corpus_size=len(_TEETH_CASES),
    kind="oracle_swap",
    notes="after start()+complete(), a replay must return the original response",
)


# ---------------------------------------------------------------------------
# SafeMethodTester
# ---------------------------------------------------------------------------

class SafeMethodTester:
    """
    Classifies HTTP methods as idempotent vs non-idempotent.

    Idempotent: GET, PUT, DELETE, HEAD, OPTIONS, TRACE
    Non-idempotent (not safe to retry blindly): POST, PATCH
    """

    # RFC 7231 / RFC 5789 classification
    IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "DELETE", "HEAD", "OPTIONS", "TRACE"})
    NON_IDEMPOTENT_METHODS = frozenset({"POST", "PATCH"})

    def is_idempotent(self, method: str) -> bool:
        """Return True if the HTTP method is idempotent."""
        return method.upper() in self.IDEMPOTENT_METHODS

    def is_safe(self, method: str) -> bool:
        """
        Return True if the method is 'safe' (read-only, no side effects).
        Safe methods: GET, HEAD, OPTIONS, TRACE
        """
        return method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}

    def classify(self, method: str) -> dict[str, Any]:
        """Return full classification of an HTTP method."""
        m = method.upper()
        return {
            "method": m,
            "idempotent": self.is_idempotent(m),
            "safe": self.is_safe(m),
            "requires_idempotency_key": not self.is_idempotent(m),
        }

    def methods_requiring_key(self) -> list:
        """Return list of HTTP methods that should require an idempotency key."""
        return sorted(self.NON_IDEMPOTENT_METHODS)

    def idempotent_methods(self) -> list:
        """Return sorted list of idempotent methods."""
        return sorted(self.IDEMPOTENT_METHODS)


# ---------------------------------------------------------------------------
# MockIdempotencyHandler: HTTP server that enforces idempotency keys
# ---------------------------------------------------------------------------

class MockIdempotencyHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP request handler that enforces idempotency keys via
    X-Idempotency-Key header for non-idempotent methods (POST/PATCH).
    """

    # Shared store across all handler instances (set on server object)
    def _get_store(self) -> IdempotencyStore:
        return self.server.idempotency_store

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def do_GET(self):
        self._send_json(200, {"method": "GET", "path": self.path, "idempotent": True})

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_OPTIONS(self):
        self._send_json(200, {"method": "OPTIONS", "idempotent": True})

    def do_DELETE(self):
        self._send_json(200, {"method": "DELETE", "path": self.path, "idempotent": True})

    def do_PUT(self):
        self._read_body()
        self._send_json(200, {"method": "PUT", "path": self.path, "idempotent": True})

    def do_POST(self):
        self._handle_non_idempotent("POST")

    def do_PATCH(self):
        self._handle_non_idempotent("PATCH")

    def _handle_non_idempotent(self, method: str):
        """Handle POST/PATCH with idempotency key enforcement."""
        store = self._get_store()
        idempotency_key = self.headers.get("X-Idempotency-Key")

        if not idempotency_key:
            self._read_body()
            self._send_json(400, {
                "error": "Missing X-Idempotency-Key header",
                "code": "MISSING_IDEMPOTENCY_KEY"
            })
            return

        body_bytes = self._read_body()
        try:
            body = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            body = {"raw": body_bytes.decode("utf-8", errors="replace")}

        existing = store.get(idempotency_key)
        if existing is not None:
            if existing.state == IdempotencyState.COMPLETED:
                self._send_json(200, {
                    "cached": True,
                    "idempotency_key": idempotency_key,
                    "response": existing.response,
                })
                return
            elif existing.state == IdempotencyState.PENDING:
                self._send_json(409, {
                    "error": "Request in progress",
                    "idempotency_key": idempotency_key,
                    "state": "PENDING"
                })
                return
            elif existing.state == IdempotencyState.FAILED:
                self._send_json(422, {
                    "cached": True,
                    "idempotency_key": idempotency_key,
                    "error": existing.error
                })
                return

        won = store.start(idempotency_key)
        if not won:
            self._send_json(409, {
                "error": "Request in progress",
                "idempotency_key": idempotency_key,
            })
            return

        # Execute the "business logic"
        result = {
            "id": str(uuid.uuid4()),
            "method": method,
            "path": self.path,
            "body": body,
            "processed_at": time.time(),
        }
        store.complete(idempotency_key, result)

        self._send_json(201, {
            "cached": False,
            "idempotency_key": idempotency_key,
            "response": result,
        })


class MockIdempotencyServer:
    """
    Wrapper to start/stop the MockIdempotencyHandler HTTP server
    on a dynamic port.
    """

    DEFAULT_PORT = 19070

    def __init__(self, port: int = 0, store: IdempotencyStore | None = None):
        """
        port=0 means OS assigns a free port (recommended for tests).
        port=DEFAULT_PORT (19070) uses the default port.
        """
        self.store = store or IdempotencyStore()
        self._server = None
        self._thread = None
        self._port = port

    def start(self) -> int:
        """Start the server. Returns the actual port number."""
        self._server = http.server.HTTPServer(("127.0.0.1", self._port), MockIdempotencyHandler)
        self._server.idempotency_store = self.store
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self._port

    def stop(self):
        """Shut down the server."""
        server = self._server
        if server:
            server.shutdown()
            server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def generate_idempotency_key() -> str:
    """Generate a unique idempotency key."""
    return str(uuid.uuid4())


def http_post(url: str, data: dict, headers: dict | None = None) -> dict[str, Any]:
    """Simple HTTP POST helper using stdlib urllib."""
    body = json.dumps(data).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {
                "status_code": resp.status,
                "body": json.loads(resp.read().decode("utf-8")),
            }
    except urllib.error.HTTPError as e:
        return {
            "status_code": e.code,
            "body": json.loads(e.read().decode("utf-8")),
        }


def http_get(url: str) -> dict[str, Any]:
    """Simple HTTP GET helper using stdlib urllib."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {
                "status_code": resp.status,
                "body": json.loads(resp.read().decode("utf-8")),
            }
    except urllib.error.HTTPError as e:
        return {
            "status_code": e.code,
            "body": json.loads(e.read().decode("utf-8")),
        }
