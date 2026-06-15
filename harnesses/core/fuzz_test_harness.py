"""
Fuzz Test Harness (Harness 10 of 36)
Feeds random/malformed/adversarial inputs to find crashes.
Pure stdlib, zero external dependencies.
Mock HTTP server on dynamic port (default 18960).
"""

import argparse
import hashlib
import math
import random
import socket
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
import time
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from typing import Any
from urllib.parse import parse_qs, urlparse

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import contextlib

from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ─── Constants ────────────────────────────────────────────────────────────────

MIN_INT = -(2 ** 63)
MAX_INT = 2 ** 63 - 1
DEFAULT_PORT = 18960

SQL_INJECTION_STRINGS = [
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "' UNION SELECT * FROM users --",
    "1; SELECT * FROM information_schema.tables",
    "' OR 1=1 --",
    "admin'--",
    "' OR 'x'='x",
    "1' AND '1'='1",
    "' OR ''='",
    "1 OR 1=1",
    "'; EXEC xp_cmdshell('dir'); --",
    "' AND SLEEP(5) --",
]

XSS_STRINGS = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    "'\"><script>alert(document.cookie)</script>",
    "<body onload=alert('XSS')>",
    "<<SCRIPT>alert('XSS');//<</SCRIPT>",
    "%3Cscript%3Ealert('xss')%3C/script%3E",
    "<iframe src=javascript:alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//",
]

UNICODE_STRINGS = [
    "\x00",  # null char
    "￿",  # max BMP
    "\ud800",  # surrogate
    "𝒉𝒆𝒍𝒍𝒐",  # mathematical script
    "héllo",
    "日本語",
    "العربية",
    "🎉🔥💯",
    "​",  # zero-width space
    "‮",  # right-to-left override
    "",  # next line
    "A" * 100 + "\x00" + "B" * 100,
]

BOUNDARY_INTS = [
    0, -1, 1, MIN_INT, MAX_INT,
    MIN_INT + 1, MAX_INT - 1,
    -2 ** 31, 2 ** 31 - 1,  # 32-bit boundaries
    -2 ** 15, 2 ** 15 - 1,  # 16-bit boundaries
    -2 ** 7, 2 ** 7 - 1,    # 8-bit boundaries
    2 ** 53,                  # float precision boundary
    -(2 ** 53),
]

BOUNDARY_FLOATS = [
    0.0, -0.0, 1.0, -1.0,
    float('inf'), float('-inf'), float('nan'),
    sys.float_info.max, sys.float_info.min,
    sys.float_info.epsilon,
    -sys.float_info.max, -sys.float_info.min,
    1e308, -1e308,
    1e-308, -1e-308,
    0.1 + 0.2,  # floating point imprecision
]

BOUNDARY_STRINGS = [
    "",                          # empty
    " ",                         # single space
    "  ",                        # double space
    "\t",                        # tab
    "\n",                        # newline
    "\r\n",                      # CRLF
    "\x00",                      # null byte
    "a" * 1000,                  # long string
    "a" * 10000,                 # very long string
    "a" * 100000,                # extremely long string
    "\x00" * 10,                 # null bytes
    "None",                      # Python None as string
    "null",                      # JSON null
    "undefined",                 # JS undefined
    "true", "false",             # boolean strings
    "NaN", "Infinity", "-Infinity",
    "../../../etc/passwd",       # path traversal
    "/dev/null",
    "CON", "PRN", "AUX",        # Windows reserved names
    "%s" * 100,                  # format string
    "{}" * 50,                   # Python format string
    "{{" * 50,
]


# ─── CrashRecord ──────────────────────────────────────────────────────────────

@dataclass
class CrashRecord:
    """Represents a single crash event."""
    input_value: Any
    exception_type: str
    message: str
    traceback_fingerprint: str
    full_traceback: str = ""
    iteration: int = 0

    def to_dict(self) -> dict:
        return {
            "input_value": repr(self.input_value),
            "exception_type": self.exception_type,
            "message": self.message,
            "traceback_fingerprint": self.traceback_fingerprint,
            "iteration": self.iteration,
        }


# ─── CrashClassifier ──────────────────────────────────────────────────────────

class CrashClassifier:
    """Classifies and deduplicates crashes by exception type and location."""

    SEVERITY_MAP = {
        "MemoryError": "critical",
        "RecursionError": "critical",
        "SystemError": "critical",
        "OverflowError": "high",
        "ZeroDivisionError": "high",
        "AssertionError": "high",
        "ValueError": "medium",
        "TypeError": "medium",
        "IndexError": "medium",
        "KeyError": "medium",
        "AttributeError": "medium",
        "RuntimeError": "medium",
        "NotImplementedError": "low",
        "StopIteration": "low",
        "UnicodeDecodeError": "low",
        "UnicodeEncodeError": "low",
        "OSError": "low",
        "IOError": "low",
    }

    def __init__(self):
        self._seen_fingerprints: dict[str, CrashRecord] = {}
        self._by_type: dict[str, list[CrashRecord]] = defaultdict(list)
        self._all_crashes: list[CrashRecord] = []

    def classify(self, record: CrashRecord) -> tuple[bool, str]:
        """
        Classify and store a crash record.
        Returns (is_new, severity).
        """
        is_new = record.traceback_fingerprint not in self._seen_fingerprints
        if is_new:
            self._seen_fingerprints[record.traceback_fingerprint] = record
        self._by_type[record.exception_type].append(record)
        self._all_crashes.append(record)
        severity = self.SEVERITY_MAP.get(record.exception_type, "unknown")
        return is_new, severity

    def get_unique_crashes(self) -> list[CrashRecord]:
        return list(self._seen_fingerprints.values())

    def get_crashes_by_type(self, exception_type: str) -> list[CrashRecord]:
        return list(self._by_type.get(exception_type, []))

    def get_all_crashes(self) -> list[CrashRecord]:
        return list(self._all_crashes)

    def get_type_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._by_type.items()}

    def get_severity(self, exception_type: str) -> str:
        return self.SEVERITY_MAP.get(exception_type, "unknown")

    def summary(self) -> dict:
        return {
            "total_crashes": len(self._all_crashes),
            "unique_crashes": len(self._seen_fingerprints),
            "by_type": self.get_type_counts(),
            "severity_breakdown": self._severity_breakdown(),
        }

    def _severity_breakdown(self) -> dict[str, int]:
        breakdown: dict[str, int] = defaultdict(int)
        for exc_type, crashes in self._by_type.items():
            sev = self.SEVERITY_MAP.get(exc_type, "unknown")
            breakdown[sev] += len(crashes)
        return dict(breakdown)


