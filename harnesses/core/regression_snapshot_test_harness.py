"""
Regression & Snapshot Test Harness
Harness 13 of 36 — Pure stdlib, zero external dependencies.

Captures known-good outputs as baselines, then re-runs to detect regressions.
Includes a mock HTTP server on a dynamic port (default 18990).
"""

from __future__ import annotations

import argparse
import builtins
import dataclasses
import difflib
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Any

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Snapshot:
    """A captured snapshot of a value."""
    name: str
    value: Any
    checksum: str          # SHA-256 of the JSON-serialised value
    created_at: str        # ISO-8601 timestamp (UTC)

    @staticmethod
    def _compute_checksum(value: Any) -> str:
        """Return SHA-256 hex digest of the canonical JSON representation."""
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def create(cls, name: str, value: Any) -> Snapshot:
        """Create a new Snapshot, computing the checksum automatically."""
        checksum = cls._compute_checksum(value)
        created_at = datetime.now(timezone.utc).isoformat()
        return cls(name=name, value=value, checksum=checksum, created_at=created_at)

    def verify_checksum(self) -> bool:
        """Return True if the stored checksum matches a freshly computed one."""
        return self.checksum == self._compute_checksum(self.value)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        return cls(
            name=data["name"],
            value=data["value"],
            checksum=data["checksum"],
            created_at=data["created_at"],
        )


# ---------------------------------------------------------------------------
# SnapshotStore
# ---------------------------------------------------------------------------

class SnapshotStore:
    """Persists snapshots as JSON files inside a directory."""

    def __init__(self, directory: str | None = None) -> None:
        if directory is None:
            directory = tempfile.mkdtemp(prefix="snapshot_store_")
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, name: str) -> str:
        """Return the filesystem path for a snapshot name."""
        safe = name.replace(os.sep, "_").replace("/", "_")
        return os.path.join(self.directory, f"{safe}.json")

    def save(self, name: str, value: Any) -> Snapshot:
        """Create and persist a snapshot; returns the Snapshot object."""
        snapshot = Snapshot.create(name, value)
        with open(self._path(name), "w", encoding="utf-8") as fh:
            json.dump(snapshot.to_dict(), fh, indent=2, ensure_ascii=False)
        return snapshot

    def load(self, name: str) -> Snapshot | None:
        """Load a snapshot by name; returns None if it does not exist."""
        path = self._path(name)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return Snapshot.from_dict(data)

    def exists(self, name: str) -> bool:
        """Return True if a snapshot with this name exists."""
        return os.path.isfile(self._path(name))

    def delete(self, name: str) -> bool:
        """Delete a snapshot; returns True if it was deleted, False if not found."""
        path = self._path(name)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False

    def list(self) -> builtins.list[str]:
        """Return a sorted list of all snapshot names."""
        names = []
        for fname in os.listdir(self.directory):
            if fname.endswith(".json"):
                names.append(fname[:-5])  # strip .json
        return sorted(names)

    def clear(self) -> None:
        """Remove all snapshots from the store."""
        for name in self.list():
            self.delete(name)

    def destroy(self) -> None:
        """Remove the entire snapshot directory."""
        shutil.rmtree(self.directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# SnapshotComparator
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ComparisonResult:
    """Outcome of a single snapshot comparison."""
    match: bool
    mode: str
    diff: str | None = None        # unified diff text when available
    message: str | None = None

    def __bool__(self) -> bool:
        return self.match


class SnapshotComparator:
    """Compares an actual value against a stored Snapshot."""

    def compare_exact(self, actual: Any, snapshot: Snapshot) -> ComparisonResult:
        """
        Byte-exact comparison: both values are serialised to canonical JSON
        (sort_keys=True) and the strings must be identical.
        """
        actual_json = json.dumps(actual, sort_keys=True, ensure_ascii=False)
        stored_json = json.dumps(snapshot.value, sort_keys=True, ensure_ascii=False)
        match = actual_json == stored_json
        if match:
            return ComparisonResult(match=True, mode="exact")
        diff = self._unified_diff(stored_json, actual_json, "stored", "actual")
        return ComparisonResult(match=False, mode="exact", diff=diff,
                                message="Values differ (exact comparison)")

    def compare_json_normalized(self, actual: Any, snapshot: Snapshot) -> ComparisonResult:
        """
        Key-order-insensitive JSON comparison.
        Serialises both with sort_keys=True and compares the resulting strings.
        This is structurally equivalent to compare_exact for most cases but is
        semantically distinct — it explicitly normalises key order.
        """
        def _normalise(v: Any) -> Any:
            if isinstance(v, dict):
                return {k: _normalise(val) for k, val in sorted(v.items())}
            if isinstance(v, list):
                return [_normalise(i) for i in v]
            return v

        actual_norm = _normalise(actual)
        stored_norm = _normalise(snapshot.value)
        match = actual_norm == stored_norm
        if match:
            return ComparisonResult(match=True, mode="json_normalized")
        actual_json = json.dumps(actual_norm, indent=2, ensure_ascii=False)
        stored_json = json.dumps(stored_norm, indent=2, ensure_ascii=False)
        diff = self._unified_diff(stored_json, actual_json, "stored", "actual")
        return ComparisonResult(match=False, mode="json_normalized", diff=diff,
                                message="Values differ (JSON-normalised comparison)")

    def compare_lines(
        self,
        actual: str,
        snapshot: Snapshot,
        ignore_whitespace: bool = False,
    ) -> ComparisonResult:
        """
        Line-by-line comparison with optional whitespace ignoring.
        Returns a unified diff when values differ.
        actual must be a string; snapshot.value must also be a string.
        """
        if not isinstance(actual, str):
            raise TypeError(f"compare_lines expects str, got {type(actual).__name__}")
        stored = snapshot.value
        if not isinstance(stored, str):
            raise TypeError(
                f"compare_lines: stored snapshot value must be str, got {type(stored).__name__}"
            )

        def _lines(text: str) -> list[str]:
            lines = text.splitlines(keepends=True)
            if ignore_whitespace:
                lines = [line.strip() + "\n" for line in lines]
            return lines

        actual_lines = _lines(actual)
        stored_lines = _lines(stored)

        if actual_lines == stored_lines:
            return ComparisonResult(match=True, mode="lines")

        diff_lines = list(
            difflib.unified_diff(
                stored_lines,
                actual_lines,
                fromfile="stored",
                tofile="actual",
            )
        )
        diff = "".join(diff_lines)
        return ComparisonResult(match=False, mode="lines", diff=diff,
                                message="Values differ (line comparison)")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unified_diff(a: str, b: str, fromfile: str = "a", tofile: str = "b") -> str:
        a_lines = a.splitlines(keepends=True)
        b_lines = b.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(a_lines, b_lines, fromfile=fromfile, tofile=tofile)
        )


