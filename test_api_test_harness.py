"""Tests for api_test_harness.py — 64 tests."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from api_test_harness import (
    ApiTestCase,
    ApiTestResult,
    ApiTestSuite,
    ApiSuiteReport,
    MockApiHandler,
    RequestBuilder,
    ResponseValidator,
    SchemaChecker,
    SchemaError,
    reset_server_state,
    start_mock_server,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_server():
    server, port = start_mock_server(0)
    reset_server_state()
    return server, port, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# SchemaChecker tests
# ---------------------------------------------------------------------------

class TestSchemaChecker(unittest.TestCase):
    def setUp(self):
        self.sc = SchemaChecker()

    def test_object_valid(self):
        self.sc.validate({"id": 1, "name": "x"}, {
            "type": "object",
            "required": ["id", "name"],
            "properties": {
                "id": {"type": "number"},
                "name": {"type": "string"},
            },
        })

    def test_object_missing_required(self):
        with self.assertRaises(SchemaError) as ctx:
            self.sc.validate({"id": 1}, {
                "type": "object",
                "required": ["id", "name"],
            })
        self.assertIn("name", str(ctx.exception))

    def test_object_wrong_type(self):
        with self.assertRaises(SchemaError):
            self.sc.validate([1, 2], {"type": "object"})

    def test_array_valid(self):
        self.sc.validate([1, 2, 3], {"type": "array", "items": {"type": "number"}})

    def test_array_wrong_item_type(self):
        with self.assertRaises(SchemaError):
            self.sc.validate(["a", "b"], {"type": "array", "items": {"type": "number"}})

    def test_array_wrong_type(self):
        with self.assertRaises(SchemaError):
            self.sc.validate("not-array", {"type": "array"})

    def test_string_valid(self):
        self.sc.validate("hello", {"type": "string"})

    def test_string_wrong_type(self):
        with self.assertRaises(SchemaError):
            self.sc.validate(42, {"type": "string"})

    def test_number_valid(self):
        self.sc.validate(3.14, {"type": "number"})

    def test_number_int_valid(self):
        self.sc.validate(5, {"type": "number"})

    def test_boolean_valid(self):
        self.sc.validate(True, {"type": "boolean"})

    def test_boolean_wrong_type(self):
        with self.assertRaises(SchemaError):
            self.sc.validate(1, {"type": "boolean"})

    def test_null_valid(self):
        self.sc.validate(None, {"type": "null"})

    def test_null_wrong_type(self):
        with self.assertRaises(SchemaError):
            self.sc.validate(0, {"type": "null"})

    def test_nested_object(self):
        self.sc.validate(
            {"user": {"id": 1, "name": "Alice"}},
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "required": ["id", "name"],
                    }
                },
            },
        )

    def test_unknown_type_raises(self):
        with self.assertRaises(SchemaError):
            self.sc.validate("x", {"type": "unknowntype"})

    def test_no_type_object_skips_type_check(self):
        # No type key — should not raise for any value
        self.sc.validate({"a": 1}, {"required": ["a"]})

    def test_path_reported_in_error(self):
        with self.assertRaises(SchemaError) as ctx:
            self.sc.validate(
                {"items": ["not-a-number"]},
                {
                    "type": "object",
                    "properties": {
                        "items": {"type": "array", "items": {"type": "number"}}
                    },
                },
            )
        self.assertIn("items[0]", str(ctx.exception))


# ---------------------------------------------------------------------------
# ResponseValidator tests
# ---------------------------------------------------------------------------

class TestResponseValidator(unittest.TestCase):
    def setUp(self):
        self.rv = ResponseValidator()

    def test_status_match(self):
        self.assertIsNone(self.rv.validate_status(200, 200))

    def test_status_mismatch(self):
        result = self.rv.validate_status(404, 200)
        self.assertIsNotNone(result)
        self.assertIn("404", result)

    def test_content_type_match(self):
        self.assertIsNone(
            self.rv.validate_content_type("application/json", "application/json")
        )

    def test_content_type_with_charset(self):
        self.assertIsNone(
            self.rv.validate_content_type(
                "application/json; charset=utf-8", "application/json"
            )
        )

    def test_content_type_mismatch(self):
        result = self.rv.validate_content_type("text/plain", "application/json")
        self.assertIsNotNone(result)

    def test_schema_valid(self):
        self.assertIsNone(
            self.rv.validate_schema({"id": 1}, {"type": "object", "required": ["id"]})
        )

    def test_schema_invalid(self):
        result = self.rv.validate_schema(
            {}, {"type": "object", "required": ["id"]}
        )
        self.assertIsNotNone(result)

    def test_headers_match(self):
        self.assertIsNone(
            self.rv.validate_headers(
                {"content-type": "application/json"},
                {"content-type": "application/json"},
            )
        )

    def test_headers_missing(self):
        result = self.rv.validate_headers({}, {"x-custom": "value"})
        self.assertIsNotNone(result)
        self.assertIn("x-custom", result)


# ---------------------------------------------------------------------------
# RequestBuilder tests
# ---------------------------------------------------------------------------

class TestRequestBuilder(unittest.TestCase):
    def test_get_request(self):
        rb = RequestBuilder("http://localhost:8000")
        req = rb.build("GET", "/items")
        self.assertEqual(req.full_url, "http://localhost:8000/items")
        self.assertEqual(req.method, "GET")

    def test_post_with_body(self):
        rb = RequestBuilder("http://localhost:8000")
        req = rb.build("POST", "/items", body={"name": "test"})
        self.assertEqual(req.method, "POST")
        self.assertIn("application/json", req.get_header("Content-type"))

    def test_default_headers_applied(self):
        rb = RequestBuilder("http://localhost:8000", {"X-Token": "secret"})
        req = rb.build("GET", "/")
        self.assertEqual(req.get_header("X-token"), "secret")

    def test_extra_headers_override(self):
        rb = RequestBuilder("http://localhost:8000", {"Accept": "text/plain"})
        req = rb.build("GET", "/", extra_headers={"Accept": "application/json"})
        self.assertEqual(req.get_header("Accept"), "application/json")


# ---------------------------------------------------------------------------
# Mock server + ApiTestSuite integration tests
# ---------------------------------------------------------------------------

class TestMockServerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port, cls.base = _get_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        reset_server_state()
        self.suite = ApiTestSuite(self.base)

    def _run_one(self, case: ApiTestCase) -> ApiTestResult:
        self.suite.add(case)
        report = self.suite.run()
        return report.results[0]

    # Health
    def test_health_endpoint(self):
        r = self._run_one(ApiTestCase("health", "GET", "/health", expected_status=200))
        self.assertTrue(r.passed)

    def test_health_content_type(self):
        r = self._run_one(ApiTestCase(
            "health_ct", "GET", "/health",
            expected_status=200,
            expected_content_type="application/json",
        ))
        self.assertTrue(r.passed)

    # CRUD: create
    def test_create_item(self):
        r = self._run_one(ApiTestCase(
            "create", "POST", "/items",
            body={"name": "foo", "value": 1},
            expected_status=201,
        ))
        self.assertTrue(r.passed)

    def test_create_item_schema(self):
        r = self._run_one(ApiTestCase(
            "create_schema", "POST", "/items",
            body={"name": "bar"},
            expected_status=201,
            expected_schema={"type": "object", "required": ["id", "name", "value"]},
        ))
        self.assertTrue(r.passed)

    def test_create_sets_location_header(self):
        r = self._run_one(ApiTestCase(
            "create_location", "POST", "/items",
            body={"name": "baz"},
            expected_status=201,
            expected_headers={"location": ""},
        ))
        self.assertTrue(r.passed)
        self.assertIn("/items/", r.response_headers.get("location", ""))

    def test_create_missing_name_returns_422(self):
        r = self._run_one(ApiTestCase(
            "create_bad", "POST", "/items",
            body={"value": 5},
            expected_status=422,
        ))
        self.assertTrue(r.passed)

    def test_create_bad_json_returns_400(self):
        # Send raw non-JSON bytes via low-level request
        import socket as _socket
        s = _socket.create_connection(("127.0.0.1", self.port))
        body = b"notjson"
        req = (
            b"POST /items HTTP/1.0\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
            + body
        )
        s.sendall(req)
        response = s.recv(4096).decode()
        s.close()
        self.assertIn("400", response)

    # CRUD: read
    def test_get_item(self):
        # Create first
        create_req = urllib.request.Request(
            f"{self.base}/items",
            data=json.dumps({"name": "getme"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(create_req) as resp:
            created = json.loads(resp.read())
        item_id = created["id"]

        r = self._run_one(ApiTestCase(
            "get_item", "GET", f"/items/{item_id}", expected_status=200,
        ))
        self.assertTrue(r.passed)

    def test_get_nonexistent_item_404(self):
        r = self._run_one(ApiTestCase(
            "get_404", "GET", "/items/99999", expected_status=404,
        ))
        self.assertTrue(r.passed)

    # CRUD: list and pagination
    def test_list_items_empty(self):
        r = self._run_one(ApiTestCase(
            "list_empty", "GET", "/items", expected_status=200,
        ))
        self.assertTrue(r.passed)
        self.assertEqual(r.response_body["total"], 0)

    def test_list_items_after_create(self):
        # Create two items
        for name in ["alpha", "beta"]:
            req = urllib.request.Request(
                f"{self.base}/items",
                data=json.dumps({"name": name}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req).close()

        r = self._run_one(ApiTestCase("list_two", "GET", "/items", expected_status=200))
        self.assertTrue(r.passed)
        self.assertEqual(r.response_body["total"], 2)

    def test_pagination_page_size(self):
        for i in range(6):
            req = urllib.request.Request(
                f"{self.base}/items",
                data=json.dumps({"name": f"item{i}"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req).close()

        r = self._run_one(ApiTestCase(
            "page_size", "GET", "/items?page=1&page_size=3", expected_status=200,
        ))
        self.assertTrue(r.passed)
        self.assertEqual(len(r.response_body["items"]), 3)

    def test_pagination_page2(self):
        for i in range(4):
            req = urllib.request.Request(
                f"{self.base}/items",
                data=json.dumps({"name": f"p{i}"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req).close()

        r = self._run_one(ApiTestCase(
            "page2", "GET", "/items?page=2&page_size=2", expected_status=200,
        ))
        self.assertTrue(r.passed)
        self.assertEqual(len(r.response_body["items"]), 2)

    # CRUD: update
    def test_update_item(self):
        req = urllib.request.Request(
            f"{self.base}/items",
            data=json.dumps({"name": "old"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            created = json.loads(resp.read())
        item_id = created["id"]

        r = self._run_one(ApiTestCase(
            "update", "PUT", f"/items/{item_id}",
            body={"name": "updated"},
            expected_status=200,
            validator=lambda b: None if b.get("name") == "updated" else "name not updated",
        ))
        self.assertTrue(r.passed)

    def test_update_nonexistent_404(self):
        r = self._run_one(ApiTestCase(
            "update_404", "PUT", "/items/99999",
            body={"name": "x"},
            expected_status=404,
        ))
        self.assertTrue(r.passed)

    # CRUD: delete
    def test_delete_item(self):
        req = urllib.request.Request(
            f"{self.base}/items",
            data=json.dumps({"name": "del"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            created = json.loads(resp.read())
        item_id = created["id"]

        r = self._run_one(ApiTestCase(
            "delete", "DELETE", f"/items/{item_id}", expected_status=204,
        ))
        self.assertTrue(r.passed)

    def test_delete_idempotent_404(self):
        r = self._run_one(ApiTestCase(
            "delete_404", "DELETE", "/items/99999", expected_status=404,
        ))
        self.assertTrue(r.passed)

    # Auth flows
    def test_auth_no_token_401(self):
        r = self._run_one(ApiTestCase(
            "no_token", "GET", "/auth/me", expected_status=401,
        ))
        self.assertTrue(r.passed)

    def test_auth_valid_token_200(self):
        r = self._run_one(ApiTestCase(
            "valid_token", "GET", "/auth/me",
            headers={"Authorization": "Bearer valid-token"},
            expected_status=200,
        ))
        self.assertTrue(r.passed)

    def test_auth_invalid_token_403(self):
        r = self._run_one(ApiTestCase(
            "bad_token", "GET", "/auth/me",
            headers={"Authorization": "Bearer wrong"},
            expected_status=403,
        ))
        self.assertTrue(r.passed)

    # Content negotiation
    def test_content_json_default(self):
        r = self._run_one(ApiTestCase(
            "content_json", "GET", "/content",
            expected_status=200,
            expected_content_type="application/json",
        ))
        self.assertTrue(r.passed)

    def test_content_xml_negotiation(self):
        r = self._run_one(ApiTestCase(
            "content_xml", "GET", "/content",
            headers={"Accept": "application/xml"},
            expected_status=200,
            expected_content_type="application/xml",
        ))
        self.assertTrue(r.passed)

    # Rate limiting
    def test_rate_limit_header_present(self):
        r = self._run_one(ApiTestCase(
            "ratelimit_header", "GET", "/rate-limited",
            headers={"X-API-Key": "test-key-rate"},
            expected_status=200,
            expected_headers={"x-ratelimit-limit": ""},
        ))
        self.assertTrue(r.passed)

    def test_rate_limit_exceeded_429(self):
        # Exhaust the limit
        key = "exhaust-key-429"
        for _ in range(11):
            req = urllib.request.Request(
                f"{self.base}/rate-limited",
                method="GET",
                headers={"X-API-Key": key},
            )
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError:
                pass

        r = self._run_one(ApiTestCase(
            "ratelimit_429", "GET", "/rate-limited",
            headers={"X-API-Key": key},
            expected_status=429,
        ))
        self.assertTrue(r.passed)

    def test_rate_limit_retry_after_header(self):
        key = "retry-after-key"
        for _ in range(11):
            req = urllib.request.Request(
                f"{self.base}/rate-limited",
                method="GET",
                headers={"X-API-Key": key},
            )
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError:
                pass

        r = self._run_one(ApiTestCase(
            "retry_after", "GET", "/rate-limited",
            headers={"X-API-Key": key},
            expected_status=429,
            expected_headers={"retry-after": ""},
        ))
        self.assertTrue(r.passed)

    # HEAD / OPTIONS
    def test_head_request(self):
        r = self._run_one(ApiTestCase(
            "head_items", "HEAD", "/items", expected_status=200,
        ))
        self.assertTrue(r.passed)

    def test_options_request(self):
        r = self._run_one(ApiTestCase(
            "options", "OPTIONS", "/items", expected_status=200,
        ))
        self.assertTrue(r.passed)

    # 404 on unknown route
    def test_unknown_route_404(self):
        r = self._run_one(ApiTestCase(
            "unknown_route", "GET", "/does-not-exist", expected_status=404,
        ))
        self.assertTrue(r.passed)

    # Custom validator
    def test_custom_validator_passes(self):
        r = self._run_one(ApiTestCase(
            "custom_pass", "GET", "/health",
            expected_status=200,
            validator=lambda b: None if b.get("status") == "ok" else "status not ok",
        ))
        self.assertTrue(r.passed)

    def test_custom_validator_fails(self):
        r = self._run_one(ApiTestCase(
            "custom_fail", "GET", "/health",
            expected_status=200,
            validator=lambda b: "always fails",
        ))
        self.assertFalse(r.passed)
        self.assertIn("always fails", r.error)

    # Suite-level report
    def test_suite_report_counts(self):
        self.suite.add(ApiTestCase("p1", "GET", "/health", expected_status=200))
        self.suite.add(ApiTestCase("p2", "GET", "/health", expected_status=200))
        self.suite.add(ApiTestCase("f1", "GET", "/health", expected_status=999))
        report = self.suite.run()
        self.assertEqual(report.total, 3)
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.failed, 1)

    def test_suite_success_rate(self):
        self.suite.add(ApiTestCase("ok", "GET", "/health", expected_status=200))
        report = self.suite.run()
        self.assertAlmostEqual(report.success_rate, 1.0)

    def test_suite_duration_positive(self):
        self.suite.add(ApiTestCase("dur", "GET", "/health", expected_status=200))
        report = self.suite.run()
        self.assertGreater(report.duration_ms, 0)

    def test_result_duration_tracked(self):
        r = self._run_one(ApiTestCase("dur2", "GET", "/health", expected_status=200))
        self.assertGreater(r.duration_ms, 0)

    def test_full_crud_lifecycle(self):
        # Create
        create_req = urllib.request.Request(
            f"{self.base}/items",
            data=json.dumps({"name": "lifecycle", "value": 10}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(create_req) as resp:
            item = json.loads(resp.read())
        item_id = item["id"]
        self.assertEqual(item["name"], "lifecycle")

        # Read
        with urllib.request.urlopen(f"{self.base}/items/{item_id}") as resp:
            fetched = json.loads(resp.read())
        self.assertEqual(fetched["id"], item_id)

        # Update
        put_req = urllib.request.Request(
            f"{self.base}/items/{item_id}",
            data=json.dumps({"name": "updated"}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(put_req) as resp:
            updated = json.loads(resp.read())
        self.assertEqual(updated["name"], "updated")

        # Delete
        del_req = urllib.request.Request(
            f"{self.base}/items/{item_id}", method="DELETE"
        )
        with urllib.request.urlopen(del_req) as resp:
            self.assertEqual(resp.status, 204)

        # Confirm gone
        try:
            urllib.request.urlopen(f"{self.base}/items/{item_id}")
            self.fail("Expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_concurrent_creates(self):
        errors = []
        ids = []
        lock = threading.Lock()

        def create(i):
            try:
                req = urllib.request.Request(
                    f"{self.base}/items",
                    data=json.dumps({"name": f"concurrent-{i}"}).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req) as resp:
                    item = json.loads(resp.read())
                with lock:
                    ids.append(item["id"])
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=create, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(set(ids)), 5, "IDs should be unique")

    def test_network_error_captured(self):
        bad_suite = ApiTestSuite("http://127.0.0.1:1")  # nothing listening
        bad_suite.add(ApiTestCase("fail", "GET", "/", expected_status=200))
        report = bad_suite.run()
        self.assertEqual(report.failed, 1)
        self.assertIn("exception", report.results[0].error)


if __name__ == "__main__":
    unittest.main()
