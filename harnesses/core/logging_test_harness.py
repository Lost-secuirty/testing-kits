"""
Logging / Observability Test Harness (Harness 17 of 36)
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    """Structured log entry."""
    timestamp: str
    level: str
    message: str
    correlation_id: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "correlation_id": self.correlation_id,
            "fields": self.fields,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogEntry:
        return cls(
            timestamp=data.get("timestamp", ""),
            level=data.get("level", ""),
            message=data.get("message", ""),
            correlation_id=data.get("correlation_id", ""),
            fields=data.get("fields", {}),
        )


# ---------------------------------------------------------------------------
# LogFormatValidator
# ---------------------------------------------------------------------------

REQUIRED_LOG_FIELDS = {"timestamp", "level", "message"}

# ISO 8601 basic pattern (accepts with/without timezone, with/without microseconds)
_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(\.\d+)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


class LogFormatValidator:
    """Validates that a log entry (as JSON string or dict) is well-formed."""

    # ---- public API --------------------------------------------------------

    def validate_json_string(self, raw: str) -> tuple[bool, list[str]]:
        """Return (valid, errors) for a raw JSON string."""
        errors: list[str] = []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return False, [f"Invalid JSON: {exc}"]
        ok, field_errors = self._validate_dict(data)
        errors.extend(field_errors)
        return (len(errors) == 0), errors

    def validate_dict(self, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Return (valid, errors) for a pre-parsed dict."""
        ok, errors = self._validate_dict(data)
        return ok, errors

    def validate_entry(self, entry: LogEntry) -> tuple[bool, list[str]]:
        return self.validate_dict(entry.to_dict())

    # ---- helpers -----------------------------------------------------------

    def _validate_dict(self, data: dict[str, Any]) -> tuple[bool, list[str]]:
        errors: list[str] = []
        missing = REQUIRED_LOG_FIELDS - data.keys()
        if missing:
            errors.append(f"Missing required fields: {sorted(missing)}")
        if "timestamp" in data:
            ts_valid, ts_err = self._validate_timestamp(data["timestamp"])
            if not ts_valid:
                errors.append(ts_err)
        return (len(errors) == 0), errors

    @staticmethod
    def _validate_timestamp(ts: Any) -> tuple[bool, str]:
        if not isinstance(ts, str):
            return False, f"timestamp must be a string, got {type(ts).__name__}"
        if not _ISO8601_RE.match(ts):
            return False, f"timestamp is not ISO 8601: {ts!r}"
        return True, ""


# ---------------------------------------------------------------------------
# LogLevelChecker
# ---------------------------------------------------------------------------

LOG_LEVEL_ORDER: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class LogLevelChecker:
    """Verifies level hierarchy and level filtering."""

    def is_valid_level(self, level: str) -> bool:
        return level.upper() in LOG_LEVEL_ORDER

    def level_value(self, level: str) -> int:
        """Return numeric value; raises KeyError for unknown levels."""
        return LOG_LEVEL_ORDER[level.upper()]

    def compare_levels(self, level_a: str, level_b: str) -> int:
        """Return negative/0/positive like cmp(a, b)."""
        return self.level_value(level_a) - self.level_value(level_b)

    def is_at_least(self, level: str, minimum: str) -> bool:
        """Return True if level >= minimum."""
        return self.level_value(level) >= self.level_value(minimum)

    def filter_entries(
        self, entries: list[LogEntry], minimum_level: str
    ) -> list[LogEntry]:
        """Return only entries at or above minimum_level."""
        min_val = self.level_value(minimum_level)
        return [e for e in entries if LOG_LEVEL_ORDER.get(e.level.upper(), 0) >= min_val]

    def hierarchy_is_correct(self) -> bool:
        """Verify DEBUG < INFO < WARNING < ERROR < CRITICAL."""
        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        values = [LOG_LEVEL_ORDER[lv] for lv in levels]
        return values == sorted(values) and len(set(values)) == len(values)


# ---------------------------------------------------------------------------
# SensitiveDataScanner
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS: dict[str, re.Pattern] = {
    "password": re.compile(
        r"password\s*[=:]\s*\S+", re.IGNORECASE
    ),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{16}\b"),
    "api_key": re.compile(
        r"(api[_-]?key|apikey)\s*[=:]\s*\S+", re.IGNORECASE
    ),
    "token": re.compile(
        r"(token|bearer)\s*[=:]\s*\S+", re.IGNORECASE
    ),
}


