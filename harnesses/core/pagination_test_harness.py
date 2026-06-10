"""
Pagination / Cursor Consistency Test Harness (Harness 31 of 36)
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# BackingStore
# ---------------------------------------------------------------------------

class BackingStore:
    """Thread-safe store of records (list of dicts with id, sort_key, data)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []

    def add(self, record: Dict[str, Any]) -> None:
        """Add a record. Record must have 'id', 'sort_key', and 'data' keys."""
        if "id" not in record:
            raise ValueError("Record must have 'id' field")
        if "sort_key" not in record:
            raise ValueError("Record must have 'sort_key' field")
        with self._lock:
            self._records.append(dict(record))

    def delete(self, record_id: Any) -> bool:
        """Delete a record by id. Returns True if found and deleted."""
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records if r["id"] != record_id]
            return len(self._records) < before

    def all(self) -> List[Dict[str, Any]]:
        """Return a snapshot of all records."""
        with self._lock:
            return [dict(r) for r in self._records]

    def clear(self) -> None:
        """Remove all records."""
        with self._lock:
            self._records.clear()

    def count(self) -> int:
        """Return the number of records."""
        with self._lock:
            return len(self._records)


# ---------------------------------------------------------------------------
# Page / PageResult dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Page:
    items: List[Dict[str, Any]]
    total: int
    has_next: bool
    cursor: Optional[str] = None  # opaque cursor for next page, or None


@dataclass
class PageResult:
    page: Optional[Page]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Cursor encoding/decoding
# ---------------------------------------------------------------------------

