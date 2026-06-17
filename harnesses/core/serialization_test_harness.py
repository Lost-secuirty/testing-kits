"""
Serialization / Roundtrip Test Harness (Harness 15 of 36)

Tests encode→decode roundtrip survival across six formats:
JSON, CSV, binary/struct, pickle, XML, and INI/ConfigParser.

Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import io
import json
import math
import pickle
import struct

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from threading import Thread
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Enums and Dataclasses
# ---------------------------------------------------------------------------

class SerializationFormat(Enum):
    JSON = auto()
    CSV = auto()
    BINARY = auto()
    PICKLE = auto()
    XML = auto()
    INI = auto()


@dataclass
class RoundtripResult:
    format: SerializationFormat
    original: Any
    encoded: Any          # bytes or str after encoding
    decoded: Any          # value after decoding
    passed: bool
    lossy_fields: list[str] = field(default_factory=list)


@dataclass
class SerializationReport:
    results: list[RoundtripResult] = field(default_factory=list)

    @property
    def lossless_count(self) -> int:
        return sum(1 for r in self.results if not r.lossy_fields)

    @property
    def lossy_count(self) -> int:
        return sum(1 for r in self.results if r.lossy_fields)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)


# ---------------------------------------------------------------------------
# Loss Detection
# ---------------------------------------------------------------------------

class LossDetector:
    """Identifies which fields changed type or value after a roundtrip."""

    @staticmethod
    def detect(original: dict[str, Any], decoded: dict[str, Any]) -> list[str]:
        """Return list of field names where data changed type or value."""
        lossy: list[str] = []
        all_keys = set(original.keys()) | set(decoded.keys())
        for key in all_keys:
            if key not in original:
                lossy.append(key)
                continue
            if key not in decoded:
                lossy.append(key)
                continue
            orig_val = original[key]
            dec_val = decoded[key]
            if not LossDetector._values_equal(orig_val, dec_val):
                lossy.append(key)
        return lossy

    @staticmethod
    def _values_equal(a: Any, b: Any) -> bool:
        """Check if two values are semantically equal (handling NaN, inf, etc.)."""
        if type(a) is not type(b):
            # Allow int/float equivalence when value is the same
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if math.isnan(a) and math.isnan(b):
                    return True
                if math.isinf(a) and math.isinf(b):
                    return math.copysign(1, a) == math.copysign(1, b)
                return float(a) == float(b)
            return False
        if isinstance(a, float):
            if math.isnan(a) and math.isnan(b):
                return True
            if math.isinf(a) and math.isinf(b):
                return a == b
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a.keys()) != set(b.keys()):
                return False
            return all(LossDetector._values_equal(a[k], b[k]) for k in a)
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                return False
            return all(LossDetector._values_equal(x, y) for x, y in zip(a, b, strict=False))
        return a == b


# ---------------------------------------------------------------------------
# Format Tester
# ---------------------------------------------------------------------------

class FormatTester:
    """Encode and decode data in a specific format, returning a RoundtripResult."""

    # ---- JSON ----

    def test_json(self, data: dict[str, Any]) -> RoundtripResult:
        """JSON roundtrip. Known lossy: tuple→list, large floats may become inf."""
        try:
            encoded = json.dumps(data, allow_nan=True)
            decoded = json.loads(encoded)
            lossy = self._detect_json_loss(data, decoded)
            return RoundtripResult(
                format=SerializationFormat.JSON,
                original=data,
                encoded=encoded,
                decoded=decoded,
                passed=True,
                lossy_fields=lossy,
            )
        except Exception:
            return RoundtripResult(
                format=SerializationFormat.JSON,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()),
            )

    def _detect_json_loss(self, original: dict, decoded: dict) -> list[str]:
        lossy: list[str] = []
        for key in original:
            orig = original[key]
            dec = decoded.get(key)
            # tuple → list coercion
            if isinstance(orig, tuple) and isinstance(dec, list):
                lossy.append(key)
                continue
            # NaN/inf roundtrip: json.loads re-parses NaN/Infinity back correctly
            if isinstance(orig, float) and isinstance(dec, float):
                if math.isnan(orig) and math.isnan(dec):
                    continue
                if math.isinf(orig) and math.isinf(dec) and orig == dec:
                    continue
            # Type mismatch
            if type(orig) is not type(dec) and not (isinstance(orig, bool) and isinstance(dec, bool)):
                # bool is subclass of int; handle carefully
                if isinstance(orig, bool) or isinstance(dec, bool):
                    if orig != dec:
                        lossy.append(key)
                    continue
                lossy.append(key)
                continue
            if orig != dec:
                lossy.append(key)
        return lossy

    # ---- CSV ----

    def test_csv(self, data: dict[str, Any]) -> RoundtripResult:
        """CSV roundtrip. Everything becomes a string — always lossy for non-str."""
        try:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=list(data.keys()))
            writer.writeheader()
            writer.writerow({k: str(v) for k, v in data.items()})
            encoded = buf.getvalue()

            buf2 = io.StringIO(encoded)
            reader = csv.DictReader(buf2)
            rows = list(reader)
            decoded = rows[0] if rows else {}

            lossy = self._detect_csv_loss(data, decoded)
            return RoundtripResult(
                format=SerializationFormat.CSV,
                original=data,
                encoded=encoded,
                decoded=decoded,
                passed=True,
                lossy_fields=lossy,
            )
        except Exception:
            return RoundtripResult(
                format=SerializationFormat.CSV,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()),
            )

    def _detect_csv_loss(self, original: dict, decoded: dict) -> list[str]:
        lossy: list[str] = []
        for key in original:
            orig = original[key]
            decoded.get(key, "")
            # In CSV everything is a string; any non-string value is lossy
            if not isinstance(orig, str):
                lossy.append(key)
        return lossy

    # ---- BINARY (struct) ----

    # We support a fixed schema: int, float, bool encoded as specific struct fields.
    # For keys not in the schema, they are excluded (lossy).

    BINARY_STRUCT_FORMAT = "!idf?"  # big-endian: int, double(64-bit), float(32-bit), bool
    BINARY_KEYS = ["int_val", "double_val", "float_val", "bool_val"]
    BINARY_DEFAULTS = {"int_val": 0, "double_val": 0.0, "float_val": 0.0, "bool_val": False}

    def test_binary(self, data: dict[str, Any]) -> RoundtripResult:
        """Binary/struct roundtrip. Fixed-width schema; precision loss for floats."""
        try:
            int_val = int(data.get("int_val", 0))
            double_val = float(data.get("double_val", 0.0))
            float_val = float(data.get("float_val", 0.0))
            bool_val = bool(data.get("bool_val", False))

            # Handle NaN/inf for struct (struct supports them in 'd' and 'f')
            encoded = struct.pack(
                self.BINARY_STRUCT_FORMAT,
                int_val,
                double_val,
                float_val,
                bool_val,
            )

            unpacked = struct.unpack(self.BINARY_STRUCT_FORMAT, encoded)
            decoded = {
                "int_val": unpacked[0],
                "double_val": unpacked[1],
                "float_val": unpacked[2],
                "bool_val": unpacked[3],
            }

            lossy = self._detect_binary_loss(data, decoded)
            return RoundtripResult(
                format=SerializationFormat.BINARY,
                original=data,
                encoded=encoded,
                decoded=decoded,
                passed=True,
                lossy_fields=lossy,
            )
        except Exception:
            return RoundtripResult(
                format=SerializationFormat.BINARY,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()),
            )

    def _detect_binary_loss(self, original: dict, decoded: dict) -> list[str]:
        lossy: list[str] = []
        for key in self.BINARY_KEYS:
            if key not in original:
                continue
            orig = original[key]
            dec = decoded.get(key)
            if dec is None:
                lossy.append(key)
                continue
            # float_val uses single-precision (4 bytes) → precision loss expected
            if key == "float_val":
                # Re-pack with single precision to check
                orig_f = float(orig)
                dec_f = float(dec)
                if not math.isnan(orig_f) and not math.isinf(orig_f):
                    if abs(orig_f - dec_f) > 1e-5 * (abs(orig_f) + 1e-30):
                        lossy.append(key)
                        continue
                elif math.isnan(orig_f) and not math.isnan(dec_f) or math.isinf(orig_f) and (not math.isinf(dec_f) or orig_f != dec_f):
                    lossy.append(key)
                    continue
            else:
                if not LossDetector._values_equal(orig, dec):
                    lossy.append(key)
        # Keys in original but not in binary schema
        for key in original:
            if key not in self.BINARY_KEYS:
                lossy.append(key)
        return lossy

    # ---- PICKLE ----

    def test_pickle(self, data: Any) -> RoundtripResult:
        """Pickle roundtrip. Should be lossless for standard Python types."""
        try:
            encoded = pickle.dumps(data)
            decoded = pickle.loads(encoded)
            lossy = self._detect_pickle_loss(data, decoded)
            return RoundtripResult(
                format=SerializationFormat.PICKLE,
                original=data,
                encoded=encoded,
                decoded=decoded,
                passed=True,
                lossy_fields=lossy,
            )
        except Exception:
            return RoundtripResult(
                format=SerializationFormat.PICKLE,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()) if isinstance(data, dict) else [],
            )

    def _detect_pickle_loss(self, original: Any, decoded: Any) -> list[str]:
        if not isinstance(original, dict):
            return []
        lossy: list[str] = []
        for key in original:
            orig = original[key]
            dec = decoded.get(key)
            if not LossDetector._values_equal(orig, dec):
                lossy.append(key)
        return lossy

    # ---- XML ----

    def test_xml(self, data: dict[str, Any]) -> RoundtripResult:
        """XML roundtrip. Values become text nodes; type info is lost."""
        try:
            root = ET.Element("root")
            for key, val in data.items():
                child = ET.SubElement(root, "field")
                child.set("name", str(key))
                child.text = self._xml_encode_value(val)

            encoded = ET.tostring(root, encoding="unicode")

            # Decode
            parsed = ET.fromstring(encoded)
            decoded = {}
            for child in parsed:
                name = child.get("name")
                decoded[name] = child.text if child.text is not None else ""

            lossy = self._detect_xml_loss(data, decoded)
            return RoundtripResult(
                format=SerializationFormat.XML,
                original=data,
                encoded=encoded,
                decoded=decoded,
                passed=True,
                lossy_fields=lossy,
            )
        except Exception:
            return RoundtripResult(
                format=SerializationFormat.XML,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()),
            )

    def _xml_encode_value(self, val: Any) -> str:
        if val is None:
            return "__none__"
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, float):
            if math.isnan(val):
                return "nan"
            if math.isinf(val):
                return "inf" if val > 0 else "-inf"
        return str(val)

    def _detect_xml_loss(self, original: dict, decoded: dict) -> list[str]:
        lossy: list[str] = []
        for key in original:
            orig = original[key]
            decoded.get(str(key), "")
            # Everything becomes string in XML
            if not isinstance(orig, str):
                lossy.append(key)
        return lossy

    # ---- INI ----

    def test_ini(self, data: dict[str, Any], section: str = "data") -> RoundtripResult:
        """INI/ConfigParser roundtrip. All values become strings; no nesting."""
        try:
            config = configparser.ConfigParser()
            config[section] = {}
            for key, val in data.items():
                # ConfigParser keys are lowercased
                config[section][str(key)] = self._ini_encode_value(val)

            buf = io.StringIO()
            config.write(buf)
            encoded = buf.getvalue()

            config2 = configparser.ConfigParser()
            config2.read_string(encoded)
            decoded = {}
            if section in config2:
                for key in config2[section]:
                    decoded[key] = config2[section][key]

            lossy = self._detect_ini_loss(data, decoded)
            return RoundtripResult(
                format=SerializationFormat.INI,
                original=data,
                encoded=encoded,
                decoded=decoded,
                passed=True,
                lossy_fields=lossy,
            )
        except Exception:
            return RoundtripResult(
                format=SerializationFormat.INI,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()),
            )

    def _ini_encode_value(self, val: Any) -> str:
        if val is None:
            return ""
        return str(val)

    def _detect_ini_loss(self, original: dict, decoded: dict) -> list[str]:
        lossy: list[str] = []
        for key in original:
            orig = original[key]
            # INI keys are lowercased; check the lowercased key
            dec_key = str(key).lower()
            dec = decoded.get(dec_key)
            # All values are strings in INI; any non-string value is lossy
            if not isinstance(orig, str):
                lossy.append(key)
            elif dec != orig:
                # String values are preserved (unless they contain special chars)
                lossy.append(key)
        return lossy


# ---------------------------------------------------------------------------
# Roundtrip Runner
# ---------------------------------------------------------------------------

class RoundtripRunner:
    """Runs all serialization formats and returns a SerializationReport."""

    def __init__(self):
        self.tester = FormatTester()

    def run_all(self, data: dict[str, Any]) -> SerializationReport:
        """Run all formats on the given data dict."""
        report = SerializationReport()

        # JSON
        report.results.append(self.tester.test_json(data))

        # CSV
        report.results.append(self.tester.test_csv(data))

        # BINARY — only works on specific keys; create sub-dict
        binary_data = {k: data[k] for k in FormatTester.BINARY_KEYS if k in data}
        if binary_data:
            report.results.append(self.tester.test_binary(binary_data))
        else:
            report.results.append(self.tester.test_binary({"int_val": 0, "double_val": 0.0, "float_val": 0.0, "bool_val": False}))

        # PICKLE
        report.results.append(self.tester.test_pickle(data))

        # XML
        report.results.append(self.tester.test_xml(data))

        # INI — only scalar values
        ini_data = {k: v for k, v in data.items()
                    if v is None or isinstance(v, (str, int, float, bool))}
        report.results.append(self.tester.test_ini(ini_data))

        return report

    def run_format(self, fmt: SerializationFormat, data: Any) -> RoundtripResult:
        """Run a single format."""
        if fmt == SerializationFormat.JSON:
            return self.tester.test_json(data)
        elif fmt == SerializationFormat.CSV:
            return self.tester.test_csv(data)
        elif fmt == SerializationFormat.BINARY:
            return self.tester.test_binary(data)
        elif fmt == SerializationFormat.PICKLE:
            return self.tester.test_pickle(data)
        elif fmt == SerializationFormat.XML:
            return self.tester.test_xml(data)
        elif fmt == SerializationFormat.INI:
            return self.tester.test_ini(data)
        else:
            raise ValueError(f"Unknown format: {fmt}")


# ---------------------------------------------------------------------------
# Mock HTTP Server
# ---------------------------------------------------------------------------

DEFAULT_PORT = 19010


class MockSerializationHandler(BaseHTTPRequestHandler):
    """HTTP handler for the mock serialization server."""

    def log_message(self, fmt, *args):
        pass  # Suppress default access log

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json({"status": "ok"})

        elif path == "/formats":
            formats = [f.name for f in SerializationFormat]
            self._send_json({"formats": formats})

        elif path == "/roundtrip/json":
            sample = {"key": "value", "number": 42, "flag": True}
            tester = FormatTester()
            result = tester.test_json(sample)
            self._send_json(self._result_to_dict(result))

        elif path == "/roundtrip/csv":
            sample = {"name": "Alice", "age": "30", "city": "NYC"}
            tester = FormatTester()
            result = tester.test_csv(sample)
            self._send_json(self._result_to_dict(result))

        elif path == "/roundtrip/pickle":
            sample = {"key": "value", "num": 42}
            tester = FormatTester()
            result = tester.test_pickle(sample)
            self._send_json(self._result_to_dict(result))

        elif path == "/roundtrip/xml":
            sample = {"tag": "hello", "count": "5"}
            tester = FormatTester()
            result = tester.test_xml(sample)
            self._send_json(self._result_to_dict(result))

        elif path == "/roundtrip/ini":
            sample = {"host": "localhost", "port": "8080"}
            tester = FormatTester()
            result = tester.test_ini(sample)
            self._send_json(self._result_to_dict(result))

        elif path == "/report":
            sample = {
                "name": "test",
                "value": 123,
                "int_val": 10,
                "double_val": 3.14,
                "float_val": 1.5,
                "bool_val": True,
            }
            runner = RoundtripRunner()
            report = runner.run_all(sample)
            self._send_json({
                "lossless_count": report.lossless_count,
                "lossy_count": report.lossy_count,
                "passed_count": report.passed_count,
                "results": [self._result_to_dict(r) for r in report.results],
            })

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/roundtrip":
            try:
                payload = json.loads(body)
                fmt_name = payload.get("format", "JSON").upper()
                data = payload.get("data", {})
                fmt = SerializationFormat[fmt_name]
                runner = RoundtripRunner()
                result = runner.run_format(fmt, data)
                self._send_json(self._result_to_dict(result))
            except (KeyError, json.JSONDecodeError) as exc:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Bad request: {exc}".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, obj: Any):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _result_to_dict(self, result: RoundtripResult) -> dict:
        return {
            "format": result.format.name,
            "passed": result.passed,
            "lossy_fields": result.lossy_fields,
            "has_encoded": result.encoded is not None,
            "has_decoded": result.decoded is not None,
        }


class SerializationServer:
    """Manages the lifecycle of the mock HTTP server."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.server: HTTPServer | None = None
        self._thread: Thread | None = None

    def start(self):
        self.server = HTTPServer(("127.0.0.1", self.port), MockSerializationHandler)
        self._thread = Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        server = self.server
        if server:
            server.shutdown()
            server.server_close()
            self.server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def make_server(port: int = 0) -> SerializationServer:
    """Create and start a server on *port* (0 = OS-assigned dynamic port)."""
    if port == 0:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    srv = SerializationServer(port=port)
    srv.start()
    return srv


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (value, format) -> expected roundtrip outcome.
#
# A serialization harness only has teeth if it CATCHES a serializer/roundtrip
# that silently loses data, or a loss-detector that misses a real loss. The
# networked FormatTester above is the correct ORACLE; each Mutant below is a
# faithful in-process model of a genuine real-world serialization defect:
#
#   * a serializer that silently DROPS a field (the classic "skip falsy values"
#     optimization that quietly eats 0 / "" / False / None);
#   * a serializer that silently WIDENS int->float (treats every numeric pair as
#     equal), corrupting a 54-bit integer beyond float64's exact range while the
#     coercion-blind detector reports it lossless;
#   * a loss-detector that only inspects the fixed binary SCHEMA keys, so a field
#     the struct schema cannot encode is silently dropped yet reported lossless.
#
# The teeth run a PURE in-process roundtrip: zero clock/network/filesystem I/O,
# fully deterministic. An impl is a callable
#     roundtrip(value: Dict[str, Any], fmt: str) -> Tuple[Any, List[str]]
# returning (decoded, lossy_fields). prove() judges each impl against the
# corpus's FROZEN literal expectations (decoded value + which fields MUST be
# flagged lossy) -- it NEVER compares an impl's output to the oracle object at
# runtime, so the check is non-circular. prove(impl) is True iff a real loss
# goes undetected or a roundtrip mismatch is missed.
# ---------------------------------------------------------------------------