class SensitiveDataScanner:
    """Scans log messages and field values for sensitive data patterns."""

    def scan_message(self, message: str) -> dict[str, list[str]]:
        """Return dict mapping pattern_name -> list of matched strings."""
        findings: dict[str, list[str]] = {}
        for name, pattern in _SENSITIVE_PATTERNS.items():
            matches = pattern.findall(message)
            if matches:
                findings[name] = matches
        return findings

    def scan_entry(self, entry: LogEntry) -> dict[str, list[str]]:
        """Scan message and all string field values."""
        combined = entry.message
        for v in entry.fields.values():
            if isinstance(v, str):
                combined += " " + v
        return self.scan_message(combined)

    def is_clean(self, text: str) -> bool:
        return len(self.scan_message(text)) == 0

    def entry_is_clean(self, entry: LogEntry) -> bool:
        return len(self.scan_entry(entry)) == 0


# ---------------------------------------------------------------------------
# CorrelationTracker
# ---------------------------------------------------------------------------

class CorrelationTracker:
    """Verifies correlation_id propagates through a sequence of log entries."""

    def all_have_correlation_id(self, entries: list[LogEntry]) -> bool:
        return all(bool(e.correlation_id) for e in entries)

    def single_correlation_id(self, entries: list[LogEntry]) -> bool:
        """All entries share exactly one correlation_id."""
        ids = {e.correlation_id for e in entries if e.correlation_id}
        return len(ids) == 1

    def group_by_correlation(
        self, entries: list[LogEntry]
    ) -> dict[str, list[LogEntry]]:
        groups: dict[str, list[LogEntry]] = {}
        for e in entries:
            cid = e.correlation_id or "__none__"
            groups.setdefault(cid, []).append(e)
        return groups

    def missing_correlation_entries(
        self, entries: list[LogEntry]
    ) -> list[LogEntry]:
        return [e for e in entries if not e.correlation_id]

    def correlation_id_is_valid_uuid(self, correlation_id: str) -> bool:
        try:
            uuid.UUID(correlation_id)
            return True
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# TimestampValidator
# ---------------------------------------------------------------------------

def _parse_iso8601(ts: str) -> datetime | None:
    """Parse ISO 8601 timestamp; return None on failure."""
    ts = ts.strip()
    # Normalise Z -> +00:00
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    # Python 3.7+ fromisoformat doesn't support +HH:MM without colon in some versions
    # Try common formats
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            pass
    # Last resort: fromisoformat (Python 3.7+)
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


class TimestampValidator:
    """Validates ISO 8601 timestamps and checks monotonic ordering."""

    def is_valid_iso8601(self, ts: str) -> bool:
        return _ISO8601_RE.match(ts) is not None

    def parse(self, ts: str) -> datetime | None:
        return _parse_iso8601(ts)

    def is_monotonic(self, entries: list[LogEntry]) -> bool:
        """Return True if timestamps are non-decreasing."""
        parsed = []
        for e in entries:
            dt = _parse_iso8601(e.timestamp)
            if dt is None:
                return False
            parsed.append(dt)
        return all(parsed[i] <= parsed[i + 1] for i in range(len(parsed) - 1))

    def find_out_of_order(
        self, entries: list[LogEntry]
    ) -> list[tuple[int, int]]:
        """Return list of (i, j) index pairs where entry[j] < entry[i]."""
        out: list[tuple[int, int]] = []
        parsed = [_parse_iso8601(e.timestamp) for e in entries]
        for i in range(len(parsed) - 1):
            if (
                parsed[i] is not None
                and parsed[i + 1] is not None
                and parsed[i + 1] < parsed[i]  # type: ignore[operator]
            ):
                out.append((i, i + 1))
        return out

    def all_timestamps_valid(self, entries: list[LogEntry]) -> bool:
        return all(self.is_valid_iso8601(e.timestamp) for e in entries)


# ---------------------------------------------------------------------------
# PerformanceLogChecker
# ---------------------------------------------------------------------------

