"""
Serialization / Roundtrip Test Harness (Harness 15 of 36)

Tests encode→decode roundtrip survival across six formats:
JSON, CSV, binary/struct, pickle, XML, and INI/ConfigParser.

Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import csv
import io
import json
import math
import pickle
import struct
import configparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum, auto
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.parse


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
    lossy_fields: List[str] = field(default_factory=list)


@dataclass
class SerializationReport:
    results: List[RoundtripResult] = field(default_factory=list)

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
    def detect(original: Dict[str, Any], decoded: Dict[str, Any]) -> List[str]:
        """Return list of field names where data changed type or value."""
        lossy: List[str] = []
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
        if type(a) != type(b):
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
            return all(LossDetector._values_equal(x, y) for x, y in zip(a, b))
        return a == b


# ---------------------------------------------------------------------------
# Format Tester
# ---------------------------------------------------------------------------

class FormatTester:
    """Encode and decode data in a specific format, returning a RoundtripResult."""

    # ---- JSON ----

    def test_json(self, data: Dict[str, Any]) -> RoundtripResult:
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
        except Exception as exc:
            return RoundtripResult(
                format=SerializationFormat.JSON,
                original=data,
                encoded=None,
                decoded=None,
                passed=False,
                lossy_fields=list(data.keys()),
            )

    def _detect_json_loss(self, original: Dict, decoded: Dict) -> List[str]:
        lossy: List[str] = []
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
            if type(orig) != type(dec) and not (isinstance(orig, bool) and isinstance(dec, bool)):
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

    def test_csv(self, data: Dict[str, Any]) -> RoundtripResult:
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

    def _detect_csv_loss(self, original: Dict, decoded: Dict) -> List[str]:
        lossy: List[str] = []
        for key in original:
            orig = original[key]
            dec = decoded.get(key, "")
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

    def test_binary(self, data: Dict[str, Any]) -> RoundtripResult:
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

    def _detect_binary_loss(self, original: Dict, decoded: Dict) -> List[str]:
        lossy: List[str] = []
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
                elif math.isnan(orig_f) and not math.isnan(dec_f):
                    lossy.append(key)
                    continue
                elif math.isinf(orig_f) and (not math.isinf(dec_f) or orig_f != dec_f):
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

    def _detect_pickle_loss(self, original: Any, decoded: Any) -> List[str]:
        if not isinstance(original, dict):
            return []
        lossy: List[str] = []
        for key in original:
            orig = original[key]
            dec = decoded.get(key)
            if not LossDetector._values_equal(orig, dec):
                lossy.append(key)
        return lossy

    # ---- XML ----

    def test_xml(self, data: Dict[str, Any]) -> RoundtripResult:
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

    def _detect_xml_loss(self, original: Dict, decoded: Dict) -> List[str]:
        lossy: List[str] = []
        for key in original:
            orig = original[key]
            dec = decoded.get(str(key), "")
            # Everything becomes string in XML
            if not isinstance(orig, str):
                lossy.append(key)
        return lossy

    # ---- INI ----

    def test_ini(self, data: Dict[str, Any], section: str = "data") -> RoundtripResult:
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

    def _detect_ini_loss(self, original: Dict, decoded: Dict) -> List[str]:
        lossy: List[str] = []
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

    def run_all(self, data: Dict[str, Any]) -> SerializationReport:
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

    def _result_to_dict(self, result: RoundtripResult) -> Dict:
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
        self.server: Optional[HTTPServer] = None
        self._thread: Optional[Thread] = None

    def start(self):
        self.server = HTTPServer(("127.0.0.1", self.port), MockSerializationHandler)
        self._thread = Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server = None

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
