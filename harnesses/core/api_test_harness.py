"""
API / REST Test Harness — Harness 2/36

Tests REST API correctness: request/response validation, status codes,
content types, header checks, JSON schema verification, CRUD lifecycle,
pagination, error handling, and rate limiting.

Port: 18900
Self-test: python3 api_test_harness.py --self-test
"""

import argparse
import http.server
import json
import re
import socket
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ApiTestCase:
    name: str
    method: str
    path: str
    body: Any | None = None
    headers: dict[str, str] | None = None
    expected_status: int | None = None
    expected_content_type: str | None = None
    expected_schema: dict[str, Any] | None = None
    expected_headers: dict[str, str] | None = None
    validator: Callable[[Any], str | None] | None = None


@dataclass
class ApiTestResult:
    name: str
    passed: bool
    status_code: int | None = None
    response_body: Any = None
    response_headers: dict[str, str] | None = None
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class ApiSuiteReport:
    total: int
    passed: int
    failed: int
    results: list[ApiTestResult] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


# ---------------------------------------------------------------------------
# Schema checker
# ---------------------------------------------------------------------------

class SchemaError(Exception):
    pass


class SchemaChecker:
    """Validates JSON response bodies against a simple schema definition.

    Schema format:
        {
          "type": "object",              # object | array | string | number | boolean | null
          "required": ["id", "name"],   # required keys (object only)
          "properties": {               # nested schema per key
              "id":   {"type": "number"},
              "name": {"type": "string"},
              "tags": {"type": "array",  "items": {"type": "string"}},
          },
          "items": {...}                 # item schema for array type
        }
    """

    TYPE_MAP = {
        "string":  str,
        "number":  (int, float),
        "integer": int,
        "boolean": bool,
        "null":    type(None),
    }

    def validate(self, data: Any, schema: dict[str, Any], path: str = "$") -> None:
        schema_type = schema.get("type")
        if schema_type:
            self._check_type(data, schema_type, path)

        if schema_type == "object" or isinstance(data, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in data:
                    raise SchemaError(f"{path}: required key '{key}' missing")
            properties = schema.get("properties", {})
            for key, sub_schema in properties.items():
                if key in data:
                    self.validate(data[key], sub_schema, f"{path}.{key}")

        if schema_type == "array" or isinstance(data, list):
            items_schema = schema.get("items")
            if items_schema:
                for i, item in enumerate(data):
                    self.validate(item, items_schema, f"{path}[{i}]")

    def _check_type(self, data: Any, schema_type: str, path: str) -> None:
        if schema_type == "object":
            if not isinstance(data, dict):
                raise SchemaError(f"{path}: expected object, got {type(data).__name__}")
        elif schema_type == "array":
            if not isinstance(data, list):
                raise SchemaError(f"{path}: expected array, got {type(data).__name__}")
        elif schema_type in self.TYPE_MAP:
            expected = self.TYPE_MAP[schema_type]
            if not isinstance(data, expected):
                raise SchemaError(
                    f"{path}: expected {schema_type}, got {type(data).__name__}"
                )
        else:
            raise SchemaError(f"{path}: unknown schema type '{schema_type}'")


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------

class RequestBuilder:
    def __init__(self, base_url: str, default_headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.default_headers = default_headers or {}

    def build(
        self,
        method: str,
        path: str,
        body: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        url = self.base_url + path
        headers = {**self.default_headers}
        if extra_headers:
            headers.update(extra_headers)

        data = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            data = encoded
            headers.setdefault("Content-Type", "application/json")
            headers["Content-Length"] = str(len(encoded))

        req = urllib.request.Request(url, data=data, method=method)
        for k, v in headers.items():
            req.add_header(k, v)
        return req


# ---------------------------------------------------------------------------
# Response validator
# ---------------------------------------------------------------------------

class ResponseValidator:
    def __init__(self):
        self._schema_checker = SchemaChecker()

    def validate_status(self, actual: int, expected: int) -> str | None:
        if actual != expected:
            return f"status {actual} != expected {expected}"
        return None

    def validate_content_type(self, actual: str, expected: str) -> str | None:
        # Compare only the media type portion, ignore parameters
        actual_base = actual.split(";")[0].strip().lower()
        expected_base = expected.split(";")[0].strip().lower()
        if actual_base != expected_base:
            return f"content-type '{actual_base}' != expected '{expected_base}'"
        return None

    def validate_schema(self, data: Any, schema: dict[str, Any]) -> str | None:
        try:
            self._schema_checker.validate(data, schema)
            return None
        except SchemaError as e:
            return str(e)

    def validate_headers(
        self, actual: dict[str, str], expected: dict[str, str]
    ) -> str | None:
        for key, expected_val in expected.items():
            actual_val = actual.get(key.lower()) or actual.get(key)
            if actual_val is None:
                return f"header '{key}' missing"
            if expected_val and actual_val != expected_val:
                return f"header '{key}': '{actual_val}' != '{expected_val}'"
        return None


# ---------------------------------------------------------------------------
# API test suite runner
# ---------------------------------------------------------------------------

def _elapsed_ms(start_ns: int) -> float:
    elapsed = (time.perf_counter_ns() - start_ns) / 1_000_000
    return max(elapsed, 0.001)


class ApiTestSuite:
    def __init__(self, base_url: str, default_headers: dict[str, str] | None = None):
        self.base_url = base_url
        self._builder = RequestBuilder(base_url, default_headers)
        self._validator = ResponseValidator()
        self._cases: list[ApiTestCase] = []

    def add(self, case: ApiTestCase) -> None:
        self._cases.append(case)

    def run(self) -> ApiSuiteReport:
        results = []
        suite_start = time.perf_counter_ns()
        for case in self._cases:
            results.append(self._run_case(case))
        total_ms = _elapsed_ms(suite_start)
        passed = sum(1 for r in results if r.passed)
        return ApiSuiteReport(
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            results=results,
            duration_ms=total_ms,
        )

    def _run_case(self, case: ApiTestCase) -> ApiTestResult:
        start = time.perf_counter_ns()
        try:
            req = self._builder.build(
                method=case.method,
                path=case.path,
                body=case.body,
                extra_headers=case.headers,
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status = resp.status
                    resp_headers = dict(resp.headers)
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                status = e.code
                resp_headers = dict(e.headers)
                raw = e.read()

            duration_ms = _elapsed_ms(start)

            content_type = resp_headers.get("Content-Type", resp_headers.get("content-type", ""))
            body = None
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = raw.decode("utf-8", errors="replace")

            # Normalise header keys to lowercase
            norm_headers = {k.lower(): v for k, v in resp_headers.items()}

            errors = []
            if case.expected_status is not None:
                e = self._validator.validate_status(status, case.expected_status)
                if e:
                    errors.append(e)
            if case.expected_content_type is not None:
                e = self._validator.validate_content_type(content_type, case.expected_content_type)
                if e:
                    errors.append(e)
            if case.expected_schema is not None and body is not None:
                e = self._validator.validate_schema(body, case.expected_schema)
                if e:
                    errors.append(e)
            if case.expected_headers is not None:
                e = self._validator.validate_headers(norm_headers, case.expected_headers)
                if e:
                    errors.append(e)
            if case.validator is not None:
                e = case.validator(body)
                if e:
                    errors.append(e)

            passed = len(errors) == 0
            return ApiTestResult(
                name=case.name,
                passed=passed,
                status_code=status,
                response_body=body,
                response_headers=norm_headers,
                duration_ms=duration_ms,
                error="; ".join(errors) if errors else None,
            )

        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            return ApiTestResult(
                name=case.name,
                passed=False,
                duration_ms=duration_ms,
                error=f"exception: {exc}",
            )


# ---------------------------------------------------------------------------
# Mock API server
# ---------------------------------------------------------------------------

_items: dict[int, dict[str, Any]] = {}
_next_id = 1
_request_counts: dict[str, int] = {}
_rate_limit = 10  # requests per key per window
_RATE_WINDOW = 60


class MockApiHandler(http.server.BaseHTTPRequestHandler):
    """Minimal REST API: /items CRUD + /auth + /paginate + /rate-limited."""

    def log_message(self, fmt, *args):  # suppress access logs
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int, data: Any, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _parse_path(self) -> tuple[str, dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        return parsed.path, qs

    def do_GET(self):
        path, qs = self._parse_path()

        if path == "/health":
            self._send_json(200, {"status": "ok"})

        elif path == "/items":
            page = int(qs.get("page", 1))
            page_size = int(qs.get("page_size", 5))
            all_items = sorted(_items.values(), key=lambda x: x["id"])
            start = (page - 1) * page_size
            slice_ = all_items[start: start + page_size]
            self._send_json(200, {
                "items": slice_,
                "total": len(all_items),
                "page": page,
                "page_size": page_size,
                "pages": max(1, -(-len(all_items) // page_size)),
            })

        elif re.match(r"^/items/(\d+)$", path):
            item_id = int(re.match(r"^/items/(\d+)$", path).group(1))
            if item_id in _items:
                self._send_json(200, _items[item_id])
            else:
                self._send_json(404, {"error": "not found"})

        elif path == "/auth/me":
            auth = self.headers.get("Authorization", "")
            if auth == "Bearer valid-token":
                self._send_json(200, {"user_id": 1, "role": "admin"})
            elif auth:
                self._send_json(403, {"error": "forbidden"})
            else:
                self._send_json(401, {"error": "unauthorized"})

        elif path == "/rate-limited":
            key = self.headers.get("X-API-Key", "anonymous")
            count = _request_counts.get(key, 0) + 1
            _request_counts[key] = count
            if count > _rate_limit:
                self._send_json(
                    429,
                    {"error": "rate limit exceeded"},
                    {"Retry-After": "60", "X-RateLimit-Limit": str(_rate_limit)},
                )
            else:
                self._send_json(200, {"count": count}, {
                    "X-RateLimit-Limit": str(_rate_limit),
                    "X-RateLimit-Remaining": str(_rate_limit - count),
                })

        elif path == "/content":
            accept = self.headers.get("Accept", "application/json")
            if "application/xml" in accept:
                body = b"<response><message>hello</message></response>"
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(200, {"message": "hello"})

        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path, _ = self._parse_path()
        global _next_id

        if path == "/items":
            raw = self._read_body()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON"})
                return

            if "name" not in data:
                self._send_json(422, {"error": "name is required"})
                return

            item_id = _next_id
            _next_id += 1
            item = {"id": item_id, "name": data["name"], "value": data.get("value", 0)}
            _items[item_id] = item
            self._send_json(201, item, {"Location": f"/items/{item_id}"})

        else:
            self._send_json(404, {"error": "not found"})

    def do_PUT(self):
        path, _ = self._parse_path()
        m = re.match(r"^/items/(\d+)$", path)
        if m:
            item_id = int(m.group(1))
            if item_id not in _items:
                self._send_json(404, {"error": "not found"})
                return
            raw = self._read_body()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON"})
                return
            _items[item_id].update(data)
            _items[item_id]["id"] = item_id  # preserve id
            self._send_json(200, _items[item_id])
        else:
            self._send_json(404, {"error": "not found"})

    def do_PATCH(self):
        self.do_PUT()

    def do_DELETE(self):
        path, _ = self._parse_path()
        m = re.match(r"^/items/(\d+)$", path)
        if m:
            item_id = int(m.group(1))
            if item_id in _items:
                del _items[item_id]
                self.send_response(204)
                self.end_headers()
            else:
                self._send_json(404, {"error": "not found"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_HEAD(self):
        path, _ = self._parse_path()
        if path == "/items":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Allow", "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_accepting(host: str, port: int, timeout: float = 3.0) -> None:
    """Block until the listener accepts a connection, or timeout elapses.

    Closes the CI race where serve_forever() has not yet bound/listened by the
    time start_mock_server() returns and a client connects.
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


def start_mock_server(port: int = 0) -> tuple[http.server.HTTPServer, int]:
    if port == 0:
        port = _find_free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), MockApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_accepting(server.server_address[0], server.server_address[1])
    return server, port


def reset_server_state() -> None:
    global _next_id, _request_counts
    _items.clear()
    _next_id = 1
    _request_counts.clear()


# ---------------------------------------------------------------------------
# TEETH: in-process oracle handler + planted buggy twins.
#
# The networked MockApiHandler above is exercised over a real socket by the
# legacy self-test and the paired unittest. The teeth, by contrast, run a PURE
# in-process model of the same REST contract so the gate can verify "this
# harness catches a real API bug" with zero clock/network/filesystem I/O and
# full determinism (no autoincrement id race, no port binding).
#
# A handler impl maps a frozen ApiRequest to a HandledResponse. The oracle is
# the correct handler; each Mutant is a faithful real-world API defect. The
# auditor reuses the harness's own ResponseValidator/SchemaChecker to judge a
# response against the case's FROZEN expectations -- prove() never compares an
# impl to the oracle object, so the check is non-circular.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    body: Any | None = None
    headers: tuple[tuple[str, str], ...] | None = None

    def header(self, key: str) -> str | None:
        if not self.headers:
            return None
        for k, v in self.headers:
            if k.lower() == key.lower():
                return v
        return None


@dataclass(frozen=True)
class HandledResponse:
    status: int
    body: Any = None
    headers: tuple[tuple[str, str], ...] = ()

    def header(self, key: str) -> str | None:
        for k, v in self.headers:
            if k.lower() == key.lower():
                return v
        return None


# The handler operates over a tiny in-memory store seeded deterministically so
# the corpus expectations are stable across runs (no global mutable id counter).
_SEED_ITEMS: dict[int, dict[str, Any]] = {
    1: {"id": 1, "name": "seed-widget", "value": 7},
}


def oracle_handle(req: ApiRequest) -> HandledResponse:
    """Correct in-process REST handler — the contract MockApiHandler implements.

    A new item is always assigned id 2 (the store is seeded with id 1) so create
    responses are deterministic and the corpus can freeze the Location header.
    """
    store = dict(_SEED_ITEMS)
    if req.method == "GET" and req.path == "/health":
        return HandledResponse(200, {"status": "ok"},
                               (("Content-Type", "application/json"),))

    if req.method == "POST" and req.path == "/items":
        data = req.body if isinstance(req.body, dict) else {}
        if "name" not in data:  # required field
            return HandledResponse(422, {"error": "name is required"},
                                   (("Content-Type", "application/json"),))
        new_id = max(store) + 1
        item = {"id": new_id, "name": data["name"], "value": data.get("value", 0)}
        return HandledResponse(
            201, item,
            (("Content-Type", "application/json"), ("Location", f"/items/{new_id}")),
        )

    m = re.match(r"^/items/(\d+)$", req.path)
    if req.method == "GET" and m:
        item_id = int(m.group(1))
        if item_id in store:
            return HandledResponse(200, store[item_id],
                                   (("Content-Type", "application/json"),))
        return HandledResponse(404, {"error": "not found"},
                               (("Content-Type", "application/json"),))

    if req.method == "DELETE" and m:
        item_id = int(m.group(1))
        if item_id in store:
            return HandledResponse(204, None, ())
        return HandledResponse(404, {"error": "not found"},
                               (("Content-Type", "application/json"),))

    return HandledResponse(404, {"error": "not found"},
                           (("Content-Type", "application/json"),))


# --- Planted buggy twins (each models a real, common REST defect) ----------

def handle_status_200_on_create(req: ApiRequest) -> HandledResponse:
    """BUG: returns 200 OK instead of 201 Created on resource creation.

    A real and common framework misconfiguration (e.g. a controller that returns
    the object without setting the created status). Breaks clients that key off
    201 to distinguish create from idempotent replace.
    """
    resp = oracle_handle(req)
    if req.method == "POST" and req.path == "/items" and resp.status == 201:
        return HandledResponse(200, resp.body, resp.headers)
    return resp


def handle_missing_location_header(req: ApiRequest) -> HandledResponse:
    """BUG: omits the Location header on 201 Created.

    RFC 7231 requires a 201 to carry Location pointing at the new resource;
    omitting it is a frequent API regression that breaks redirect-follow clients.
    """
    resp = oracle_handle(req)
    if req.method == "POST" and req.path == "/items" and resp.status == 201:
        stripped = tuple((k, v) for k, v in resp.headers if k.lower() != "location")
        return HandledResponse(resp.status, resp.body, stripped)
    return resp


def handle_accepts_missing_required(req: ApiRequest) -> HandledResponse:
    """BUG: creates an item even when the required 'name' field is absent.

    Missing server-side required-field validation — a classic input-validation
    gap that lets malformed records into the store with 201 instead of 422.
    """
    if req.method == "POST" and req.path == "/items":
        data = req.body if isinstance(req.body, dict) else {}
        if "name" not in data:
            store = dict(_SEED_ITEMS)
            new_id = max(store) + 1
            item = {"id": new_id, "name": data.get("name", ""), "value": data.get("value", 0)}
            return HandledResponse(
                201, item,
                (("Content-Type", "application/json"), ("Location", f"/items/{new_id}")),
            )
    return oracle_handle(req)


# --- Frozen corpus: request -> expected response --------------------------

@dataclass(frozen=True)
class ApiOracleCase:
    name: str
    request: ApiRequest
    expected_status: int
    expected_content_type: str | None = None
    expected_schema: dict[str, Any] | None = None
    expected_headers: dict[str, str] | None = None
    note: str = ""


ORACLE_CASES: tuple[ApiOracleCase, ...] = (
    ApiOracleCase(
        "health",
        ApiRequest("GET", "/health"),
        expected_status=200,
        expected_content_type="application/json",
        expected_schema={"type": "object", "required": ["status"]},
        note="liveness endpoint",
    ),
    # The teeth case for the 201/Location/required-field mutants: a well-formed
    # create MUST return 201 + a Location header.
    ApiOracleCase(
        "create_returns_201_with_location",
        ApiRequest("POST", "/items", body={"name": "widget", "value": 42}),
        expected_status=201,
        expected_content_type="application/json",
        expected_schema={"type": "object", "required": ["id", "name", "value"]},
        expected_headers={"location": "/items/2"},
        note="RFC 7231: create -> 201 Created + Location header",
    ),
    # The teeth case for the missing-required-field mutant: a create with no
    # 'name' MUST be rejected with 422, not silently accepted.
    ApiOracleCase(
        "create_missing_name_rejected",
        ApiRequest("POST", "/items", body={"value": 5}),
        expected_status=422,
        expected_content_type="application/json",
        note="required-field validation: missing name -> 422",
    ),
    ApiOracleCase(
        "get_seeded_item",
        ApiRequest("GET", "/items/1"),
        expected_status=200,
        expected_content_type="application/json",
        expected_schema={"type": "object", "required": ["id", "name"]},
        note="read of the seeded resource",
    ),
    ApiOracleCase(
        "get_missing_item_404",
        ApiRequest("GET", "/items/9999"),
        expected_status=404,
        note="unknown id -> 404",
    ),
)


def _audit_response(resp: HandledResponse, case: ApiOracleCase) -> list[str]:
    """Judge a HandledResponse against a frozen case using the harness's own
    ResponseValidator/SchemaChecker. Returns a list of failure strings (empty
    means the response satisfies every frozen expectation)."""
    validator = ResponseValidator()
    errors: list[str] = []

    e = validator.validate_status(resp.status, case.expected_status)
    if e:
        errors.append(e)

    if case.expected_content_type is not None:
        ct = resp.header("Content-Type") or ""
        e = validator.validate_content_type(ct, case.expected_content_type)
        if e:
            errors.append(e)

    if case.expected_schema is not None:
        if resp.body is None:
            errors.append("body missing for expected schema")
        else:
            e = validator.validate_schema(resp.body, case.expected_schema)
            if e:
                errors.append(e)

    if case.expected_headers is not None:
        actual = {k.lower(): v for k, v in resp.headers}
        e = validator.validate_headers(actual, case.expected_headers)
        if e:
            errors.append(e)

    return errors


def prove(impl: Callable[[ApiRequest], HandledResponse]) -> bool:
    """True iff handler ``impl`` MISHANDLES any frozen corpus case.

    Non-circular: each impl response is judged against the case's frozen
    expected status/headers/schema (never against the oracle object). A handler
    that raises on a corpus case counts as caught.
    """
    for case in ORACLE_CASES:
        try:
            resp = impl(case.request)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if _audit_response(resp, case):
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_handle"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_handle,
    mutants=(
        Mutant("status_200_on_create", handle_status_200_on_create,
               "returns 200 OK instead of 201 Created on resource creation"),
        Mutant("missing_location_header", handle_missing_location_header,
               "omits the RFC 7231 Location header on a 201 Created"),
        Mutant("accepts_missing_required", handle_accepts_missing_required,
               "creates an item without the required 'name' field (201 not 422)"),
    ),
    corpus_size=len(ORACLE_CASES),
    kind="oracle_swap",
    notes="a create must be 201 + Location and a missing required field must be 422",
)


def list_oracle_cases() -> list[str]:
    return [c.name for c in ORACLE_CASES]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test(port: int = 18900) -> bool:
    server, actual_port = start_mock_server(port)
    base = f"http://127.0.0.1:{actual_port}"
    reset_server_state()
    suite = ApiTestSuite(base)

    # Health check
    suite.add(ApiTestCase(
        name="health",
        method="GET",
        path="/health",
        expected_status=200,
        expected_content_type="application/json",
        expected_schema={"type": "object", "required": ["status"]},
    ))

    # CRUD: create
    suite.add(ApiTestCase(
        name="create_item",
        method="POST",
        path="/items",
        body={"name": "widget", "value": 42},
        expected_status=201,
        expected_schema={"type": "object", "required": ["id", "name", "value"]},
    ))

    # CRUD: list
    suite.add(ApiTestCase(
        name="list_items",
        method="GET",
        path="/items",
        expected_status=200,
        expected_schema={
            "type": "object",
            "required": ["items", "total"],
            "properties": {"items": {"type": "array"}},
        },
    ))

    # Pagination
    suite.add(ApiTestCase(
        name="pagination",
        method="GET",
        path="/items?page=1&page_size=2",
        expected_status=200,
        expected_schema={"type": "object", "required": ["page", "page_size", "pages"]},
    ))

    # Auth: missing token
    suite.add(ApiTestCase(
        name="auth_missing_token",
        method="GET",
        path="/auth/me",
        expected_status=401,
    ))

    # Auth: valid token
    suite.add(ApiTestCase(
        name="auth_valid_token",
        method="GET",
        path="/auth/me",
        headers={"Authorization": "Bearer valid-token"},
        expected_status=200,
        expected_schema={"type": "object", "required": ["user_id", "role"]},
    ))

    # Content negotiation
    suite.add(ApiTestCase(
        name="content_json",
        method="GET",
        path="/content",
        expected_status=200,
        expected_content_type="application/json",
    ))

    report = suite.run()
    server.shutdown()
    server.server_close()

    ok = report.passed == report.total
    status = "PASS" if ok else "FAIL"
    print(f"[self-test] {status}: {report.passed}/{report.total} tests passed")
    for r in report.results:
        mark = "." if r.passed else "F"
        print(f"  {mark} {r.name}" + (f" — {r.error}" if r.error else ""))
    return ok


def _run_self_test(as_json: bool = False, *, networked: bool = True, port: int = 18900) -> int:
    """Report-based self-test: fail loud, report structured findings.

    1. The pure in-process oracle handler satisfies every frozen corpus case.
    2. Teeth: the oracle is clean and every planted mutant is caught.
    3. (optional) A live socket smoke test of MockApiHandler — skipped under
       --no-network so the teeth/oracle checks stay pure and offline.
    """
    report = Report("core/api")

    # 1. The correct oracle handler agrees with every frozen expectation.
    for case in ORACLE_CASES:
        resp = oracle_handle(case.request)
        report.record(f"oracle_case:{case.name}", not _audit_response(resp, case),
                      detail=case.note)

    # 2. Teeth: oracle is not flagged and every planted mutant IS flagged.
    report.assert_teeth(TEETH)

    # 3. Live mock-server smoke test (uses a socket; opt-out for pure runs).
    if networked:
        report.record("mock_server_smoke", _self_test(port), detail="MockApiHandler over a socket")

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="API/REST Test Harness")
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true",
                   help="list the frozen oracle corpus case names")
    p.add_argument("--no-network", action="store_true",
                   help="skip the live socket smoke test (teeth/oracle checks only)")
    p.add_argument("--port", type=int, default=18900, help="Mock server port")
    p.add_argument("--target", type=str, help="Target base URL to test")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_oracle_cases()))
        return 0
    if args.target:
        suite = ApiTestSuite(args.target)
        report = suite.run()
        print(f"Results: {report.passed}/{report.total} passed")
        return 0 if report.failed == 0 else 1
    if args.self_test or args.json:
        return _run_self_test(as_json=args.json, networked=not args.no_network, port=args.port)
    # Default: run the self-test (repo convention).
    return _run_self_test(networked=not args.no_network, port=args.port)


if __name__ == "__main__":
    sys.exit(main())