class PerformanceLogChecker:
    """Validates performance timing fields in log entries."""

    TIMING_FIELD = "duration_ms"

    def has_timing(self, entry: LogEntry) -> bool:
        return self.TIMING_FIELD in entry.fields

    def timing_is_valid(self, entry: LogEntry) -> tuple[bool, str]:
        """Return (valid, error_message)."""
        if self.TIMING_FIELD not in entry.fields:
            return False, f"Missing field '{self.TIMING_FIELD}'"
        val = entry.fields[self.TIMING_FIELD]
        if not isinstance(val, (int, float)):
            return False, f"'{self.TIMING_FIELD}' must be a number, got {type(val).__name__}"
        if val < 0:
            return False, f"'{self.TIMING_FIELD}' must be non-negative, got {val}"
        return True, ""

    def validate_entries(
        self, entries: list[LogEntry]
    ) -> list[tuple[LogEntry, str]]:
        """Return list of (entry, error) for invalid timing entries."""
        invalid = []
        for e in entries:
            if self.has_timing(e):
                ok, err = self.timing_is_valid(e)
                if not ok:
                    invalid.append((e, err))
        return invalid

    def average_duration(self, entries: list[LogEntry]) -> float | None:
        """Return average duration_ms over entries that have it; None if none."""
        vals = [
            e.fields[self.TIMING_FIELD]
            for e in entries
            if self.has_timing(e) and isinstance(e.fields[self.TIMING_FIELD], (int, float))
        ]
        if not vals:
            return None
        return sum(vals) / len(vals)

    def max_duration(self, entries: list[LogEntry]) -> float | None:
        vals = [
            e.fields[self.TIMING_FIELD]
            for e in entries
            if self.has_timing(e) and isinstance(e.fields[self.TIMING_FIELD], (int, float))
        ]
        return max(vals) if vals else None


# ---------------------------------------------------------------------------
# ErrorContextChecker
# ---------------------------------------------------------------------------

ERROR_REQUIRED_FIELDS = {"stack_trace", "error_code"}


class ErrorContextChecker:
    """Validates error log entries have required context fields."""

    def is_error_entry(self, entry: LogEntry) -> bool:
        return entry.level.upper() in ("ERROR", "CRITICAL")

    def validate_error_entry(self, entry: LogEntry) -> tuple[bool, list[str]]:
        """Return (valid, errors). Only meaningful for ERROR/CRITICAL entries."""
        errors: list[str] = []
        if not self.is_error_entry(entry):
            return True, []
        missing = ERROR_REQUIRED_FIELDS - entry.fields.keys()
        if missing:
            errors.append(f"Error entry missing required fields: {sorted(missing)}")
        if "stack_trace" in entry.fields:
            st = entry.fields["stack_trace"]
            if not isinstance(st, str) or not st.strip():
                errors.append("'stack_trace' must be a non-empty string")
        if "error_code" in entry.fields:
            ec = entry.fields["error_code"]
            if ec is None or (isinstance(ec, str) and not ec.strip()):
                errors.append("'error_code' must be a non-empty value")
        return (len(errors) == 0), errors

    def validate_all_errors(
        self, entries: list[LogEntry]
    ) -> list[tuple[LogEntry, list[str]]]:
        """Return (entry, errors) for each invalid error entry."""
        invalid = []
        for e in entries:
            if self.is_error_entry(e):
                ok, errs = self.validate_error_entry(e)
                if not ok:
                    invalid.append((e, errs))
        return invalid


# ---------------------------------------------------------------------------
# LoggingReport
# ---------------------------------------------------------------------------

@dataclass
class LoggingReport:
    """Aggregates validation results from all checkers."""
    total_entries: int = 0
    format_errors: list[str] = field(default_factory=list)
    level_issues: list[str] = field(default_factory=list)
    sensitive_data_findings: list[dict[str, Any]] = field(default_factory=list)
    correlation_issues: list[str] = field(default_factory=list)
    timestamp_issues: list[str] = field(default_factory=list)
    performance_issues: list[str] = field(default_factory=list)
    error_context_issues: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def passed(self) -> bool:
        return (
            not self.format_errors
            and not self.level_issues
            and not self.sensitive_data_findings
            and not self.correlation_issues
            and not self.timestamp_issues
            and not self.performance_issues
            and not self.error_context_issues
        )

    @property
    def total_issues(self) -> int:
        return (
            len(self.format_errors)
            + len(self.level_issues)
            + len(self.sensitive_data_findings)
            + len(self.correlation_issues)
            + len(self.timestamp_issues)
            + len(self.performance_issues)
            + len(self.error_context_issues)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_entries": self.total_entries,
            "passed": self.passed,
            "total_issues": self.total_issues,
            "format_errors": self.format_errors,
            "level_issues": self.level_issues,
            "sensitive_data_findings": self.sensitive_data_findings,
            "correlation_issues": self.correlation_issues,
            "timestamp_issues": self.timestamp_issues,
            "performance_issues": self.performance_issues,
            "error_context_issues": self.error_context_issues,
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"LoggingReport [{status}] entries={self.total_entries} "
            f"issues={self.total_issues}"
        )