# Sentinel meaning "do not assert on the decoded value for this case" (used when
# only the lossy-field verdict is the load-bearing expectation).
_ANY = object()


@dataclass(frozen=True)
class SerCase:
    """One frozen roundtrip case with literal, hand-computed expectations."""
    name: str
    value: dict[str, Any]
    fmt: str                       # "json" | "binary"
    field: str                     # the field whose loss-verdict is asserted
    expect_lossy: bool             # MUST this field be flagged as lossy?
    expect_decoded: Any = _ANY     # expected decoded value of `field` (or _ANY)
    note: str = ""


# Cases chosen so the correct oracle agrees with every expectation AND at least
# one planted mutant gets each one WRONG. All literals are computed by hand from
# the serialization contract, never read back from the oracle.
SER_CORPUS: tuple[SerCase, ...] = (
    # --- JSON: a present, non-falsy field is preserved losslessly -----------
    SerCase("json_keeps_string", {"name": "alice"}, "json", "name",
            expect_lossy=False, expect_decoded="alice",
            note="JSON preserves a plain string roundtrip"),
    # --- JSON: a FALSY field (zero) is still present and lossless -----------
    # This is the teeth case for the drop-field mutant: a serializer that skips
    # falsy values would silently drop `count=0`, losing the field entirely.
    SerCase("json_keeps_zero", {"count": 0}, "json", "count",
            expect_lossy=False, expect_decoded=0,
            note="JSON must roundtrip an integer 0, not drop it as falsy"),
    SerCase("json_keeps_empty_string", {"label": ""}, "json", "label",
            expect_lossy=False, expect_decoded="",
            note="JSON must roundtrip an empty string, not drop it as falsy"),
    # --- JSON: tuple -> list coercion IS a real loss and must be flagged ----
    SerCase("json_tuple_to_list_lossy", {"t": (1, 2, 3)}, "json", "t",
            expect_lossy=True,
            note="JSON coerces tuple->list: a real type loss the detector must flag"),
    # --- JSON: a 54-bit integer survives exactly (no float coercion) --------
    # Teeth case for the coercion-blind mutant: 2**53+1 is the smallest integer
    # that float64 cannot represent. A serializer that silently widens ints to
    # float corrupts it to 2**53, while a coercion-blind detector reports it
    # lossless. The correct JSON oracle keeps the integer exact.
    SerCase("json_big_int_preserved", {"n": 2 ** 53 + 1}, "json", "n",
            expect_lossy=False, expect_decoded=2 ** 53 + 1,
            note="JSON must keep a 54-bit integer exact, never widen it to float"),
    # --- BINARY: a 64-bit double survives the 'd' field losslessly ----------
    SerCase("binary_double_lossless",
            {"int_val": 0, "double_val": 3.141592653589793,
             "float_val": 0.0, "bool_val": False},
            "binary", "double_val", expect_lossy=False,
            note="the 64-bit 'd' field preserves a full double"),
    # --- BINARY: a field the fixed struct schema cannot encode is DROPPED ---
    # Teeth case for the schema-drop-blind detector: the binary schema only has
    # slots for int/double/float/bool, so a string ``session_id`` is silently
    # discarded by the roundtrip. The correct detector flags the dropped field;
    # a detector that only inspects the four schema keys never notices it left.
    SerCase("binary_drops_unknown_field",
            {"int_val": 0, "double_val": 0.0, "float_val": 0.0,
             "bool_val": False, "session_id": "abc-123"},
            "binary", "session_id", expect_lossy=True,
            note="fixed binary schema cannot hold an extra field -> silently dropped"),
)