# ---------------------------------------------------------------------------
# Comparison mode constants
# ---------------------------------------------------------------------------

class CompareMode:
    EXACT = "exact"
    JSON_NORMALIZED = "json_normalized"
    LINES = "lines"


# ---------------------------------------------------------------------------
# RegressionResult / RegressionTest / SuiteReport
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RegressionResult:
    """Outcome of a single regression check."""
    test_name: str
    passed: bool
    first_run: bool          # True when no snapshot existed and one was created
    comparison: ComparisonResult | None = None
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def failed(self) -> bool:
        return not self.passed


@dataclasses.dataclass
class RegressionTest:
    """A named test: a callable plus optional comparison mode."""
    name: str
    func: Callable[[], Any]
    compare_mode: str = CompareMode.EXACT
    ignore_whitespace: bool = False   # used only with LINES mode


@dataclasses.dataclass
class SuiteReport:
    """Aggregate results for a test suite run."""
    results: list[RegressionResult] = dataclasses.field(default_factory=list)
    started_at: str = dataclasses.field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.failed)

    @property
    def first_runs(self) -> int:
        return sum(1 for r in self.results if r.first_run)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def summary(self) -> str:
        lines = [
            f"Suite Report — {self.started_at}",
            f"  Total   : {self.total}",
            f"  Passed  : {self.passed}",
            f"  Failed  : {self.failed}",
            f"  First   : {self.first_runs} (baseline captures)",
        ]
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            tag = " [first-run]" if r.first_run else ""
            lines.append(f"  [{status}] {r.test_name}{tag}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RegressionRunner
# ---------------------------------------------------------------------------

class RegressionRunner:
    """
    Runs a function, compares its output to a stored snapshot.
    On first run (no snapshot), captures the output as the baseline.
    """

    def __init__(self, store: SnapshotStore) -> None:
        self.store = store
        self.comparator = SnapshotComparator()

    def run(self, test: RegressionTest) -> RegressionResult:
        """Execute the test function and compare against the stored snapshot."""
        t0 = time.monotonic()
        try:
            actual = test.func()
        except Exception:
            duration = time.monotonic() - t0
            return RegressionResult(
                test_name=test.name,
                passed=False,
                first_run=False,
                error=traceback.format_exc(),
                duration_seconds=duration,
            )

        duration = time.monotonic() - t0

        # First-run mode: no snapshot yet — save and report success
        if not self.store.exists(test.name):
            self.store.save(test.name, actual)
            return RegressionResult(
                test_name=test.name,
                passed=True,
                first_run=True,
                duration_seconds=duration,
            )

        snapshot = self.store.load(test.name)
        if snapshot is None:
            return RegressionResult(
                test_name=test.name,
                passed=False,
                first_run=False,
                error="Snapshot disappeared between exists() and load()",
                duration_seconds=duration,
            )

        comparison = self._compare(actual, snapshot, test)
        return RegressionResult(
            test_name=test.name,
            passed=comparison.match,
            first_run=False,
            comparison=comparison,
            duration_seconds=duration,
        )

    def run_all(self, tests: list[RegressionTest]) -> SuiteReport:
        """Run a list of tests and return an aggregate SuiteReport."""
        report = SuiteReport()
        for test in tests:
            result = self.run(test)
            report.results.append(result)
        report.finish()
        return report

    def reset(self, name: str) -> bool:
        """Delete the stored snapshot so the next run becomes a first run."""
        return self.store.delete(name)

    # ------------------------------------------------------------------

    def _compare(
        self, actual: Any, snapshot: Snapshot, test: RegressionTest
    ) -> ComparisonResult:
        if test.compare_mode == CompareMode.EXACT:
            return self.comparator.compare_exact(actual, snapshot)
        elif test.compare_mode == CompareMode.JSON_NORMALIZED:
            return self.comparator.compare_json_normalized(actual, snapshot)
        elif test.compare_mode == CompareMode.LINES:
            return self.comparator.compare_lines(
                actual, snapshot, ignore_whitespace=test.ignore_whitespace
            )
        else:
            raise ValueError(f"Unknown compare_mode: {test.compare_mode!r}")


# ---------------------------------------------------------------------------
# MockRegressionHandler — HTTP server
# ---------------------------------------------------------------------------

class MockRegressionHandler(http.server.BaseHTTPRequestHandler):
    """
    Minimal HTTP request handler that serves snapshot data over HTTP.

    Endpoints:
        GET  /snapshots          — list all snapshot names (JSON array)
        GET  /snapshots/<name>   — retrieve a snapshot by name
        POST /snapshots/<name>   — save a snapshot (body: JSON value)
        DELETE /snapshots/<name> — delete a snapshot
        GET  /health             — simple health check
    """

    # The store is set on the class by MockRegressionServer
    store: SnapshotStore = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        # Suppress default stderr logging during tests
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/snapshots":
            names = self.store.list() if self.store else []
            self._send_json(200, names)
        elif self.path.startswith("/snapshots/"):
            name = self.path[len("/snapshots/"):]
            snapshot = self.store.load(name) if self.store else None
            if snapshot is None:
                self._send_json(404, {"error": f"Snapshot '{name}' not found"})
            else:
                self._send_json(200, snapshot.to_dict())
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/snapshots/"):
            name = self.path[len("/snapshots/"):]
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                value = json.loads(body)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return
            snapshot = self.store.save(name, value) if self.store else None
            if snapshot:
                self._send_json(201, snapshot.to_dict())
            else:
                self._send_json(500, {"error": "No store configured"})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/snapshots/"):
            name = self.path[len("/snapshots/"):]
            deleted = self.store.delete(name) if self.store else False
            if deleted:
                self._send_json(200, {"deleted": name})
            else:
                self._send_json(404, {"error": f"Snapshot '{name}' not found"})
        else:
            self._send_json(404, {"error": "Not found"})

    # ------------------------------------------------------------------

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockRegressionServer:
    """
    Wraps an HTTPServer running MockRegressionHandler in a background thread.
    Binds to localhost on an ephemeral port (or the requested port if free).
    """

    DEFAULT_PORT = 18990

    def __init__(
        self,
        store: SnapshotStore | None = None,
        port: int = 0,   # 0 = let the OS pick
    ) -> None:
        self.store = store or SnapshotStore()
        self._port_hint = port

        # Build a handler class with the store injected
        store_ref = self.store

        class _Handler(MockRegressionHandler):
            pass

        _Handler.store = store_ref  # type: ignore[attr-defined]
        self._handler_class = _Handler
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._server = http.server.HTTPServer(
            ("127.0.0.1", self._port_hint), self._handler_class
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("Server not started")
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> MockRegressionServer:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Convenience HTTP helpers
    # ------------------------------------------------------------------

    def get(self, path: str) -> tuple[int, Any]:
        url = self.base_url + path
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def post(self, path: str, value: Any) -> tuple[int, Any]:
        url = self.base_url + path
        body = json.dumps(value).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def delete(self, path: str) -> tuple[int, Any]:
        url = self.base_url + path
        req = urllib.request.Request(url, method="DELETE")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


# ---------------------------------------------------------------------------
# Convenience factory functions
# ---------------------------------------------------------------------------

def make_store(directory: str | None = None) -> SnapshotStore:
    """Create a SnapshotStore (temp dir if directory is None)."""
    return SnapshotStore(directory)


def make_runner(store: SnapshotStore | None = None) -> RegressionRunner:
    """Create a RegressionRunner with an optional existing store."""
    return RegressionRunner(store or make_store())


def make_test(
    name: str,
    func: Callable[[], Any],
    mode: str = CompareMode.EXACT,
    ignore_whitespace: bool = False,
) -> RegressionTest:
    """Convenience constructor for RegressionTest."""
    return RegressionTest(name=name, func=func, compare_mode=mode,
                          ignore_whitespace=ignore_whitespace)


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (actual, stored, expected_match) snapshot-comparison
# cases and the EXACT match verdict a CORRECT key-order-insensitive comparator
# MUST produce.
#
# The oracle-able core is the pure SnapshotComparator.compare_json_normalized
# decision: two JSON-like structures MATCH iff they are equal regardless of dict
# key order, recursing into nested dicts AND lists. We scope the teeth to that
# pure (actual, stored) -> bool verdict, deliberately EXCLUDING SnapshotStore
# (filesystem/tempdir) and Snapshot.created_at (clock) so prove() stays pure and
# deterministic — no clock, network, filesystem, threads, or RNG.
#
# A regression/snapshot comparator only has teeth if it CATCHES a comparator that
# (a) stops at the top level and never recurses into nested values — so a changed
# DEEP value sails through as a false MATCH (the classic "we only diffed the top
# keys" regression-suite blind spot); or (b) drops key-order normalization — so a
# semantically identical snapshot with reordered dict keys is flagged as a false
# MISMATCH (a noisy, trust-eroding regression failure). The contract every correct
# normalized comparator must hold:
#
#   * dict key ORDER is irrelevant at every depth (normalize before comparing);
#   * a difference in ANY nested value — however deep, inside dicts OR lists —
#     is a real mismatch and must be reported.
#
# An impl is a callable ``match(actual, stored) -> bool`` returning the match
# verdict. prove() judges each impl against the corpus's FROZEN LITERAL
# expected_match booleans (hand-decided from the contract above, NEVER read back
# from the oracle at runtime), so the check is non-circular. prove(impl) is True
# iff any verdict diverges from the frozen literal — i.e. the planted comparator
# bug is caught.
#
# The two planted mutants model genuine real-world comparator defects (per the
# campaign plan):
#
#   * no_recurse — compares only the top-level dict keys/values shallowly and
#     does NOT recurse, so a changed nested value wrongly MATCHES (a deep
#     regression slips past the snapshot gate);
#   * order_sensitive — drops the recursive key-order normalization (plain ==),
#     so a reordered-key dict wrongly MISMATCHES (a false regression alarm).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CompareCase:
    """One frozen comparison case with a literal, hand-decided match verdict."""
    name: str
    actual: Any
    stored: Any
    expected_match: bool   # the EXACT verdict a correct normalized comparator yields
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND each planted mutant
# is caught by >=2 cases (no single-load-bearing fixture). Every ``expected_match``
# is a hand-decided constant from the contract, never derived at runtime.
#
#   * reordered_keys_* (match=True): same content, dict keys reordered at the top
#     AND inside a nested dict. order_sensitive wrongly reports MISMATCH on both.
#   * changed_nested_* (match=False): identical top-level shape but a genuinely
#     changed value buried inside a nested dict / inside a list-of-dicts.
#     no_recurse wrongly reports MATCH on both (it never looks that deep).
#   * top_level_diff (match=False): a decoy whose difference is at the TOP level,
#     which BOTH mutants still catch — so it cannot prop up either mutant alone.
COMPARE_CORPUS: tuple[CompareCase, ...] = (
    # --- reordered keys: must MATCH (order is irrelevant at every depth) ---
    CompareCase(
        "reordered_top_keys",
        {"b": 2, "a": 1, "c": 3},
        {"a": 1, "b": 2, "c": 3},
        True,
        "top-level dict keys reordered: order_sensitive wrongly mismatches",
    ),
    CompareCase(
        "reordered_nested_keys",
        {"outer": {"z": 9, "y": 8, "x": 7}, "k": 0},
        {"k": 0, "outer": {"x": 7, "y": 8, "z": 9}},
        True,
        "nested dict keys reordered: order_sensitive wrongly mismatches",
    ),
    # --- changed nested value: must MISMATCH (a deep regression is real) ---
    CompareCase(
        "changed_nested_dict_value",
        {"id": 1, "meta": {"role": "user", "tier": "gold"}},
        {"id": 1, "meta": {"role": "user", "tier": "silver"}},
        False,
        "a value changed one level deep: no_recurse wrongly matches",
    ),
    CompareCase(
        "changed_value_in_list_of_dicts",
        {"items": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 5}]},
        {"items": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 9}]},
        False,
        "a value changed inside a list-of-dicts: no_recurse wrongly matches",
    ),
    # --- top-level difference: must MISMATCH (decoy both mutants catch) ---
    CompareCase(
        "top_level_value_diff",
        {"status": "ok", "count": 1},
        {"status": "ok", "count": 2},
        False,
        "top-level value differs: both mutants catch this, so neither leans on it",
    ),
    # --- identical scalars / empty: must MATCH (decoy; baseline sanity) ---
    CompareCase(
        "identical_scalars",
        {"a": 1, "b": [1, 2, 3], "c": None},
        {"a": 1, "b": [1, 2, 3], "c": None},
        True,
        "byte-identical content: every correct impl matches",
    ),
)


