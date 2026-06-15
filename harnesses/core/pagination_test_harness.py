"""
Pagination / Cursor Consistency Test Harness (Harness 31 of 36)
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import argparse
import base64
import json

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from typing import Any
from urllib.parse import parse_qs, urlparse

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# BackingStore
# ---------------------------------------------------------------------------

class BackingStore:
    """Thread-safe store of records (list of dicts with id, sort_key, data)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []

    def add(self, record: dict[str, Any]) -> None:
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

    def all(self) -> list[dict[str, Any]]:
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
    items: list[dict[str, Any]]
    total: int
    has_next: bool
    cursor: str | None = None  # opaque cursor for next page, or None


@dataclass
class PageResult:
    page: Page | None
    error: str | None = None


# ---------------------------------------------------------------------------
# Cursor encoding/decoding
# ---------------------------------------------------------------------------

def encode_cursor(sort_key: Any, record_id: Any) -> str:
    """Encode (sort_key, id) as an opaque base64+json cursor."""
    payload = json.dumps({"sort_key": sort_key, "id": record_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[Any, Any]:
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

    def _sorted_records(self) -> list[dict[str, Any]]:
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

    def _sorted_records(self) -> list[dict[str, Any]]:
        records = self._store.all()
        records.sort(key=lambda r: (r["sort_key"], r["id"]))
        return records

    def page(self, cursor: str | None = None, limit: int = 10) -> PageResult:
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

        next_cursor: str | None = None
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

    def all_pages(self, limit: int = 10) -> tuple[list[dict[str, Any]], list[PageResult]]:
        """
        Traverse all pages and return (all_items, all_page_results).
        Useful for full-traversal reconciliation tests.
        """
        all_items: list[dict[str, Any]] = []
        results: list[PageResult] = []
        cursor: str | None = None
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
    results: list[PaginationTestResult] = field(default_factory=list)
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

        def get_int(key: str, default: int) -> int | None:
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

    def __init__(self, store: BackingStore | None = None, port: int = 0) -> None:
        self.store = store or BackingStore()
        self._server = HTTPServer(("127.0.0.1", port), MockPaginationHandler)
        self._server.store = self.store  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

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

    def get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def get_raw(self, path: str) -> tuple[int, dict[str, Any]]:
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

def make_records(n: int, sort_key_fn=None) -> list[dict[str, Any]]:
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

def demonstrate_offset_delete_bug(store: BackingStore, limit: int = 3) -> dict[str, Any]:
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


def demonstrate_offset_insert_bug(store: BackingStore, limit: int = 3) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# TEETH: a FROZEN dataset + page-request script with a literal expected
# concatenation, judged against pure keyset-paging implementations.
#
# A paging impl is a function ``page(records, after, limit) -> (items, next_after)``
# where ``records`` is the (already sorted) full dataset, ``after`` is the keyset
# position to resume strictly after (None on the first page), and ``next_after`` is
# the cursor key to resume from on the next call (None when exhausted). prove()
# drives the impl across the frozen sequence of page sizes, concatenates the pages,
# and checks the result against a LITERAL expected id list baked into the corpus.
# It is NON-CIRCULAR: expectations are hand-computed constants, never read back
# from the oracle object. prove(impl) is True iff the impl diverges (the planted
# boundary bug is caught). Pure + deterministic: no clock/network/filesystem I/O,
# no RNG. The contract a correct pager must hold: every dataset item appears
# exactly once across all pages, in stable order, with no dup/skip at boundaries.
# ---------------------------------------------------------------------------

# A frozen dataset: ids 1..7 with strictly increasing (sort_key, id) keys. The
# uneven page-size script (3, 2, 2, 2) crosses every page boundary, including the
# exact-multiple final boundary, so an off-by-one at the seam is observable.
_FROZEN_RECORDS: tuple[dict[str, Any], ...] = tuple(
    {"id": i, "sort_key": i * 10, "data": f"item-{i}"} for i in range(1, 8)
)
# The page sizes requested, in order. Sum (3+2+2) reaches the end at 7 items; the
# traversal stops when an impl reports no next cursor.
_PAGE_SCRIPT: tuple[int, ...] = (3, 2, 2, 2)
# The hand-computed, literal expected concatenation: each of ids 1..7 exactly once
# in stable ascending order. This is the oracle the impl is judged against — a
# constant, NOT derived from any paginator at runtime.
_EXPECTED_IDS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)


def _key_of(record: dict[str, Any]) -> tuple[Any, Any]:
    return (record["sort_key"], record["id"])


def oracle_page(
    records: list[dict[str, Any]],
    after: tuple[Any, Any] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], tuple[Any, Any] | None]:
    """Correct keyset page: items strictly after `after`, sliced to `limit`.

    Returns (items, next_after). next_after is the key of the last returned item
    when more records remain, else None. This is the reference behaviour the
    harness's CursorPaginator implements; reused here as the ORACLE.
    """
    candidates = records if after is None else [r for r in records if _key_of(r) > after]
    items = candidates[:limit]
    has_next = len(candidates) > limit
    next_after = _key_of(items[-1]) if (has_next and items) else None
    return items, next_after


# --- Planted buggy twins (each models a real keyset-pagination defect) ------