# --- ORACLE: reuse the harness's own correct FormatTester + LossDetector ----

def oracle_roundtrip(value: dict[str, Any], fmt: str) -> tuple[Any, list[str]]:
    """Correct roundtrip + loss detection, delegating to the harness's tested
    FormatTester. Returns (decoded, lossy_fields)."""
    tester = FormatTester()
    if fmt == "json":
        result = tester.test_json(value)
    elif fmt == "binary":
        result = tester.test_binary(value)
    else:  # pragma: no cover - corpus only uses json/binary
        raise ValueError(f"unsupported teeth format: {fmt!r}")
    return result.decoded, list(result.lossy_fields)


# --- Planted buggy twins (each models a real serialization defect) ----------

def drop_falsy_roundtrip(value: dict[str, Any], fmt: str) -> tuple[Any, list[str]]:
    """BUG: the serializer silently DROPS any field whose value is falsy.

    A startlingly common 'optimization' (``{k: v for k, v in d.items() if v}``)
    that quietly eats 0, "", False and None. The decoded dict loses the key
    entirely, and because this buggy detector only walks the DECODED keys it
    never notices the field went missing -> silent data loss.
    """
    if fmt == "json":
        pruned = {k: v for k, v in value.items() if v}  # BUG: drops falsy
        encoded = json.dumps(pruned, allow_nan=True)
        decoded = json.loads(encoded)
    elif fmt == "binary":
        # mirror oracle for binary so this mutant is isolated to the JSON drop
        return oracle_roundtrip(value, fmt)
    else:  # pragma: no cover
        raise ValueError(fmt)
    # BUG: detector only iterates decoded keys, so a dropped key is invisible.
    lossy: list[str] = []
    for key in decoded:
        if value.get(key) != decoded.get(key):
            lossy.append(key)
    return decoded, lossy