# ─── FuzzReport ───────────────────────────────────────────────────────────────

@dataclass
class FuzzReport:
    """Aggregates fuzz run results."""
    total_iterations: int = 0
    successful_runs: int = 0
    crashed_runs: int = 0
    unique_crashes: int = 0
    crash_records: list[CrashRecord] = field(default_factory=list)
    coverage_hints: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    seed: int = 0

    @property
    def survival_rate(self) -> float:
        if self.total_iterations == 0:
            return 1.0
        return self.successful_runs / self.total_iterations

    @property
    def crash_rate(self) -> float:
        if self.total_iterations == 0:
            return 0.0
        return self.crashed_runs / self.total_iterations

    def to_dict(self) -> dict:
        return {
            "total_iterations": self.total_iterations,
            "successful_runs": self.successful_runs,
            "crashed_runs": self.crashed_runs,
            "unique_crashes": self.unique_crashes,
            "survival_rate": self.survival_rate,
            "crash_rate": self.crash_rate,
            "elapsed_seconds": self.elapsed_seconds,
            "seed": self.seed,
            "coverage_hints": self.coverage_hints,
            "crashes": [r.to_dict() for r in self.crash_records],
        }

    def __str__(self) -> str:
        return (
            f"FuzzReport(iterations={self.total_iterations}, "
            f"survival={self.survival_rate:.1%}, "
            f"unique_crashes={self.unique_crashes})"
        )


# ─── Fingerprinting ───────────────────────────────────────────────────────────

def _make_fingerprint(exc: Exception, tb_str: str) -> str:
    """Create a deduplication fingerprint from exception type + last frame."""
    exc_type = type(exc).__name__
    # Extract last meaningful frame from traceback
    lines = [l.strip() for l in tb_str.splitlines() if l.strip()]
    # Find "File ... line ..." frames
    frames = [l for l in lines if l.startswith("File ")]
    last_frame = frames[-1] if frames else ""
    raw = f"{exc_type}:{last_frame}"
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()[:12]


# ─── Generators ───────────────────────────────────────────────────────────────

def fuzz_int(rng: random.Random, include_boundary: bool = True) -> int:
    """Generate a fuzz integer value."""
    choices = list(BOUNDARY_INTS) if include_boundary else []
    # Add random values
    choices.extend([
        rng.randint(MIN_INT, MAX_INT),
        rng.randint(-1000, 1000),
        rng.randint(0, 255),
        rng.randint(-128, 127),
    ])
    return rng.choice(choices)


def fuzz_float(rng: random.Random, include_boundary: bool = True) -> float:
    """Generate a fuzz float value."""
    choices = list(BOUNDARY_FLOATS) if include_boundary else []
    choices.extend([
        rng.uniform(-1e10, 1e10),
        rng.uniform(-1.0, 1.0),
        rng.gauss(0, 1),
        rng.expovariate(1),
    ])
    return rng.choice(choices)


def fuzz_string(rng: random.Random, include_boundary: bool = True) -> str:
    """Generate a fuzz string value."""
    choices = []
    if include_boundary:
        choices.extend(BOUNDARY_STRINGS)
        choices.extend(SQL_INJECTION_STRINGS)
        choices.extend(XSS_STRINGS)
        choices.extend(UNICODE_STRINGS)

    # Random strings
    length = rng.choice([0, 1, 10, 100, 1000])
    charset = rng.choice([
        "abcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "!@#$%^&*()_+-=[]{}|;':\",./<>?",
        "\x00\x01\x02\x03\x04\x05\xff\xfe",
        "abcABC123!@#\x00\xff",
    ])
    random_str = "".join(rng.choice(charset) for _ in range(length))
    choices.append(random_str)

    return rng.choice(choices)


def fuzz_bytes(rng: random.Random, include_boundary: bool = True) -> bytes:
    """Generate fuzz bytes."""
    choices = [b"", b"\x00", b"\xff" * 8, b"\x00" * 8]
    if include_boundary:
        choices.extend([
            bytes(range(256)),
            b"\x00\xff" * 50,
            b"\xde\xad\xbe\xef",
            b"\x89PNG\r\n\x1a\n",  # PNG magic
            b"PK\x03\x04",         # ZIP magic
            b"%PDF-1.4",            # PDF magic
        ])

    length = rng.choice([0, 1, 16, 64, 256, 1024])
    random_bytes = bytes(rng.randint(0, 255) for _ in range(length))
    choices.append(random_bytes)

    return rng.choice(choices)


def fuzz_list(rng: random.Random, depth: int = 0) -> list:
    """Generate a fuzz list."""
    if depth > 3:
        return []

    length = rng.choice([0, 1, 2, 10, 100])
    generators = [
        lambda: fuzz_int(rng, False),
        lambda: fuzz_float(rng, False),
        lambda: fuzz_string(rng, False),
        lambda: None,
        lambda: True,
        lambda: False,
    ]
    if depth < 2:
        generators.append(lambda: fuzz_list(rng, depth + 1))
        generators.append(lambda: fuzz_dict(rng, depth + 1))

    return [rng.choice(generators)() for _ in range(length)]


