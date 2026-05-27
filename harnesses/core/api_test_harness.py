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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ApiTestCase:
    name: str
    method: str
    path: str
    body: Optional[Any] = None
    headers: Optional[Dict[str, str]] = None
    expected_status: Optional[int] = None
    expected_content_type: Optional[str] = None
    expected_schema: Optional[Dict[str, Any]] = None
    expected_headers: Optional[Dict[str, str]] = None
    validator: Optional[Callable[[Any], Optional[str]]] = None


@dataclass
class ApiTestResult:
    name: str
    passed: bool
    status_code: Optional[int] = None
    response_body: Any = None
    response_headers: Optional[Dict[str, str]] = None
    duration_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class ApiSuiteReport:
    total: int
    passed: int
    failed: int
    results: List[ApiTestResult] = field(default_factory=list)
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

    def validate(self, data: Any, schema: Dict[str, Any], path: str = "$") -> None:
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
    def __init__(self, base_url: str, default_headers: Optional[Dict[str, str]] = None):
        self.base_url = base_url.rstrip("/")
        self.default_headers = default_headers or {}

    def build(
        self,
        method: str,
        path: str,
        body: Any = None,
        extra_headers: Optional[Dict[str, str]] = None,
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

    def validate_status(self, actual: int, expected: int) -> Optional[str]:
        if actual != expected:
            return f"status {actual} != expected {expected}"
        return None

    def validate_content_type(self, actual: str, expected: str) -> Optional[str]:
        # Compare only the media type portion, ignore parameters
        actual_base = actual.split(";")[0].strip().lower()
        expected_base = expected.split(";")[0].strip().lower()
        if actual_base != expected_base:
            return f"content-type '{actual_base}' != expected '{expected_base}'"
        return None

    def validate_schema(self, data: Any, schema: Dict[str, Any]) -> Optional[str]:
        try:
            self._schema_checker.validate(data, schema)
            return None
        except SchemaError as e:
            return str(e)

    def validate_headers(
        self, actual: Dict[str, str], expected: Dict[str, str]
    ) -> Optional[str]:
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

class ApiTestSuite:
    def __init__(self, base_url: str, default_headers: Optional[Dict[str, str]] = None):
        self.base_url = base_url
        self._builder = RequestBuilder(base_url, default_headers)
        self._validator = ResponseValidator()
        self._cases: List[ApiTestCase] = []

    def add(self, case: ApiTestCase) -> None:
        self._cases.append(case)

    def run(self) -> ApiSuiteReport:
        results = []
        suite_start = time.monotonic()
        for case in self._cases:
            results.append(self._run_case(case))
        total_ms = (time.monotonic() - suite_start) * 1000
        passed = sum(1 for r in results if r.passed)
        return ApiSuiteReport(
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            results=results,
            duration_ms=total_ms,
        )

    def _run_case(self, case: ApiTestCase) -> ApiTestResult:
        start = time.monotonic()
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

            duration_ms = (time.monotonic() - start) * 1000

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
            duration_ms = (time.monotonic() - start) * 1000
            return ApiTestResult(
                name=case.name,
                passed=False,
                duration_ms=duration_ms,
                error=f"exception: {exc}",
            )


# ---------------------------------------------------------------------------
# Mock API server
# ---------------------------------------------------------------------------

_items: Dict[int, Dict[str, Any]] = {}
_next_id = 1
_request_counts: Dict[str, int] = {}
_rate_limit = 10  # requests per key per window
_RATE_WINDOW = 60


class MockApiHandler(http.server.BaseHTTPRequestHandler):
    """Minimal REST API: /items CRUD + /auth + /paginate + /rate-limited."""

    def log_message(self, fmt, *args):  # suppress access logs
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int, data: Any, extra_headers: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _parse_path(self) -> Tuple[str, Dict[str, str]]:
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
        s.bind(("", 0))
        return s.getsockname()[1]


def start_mock_server(port: int = 0) -> Tuple[http.server.HTTPServer, int]:
    if port == 0:
        port = _find_free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), MockApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def reset_server_state() -> None:
    global _next_id, _request_counts
    _items.clear()
    _next_id = 1
    _request_counts.clear()


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

    ok = report.passed == report.total
    status = "PASS" if ok else "FAIL"
    print(f"[self-test] {status}: {report.passed}/{report.total} tests passed")
    for r in report.results:
        mark = "." if r.passed else "F"
        print(f"  {mark} {r.name}" + (f" — {r.error}" if r.error else ""))
    return ok


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="API/REST Test Harness")
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--port", type=int, default=18900, help="Mock server port")
    p.add_argument("--target", type=str, help="Target base URL to test")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.self_test:
        ok = _self_test(args.port)
        sys.exit(0 if ok else 1)
    elif args.target:
        suite = ApiTestSuite(args.target)
        report = suite.run()
        print(f"Results: {report.passed}/{report.total} passed")
        sys.exit(0 if report.failed == 0 else 1)
    else:
        print("Start mock server on port", args.port)
        server, port = start_mock_server(args.port)
        print(f"Mock API server listening on http://127.0.0.1:{port}")
        print("Press Ctrl-C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