def int_float_blind_roundtrip(value: dict[str, Any], fmt: str) -> tuple[Any, list[str]]:
    """BUG: the JSON serializer silently widens every int to a float, AND the
    detector treats any int/float pair as interchangeable.

    Coercing ints to float (a real defect in some 'normalize-before-encode'
    pipelines) is lossless for small values but silently CORRUPTS any integer
    above 2**53, which float64 cannot represent exactly (e.g. 2**53+1 -> 2**53).
    Because the detector exempts all numeric pairs from comparison, the value
    corruption is never reported -> silent data loss.
    """
    if fmt != "json":
        return oracle_roundtrip(value, fmt)
    coerced = {k: (float(v) if isinstance(v, int) and not isinstance(v, bool) else v)
               for k, v in value.items()}  # BUG: int -> float widening
    encoded = json.dumps(coerced, allow_nan=True)
    decoded = json.loads(encoded)
    lossy: list[str] = []
    for key in value:
        orig = value[key]
        dec = decoded.get(key)
        # BUG: any two numbers are "equal" regardless of type/value/precision.
        if isinstance(orig, (int, float)) and isinstance(dec, (int, float)):
            continue
        if orig != dec:
            lossy.append(key)
    return decoded, lossy


def schema_drop_blind_roundtrip(value: dict[str, Any], fmt: str) -> tuple[Any, list[str]]:
    """BUG: the binary loss-detector only inspects the four schema keys.

    The fixed struct schema (int/double/float/bool) physically cannot encode any
    other field, so an extra key is silently discarded on encode. The correct
    detector flags such dropped fields, but this detector iterates ONLY the known
    schema keys -- so a field that fell off the edge of the schema is reported
    lossless. A classic 'we only diff the columns we know about' blind spot.
    """
    if fmt != "binary":
        return oracle_roundtrip(value, fmt)
    decoded, _ = oracle_roundtrip(value, fmt)
    lossy: list[str] = []
    for key in FormatTester.BINARY_KEYS:  # BUG: never looks at non-schema keys
        if key not in value:
            continue
        if not LossDetector._values_equal(value[key], decoded.get(key)):
            lossy.append(key)
    return decoded, lossy