def fuzz_dict(rng: random.Random, depth: int = 0) -> dict:
    """Generate a fuzz dict."""
    if depth > 3:
        return {}

    length = rng.choice([0, 1, 2, 5])
    result = {}
    for _ in range(length):
        key = rng.choice([
            fuzz_string(rng, False),
            fuzz_int(rng, False),
            str(rng.randint(0, 100)),
        ])
        generators = [
            lambda: fuzz_int(rng, False),
            lambda: fuzz_float(rng, False),
            lambda: fuzz_string(rng, False),
            lambda: None,
            lambda: fuzz_list(rng, depth + 1),
        ]
        result[key] = rng.choice(generators)()
    return result


def fuzz_none() -> None:
    """Return None (for testing None handling)."""
    return None


def fuzz_bool(rng: random.Random) -> bool:
    return rng.choice([True, False])


# ─── BoundaryExplorer ─────────────────────────────────────────────────────────

class BoundaryExplorer:
    """
    Systematic edge-case probing for various data types.
    Generates comprehensive boundary and adversarial values.
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def int_boundaries(self) -> list[int]:
        return list(BOUNDARY_INTS)

    def float_boundaries(self) -> list[float]:
        return list(BOUNDARY_FLOATS)

    def string_boundaries(self) -> list[str]:
        return (
            list(BOUNDARY_STRINGS)
            + list(SQL_INJECTION_STRINGS)
            + list(XSS_STRINGS)
            + list(UNICODE_STRINGS)
        )

    def bytes_boundaries(self) -> list[bytes]:
        return [
            b"",
            b"\x00",
            b"\xff",
            b"\x00\xff" * 8,
            bytes(range(256)),
            b"A" * 1000,
            b"\xde\xad\xbe\xef",
            b"\x89PNG\r\n\x1a\n",
            b"PK\x03\x04",
            b"%PDF-1.4",
            b"\xff\xfe",  # BOM
            b"\xef\xbb\xbf",  # UTF-8 BOM
        ]

    def none_boundary(self) -> list[None]:
        return [None]

    def bool_boundaries(self) -> list[bool]:
        return [True, False]

    def collection_boundaries(self) -> list[Any]:
        return [
            [], [None], [0, 0, 0],
            {}, {"key": None}, {"": ""},
            set(), {0, 1, 2},
            tuple(), (None,), (1, 2, 3),
        ]

    def all_boundaries(self) -> list[Any]:
        result: list[Any] = []
        result.extend(self.int_boundaries())
        result.extend(self.float_boundaries())
        result.extend(self.string_boundaries())
        result.extend(self.bytes_boundaries())
        result.extend(self.none_boundary())
        result.extend(self.bool_boundaries())
        result.extend(self.collection_boundaries())
        return result

    def probe_function(
        self,
        func: Callable,
        input_type: str = "all",
    ) -> list[CrashRecord]:
        """Probe a function with boundary values for the given type."""
        if input_type == "int":
            values = self.int_boundaries()
        elif input_type == "float":
            values = self.float_boundaries()
        elif input_type == "string":
            values = self.string_boundaries()
        elif input_type == "bytes":
            values = self.bytes_boundaries()
        else:
            values = self.all_boundaries()

        crashes = []
        for val in values:
            try:
                func(val)
            except Exception as exc:
                tb_str = traceback.format_exc()
                fp = _make_fingerprint(exc, tb_str)
                record = CrashRecord(
                    input_value=val,
                    exception_type=type(exc).__name__,
                    message=str(exc),
                    traceback_fingerprint=fp,
                    full_traceback=tb_str,
                )
                crashes.append(record)
        return crashes


# ─── FuzzRunner ───────────────────────────────────────────────────────────────

class FuzzRunner:
    """
    Core fuzzing engine.
    Calls a target function with generated inputs, catching all exceptions,
    classifying crashes, and deduplicating via fingerprint.
    """

    def __init__(self, seed: int = 42, max_iterations: int = 1000):
        self.seed = seed
        self.max_iterations = max_iterations
        self._rng = random.Random(seed)
        self.classifier = CrashClassifier()
        self._coverage_hints: list[str] = []

    def _generate_input(self, input_type: str) -> Any:
        """Generate a single fuzz input of the requested type."""
        generators = {
            "int": lambda: fuzz_int(self._rng),
            "float": lambda: fuzz_float(self._rng),
            "string": lambda: fuzz_string(self._rng),
            "str": lambda: fuzz_string(self._rng),
            "bytes": lambda: fuzz_bytes(self._rng),
            "list": lambda: fuzz_list(self._rng),
            "dict": lambda: fuzz_dict(self._rng),
            "none": fuzz_none,
            "bool": lambda: fuzz_bool(self._rng),
            "any": lambda: self._rng.choice([
                fuzz_int(self._rng, False),
                fuzz_float(self._rng, False),
                fuzz_string(self._rng, False),
                fuzz_bytes(self._rng, False),
                fuzz_list(self._rng),
                fuzz_dict(self._rng),
                None,
            ]),
        }
        gen = generators.get(input_type, generators["any"])
        return gen()

    def fuzz(
        self,
        target: Callable,
        input_type: str = "any",
        iterations: int | None = None,
        timeout_seconds: float | None = None,
    ) -> FuzzReport:
        """
        Fuzz a function by calling it with generated inputs.
        Returns a FuzzReport with crash statistics.
        """
        num_iterations = iterations or self.max_iterations
        start_time = time.monotonic()
        report = FuzzReport(seed=self.seed)

        for i in range(num_iterations):
            if timeout_seconds is not None:
                if time.monotonic() - start_time > timeout_seconds:
                    break

            inp = self._generate_input(input_type)
            report.total_iterations += 1

            try:
                result = target(inp)
                report.successful_runs += 1
                # Track coverage hints
                if result is not None:
                    hint = f"iter={i} type={type(result).__name__}"
                    if hint not in self._coverage_hints:
                        self._coverage_hints.append(hint)
            except KeyboardInterrupt:
                break
            except SystemExit:
                break
            except Exception as exc:
                report.crashed_runs += 1
                tb_str = traceback.format_exc()
                fp = _make_fingerprint(exc, tb_str)
                record = CrashRecord(
                    input_value=inp,
                    exception_type=type(exc).__name__,
                    message=str(exc)[:500],
                    traceback_fingerprint=fp,
                    full_traceback=tb_str,
                    iteration=i,
                )
                is_new, severity = self.classifier.classify(record)
                if is_new:
                    report.crash_records.append(record)

        report.unique_crashes = len(report.crash_records)
        report.coverage_hints = list(self._coverage_hints)
        report.elapsed_seconds = time.monotonic() - start_time
        return report

    def fuzz_with_inputs(
        self,
        target: Callable,
        inputs: list[Any],
    ) -> FuzzReport:
        """Fuzz with a provided list of inputs (deterministic)."""
        start_time = time.monotonic()
        report = FuzzReport(seed=self.seed)

        for i, inp in enumerate(inputs):
            report.total_iterations += 1
            try:
                target(inp)
                report.successful_runs += 1
            except KeyboardInterrupt:
                break
            except SystemExit:
                break
            except Exception as exc:
                report.crashed_runs += 1
                tb_str = traceback.format_exc()
                fp = _make_fingerprint(exc, tb_str)
                record = CrashRecord(
                    input_value=inp,
                    exception_type=type(exc).__name__,
                    message=str(exc)[:500],
                    traceback_fingerprint=fp,
                    full_traceback=tb_str,
                    iteration=i,
                )
                is_new, _ = self.classifier.classify(record)
                if is_new:
                    report.crash_records.append(record)

        report.unique_crashes = len(report.crash_records)
        report.elapsed_seconds = time.monotonic() - start_time
        return report

    def get_crash_summary(self) -> dict:
        return self.classifier.summary()


# ─── Mock HTTP Handler ────────────────────────────────────────────────────────

class MockFuzzHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP handler for fuzz testing HTTP endpoints.
    Records all requests, responds with configurable status/body.
    """

    # Class-level storage shared across handler instances
    received_requests: list[dict] = []
    response_status: int = 200
    response_body: bytes = b'{"status": "ok"}'
    response_headers: dict[str, str] = {"Content-Type": "application/json"}
    crash_on_path: str | None = None

    def log_message(self, format, *args):
        # Suppress default logging
        pass

    def _record_request(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        parsed = urlparse(self.path)
        record = {
            "method": self.command,
            "path": parsed.path,
            "query": parse_qs(parsed.query),
            "headers": dict(self.headers),
            "body": body,
            "timestamp": time.time(),
        }
        MockFuzzHandler.received_requests.append(record)
        return record

    def _send_response(self, record: dict):
        if (
            MockFuzzHandler.crash_on_path is not None
            and record["path"] == MockFuzzHandler.crash_on_path
        ):
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Simulated crash")
            return

        self.send_response(MockFuzzHandler.response_status)
        for k, v in MockFuzzHandler.response_headers.items():
            self.send_header(k, v)
        body = MockFuzzHandler.response_body
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        record = self._record_request()
        self._send_response(record)

    def do_POST(self):
        record = self._record_request()
        self._send_response(record)

    def do_PUT(self):
        record = self._record_request()
        self._send_response(record)

    def do_DELETE(self):
        record = self._record_request()
        self._send_response(record)

    def do_PATCH(self):
        record = self._record_request()
        self._send_response(record)

    def do_HEAD(self):
        record = self._record_request()
        self._send_response(record)

    @classmethod
    def reset(cls):
        cls.received_requests = []
        cls.response_status = 200
        cls.response_body = b'{"status": "ok"}'
        cls.response_headers = {"Content-Type": "application/json"}
        cls.crash_on_path = None


# ─── Mock HTTP Server ─────────────────────────────────────────────────────────

class FuzzHTTPServer:
    """
    Threaded mock HTTP server for fuzz testing HTTP endpoints.
    Starts on a dynamic port (or specified port).
    """

    def __init__(self, port: int = 0, host: str = "127.0.0.1"):
        self.host = host
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = port
        self._running = False

    @property
    def port(self) -> int:
        if self._server is None:
            return self._port
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 5.0) -> "FuzzHTTPServer":
        MockFuzzHandler.reset()
        self._server = HTTPServer((self.host, self._port), MockFuzzHandler)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        # Wait until port is open
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.01)
        return self

    def _serve(self):
        while self._running:
            with contextlib.suppress(Exception):
                self._server.handle_request()

    def stop(self):
        if self._server is not None:
            self._running = False
            # Unblock handle_request by connecting briefly
            try:
                with socket.create_connection(
                    (self.host, self.port), timeout=0.2
                ):
                    pass
            except OSError:
                pass
            self._server.server_close()
            self._server = None
            if self._thread is not None:
                self._thread.join(timeout=2.0)
                self._thread = None

    def get_requests(self) -> list[dict]:
        return list(MockFuzzHandler.received_requests)

    def set_response(
        self,
        status: int = 200,
        body: bytes = b'{"status": "ok"}',
        headers: dict[str, str] | None = None,
    ):
        MockFuzzHandler.response_status = status
        MockFuzzHandler.response_body = body
        MockFuzzHandler.response_headers = headers or {"Content-Type": "application/json"}

    def set_crash_path(self, path: str | None):
        MockFuzzHandler.crash_on_path = path

    def __enter__(self) -> "FuzzHTTPServer":
        return self.start()

    def __exit__(self, *_):
        self.stop()