# --- ORACLE: the harness's own correct normalized-match decision ---------------

def oracle_match(actual: Any, stored: Any) -> bool:
    """Correct key-order-insensitive structural match.

    Recursively normalizes dict key order at EVERY depth (sorting dict items and
    recursing through both dict values and list elements), then compares for
    equality. This is the pure verdict behind
    ``SnapshotComparator.compare_json_normalized``.
    """
    def _normalise(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: _normalise(val) for k, val in sorted(v.items())}
        if isinstance(v, list):
            return [_normalise(i) for i in v]
        return v

    return _normalise(actual) == _normalise(stored)


# --- Planted buggy twins (each models a real comparator defect) ----------------

def _shallow_value_matches(av: Any, sv: Any) -> bool:
    """One key's NON-recursive comparison for ``no_recurse_match``: two nested
    containers are deemed equal by TYPE alone (never recursed) — the planted
    bug; everything else falls back to plain equality."""
    if isinstance(av, (dict, list)) and isinstance(sv, (dict, list)):
        return type(av) is type(sv)
    return av == sv


def no_recurse_match(actual: Any, stored: Any) -> bool:
    """BUG: compares only the TOP level and never recurses into nested values.

    For two dicts it checks that the key sets match and that each top-level value
    is either equal OR (when both are dicts/lists) merely the same *type* — it
    stops there instead of recursing. So a value changed one-or-more levels deep
    (inside a nested dict, or inside a list-of-dicts) sails through as a false
    MATCH. Models the "our snapshot diff only looked at the top keys" regression
    blind spot.
    """
    if isinstance(actual, dict) and isinstance(stored, dict):
        if sorted(actual.keys()) != sorted(stored.keys()):
            return False
        return all(_shallow_value_matches(actual[k], stored[k]) for k in actual)
    return actual == stored


