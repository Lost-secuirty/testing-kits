"""
Tests for Logging / Observability Test Harness (Harness 17 of 36).
~88 tests, pure stdlib.
"""

import json
import time
import unittest
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from logging_test_harness import (
    LogEntry,
    LogFormatValidator,
    LogLevelChecker,
    LOG_LEVEL_ORDER,
    SensitiveDataScanner,
    CorrelationTracker,
    TimestampValidator,
    PerformanceLogChecker,
    ErrorContextChecker,
    LoggingReport,
    MockLoggingHandler,
    run_full_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_entry(
    level="INFO",
    message="Test message",
    timestamp="2024-01-15T10:30:00Z",
    correlation_id="corr-123",
    fields=None,
) -> LogEntry:
    return LogEntry(
        timestamp=timestamp,
        level=level,
        message=message,
        correlation_id=correlation_id,
        fields=fields or {},
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# LogEntry tests
# ---------------------------------------------------------------------------

class TestLogEntry(unittest.TestCase):

    def test_basic_creation(self):
        entry = LogEntry(timestamp="2024-01-01T00:00:00Z", level="INFO", message="hello")
        self.assertEqual(entry.level, "INFO")
        self.assertEqual(entry.message, "hello")

    def test_default_correlation_id_empty(self):
        entry = LogEntry(timestamp="2024-01-01T00:00:00Z", level="INFO", message="m")
        self.assertEqual(entry.correlation_id, "")

    def test_default_fields_empty_dict(self):
        entry = LogEntry(timestamp="2024-01-01T00:00:00Z", level="INFO", message="m")
        self.assertIsInstance(entry.fields, dict)
        self.assertEqual(len(entry.fields), 0)

    def test_to_dict_has_required_keys(self):
        entry = make_entry()
        d = entry.to_dict()
        for key in ("timestamp", "level", "message", "correlation_id", "fields"):
            self.assertIn(key, d)

    def test_from_dict_roundtrip(self):
        entry = make_entry(fields={"key": "value"})
        d = entry.to_dict()
        restored = LogEntry.from_dict(d)
        self.assertEqual(entry.timestamp, restored.timestamp)
        self.assertEqual(entry.level, restored.level)
        self.assertEqual(entry.message, restored.message)
        self.assertEqual(entry.correlation_id, restored.correlation_id)
        self.assertEqual(entry.fields, restored.fields)

    def test_from_dict_missing_keys_use_defaults(self):
        restored = LogEntry.from_dict({})
        self.assertEqual(restored.timestamp, "")
        self.assertEqual(restored.level, "")
        self.assertEqual(restored.message, "")

    def test_fields_mutable_independence(self):
        e1 = LogEntry(timestamp="t", level="INFO", message="m")
        e2 = LogEntry(timestamp="t", level="INFO", message="m")
        e1.fields["x"] = 1
        self.assertNotIn("x", e2.fields)


# ---------------------------------------------------------------------------
# LogFormatValidator tests
# ---------------------------------------------------------------------------

class TestLogFormatValidator(unittest.TestCase):

    def setUp(self):
        self.v = LogFormatValidator()

    def test_valid_json_string(self):
        raw = json.dumps({"timestamp": "2024-01-01T00:00:00Z", "level": "INFO", "message": "ok"})
        ok, errs = self.v.validate_json_string(raw)
        self.assertTrue(ok)
        self.assertEqual(errs, [])

    def test_invalid_json_string(self):
        ok, errs = self.v.validate_json_string("{not valid json}")
        self.assertFalse(ok)
        self.assertTrue(any("Invalid JSON" in e for e in errs))

    def test_missing_required_fields(self):
        ok, errs = self.v.validate_dict({"timestamp": "2024-01-01T00:00:00Z"})
        self.assertFalse(ok)
        self.assertTrue(any("Missing" in e for e in errs))

    def test_all_required_fields_present(self):
        ok, errs = self.v.validate_dict(
            {"timestamp": "2024-01-01T00:00:00Z", "level": "INFO", "message": "hi"}
        )
        self.assertTrue(ok)

    def test_invalid_timestamp_format(self):
        ok, errs = self.v.validate_dict(
            {"timestamp": "01/01/2024 00:00:00", "level": "INFO", "message": "m"}
        )
        self.assertFalse(ok)
        self.assertTrue(any("ISO 8601" in e for e in errs))

    def test_timestamp_with_microseconds_valid(self):
        ok, errs = self.v.validate_dict(
            {"timestamp": "2024-01-01T00:00:00.123456Z", "level": "INFO", "message": "m"}
        )
        self.assertTrue(ok)

    def test_timestamp_with_offset_valid(self):
        ok, errs = self.v.validate_dict(
            {"timestamp": "2024-01-01T00:00:00+05:30", "level": "INFO", "message": "m"}
        )
        self.assertTrue(ok)

    def test_validate_entry_uses_entry_data(self):
        entry = make_entry()
        ok, errs = self.v.validate_entry(entry)
        self.assertTrue(ok)

    def test_non_string_timestamp_invalid(self):
        ok, errs = self.v.validate_dict(
            {"timestamp": 12345, "level": "INFO", "message": "m"}
        )
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# LogLevelChecker tests
# ---------------------------------------------------------------------------

class TestLogLevelChecker(unittest.TestCase):

    def setUp(self):
        self.checker = LogLevelChecker()

    def test_hierarchy_correct(self):
        self.assertTrue(self.checker.hierarchy_is_correct())

    def test_debug_less_than_info(self):
        self.assertLess(self.checker.compare_levels("DEBUG", "INFO"), 0)

    def test_info_less_than_warning(self):
        self.assertLess(self.checker.compare_levels("INFO", "WARNING"), 0)

    def test_warning_less_than_error(self):
        self.assertLess(self.checker.compare_levels("WARNING", "ERROR"), 0)

    def test_error_less_than_critical(self):
        self.assertLess(self.checker.compare_levels("ERROR", "CRITICAL"), 0)

    def test_same_level_compare_zero(self):
        self.assertEqual(self.checker.compare_levels("INFO", "INFO"), 0)

    def test_valid_levels(self):
        for lv in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.assertTrue(self.checker.is_valid_level(lv))

    def test_invalid_level(self):
        self.assertFalse(self.checker.is_valid_level("TRACE"))

    def test_case_insensitive_valid(self):
        self.assertTrue(self.checker.is_valid_level("debug"))

    def test_filter_entries_by_level(self):
        entries = [
            make_entry(level="DEBUG"),
            make_entry(level="INFO"),
            make_entry(level="ERROR"),
        ]
        filtered = self.checker.filter_entries(entries, "INFO")
        self.assertEqual(len(filtered), 2)
        levels = {e.level for e in filtered}
        self.assertNotIn("DEBUG", levels)

    def test_filter_removes_all_below_critical(self):
        entries = [make_entry(level=lv) for lv in ("DEBUG", "INFO", "WARNING", "ERROR")]
        filtered = self.checker.filter_entries(entries, "CRITICAL")
        self.assertEqual(len(filtered), 0)

    def test_is_at_least(self):
        self.assertTrue(self.checker.is_at_least("ERROR", "INFO"))
        self.assertFalse(self.checker.is_at_least("DEBUG", "WARNING"))

    def test_level_value_returns_int(self):
        self.assertIsInstance(self.checker.level_value("INFO"), int)

    def test_unknown_level_raises(self):
        with self.assertRaises(KeyError):
            self.checker.level_value("UNKNOWN")


# ---------------------------------------------------------------------------
# SensitiveDataScanner tests
# ---------------------------------------------------------------------------

class TestSensitiveDataScanner(unittest.TestCase):

    def setUp(self):
        self.scanner = SensitiveDataScanner()

    def test_clean_message(self):
        self.assertTrue(self.scanner.is_clean("Everything is fine"))

    def test_detects_password(self):
        findings = self.scanner.scan_message("password=secret123")
        self.assertIn("password", findings)

    def test_detects_ssn(self):
        findings = self.scanner.scan_message("User SSN: 123-45-6789")
        self.assertIn("ssn", findings)

    def test_detects_credit_card(self):
        findings = self.scanner.scan_message("Card: 4111111111111111")
        self.assertIn("credit_card", findings)

    def test_detects_api_key(self):
        findings = self.scanner.scan_message("api_key=abc123xyz")
        self.assertIn("api_key", findings)

    def test_detects_token(self):
        findings = self.scanner.scan_message("token=eyJhbGciOiJIUzI1NiJ9")
        self.assertIn("token", findings)

    def test_detects_bearer(self):
        findings = self.scanner.scan_message("Bearer=myBearerToken")
        self.assertIn("token", findings)

    def test_no_partial_ssn_match(self):
        findings = self.scanner.scan_message("123-456-7890")
        self.assertNotIn("ssn", findings)

    def test_scan_entry_checks_fields(self):
        entry = make_entry(fields={"info": "password=hidden"})
        findings = self.scanner.scan_entry(entry)
        self.assertIn("password", findings)

    def test_entry_is_clean(self):
        entry = make_entry(message="User logged in successfully")
        self.assertTrue(self.scanner.entry_is_clean(entry))

    def test_entry_not_clean_with_ssn(self):
        entry = make_entry(message="Processing SSN 987-65-4321")
        self.assertFalse(self.scanner.entry_is_clean(entry))

    def test_multiple_findings(self):
        msg = "password=x and SSN 111-22-3333"
        findings = self.scanner.scan_message(msg)
        self.assertIn("password", findings)
        self.assertIn("ssn", findings)


# ---------------------------------------------------------------------------
# CorrelationTracker tests
# ---------------------------------------------------------------------------

class TestCorrelationTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = CorrelationTracker()

    def test_all_have_correlation_id_true(self):
        entries = [make_entry(correlation_id="cid-1") for _ in range(3)]
        self.assertTrue(self.tracker.all_have_correlation_id(entries))

    def test_all_have_correlation_id_false(self):
        entries = [make_entry(correlation_id=""), make_entry()]
        self.assertFalse(self.tracker.all_have_correlation_id(entries))

    def test_single_correlation_id_true(self):
        entries = [make_entry(correlation_id="abc") for _ in range(5)]
        self.assertTrue(self.tracker.single_correlation_id(entries))

    def test_single_correlation_id_false(self):
        entries = [make_entry(correlation_id="abc"), make_entry(correlation_id="xyz")]
        self.assertFalse(self.tracker.single_correlation_id(entries))

    def test_group_by_correlation(self):
        entries = [
            make_entry(correlation_id="a"),
            make_entry(correlation_id="b"),
            make_entry(correlation_id="a"),
        ]
        groups = self.tracker.group_by_correlation(entries)
        self.assertEqual(len(groups["a"]), 2)
        self.assertEqual(len(groups["b"]), 1)

    def test_missing_correlation_entries(self):
        entries = [make_entry(correlation_id="ok"), make_entry(correlation_id="")]
        missing = self.tracker.missing_correlation_entries(entries)
        self.assertEqual(len(missing), 1)

    def test_valid_uuid_correlation_id(self):
        import uuid
        cid = str(uuid.uuid4())
        self.assertTrue(self.tracker.correlation_id_is_valid_uuid(cid))

    def test_invalid_uuid_correlation_id(self):
        self.assertFalse(self.tracker.correlation_id_is_valid_uuid("not-a-uuid"))


# ---------------------------------------------------------------------------
# TimestampValidator tests
# ---------------------------------------------------------------------------

class TestTimestampValidator(unittest.TestCase):

    def setUp(self):
        self.tv = TimestampValidator()

    def test_valid_utc_z(self):
        self.assertTrue(self.tv.is_valid_iso8601("2024-01-15T10:30:00Z"))

    def test_valid_with_microseconds(self):
        self.assertTrue(self.tv.is_valid_iso8601("2024-01-15T10:30:00.123456Z"))

    def test_valid_with_offset(self):
        self.assertTrue(self.tv.is_valid_iso8601("2024-01-15T10:30:00+05:30"))

    def test_invalid_format(self):
        self.assertFalse(self.tv.is_valid_iso8601("15/01/2024 10:30:00"))

    def test_parse_returns_datetime(self):
        dt = self.tv.parse("2024-01-15T10:30:00Z")
        self.assertIsInstance(dt, datetime)

    def test_parse_invalid_returns_none(self):
        dt = self.tv.parse("not-a-timestamp")
        self.assertIsNone(dt)

    def test_is_monotonic_true(self):
        entries = [
            make_entry(timestamp="2024-01-01T00:00:00Z"),
            make_entry(timestamp="2024-01-01T00:00:01Z"),
            make_entry(timestamp="2024-01-01T00:00:02Z"),
        ]
        self.assertTrue(self.tv.is_monotonic(entries))

    def test_is_monotonic_false(self):
        entries = [
            make_entry(timestamp="2024-01-01T00:00:02Z"),
            make_entry(timestamp="2024-01-01T00:00:01Z"),
        ]
        self.assertFalse(self.tv.is_monotonic(entries))

    def test_equal_timestamps_monotonic(self):
        entries = [
            make_entry(timestamp="2024-01-01T00:00:01Z"),
            make_entry(timestamp="2024-01-01T00:00:01Z"),
        ]
        self.assertTrue(self.tv.is_monotonic(entries))

    def test_find_out_of_order(self):
        entries = [
            make_entry(timestamp="2024-01-01T00:00:02Z"),
            make_entry(timestamp="2024-01-01T00:00:01Z"),
        ]
        oot = self.tv.find_out_of_order(entries)
        self.assertGreater(len(oot), 0)

    def test_all_timestamps_valid(self):
        entries = [make_entry(timestamp="2024-01-01T00:00:00Z") for _ in range(3)]
        self.assertTrue(self.tv.all_timestamps_valid(entries))

    def test_all_timestamps_valid_false(self):
        entries = [make_entry(timestamp="bad-ts")]
        self.assertFalse(self.tv.all_timestamps_valid(entries))


# ---------------------------------------------------------------------------
# PerformanceLogChecker tests
# ---------------------------------------------------------------------------

class TestPerformanceLogChecker(unittest.TestCase):

    def setUp(self):
        self.checker = PerformanceLogChecker()

    def test_has_timing_true(self):
        entry = make_entry(fields={"duration_ms": 100})
        self.assertTrue(self.checker.has_timing(entry))

    def test_has_timing_false(self):
        entry = make_entry()
        self.assertFalse(self.checker.has_timing(entry))

    def test_timing_valid(self):
        entry = make_entry(fields={"duration_ms": 250.5})
        ok, err = self.checker.timing_is_valid(entry)
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_timing_zero_valid(self):
        entry = make_entry(fields={"duration_ms": 0})
        ok, _ = self.checker.timing_is_valid(entry)
        self.assertTrue(ok)

    def test_timing_negative_invalid(self):
        entry = make_entry(fields={"duration_ms": -5})
        ok, err = self.checker.timing_is_valid(entry)
        self.assertFalse(ok)
        self.assertIn("non-negative", err)

    def test_timing_wrong_type_invalid(self):
        entry = make_entry(fields={"duration_ms": "fast"})
        ok, err = self.checker.timing_is_valid(entry)
        self.assertFalse(ok)

    def test_timing_missing_invalid(self):
        entry = make_entry()
        ok, err = self.checker.timing_is_valid(entry)
        self.assertFalse(ok)
        self.assertIn("Missing", err)

    def test_validate_entries_returns_invalid(self):
        entries = [
            make_entry(fields={"duration_ms": 100}),
            make_entry(fields={"duration_ms": -1}),
            make_entry(),  # no timing field – skipped
        ]
        invalid = self.checker.validate_entries(entries)
        self.assertEqual(len(invalid), 1)

    def test_average_duration(self):
        entries = [
            make_entry(fields={"duration_ms": 100}),
            make_entry(fields={"duration_ms": 200}),
        ]
        avg = self.checker.average_duration(entries)
        self.assertEqual(avg, 150.0)

    def test_average_duration_none_when_no_timing(self):
        entries = [make_entry()]
        self.assertIsNone(self.checker.average_duration(entries))

    def test_max_duration(self):
        entries = [
            make_entry(fields={"duration_ms": 50}),
            make_entry(fields={"duration_ms": 300}),
            make_entry(fields={"duration_ms": 150}),
        ]
        self.assertEqual(self.checker.max_duration(entries), 300)

    def test_max_duration_none_when_no_timing(self):
        self.assertIsNone(self.checker.max_duration([make_entry()]))


# ---------------------------------------------------------------------------
# ErrorContextChecker tests
# ---------------------------------------------------------------------------

class TestErrorContextChecker(unittest.TestCase):

    def setUp(self):
        self.checker = ErrorContextChecker()

    def test_info_not_error_entry(self):
        self.assertFalse(self.checker.is_error_entry(make_entry(level="INFO")))

    def test_error_is_error_entry(self):
        self.assertTrue(self.checker.is_error_entry(make_entry(level="ERROR")))

    def test_critical_is_error_entry(self):
        self.assertTrue(self.checker.is_error_entry(make_entry(level="CRITICAL")))

    def test_valid_error_entry(self):
        entry = make_entry(
            level="ERROR",
            fields={"stack_trace": "Traceback...", "error_code": "ERR_001"},
        )
        ok, errs = self.checker.validate_error_entry(entry)
        self.assertTrue(ok)

    def test_missing_stack_trace(self):
        entry = make_entry(level="ERROR", fields={"error_code": "E01"})
        ok, errs = self.checker.validate_error_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("stack_trace" in e for e in errs))

    def test_missing_error_code(self):
        entry = make_entry(level="ERROR", fields={"stack_trace": "Traceback..."})
        ok, errs = self.checker.validate_error_entry(entry)
        self.assertFalse(ok)
        self.assertTrue(any("error_code" in e for e in errs))

    def test_empty_stack_trace_invalid(self):
        entry = make_entry(
            level="ERROR",
            fields={"stack_trace": "   ", "error_code": "E01"},
        )
        ok, errs = self.checker.validate_error_entry(entry)
        self.assertFalse(ok)

    def test_validate_all_errors(self):
        entries = [
            make_entry(level="INFO"),
            make_entry(level="ERROR", fields={"stack_trace": "tb", "error_code": "E1"}),
            make_entry(level="ERROR"),  # missing fields
        ]
        invalid = self.checker.validate_all_errors(entries)
        self.assertEqual(len(invalid), 1)

    def test_info_entry_always_passes(self):
        entry = make_entry(level="INFO")
        ok, errs = self.checker.validate_error_entry(entry)
        self.assertTrue(ok)
        self.assertEqual(errs, [])


# ---------------------------------------------------------------------------
# LoggingReport tests
# ---------------------------------------------------------------------------

class TestLoggingReport(unittest.TestCase):

    def test_passed_when_no_issues(self):
        report = LoggingReport(total_entries=5)
        self.assertTrue(report.passed)

    def test_not_passed_with_format_errors(self):
        report = LoggingReport(total_entries=1, format_errors=["bad format"])
        self.assertFalse(report.passed)

    def test_total_issues_sums_all(self):
        report = LoggingReport(
            format_errors=["e1"],
            level_issues=["l1", "l2"],
            sensitive_data_findings=[{"x": 1}],
        )
        self.assertEqual(report.total_issues, 4)

    def test_to_dict_has_passed_key(self):
        report = LoggingReport()
        d = report.to_dict()
        self.assertIn("passed", d)

    def test_summary_contains_status(self):
        report = LoggingReport()
        self.assertIn("PASS", report.summary())

    def test_summary_fail_status(self):
        report = LoggingReport(format_errors=["err"])
        self.assertIn("FAIL", report.summary())

    def test_run_full_report_clean(self):
        entries = [
            LogEntry(
                timestamp="2024-01-01T00:00:01Z",
                level="INFO",
                message="All good",
                correlation_id="cid-1",
            ),
            LogEntry(
                timestamp="2024-01-01T00:00:02Z",
                level="WARNING",
                message="Watch out",
                correlation_id="cid-1",
            ),
        ]
        report = run_full_report(entries)
        self.assertEqual(report.total_entries, 2)

    def test_run_full_report_detects_sensitive(self):
        entries = [
            LogEntry(
                timestamp="2024-01-01T00:00:00Z",
                level="INFO",
                message="password=hunter2",
                correlation_id="cid",
            )
        ]
        report = run_full_report(entries)
        self.assertGreater(len(report.sensitive_data_findings), 0)

    def test_run_full_report_detects_missing_correlation(self):
        entries = [
            LogEntry(timestamp="2024-01-01T00:00:00Z", level="INFO", message="m")
        ]
        report = run_full_report(entries)
        self.assertGreater(len(report.correlation_issues), 0)


# ---------------------------------------------------------------------------
# MockLoggingHandler tests
# ---------------------------------------------------------------------------

def _fetch(url: str, method: str = "GET", body: bytes = None) -> tuple:
    """Return (status_code, parsed_json)."""
    req = urllib.request.Request(url, data=body, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestMockLoggingHandler(unittest.TestCase):

    def setUp(self):
        self.server = MockLoggingHandler(port=0)
        self.server.start()
        self.base = self.server.base_url

    def tearDown(self):
        self.server.stop()

    def _post_entry(self, entry: LogEntry):
        body = json.dumps(entry.to_dict()).encode()
        return _fetch(f"{self.base}/logs", method="POST", body=body)

    def test_health_endpoint(self):
        status, data = _fetch(f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_post_log_entry(self):
        entry = make_entry()
        status, data = self._post_entry(entry)
        self.assertEqual(status, 201)
        self.assertEqual(data["status"], "accepted")

    def test_get_logs_empty_initially(self):
        status, data = _fetch(f"{self.base}/logs")
        self.assertEqual(status, 200)
        self.assertEqual(data, [])

    def test_get_logs_after_posting(self):
        self._post_entry(make_entry(message="first"))
        self._post_entry(make_entry(message="second"))
        status, data = _fetch(f"{self.base}/logs")
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 2)

    def test_get_logs_count(self):
        self._post_entry(make_entry())
        self._post_entry(make_entry())
        status, data = _fetch(f"{self.base}/logs/count")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 2)

    def test_clear_logs(self):
        self._post_entry(make_entry())
        status, _ = _fetch(f"{self.base}/logs/clear")
        self.assertEqual(status, 200)
        _, data = _fetch(f"{self.base}/logs")
        self.assertEqual(len(data), 0)

    def test_filter_by_level(self):
        self._post_entry(make_entry(level="INFO"))
        self._post_entry(make_entry(level="ERROR"))
        status, data = _fetch(f"{self.base}/logs?level=ERROR")
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["level"], "ERROR")

    def test_filter_by_correlation_id(self):
        self._post_entry(make_entry(correlation_id="abc"))
        self._post_entry(make_entry(correlation_id="xyz"))
        status, data = _fetch(f"{self.base}/logs?correlation_id=abc")
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1)

    def test_invalid_json_body_returns_400(self):
        req = urllib.request.Request(
            f"{self.base}/logs",
            data=b"{bad json}",
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", "10")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 400)

    def test_unknown_path_returns_404(self):
        status, _ = _fetch(f"{self.base}/unknown")
        self.assertEqual(status, 404)

    def test_entry_count_method(self):
        self._post_entry(make_entry())
        self.assertEqual(self.server.entry_count(), 1)

    def test_get_entries_method(self):
        entry = make_entry(message="track me")
        self._post_entry(entry)
        entries = self.server.get_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].message, "track me")

    def test_clear_method(self):
        self._post_entry(make_entry())
        self.server.clear()
        self.assertEqual(self.server.entry_count(), 0)

    def test_context_manager(self):
        with MockLoggingHandler(port=0) as srv:
            status, data = _fetch(f"{srv.base_url}/health")
            self.assertEqual(status, 200)

    def test_dynamic_port_assigned(self):
        self.assertGreater(self.server.port, 0)

    def test_concurrent_posts(self):
        import threading
        n = 10

        def post_one():
            self._post_entry(make_entry())

        threads = [threading.Thread(target=post_one) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.server.entry_count(), n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