# ─── HTTP Fuzz Client ─────────────────────────────────────────────────────────

class HTTPFuzzClient:
    """
    Sends fuzz HTTP requests to a target server using only stdlib.
    """

    def __init__(self, base_url: str, seed: int = 42, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.seed = seed
        self.timeout = timeout
        self._rng = random.Random(seed)

    def _send_request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict]:
        """Send an HTTP request using low-level socket."""
        import http.client
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80

        conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
        try:
            h = headers or {}
            if body is not None:
                h["Content-Length"] = str(len(body))
            conn.request(method, path, body=body, headers=h)
            resp = conn.getresponse()
            status = resp.status
            resp_body = resp.read()
            resp_headers = dict(resp.getheaders())
            return status, resp_body, resp_headers
        finally:
            conn.close()

    def fuzz_paths(self, num: int = 20) -> list[dict]:
        """Send requests with fuzzed URL paths."""
        results = []
        path_fuzzers = [
            lambda: "/" + fuzz_string(self._rng, False)[:50],
            lambda: "/../../../etc/passwd",
            lambda: "/" + "A" * 500,
            lambda: "/\x00",
            lambda: "/%00",
            lambda: "/?q=" + fuzz_string(self._rng, False)[:30],
            lambda: "/" + "%2e" * 10,
        ]
        for _ in range(num):
            path = self._rng.choice(path_fuzzers)()
            try:
                status, body, hdrs = self._send_request("GET", path)
                results.append({
                    "path": path, "status": status,
                    "error": None
                })
            except Exception as exc:
                results.append({"path": path, "status": None, "error": str(exc)})
        return results

    def fuzz_bodies(self, num: int = 20) -> list[dict]:
        """Send requests with fuzzed request bodies."""
        results = []
        for _ in range(num):
            body = fuzz_bytes(self._rng)
            ct = self._rng.choice([
                "application/json",
                "text/plain",
                "application/x-www-form-urlencoded",
                "application/octet-stream",
                "text/xml",
            ])
            try:
                status, resp_body, hdrs = self._send_request(
                    "POST", "/",
                    body=body,
                    headers={"Content-Type": ct},
                )
                results.append({"status": status, "error": None})
            except Exception as exc:
                results.append({"status": None, "error": str(exc)})
        return results