def order_sensitive_match(actual: Any, stored: Any) -> bool:
    """BUG: drops key-order normalization and compares with plain ``==``.

    Python dict equality is actually order-insensitive, so the realistic defect
    is to compare the SERIALIZED form without sorting keys: ``json.dumps`` without
    ``sort_keys=True`` preserves insertion order, so two semantically identical
    snapshots whose dict keys were written in a different order produce different
    strings and are flagged as a false MISMATCH. Models a comparator that diffs
    raw serialized text instead of normalized structure.
    """
    return json.dumps(actual, ensure_ascii=False) == json.dumps(stored, ensure_ascii=False)


def prove(impl: Callable[[Any, Any], bool]) -> bool:
    """True iff ``impl`` produces the WRONG match verdict for any frozen corpus
    case (i.e. the comparator bug is caught): the boolean verdict diverges from
    the hand-decided literal, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    COMPARE_CORPUS, never read from the oracle; pure structural comparison only,
    no RNG/clock/threads/network/filesystem. An impl that raises on a corpus case
    counts as caught.
    """
    for case in COMPARE_CORPUS:
        try:
            verdict = impl(case.actual, case.stored)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if bool(verdict) != case.expected_match:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_match,
    mutants=(
        Mutant("no_recurse", no_recurse_match,
               "compares only top-level keys/values and never recurses, so a "
               "value changed inside a nested dict or a list-of-dicts wrongly "
               "MATCHES — a deep regression slips past the snapshot gate"),
        Mutant("order_sensitive", order_sensitive_match,
               "diffs serialized text without sort_keys, dropping key-order "
               "normalization, so a reordered-key dict wrongly MISMATCHES — a "
               "false regression alarm on a semantically identical snapshot"),
    ),
    corpus_size=len(COMPARE_CORPUS),
    kind="oracle_swap",
    notes="a correct normalized comparator treats dict key order as irrelevant "
          "at every depth yet reports any nested value change as a real mismatch",
)