def prove(impl: Callable[[dict[str, Any], str], tuple[Any, list[str]]]) -> bool:
    """True iff ``impl`` MISHANDLES any frozen corpus case (i.e. the bug is
    caught): a real loss goes unflagged, a clean field is wrongly flagged, the
    decoded value diverges from the frozen literal, or a field is dropped.

    Non-circular + deterministic: every expectation is a literal baked into
    SER_CORPUS, never read from the oracle; there is no RNG, clock, network, or
    filesystem access. An impl that raises on a corpus case counts as caught.
    """
    for case in SER_CORPUS:
        try:
            decoded, lossy = impl(case.value, case.fmt)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        # 1. The field's loss verdict must match the frozen expectation.
        flagged = case.field in lossy
        if flagged != case.expect_lossy:
            return True
        # 2. For lossless cases, the decoded value must survive intact. A
        #    dropped field shows up here as a missing key -> caught.
        if case.expect_decoded is not _ANY:
            if not isinstance(decoded, dict) or case.field not in decoded:
                return True
            if decoded[case.field] != case.expect_decoded:
                return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_roundtrip"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_roundtrip,
    mutants=(
        Mutant("drops_falsy_field", drop_falsy_roundtrip,
               "serializer silently drops falsy fields (0/''/False) -> field lost, "
               "detector never notices the missing key"),
        Mutant("int_float_coercion_blind", int_float_blind_roundtrip,
               "serializer widens int->float and the detector ignores numeric "
               "pairs -> a 54-bit integer is silently corrupted (2**53+1 -> 2**53)"),
        Mutant("schema_drop_blind", schema_drop_blind_roundtrip,
               "binary loss-detector only inspects the four schema keys -> misses "
               "a field the fixed struct schema silently dropped"),
    ),
    corpus_size=len(SER_CORPUS),
    kind="oracle_swap",
    notes="a serializer must not silently drop a field, and the loss-detector "
          "must flag tuple->list coercion, int->float corruption of a 54-bit "
          "integer, and a field dropped by the fixed binary schema",
)


def list_scenarios() -> list[str]:
    """Names of the frozen corpus cases (the teeth scenarios)."""
    return [c.name for c in SER_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/serialization")

    # 1. The correct oracle must agree with every frozen corpus expectation.
    for case in SER_CORPUS:
        decoded, lossy = oracle_roundtrip(case.value, case.fmt)
        report.add(f"oracle_lossy:{case.name}", case.expect_lossy,
                   case.field in lossy, detail=case.note)
        if case.expect_decoded is not _ANY:
            actual = decoded.get(case.field) if isinstance(decoded, dict) else None
            report.add(f"oracle_decoded:{case.name}", case.expect_decoded, actual,
                       detail=case.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serialization / roundtrip controls")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen corpus case names")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    _sys.exit(main())