# ─── Mutation Engine ──────────────────────────────────────────────────────────

class MutationEngine:
    """
    Mutates existing inputs to explore nearby value space.
    Useful for coverage-guided fuzzing.
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def mutate(self, value: Any) -> Any:
        """Apply a random mutation to the input value."""
        if isinstance(value, int):
            return self._mutate_int(value)
        elif isinstance(value, float):
            return self._mutate_float(value)
        elif isinstance(value, str):
            return self._mutate_str(value)
        elif isinstance(value, bytes):
            return self._mutate_bytes(value)
        elif isinstance(value, list):
            return self._mutate_list(value)
        elif isinstance(value, dict):
            return self._mutate_dict(value)
        return value

    def _mutate_int(self, v: int) -> int:
        ops = [
            lambda x: x + 1,
            lambda x: x - 1,
            lambda x: x * 2,
            lambda x: x // 2 if x != 0 else 0,
            lambda x: -x,
            lambda x: x ^ (1 << self._rng.randint(0, 30)),
            lambda x: 0,
            lambda x: MAX_INT,
            lambda x: MIN_INT,
        ]
        return self._rng.choice(ops)(v)

    def _mutate_float(self, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            return self._rng.choice(BOUNDARY_FLOATS)
        ops = [
            lambda x: x + self._rng.gauss(0, abs(x) + 1),
            lambda x: x * 2,
            lambda x: x / 2,
            lambda x: -x,
            lambda x: float('nan'),
            lambda x: float('inf'),
            lambda x: 0.0,
        ]
        try:
            result = self._rng.choice(ops)(v)
            return result
        except Exception:
            return 0.0

    def _mutate_str(self, v: str) -> str:
        if not v:
            return self._rng.choice(BOUNDARY_STRINGS)
        ops = [
            lambda s: s + s,                                  # duplicate
            lambda s: s[:len(s)//2],                          # truncate
            lambda s: s[::-1],                                # reverse
            lambda s: s.upper(),
            lambda s: s.lower(),
            lambda s: s + "\x00",                             # append null
            lambda s: "\x00" + s,                             # prepend null
            lambda s: s.replace(s[0], "X") if s else s,      # replace first char
            lambda s: s + self._rng.choice(SQL_INJECTION_STRINGS),
            lambda s: self._rng.choice(XSS_STRINGS),
            lambda s: s * self._rng.randint(2, 10),
        ]
        return self._rng.choice(ops)(v)

    def _mutate_bytes(self, v: bytes) -> bytes:
        if not v:
            return bytes([self._rng.randint(0, 255)])
        b = bytearray(v)
        # Flip a random bit
        pos = self._rng.randint(0, len(b) - 1)
        b[pos] ^= (1 << self._rng.randint(0, 7))
        return bytes(b)

    def _mutate_list(self, v: list) -> list:
        if not v:
            return [None]
        result = list(v)
        op = self._rng.choice(["append", "remove", "shuffle", "duplicate"])
        if op == "append":
            result.append(None)
        elif op == "remove" and result:
            result.pop(self._rng.randint(0, len(result) - 1))
        elif op == "shuffle":
            self._rng.shuffle(result)
        elif op == "duplicate" and result:
            result.append(result[self._rng.randint(0, len(result) - 1)])
        return result

    def _mutate_dict(self, v: dict) -> dict:
        result = dict(v)
        op = self._rng.choice(["add", "remove", "modify"])
        if op == "add":
            result[fuzz_string(self._rng, False)] = None
        elif op == "remove" and result:
            k = self._rng.choice(list(result.keys()))
            del result[k]
        elif op == "modify" and result:
            k = self._rng.choice(list(result.keys()))
            result[k] = None
        return result


# ─── Corpus Manager ───────────────────────────────────────────────────────────

class CorpusManager:
    """
    Manages a corpus of interesting inputs for coverage-guided fuzzing.
    """

    def __init__(self, seed: int = 42, max_size: int = 1000):
        self._rng = random.Random(seed)
        self._corpus: list[Any] = []
        self._max_size = max_size
        self._mutation_engine = MutationEngine(seed)

    def add(self, value: Any) -> None:
        if len(self._corpus) < self._max_size:
            self._corpus.append(value)
        else:
            # Replace random entry
            idx = self._rng.randint(0, len(self._corpus) - 1)
            self._corpus[idx] = value

    def seed_with_boundaries(self, type_hint: str = "all") -> None:
        explorer = BoundaryExplorer()
        if type_hint == "int":
            for v in explorer.int_boundaries():
                self.add(v)
        elif type_hint == "str":
            for v in explorer.string_boundaries():
                self.add(v)
        elif type_hint == "float":
            for v in explorer.float_boundaries():
                self.add(v)
        else:
            for v in explorer.all_boundaries():
                self.add(v)

    def next_input(self) -> Any:
        if not self._corpus:
            return None
        base = self._rng.choice(self._corpus)
        return self._mutation_engine.mutate(base)

    def size(self) -> int:
        return len(self._corpus)

    def get_all(self) -> list[Any]:
        return list(self._corpus)


# ─── Differential Fuzzer ──────────────────────────────────────────────────────

class DifferentialFuzzer:
    """
    Compares two implementations for behavioral differences.
    Finds inputs where the outputs diverge.
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self.divergences: list[dict] = []

    def compare(
        self,
        impl_a: Callable,
        impl_b: Callable,
        inputs: list[Any],
        label_a: str = "impl_a",
        label_b: str = "impl_b",
    ) -> list[dict]:
        """
        Run both implementations on each input and record divergences.
        """
        self.divergences = []
        for inp in inputs:
            result_a = result_b = exc_a = exc_b = None
            try:
                result_a = impl_a(inp)
            except Exception as e:
                exc_a = type(e).__name__

            try:
                result_b = impl_b(inp)
            except Exception as e:
                exc_b = type(e).__name__

            # Check for divergence
            if exc_a != exc_b:
                self.divergences.append({
                    "input": repr(inp),
                    "divergence_type": "exception_mismatch",
                    label_a: f"raised {exc_a}" if exc_a else repr(result_a),
                    label_b: f"raised {exc_b}" if exc_b else repr(result_b),
                })
            elif exc_a is None and exc_b is None:
                try:
                    if result_a != result_b:
                        self.divergences.append({
                            "input": repr(inp),
                            "divergence_type": "result_mismatch",
                            label_a: repr(result_a),
                            label_b: repr(result_b),
                        })
                except Exception:
                    pass  # Some types don't support !=

        return self.divergences