def list_teeth_scenarios() -> list[str]:
    """Names of the frozen comparison corpus cases (the teeth scenarios)."""
    return [c.name for c in COMPARE_CORPUS]


# ---------------------------------------------------------------------------
# Self-test — fails loud, reports findings.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Exercise the normalized-comparator core this harness guards and assert the
    teeth: the real ``SnapshotComparator.compare_json_normalized`` reproduces
    every frozen verdict, the standalone oracle matches every literal, and the
    universal swap-check passes (oracle clean, every planted mutant caught)."""
    report = Report("core/regression_snapshot")
    comparator = SnapshotComparator()

    # 1. The real comparator method reproduces every frozen verdict exactly,
    #    proving the teeth oracle is faithful to the shipped implementation.
    for case in COMPARE_CORPUS:
        snap = Snapshot.create("teeth", case.stored)
        got = comparator.compare_json_normalized(case.actual, snap).match
        report.add(f"comparator:{case.name}", case.expected_match, got, detail=case.note)

    # 2. The standalone oracle reproduces every frozen verdict literal.
    for case in COMPARE_CORPUS:
        report.add(f"oracle:{case.name}", case.expected_match,
                   oracle_match(case.actual, case.stored), detail=case.note)

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def _run_demo() -> None:
    """Original interactive demo: snapshot save/load, runner, and mock server.

    Kept under the CLI only (never at import). The mock HTTP server binds a port,
    so it lives here and is excluded from the pure, deterministic teeth.
    """
    print("Regression & Snapshot Test Harness — demo mode")
    store = SnapshotStore()
    print(f"Snapshot store: {store.directory}")

    snap = store.save("demo", {"hello": "world", "count": 42})
    print(f"Saved  : {snap}")
    reloaded = store.load("demo")
    print(f"Loaded : {reloaded}")
    print(f"Checksum valid: {reloaded.verify_checksum()}")

    runner = RegressionRunner(store)
    store.delete("demo")  # ensure first run

    t = RegressionTest(name="demo", func=lambda: {"hello": "world", "count": 42})
    r1 = runner.run(t)
    print(f"Run 1 (first): {r1}")

    r2 = runner.run(t)
    print(f"Run 2 (match): {r2}")

    with MockRegressionServer(store) as srv:
        print(f"Server running on {srv.base_url}")
        status, body = srv.get("/health")
        print(f"Health: {status} {body}")
        status, body = srv.get("/snapshots")
        print(f"List  : {status} {body}")

    store.destroy()
    print("Done.")


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regression & snapshot comparator controls + teeth self-test"
    )
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--demo", action="store_true",
                        help="run the interactive snapshot/server demo")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_teeth_scenarios()))
        return 0
    if args.demo:
        _run_demo()
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