def page_skip_boundary(
    records: list[dict[str, Any]],
    after: tuple[Any, Any] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], tuple[Any, Any] | None]:
    """BUG: resumes STRICTLY after the wrong key — uses the key of the item one
    past the page (a fetch-then-advance off-by-one), so the first item of every
    subsequent page is silently skipped.

    Models the classic keyset off-by-one where the next-cursor is taken from the
    record *after* the page's last item instead of the last item itself, dropping
    one record at each page boundary.
    """
    candidates = records if after is None else [r for r in records if _key_of(r) > after]
    items = candidates[:limit]
    has_next = len(candidates) > limit
    # BUG: advance past the FIRST item beyond the page, not the last item in it.
    next_after = _key_of(candidates[limit]) if has_next else None
    return items, next_after


def page_inclusive_boundary(
    records: list[dict[str, Any]],
    after: tuple[Any, Any] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], tuple[Any, Any] | None]:
    """BUG: resumes at records >= the cursor (inclusive) instead of strictly
    after it, so the boundary item is returned again on the next page.

    Models the very common `>=` vs `>` keyset error, which duplicates the last
    item of each page as the first item of the following page.
    """
    if after is None:
        candidates = records
    else:
        # BUG: `>=` re-includes the cursor record itself.
        candidates = [r for r in records if _key_of(r) >= after]
    items = candidates[:limit]
    has_next = len(candidates) > limit
    next_after = _key_of(items[-1]) if (has_next and items) else None
    return items, next_after


def page_stuck_cursor(
    records: list[dict[str, Any]],
    after: tuple[Any, Any] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], tuple[Any, Any] | None]:
    """BUG: never advances the cursor — next_after is always None even when more
    records remain, so traversal returns only the first page and loses the tail.

    Models a pager that forgets to emit a next-page token (or sets has_next but
    no cursor), silently truncating the result set after page one.
    """
    candidates = records if after is None else [r for r in records if _key_of(r) > after]
    items = candidates[:limit]
    # BUG: never report a next cursor — position is lost after the first page.
    return items, None


def _traverse(
    impl: Callable[..., tuple[list[dict[str, Any]], tuple[Any, Any] | None]],
) -> list[int]:
    """Drive `impl` across the frozen page script; return the concatenated ids.

    Pure + deterministic: walks _FROZEN_RECORDS (pre-sorted) using each scripted
    limit in turn, following the impl's own next-cursor, stopping when the impl
    reports no next cursor or the script is exhausted. A guard caps the loop so a
    cursor-that-never-advances bug cannot hang prove()."""
    records = sorted(_FROZEN_RECORDS, key=_key_of)
    after: tuple[Any, Any] | None = None
    collected: list[int] = []
    max_pages = len(_PAGE_SCRIPT)
    for i in range(max_pages):
        limit = _PAGE_SCRIPT[i]
        items, next_after = impl(records, after, limit)
        collected.extend(r["id"] for r in items)
        if next_after is None:
            break
        after = next_after
    return collected


def prove(impl: Callable[..., tuple[list[dict[str, Any]], tuple[Any, Any] | None]]) -> bool:
    """True iff paging `impl` diverges from the frozen expected concatenation
    (i.e. the planted boundary bug is CAUGHT).

    Non-circular: the traversal is compared to the literal _EXPECTED_IDS constant,
    never to the oracle object. An impl that raises while paging counts as caught.
    The contract checked: the concatenation of pages equals the dataset exactly
    once, in order — so any skip, duplicate, or lost-position at a page boundary
    makes the lists differ.
    """
    try:
        collected = _traverse(impl)
    except Exception:  # noqa: BLE001 — raising while paging counts as caught
        return True
    return tuple(collected) != _EXPECTED_IDS


TEETH = Teeth(
    prove=prove,
    oracle=oracle_page,
    mutants=(
        Mutant("skip_boundary_item", page_skip_boundary,
               "next-cursor taken from the item AFTER the page -> skips one record per boundary"),
        Mutant("duplicate_boundary_item", page_inclusive_boundary,
               "resumes at `>=` cursor instead of `>` -> duplicates the boundary item across pages"),
        Mutant("stuck_cursor", page_stuck_cursor,
               "never emits a next cursor -> traversal truncates after the first page"),
    ),
    corpus_size=len(_EXPECTED_IDS),
    kind="oracle_swap",
    notes="page concatenation must equal the frozen dataset exactly once, in order, "
          "with no dup/skip at boundaries",
)


def list_scenarios() -> list[str]:
    """Names of the planted teeth mutants (the boundary scenarios)."""
    return [m.name for m in TEETH.mutants]


# ---------------------------------------------------------------------------
# Cross-check: the harness's own CursorPaginator must traverse the frozen
# dataset to exactly the expected concatenation (proves the oracle models the
# real paginator, not just an isolated function).
# ---------------------------------------------------------------------------

def _cursor_paginator_ids(limit: int = 3) -> list[int]:
    store = BackingStore()
    for r in _FROZEN_RECORDS:
        store.add(dict(r))
    items, _results = CursorPaginator(store).all_pages(limit=limit)
    return [r["id"] for r in items]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/pagination")

    # 1. The correct oracle reproduces the frozen expected concatenation exactly.
    report.add("oracle_concatenation", list(_EXPECTED_IDS), _traverse(oracle_page))

    # 2. The harness's real CursorPaginator traverses the same dataset identically
    #    (every item once, in order) for several page sizes — no dup/skip.
    for lim in (1, 2, 3, 7):
        report.add(f"cursor_paginator_traversal:limit={lim}",
                   list(_EXPECTED_IDS), _cursor_paginator_ids(limit=lim))

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pagination / cursor consistency controls")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the planted teeth boundary scenarios")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