# ─── Stats & Utilities ────────────────────────────────────────────────────────

def compute_entropy(data: bytes) -> float:
    """Compute Shannon entropy of byte data."""
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    n = len(data)
    entropy = 0.0
    for f in freq:
        if f > 0:
            p = f / n
            entropy -= p * math.log2(p)
    return entropy


def is_valid_utf8(data: bytes) -> bool:
    """Check if bytes are valid UTF-8."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def truncate_repr(value: Any, max_len: int = 100) -> str:
    """Safe repr that truncates long values."""
    r = repr(value)
    if len(r) > max_len:
        return r[:max_len] + "..."
    return r


def generate_seed_sequence(master_seed: int, count: int) -> list[int]:
    """Generate a reproducible sequence of seeds from a master seed."""
    rng = random.Random(master_seed)
    return [rng.randint(0, 2**32 - 1) for _ in range(count)]


# ─── Module-level convenience functions ───────────────────────────────────────

def quick_fuzz(
    target: Callable,
    input_type: str = "any",
    iterations: int = 100,
    seed: int = 42,
) -> FuzzReport:
    """Convenience wrapper for quick fuzzing sessions."""
    runner = FuzzRunner(seed=seed, max_iterations=iterations)
    return runner.fuzz(target, input_type=input_type, iterations=iterations)


def explore_boundaries(
    target: Callable,
    input_type: str = "all",
    seed: int = 42,
) -> list[CrashRecord]:
    """Convenience wrapper for boundary exploration."""
    explorer = BoundaryExplorer(seed=seed)
    return explorer.probe_function(target, input_type=input_type)


# ─── TEETH ──────────────────────────────────────────────────────────────────
#
# This harness IS a fuzzer / crash finder. It has teeth only if it CATCHES a
# target that crashes on an adversarial input while NOT flagging a robust target
# that survives the same corpus. The teeth here exercise the harness's own
# deterministic crash-detection path — ``FuzzRunner.fuzz_with_inputs`` — over a
# FROZEN literal corpus of inputs that a real, naive implementation would mishandle.
#
# An "impl" under test is a single-argument target callable. prove(impl) is True
# iff the harness's runner records >=1 crash for ``impl`` over the frozen corpus.
#
# Non-circularity: prove() never compares ``impl``'s output to the oracle's. It
# asks the harness "does this target crash on any frozen input?" and judges that
# against the corpus's frozen per-input expectations (``oracle_survives`` for
# every case is True, and each planted mutant has at least one case where it must
# crash). The oracle is the harness's correct robust target; each Mutant models a
# genuine real-world bug that crashes on a specific adversarial input the corpus
# pins down. Determinism: ``fuzz_with_inputs`` replays a FIXED input list with no
# RNG, and the corpus uses no clock / network / filesystem / thread timing.

# Each frozen input also pins how a fixed-width parser should treat it, so the
# corpus expectations are literal and hand-computed rather than read from the
# oracle. ``_TEETH_SEED`` keeps every internal RNG seeded for reproducibility.
_TEETH_SEED = 1234

# A 32-bit signed range used by the "overflow" mutant — the classic real-world
# bug where code assumes values fit a fixed-width register.
_INT32_MIN = -(2 ** 31)
_INT32_MAX = 2 ** 31 - 1

# The pipe delimiter the "unescaped delimiter" mutant naively splits on. A real
# CSV/log parser that splits on a raw delimiter corrupts (or, when it then indexes
# a fixed column, crashes on) any field that itself contains the delimiter.
_DELIM = "|"


@dataclass(frozen=True)
class FuzzCase:
    """One frozen fuzz input plus the literal expectation that the correct,
    robust oracle survives it (no exception). The mutants each crash on at least
    one of these inputs; the oracle crashes on none."""
    name: str
    value: Any
    oracle_survives: bool  # always True here: the robust oracle handles every case
    note: str = ""


# The frozen corpus: a hand-picked set of adversarial inputs covering the bug
# classes in the hint (huge int, fixed-width overflow, unescaped delimiter,
# empty/None handling, off-by-one on an empty sequence). Every value is a literal;
# nothing is generated at prove() time.
_FUZZ_CORPUS: tuple[FuzzCase, ...] = (
    FuzzCase("empty_string", "", True,
             "empty input: a robust parser returns a default, a naive one indexes [0]"),
    FuzzCase("none_value", None, True,
             "None: a robust target type-checks first; a naive one calls a method on None"),
    FuzzCase("zero_int", 0, True,
             "0: divide-by-zero bait for an unguarded reciprocal"),
    FuzzCase("int32_overflow", _INT32_MAX + 1, True,
             "2**31: overflows a fixed-width 32-bit field a naive impl asserts on"),
    FuzzCase("huge_int", 2 ** 70, True,
             "a value far beyond any fixed-width register"),
    FuzzCase("field_with_delimiter", f"user{_DELIM}id{_DELIM}x", True,
             "a value containing the delimiter: naive split() yields too many columns"),
    FuzzCase("plain_field", "alice", True,
             "an ordinary delimiter-free field both impls accept"),
    FuzzCase("empty_list", [], True,
             "empty sequence: off-by-one bait for an impl that reads element [0]"),
)


# --- ORACLE: a robust target that survives every frozen adversarial input ----

def oracle_target(value: Any) -> str:
    """Correct, defensive target: validates types and bounds, never crashes on
    any frozen corpus input. This is the robust reference the harness must NOT
    flag. It models a correctly-written record parser:

      * None / empty are handled explicitly,
      * integers are range-checked instead of stuffed into a fixed-width field,
      * delimited strings are split with a column cap so an embedded delimiter
        cannot blow up downstream indexing,
      * empty sequences are handled before any element access.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):  # bool before int (bool is a subclass of int)
        return f"bool:{value}"
    if isinstance(value, int):
        # Range-check instead of asserting a fixed-width fit; arbitrary precision.
        return f"int:{'big' if not (_INT32_MIN <= value <= _INT32_MAX) else 'ok'}"
    if isinstance(value, str):
        if value == "":
            return "empty"
        # maxsplit caps the column count, so an embedded delimiter is harmless.
        first = value.split(_DELIM, 1)[0]
        return f"str:{first}"
    if isinstance(value, (list, tuple)):
        return f"seq:{len(value)}"  # length first; never indexes a maybe-empty seq
    return f"other:{type(value).__name__}"