def run_full_report(entries: list[LogEntry]) -> LoggingReport:
    """Run all checks against a list of entries and return a LoggingReport."""
    report = LoggingReport(total_entries=len(entries))

    fmt = LogFormatValidator()
    lvl = LogLevelChecker()
    scanner = SensitiveDataScanner()
    corr = CorrelationTracker()
    ts_val = TimestampValidator()
    perf = PerformanceLogChecker()
    err_ctx = ErrorContextChecker()

    for entry in entries:
        ok, errs = fmt.validate_entry(entry)
        report.format_errors.extend(errs)

        if not lvl.is_valid_level(entry.level):
            report.level_issues.append(f"Unknown level: {entry.level!r}")

        findings = scanner.scan_entry(entry)
        if findings:
            report.sensitive_data_findings.append(
                {"message_excerpt": entry.message[:80], "findings": findings}
            )

    missing_cid = corr.missing_correlation_entries(entries)
    if missing_cid:
        report.correlation_issues.append(
            f"{len(missing_cid)} entries missing correlation_id"
        )

    if not ts_val.all_timestamps_valid(entries):
        report.timestamp_issues.append("One or more invalid timestamps found")

    oot = ts_val.find_out_of_order(entries)
    if oot:
        report.timestamp_issues.append(
            f"Out-of-order timestamps at positions: {oot}"
        )

    perf_invalid = perf.validate_entries(entries)
    for _, err in perf_invalid:
        report.performance_issues.append(err)

    err_invalid = err_ctx.validate_all_errors(entries)
    for _, errs in err_invalid:
        report.error_context_issues.extend(errs)

    return report


# ---------------------------------------------------------------------------
# MockLoggingHandler (HTTP server)
# ---------------------------------------------------------------------------

DEFAULT_PORT = 19030


class _LoggingHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the mock logging server."""

    server: MockLoggingHandler  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
        pass

    # ---- routing -----------------------------------------------------------

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/logs":
            self._handle_post_log()
        else:
            self._send_json(404, {"error": "Not found"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/logs":
            self._handle_get_logs(qs)
        elif parsed.path == "/logs/count":
            self._handle_get_count()
        elif parsed.path == "/logs/clear":
            self._handle_clear()
        elif parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "Not found"})

    # ---- handlers ----------------------------------------------------------

    def _handle_post_log(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"error": "Expected a JSON object"})
            return
        entry = LogEntry.from_dict(data)
        with self.server._lock:
            self.server._entries.append(entry)
        self._send_json(201, {"status": "accepted", "id": len(self.server._entries) - 1})

    def _handle_get_logs(self, qs: dict[str, list[str]]) -> None:
        with self.server._lock:
            entries = list(self.server._entries)
        level_filter = qs.get("level", [None])[0]
        corr_filter = qs.get("correlation_id", [None])[0]
        if level_filter:
            entries = [e for e in entries if e.level.upper() == level_filter.upper()]
        if corr_filter:
            entries = [e for e in entries if e.correlation_id == corr_filter]
        self._send_json(200, [e.to_dict() for e in entries])

    def _handle_get_count(self) -> None:
        with self.server._lock:
            count = len(self.server._entries)
        self._send_json(200, {"count": count})

    def _handle_clear(self) -> None:
        with self.server._lock:
            self.server._entries.clear()
        self._send_json(200, {"status": "cleared"})

    # ---- utility -----------------------------------------------------------

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockLoggingHandler:
    """
    HTTP server that accepts log entries and provides query endpoints.

    Endpoints:
      POST /logs            – ingest a JSON log entry
      GET  /logs            – retrieve all entries (optional ?level=X&correlation_id=Y)
      GET  /logs/count      – return {"count": N}
      GET  /logs/clear      – clear all stored entries
      GET  /health          – {"status": "ok"}
    """

    def __init__(self, port: int = 0) -> None:
        """Use port=0 for an OS-assigned dynamic port."""
        self._lock = threading.Lock()
        self._entries: list[LogEntry] = []
        self._server = HTTPServer(("127.0.0.1", port), _LoggingHTTPHandler)
        # Give the handler access to this instance
        self._server._lock = self._lock  # type: ignore[attr-defined]
        self._server._entries = self._entries  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_entries(self) -> list[LogEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    # context-manager support
    def __enter__(self) -> MockLoggingHandler:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()