def encode_cursor(sort_key: Any, record_id: Any) -> str:
    """Encode (sort_key, id) as an opaque base64+json cursor."""
    payload = json.dumps({"sort_key": sort_key, "id": record_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> Tuple[Any, Any]:
    """
    Decode a cursor back to (sort_key, id).
    Raises ValueError on malformed base64, invalid JSON, or wrong structure.
    """
    try:
        decoded_bytes = base64.urlsafe_b64decode(cursor + "==")
    except Exception:
        raise ValueError(f"Malformed base64 cursor: {cursor!r}")
    try:
        payload = json.loads(decoded_bytes.decode())
    except Exception:
        raise ValueError(f"Cursor payload is not valid JSON: {cursor!r}")
    if not isinstance(payload, dict):
        raise ValueError(f"Cursor payload is not a JSON object: {cursor!r}")
    if "sort_key" not in payload or "id" not in payload:
        raise ValueError(f"Cursor missing required fields 'sort_key'/'id': {cursor!r}")
    return payload["sort_key"], payload["id"]


# ---------------------------------------------------------------------------
# OffsetPaginator — demonstrates classic bugs
# ---------------------------------------------------------------------------

class OffsetPaginator:
    """
    LIMIT/OFFSET pagination over a BackingStore.
    Sorted by (sort_key, id) for determinism.
    Demonstrates the classic bugs when the dataset mutates between pages.
    """

    def __init__(self, store: BackingStore) -> None:
        self._store = store

    def _sorted_records(self) -> List[Dict[str, Any]]:
        records = self._store.all()
        records.sort(key=lambda r: (r["sort_key"], r["id"]))
        return records

    def page(self, offset: int = 0, limit: int = 10) -> PageResult:
        if limit <= 0:
            return PageResult(page=None, error="limit must be > 0")
        if offset < 0:
            return PageResult(page=None, error="offset must be >= 0")
        records = self._sorted_records()
        total = len(records)
        items = records[offset: offset + limit]
        has_next = (offset + limit) < total
        return PageResult(
            page=Page(
                items=items,
                total=total,
                has_next=has_next,
                cursor=None,
            )
        )


# ---------------------------------------------------------------------------
# CursorPaginator — keyset pagination, immune to mutation bugs
# ---------------------------------------------------------------------------

class CursorPaginator:
    """
    Keyset pagination on (sort_key, id) tiebreaker.
    Uses an opaque base64 cursor. Immune to insert/delete mutation bugs.
    """

    def __init__(self, store: BackingStore) -> None:
        self._store = store

    def _sorted_records(self) -> List[Dict[str, Any]]:
        records = self._store.all()
        records.sort(key=lambda r: (r["sort_key"], r["id"]))
        return records

    def page(self, cursor: Optional[str] = None, limit: int = 10) -> PageResult:
        if limit <= 0:
            return PageResult(page=None, error="limit must be > 0")

        # Decode cursor if provided
        after_sort_key: Any = None
        after_id: Any = None
        if cursor is not None:
            try:
                after_sort_key, after_id = decode_cursor(cursor)
            except ValueError as exc:
                return PageResult(page=None, error=str(exc))

        records = self._sorted_records()
        total = len(records)

        # Filter to records strictly after the cursor position
        if cursor is not None:
            filtered = [
                r for r in records
                if (r["sort_key"], r["id"]) > (after_sort_key, after_id)
            ]
            # Validate: cursor past end is not an error, just returns empty
        else:
            filtered = records

        items = filtered[:limit]
        has_next = len(filtered) > limit

        next_cursor: Optional[str] = None
        if has_next and items:
            last = items[-1]
            next_cursor = encode_cursor(last["sort_key"], last["id"])

        return PageResult(
            page=Page(
                items=items,
                total=total,
                has_next=has_next,
                cursor=next_cursor,
            )
        )

    def all_pages(self, limit: int = 10) -> Tuple[List[Dict[str, Any]], List[PageResult]]:
        """
        Traverse all pages and return (all_items, all_page_results).
        Useful for full-traversal reconciliation tests.
        """
        all_items: List[Dict[str, Any]] = []
        results: List[PageResult] = []
        cursor: Optional[str] = None
        while True:
            result = self.page(cursor=cursor, limit=limit)
            results.append(result)
            if result.error:
                break
            assert result.page is not None
            all_items.extend(result.page.items)
            if not result.page.has_next:
                break
            cursor = result.page.cursor
        return all_items, results


# ---------------------------------------------------------------------------
# Test result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PaginationTestResult:
    name: str
    passed: bool
    message: str = ""
    duration_ms: float = 0.0


@dataclass
class PaginationReport:
    results: List[PaginationTestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0

    def add(self, result: PaginationTestResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.passed:
            self.passed += 1
        else:
            self.failed += 1

    @property
    def all_passed(self) -> bool:
        return self.failed == 0


# ---------------------------------------------------------------------------
# MockPaginationHandler — HTTP server
# ---------------------------------------------------------------------------

class MockPaginationHandler(BaseHTTPRequestHandler):
    """
    HTTP handler serving paginated JSON responses.

    Routes:
      GET /offset?offset=N&limit=M   → offset pagination
      GET /cursor?cursor=X&limit=M   → cursor pagination

    The handler expects `self.server.store` (a BackingStore).
    """

    def log_message(self, fmt: str, *args: Any) -> None:  # silence access logs
        pass

    def _send_json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        def get_int(key: str, default: int) -> Optional[int]:
            vals = params.get(key)
            if vals:
                try:
                    return int(vals[0])
                except ValueError:
                    return None
            return default

        store: BackingStore = self.server.store  # type: ignore[attr-defined]

        if parsed.path == "/offset":
            offset = get_int("offset", 0)
            limit = get_int("limit", 10)
            if offset is None or limit is None:
                self._send_json(400, {"error": "invalid offset or limit"})
                return
            paginator = OffsetPaginator(store)
            result = paginator.page(offset=offset, limit=limit)
            if result.error:
                self._send_json(400, {"error": result.error})
                return
            pg = result.page
            assert pg is not None
            self._send_json(200, {
                "items": pg.items,
                "total": pg.total,
                "has_next": pg.has_next,
                "cursor": pg.cursor,
            })

        elif parsed.path == "/cursor":
            cursor_vals = params.get("cursor")
            cursor = cursor_vals[0] if cursor_vals else None
            limit = get_int("limit", 10)
            if limit is None:
                self._send_json(400, {"error": "invalid limit"})
                return
            paginator = CursorPaginator(store)
            result = paginator.page(cursor=cursor, limit=limit)
            if result.error:
                self._send_json(400, {"error": result.error})
                return
            pg = result.page
            assert pg is not None
            self._send_json(200, {
                "items": pg.items,
                "total": pg.total,
                "has_next": pg.has_next,
                "cursor": pg.cursor,
            })

        else:
            self._send_json(404, {"error": "not found"})


class PaginationServer:
    """Manages a MockPaginationHandler HTTP server on a dynamic port."""

    DEFAULT_PORT = 19170

    def __init__(self, store: Optional[BackingStore] = None, port: int = 0) -> None:
        self.store = store or BackingStore()
        self._server = HTTPServer(("127.0.0.1", port), MockPaginationHandler)
        self._server.store = self.store  # type: ignore[attr-defined]
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_json(self, path: str) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def get_raw(self, path: str) -> Tuple[int, Dict[str, Any]]:
        """Return (status_code, body_dict) without raising on HTTP errors."""
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode())
            return exc.code, body


# ---------------------------------------------------------------------------
# Helpers to build test datasets
# ---------------------------------------------------------------------------

def make_records(n: int, sort_key_fn=None) -> List[Dict[str, Any]]:
    """Create n records with sequential ids and sort_keys."""
    records = []
    for i in range(1, n + 1):
        sk = sort_key_fn(i) if sort_key_fn else i
        records.append({"id": i, "sort_key": sk, "data": f"item-{i}"})
    return records


def populated_store(n: int, sort_key_fn=None) -> BackingStore:
    store = BackingStore()
    for r in make_records(n, sort_key_fn):
        store.add(r)
    return store


# ---------------------------------------------------------------------------
# High-level scenario runners
# ---------------------------------------------------------------------------

def demonstrate_offset_delete_bug(store: BackingStore, limit: int = 3) -> Dict[str, Any]:
    """
    Show that deleting a row before the current offset causes the next
    page to skip a record.

    Returns a dict with:
      page1_items, page2_items_bugged, page2_items_expected
    """
    paginator = OffsetPaginator(store)
    r1 = paginator.page(offset=0, limit=limit)
    assert r1.page is not None
    page1_ids = [x["id"] for x in r1.page.items]

    # Delete the FIRST record (before the offset for page 2)
    first_id = page1_ids[0]
    store.delete(first_id)

    # Now fetch page 2 with the original offset — it skips a record
    r2 = paginator.page(offset=limit, limit=limit)
    assert r2.page is not None
    page2_ids_bugged = [x["id"] for x in r2.page.items]

    return {
        "page1_ids": page1_ids,
        "deleted_id": first_id,
        "page2_ids_bugged": page2_ids_bugged,
    }


def demonstrate_offset_insert_bug(store: BackingStore, limit: int = 3) -> Dict[str, Any]:
    """
    Show that inserting a row before the current offset causes the next
    page to re-show a record.

    Returns a dict with page1_ids, page2_ids_bugged.
    """
    paginator = OffsetPaginator(store)
    r1 = paginator.page(offset=0, limit=limit)
    assert r1.page is not None
    page1_ids = [x["id"] for x in r1.page.items]

    # Insert a new record with a sort_key that puts it before all existing
    all_records = store.all()
    min_sort_key = min(r["sort_key"] for r in all_records) if all_records else 0
    new_id = max(r["id"] for r in all_records) + 1000
    store.add({"id": new_id, "sort_key": min_sort_key - 1, "data": "injected"})

    # Now fetch page 2 with the original offset — it re-shows a record
    r2 = paginator.page(offset=limit, limit=limit)
    assert r2.page is not None
    page2_ids_bugged = [x["id"] for x in r2.page.items]

    return {
        "page1_ids": page1_ids,
        "inserted_id": new_id,
        "page2_ids_bugged": page2_ids_bugged,
    }