# --- Planted buggy twins (each crashes on a specific frozen adversarial input) ---

def overflow_target(value: Any) -> str:
    """BUG: assumes every integer fits a signed 32-bit register and ASSERTS the
    fit — the classic fixed-width overflow defect (think a C int, a database
    INTEGER column, or a protobuf int32). Crashes (AssertionError) on the frozen
    ``int32_overflow`` and ``huge_int`` cases; survives small ints and non-ints.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        assert _INT32_MIN <= value <= _INT32_MAX, "int32 overflow"  # BUG
        return "int:ok"
    if isinstance(value, str):
        return "empty" if value == "" else f"str:{value.split(_DELIM, 1)[0]}"
    if isinstance(value, (list, tuple)):
        return f"seq:{len(value)}"
    return f"other:{type(value).__name__}"


def unescaped_delimiter_target(value: Any) -> str:
    """BUG: splits a delimited field on the RAW delimiter with no maxsplit, then
    indexes a fixed column count — a real CSV/log-parsing defect. A value that
    itself contains the delimiter yields extra columns and the fixed index
    (``parts[1]`` expecting exactly two columns) is wrong / raises. Here it
    asserts the exact column count and crashes on ``field_with_delimiter``.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        return f"int:{'big' if not (_INT32_MIN <= value <= _INT32_MAX) else 'ok'}"
    if isinstance(value, str):
        if value == "":
            return "empty"
        parts = value.split(_DELIM)  # BUG: no maxsplit cap
        # naive code expects at most 2 columns ("name|value") and asserts it
        assert len(parts) <= 2, "unexpected extra delimiter columns"  # BUG
        return f"str:{parts[0]}"
    if isinstance(value, (list, tuple)):
        return f"seq:{len(value)}"
    return f"other:{type(value).__name__}"


def off_by_one_target(value: Any) -> str:
    """BUG: reads element [0] of a sequence / first char of a string before
    checking it is non-empty, and calls a method on None — the classic
    empty/None off-by-one and null-deref family. Crashes on the frozen
    ``empty_string`` (IndexError), ``empty_list`` (IndexError), and
    ``none_value`` (AttributeError) cases.
    """
    if value is None:
        return value.upper()  # BUG: null deref before the None guard
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        return f"int:{'big' if not (_INT32_MIN <= value <= _INT32_MAX) else 'ok'}"
    if isinstance(value, str):
        return f"str:{value[0]}"  # BUG: indexes [0] without an empty-string guard
    if isinstance(value, (list, tuple)):
        return f"seq:{value[0]}"  # BUG: indexes [0] without an empty-sequence guard
    return f"other:{type(value).__name__}"


def _frozen_inputs() -> list[Any]:
    """The frozen corpus input values, in order — the deterministic replay list."""
    return [case.value for case in _FUZZ_CORPUS]


def prove(impl: Callable[[Any], Any]) -> bool:
    """True iff the harness flags ``impl`` — i.e. ``FuzzRunner.fuzz_with_inputs``
    records at least one crash when ``impl`` is replayed over the FROZEN corpus.

    Deterministic + non-circular: ``fuzz_with_inputs`` replays a fixed input list
    with no RNG (the seed only labels the report), and the verdict is the harness's
    own crash count — never a comparison of ``impl``'s output to the oracle's. The
    corpus's frozen expectation is that the robust oracle survives every input, so
    a clean impl yields zero crashes (prove False) and any impl that crashes on a
    pinned adversarial input yields >=1 crash (prove True). No clock/network/
    filesystem/thread-timing is consulted.
    """
    runner = FuzzRunner(seed=_TEETH_SEED, max_iterations=len(_FUZZ_CORPUS))
    report = runner.fuzz_with_inputs(impl, _frozen_inputs())
    return report.crashed_runs >= 1


TEETH = Teeth(
    prove=prove,
    oracle=oracle_target,
    mutants=(
        Mutant("int32_overflow", overflow_target,
               "asserts every int fits a signed 32-bit field -> crashes on 2**31 "
               "and 2**70 (fixed-width register / INT column overflow)"),
        Mutant("unescaped_delimiter", unescaped_delimiter_target,
               "splits a field on the raw delimiter with no maxsplit and asserts "
               "the column count -> crashes when a value contains the delimiter"),
        Mutant("empty_off_by_one", off_by_one_target,
               "indexes [0] of an empty string/list and derefs None before guarding "
               "-> IndexError/AttributeError on empty and None inputs"),
    ),
    corpus_size=len(_FUZZ_CORPUS),
    kind="oracle_swap",
    notes="a robust target must survive every frozen adversarial input; the fuzzer "
          "must flag a target that crashes on int32 overflow, an unescaped "
          "delimiter, or an empty/None off-by-one",
)


def list_scenarios() -> list[str]:
    """Names of the frozen fuzz-corpus cases (the teeth scenarios)."""
    return [c.name for c in _FUZZ_CORPUS]


# ─── Report-based self-test — fails loud, reports findings, asserts the teeth ──

def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/fuzz")

    # 1. The correct oracle survives every frozen adversarial input (no crash).
    runner = FuzzRunner(seed=_TEETH_SEED, max_iterations=len(_FUZZ_CORPUS))
    oracle_run = runner.fuzz_with_inputs(oracle_target, _frozen_inputs())
    report.add("oracle_no_crashes", 0, oracle_run.crashed_runs,
               detail="the robust oracle must survive the whole frozen corpus")
    report.add("oracle_all_iterations", len(_FUZZ_CORPUS), oracle_run.total_iterations,
               detail="every frozen input is replayed exactly once")

    # 2. Each planted mutant crashes on at least one frozen input (harness flags it).
    for mutant in TEETH.mutants:
        m_run = FuzzRunner(
            seed=_TEETH_SEED, max_iterations=len(_FUZZ_CORPUS)
        ).fuzz_with_inputs(mutant.impl, _frozen_inputs())
        report.record(f"mutant_crashes:{mutant.name}", m_run.crashed_runs >= 1,
                      detail=mutant.note)

    # 3. The runner is deterministic: replaying the corpus twice is identical.
    run_a = FuzzRunner(seed=_TEETH_SEED, max_iterations=len(_FUZZ_CORPUS))
    run_b = FuzzRunner(seed=_TEETH_SEED, max_iterations=len(_FUZZ_CORPUS))
    crashes_a = run_a.fuzz_with_inputs(off_by_one_target, _frozen_inputs()).crashed_runs
    crashes_b = run_b.fuzz_with_inputs(off_by_one_target, _frozen_inputs()).crashed_runs
    report.add("runner_deterministic", crashes_a, crashes_b,
               detail="fuzz_with_inputs replays a fixed list -> identical crash count")

    # 4. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ─── Main ─────────────────────────────────────────────────────────────────────

def _run_demo() -> int:
    print("=== Fuzz Test Harness Demo ===\n")

    # Demo 1: Fuzz a simple division function
    def divide(x):
        return 100 / x

    print("Fuzzing divide(x) = 100/x ...")
    report = quick_fuzz(divide, input_type="int", iterations=50, seed=42)
    print(f"  {report}")
    print(f"  Survival rate: {report.survival_rate:.1%}")
    print()

    # Demo 2: Boundary exploration
    print("Exploring int boundaries for divide()...")
    crashes = explore_boundaries(divide, "int")
    for c in crashes[:3]:
        print(f"  Crash: {c.exception_type} on input={truncate_repr(c.input_value)}")
    print()

    # Demo 3: Mock HTTP server
    print("Starting mock HTTP server...")
    server = FuzzHTTPServer(port=0)
    server.start()
    print(f"  Server running at {server.base_url}")
    server.stop()
    print("  Server stopped.")
    print()

    print("Demo complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fuzz / crash-finding controls")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen fuzz-corpus case names")
    parser.add_argument("--demo", action="store_true",
                        help="run the original interactive demo")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    if args.demo:
        return _run_demo()
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
