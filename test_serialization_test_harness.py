"""
Tests for Serialization / Roundtrip Test Harness (Harness 15 of 36)
~80 tests covering all formats, lossiness detection, and edge cases.
"""

import json
import math
import pickle
import unittest
import urllib.request
import urllib.error

from serialization_test_harness import (
    SerializationFormat,
    RoundtripResult,
    FormatTester,
    LossDetector,
    RoundtripRunner,
    SerializationReport,
    MockSerializationHandler,
    SerializationServer,
    make_server,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _start_server():
    """Start a server on a dynamic port and return it."""
    return make_server(port=0)


# ===========================================================================
# SerializationFormat enum tests
# ===========================================================================

class TestSerializationFormatEnum(unittest.TestCase):

    def test_all_formats_exist(self):
        names = {f.name for f in SerializationFormat}
        self.assertIn("JSON", names)
        self.assertIn("CSV", names)
        self.assertIn("BINARY", names)
        self.assertIn("PICKLE", names)
        self.assertIn("XML", names)
        self.assertIn("INI", names)

    def test_six_formats(self):
        self.assertEqual(len(SerializationFormat), 6)

    def test_formats_are_distinct(self):
        values = [f.value for f in SerializationFormat]
        self.assertEqual(len(values), len(set(values)))


# ===========================================================================
# RoundtripResult dataclass tests
# ===========================================================================

class TestRoundtripResult(unittest.TestCase):

    def test_basic_construction(self):
        r = RoundtripResult(
            format=SerializationFormat.JSON,
            original={"a": 1},
            encoded='{"a": 1}',
            decoded={"a": 1},
            passed=True,
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.format, SerializationFormat.JSON)
        self.assertEqual(r.lossy_fields, [])

    def test_lossy_fields_default_empty(self):
        r = RoundtripResult(
            format=SerializationFormat.CSV,
            original={},
            encoded="",
            decoded={},
            passed=True,
        )
        self.assertIsInstance(r.lossy_fields, list)
        self.assertEqual(len(r.lossy_fields), 0)

    def test_lossy_fields_can_be_set(self):
        r = RoundtripResult(
            format=SerializationFormat.CSV,
            original={"x": 1},
            encoded="x\r\n1\r\n",
            decoded={"x": "1"},
            passed=True,
            lossy_fields=["x"],
        )
        self.assertIn("x", r.lossy_fields)

    def test_failed_result(self):
        r = RoundtripResult(
            format=SerializationFormat.BINARY,
            original={"bad": object()},
            encoded=None,
            decoded=None,
            passed=False,
        )
        self.assertFalse(r.passed)
        self.assertIsNone(r.encoded)


# ===========================================================================
# SerializationReport tests
# ===========================================================================

class TestSerializationReport(unittest.TestCase):

    def _make_report(self, results):
        r = SerializationReport()
        r.results = results
        return r

    def _make_result(self, fmt, passed=True, lossy=None):
        return RoundtripResult(
            format=fmt,
            original={},
            encoded="",
            decoded={},
            passed=passed,
            lossy_fields=lossy or [],
        )

    def test_empty_report(self):
        r = SerializationReport()
        self.assertEqual(r.lossless_count, 0)
        self.assertEqual(r.lossy_count, 0)
        self.assertEqual(r.passed_count, 0)
        self.assertEqual(r.failed_count, 0)

    def test_lossless_count(self):
        report = self._make_report([
            self._make_result(SerializationFormat.JSON, lossy=[]),
            self._make_result(SerializationFormat.PICKLE, lossy=[]),
            self._make_result(SerializationFormat.CSV, lossy=["a"]),
        ])
        self.assertEqual(report.lossless_count, 2)

    def test_lossy_count(self):
        report = self._make_report([
            self._make_result(SerializationFormat.CSV, lossy=["x"]),
            self._make_result(SerializationFormat.XML, lossy=["y"]),
            self._make_result(SerializationFormat.JSON, lossy=[]),
        ])
        self.assertEqual(report.lossy_count, 2)

    def test_passed_failed_count(self):
        report = self._make_report([
            self._make_result(SerializationFormat.JSON, passed=True),
            self._make_result(SerializationFormat.CSV, passed=True),
            self._make_result(SerializationFormat.BINARY, passed=False),
        ])
        self.assertEqual(report.passed_count, 2)
        self.assertEqual(report.failed_count, 1)


# ===========================================================================
# LossDetector tests
# ===========================================================================

class TestLossDetector(unittest.TestCase):

    def test_identical_dicts_no_loss(self):
        d = {"a": 1, "b": "hello", "c": True}
        self.assertEqual(LossDetector.detect(d, d), [])

    def test_type_change_detected(self):
        orig = {"x": 42}
        decoded = {"x": "42"}
        lossy = LossDetector.detect(orig, decoded)
        self.assertIn("x", lossy)

    def test_value_change_detected(self):
        orig = {"x": 10}
        decoded = {"x": 11}
        lossy = LossDetector.detect(orig, decoded)
        self.assertIn("x", lossy)

    def test_missing_key_detected(self):
        orig = {"a": 1, "b": 2}
        decoded = {"a": 1}
        lossy = LossDetector.detect(orig, decoded)
        self.assertIn("b", lossy)

    def test_extra_key_detected(self):
        orig = {"a": 1}
        decoded = {"a": 1, "b": 2}
        lossy = LossDetector.detect(orig, decoded)
        self.assertIn("b", lossy)

    def test_nan_equal_to_nan(self):
        orig = {"v": float("nan")}
        decoded = {"v": float("nan")}
        self.assertEqual(LossDetector.detect(orig, decoded), [])

    def test_inf_equal_to_inf(self):
        orig = {"v": float("inf")}
        decoded = {"v": float("inf")}
        self.assertEqual(LossDetector.detect(orig, decoded), [])

    def test_pos_neg_inf_differ(self):
        orig = {"v": float("inf")}
        decoded = {"v": float("-inf")}
        lossy = LossDetector.detect(orig, decoded)
        self.assertIn("v", lossy)

    def test_int_float_same_value(self):
        # int vs float with same numeric value — type differs but numeric equal
        orig = {"x": 5}
        decoded = {"x": 5.0}
        # LossDetector allows int/float when value matches
        lossy = LossDetector.detect(orig, decoded)
        self.assertEqual(lossy, [])

    def test_nested_list_equal(self):
        orig = {"lst": [1, 2, 3]}
        decoded = {"lst": [1, 2, 3]}
        self.assertEqual(LossDetector.detect(orig, decoded), [])

    def test_nested_list_different(self):
        orig = {"lst": [1, 2, 3]}
        decoded = {"lst": [1, 2, 4]}
        lossy = LossDetector.detect(orig, decoded)
        self.assertIn("lst", lossy)


# ===========================================================================
# FormatTester — JSON tests
# ===========================================================================

class TestFormatTesterJSON(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def test_json_basic_roundtrip(self):
        data = {"name": "Alice", "age": 30, "active": True}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["name"], "Alice")
        self.assertEqual(result.decoded["age"], 30)
        self.assertTrue(result.decoded["active"])

    def test_json_format_enum(self):
        result = self.tester.test_json({"x": 1})
        self.assertEqual(result.format, SerializationFormat.JSON)

    def test_json_tuple_coercion_lossy(self):
        data = {"t": (1, 2, 3)}
        result = self.tester.test_json(data)
        self.assertIn("t", result.lossy_fields)
        self.assertIsInstance(result.decoded["t"], list)

    def test_json_list_not_lossy(self):
        data = {"lst": [1, 2, 3]}
        result = self.tester.test_json(data)
        self.assertNotIn("lst", result.lossy_fields)

    def test_json_none_preserved(self):
        data = {"n": None}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertIsNone(result.decoded["n"])
        self.assertNotIn("n", result.lossy_fields)

    def test_json_bool_preserved(self):
        data = {"flag": False}
        result = self.tester.test_json(data)
        self.assertFalse(result.decoded["flag"])
        self.assertNotIn("flag", result.lossy_fields)

    def test_json_int_preserved(self):
        data = {"i": 12345}
        result = self.tester.test_json(data)
        self.assertEqual(result.decoded["i"], 12345)

    def test_json_float_preserved(self):
        data = {"f": 3.14}
        result = self.tester.test_json(data)
        self.assertAlmostEqual(result.decoded["f"], 3.14)

    def test_json_unicode_string(self):
        data = {"u": "héllo wörld 日本語"}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["u"], "héllo wörld 日本語")

    def test_json_empty_dict(self):
        result = self.tester.test_json({})
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded, {})

    def test_json_nan_roundtrip(self):
        data = {"n": float("nan")}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        # NaN decoded as NaN
        self.assertTrue(math.isnan(result.decoded["n"]))

    def test_json_inf_roundtrip(self):
        data = {"v": float("inf")}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertTrue(math.isinf(result.decoded["v"]))

    def test_json_negative_inf_roundtrip(self):
        data = {"v": float("-inf")}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["v"], float("-inf"))

    def test_json_encoded_is_string(self):
        result = self.tester.test_json({"a": 1})
        self.assertIsInstance(result.encoded, str)

    def test_json_empty_string_value(self):
        data = {"s": ""}
        result = self.tester.test_json(data)
        self.assertEqual(result.decoded["s"], "")
        self.assertNotIn("s", result.lossy_fields)


# ===========================================================================
# FormatTester — CSV tests
# ===========================================================================

class TestFormatTesterCSV(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def test_csv_basic_roundtrip(self):
        data = {"name": "Bob", "city": "Seattle"}
        result = self.tester.test_csv(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["name"], "Bob")
        self.assertEqual(result.decoded["city"], "Seattle")

    def test_csv_format_enum(self):
        result = self.tester.test_csv({"x": "1"})
        self.assertEqual(result.format, SerializationFormat.CSV)

    def test_csv_int_becomes_string(self):
        data = {"age": 25}
        result = self.tester.test_csv(data)
        self.assertTrue(result.passed)
        # Everything is string after CSV roundtrip
        self.assertEqual(result.decoded["age"], "25")
        self.assertIn("age", result.lossy_fields)

    def test_csv_float_becomes_string(self):
        data = {"price": 9.99}
        result = self.tester.test_csv(data)
        self.assertIn("price", result.lossy_fields)

    def test_csv_bool_becomes_string(self):
        data = {"active": True}
        result = self.tester.test_csv(data)
        self.assertIn("active", result.lossy_fields)

    def test_csv_none_becomes_string(self):
        data = {"val": None}
        result = self.tester.test_csv(data)
        self.assertIn("val", result.lossy_fields)

    def test_csv_string_value_preserved(self):
        data = {"s": "hello"}
        result = self.tester.test_csv(data)
        self.assertEqual(result.decoded["s"], "hello")
        self.assertNotIn("s", result.lossy_fields)

    def test_csv_unicode_string(self):
        data = {"label": "café"}
        result = self.tester.test_csv(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["label"], "café")

    def test_csv_multiple_columns(self):
        data = {"a": "1", "b": "2", "c": "3"}
        result = self.tester.test_csv(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["a"], "1")
        self.assertEqual(result.decoded["b"], "2")
        self.assertEqual(result.decoded["c"], "3")

    def test_csv_empty_dict(self):
        result = self.tester.test_csv({})
        self.assertTrue(result.passed)


# ===========================================================================
# FormatTester — BINARY tests
# ===========================================================================

class TestFormatTesterBinary(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def _binary_data(self, **kwargs):
        base = {"int_val": 0, "double_val": 0.0, "float_val": 0.0, "bool_val": False}
        base.update(kwargs)
        return base

    def test_binary_basic_roundtrip(self):
        data = self._binary_data(int_val=42, double_val=3.14, float_val=1.5, bool_val=True)
        result = self.tester.test_binary(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["int_val"], 42)
        self.assertAlmostEqual(result.decoded["double_val"], 3.14, places=10)
        self.assertTrue(result.decoded["bool_val"])

    def test_binary_format_enum(self):
        result = self.tester.test_binary(self._binary_data())
        self.assertEqual(result.format, SerializationFormat.BINARY)

    def test_binary_encoded_is_bytes(self):
        result = self.tester.test_binary(self._binary_data(int_val=1))
        self.assertIsInstance(result.encoded, bytes)

    def test_binary_int_lossless(self):
        data = self._binary_data(int_val=1000000)
        result = self.tester.test_binary(data)
        self.assertEqual(result.decoded["int_val"], 1000000)
        self.assertNotIn("int_val", result.lossy_fields)

    def test_binary_double_lossless(self):
        data = self._binary_data(double_val=math.pi)
        result = self.tester.test_binary(data)
        self.assertAlmostEqual(result.decoded["double_val"], math.pi, places=14)
        self.assertNotIn("double_val", result.lossy_fields)

    def test_binary_float_single_precision(self):
        # Single-precision float has ~7 decimal digits
        data = self._binary_data(float_val=1.23456789)
        result = self.tester.test_binary(data)
        self.assertTrue(result.passed)
        # May or may not be lossy depending on precision threshold

    def test_binary_bool_roundtrip(self):
        data = self._binary_data(bool_val=True)
        result = self.tester.test_binary(data)
        self.assertTrue(result.decoded["bool_val"])

    def test_binary_nan_double(self):
        data = self._binary_data(double_val=float("nan"))
        result = self.tester.test_binary(data)
        self.assertTrue(result.passed)
        self.assertTrue(math.isnan(result.decoded["double_val"]))

    def test_binary_inf_double(self):
        data = self._binary_data(double_val=float("inf"))
        result = self.tester.test_binary(data)
        self.assertTrue(result.passed)
        self.assertTrue(math.isinf(result.decoded["double_val"]))

    def test_binary_extra_keys_lossy(self):
        data = self._binary_data(int_val=1)
        data["extra_key"] = "not_encodable"
        result = self.tester.test_binary(data)
        self.assertIn("extra_key", result.lossy_fields)


# ===========================================================================
# FormatTester — PICKLE tests
# ===========================================================================

class TestFormatTesterPickle(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def test_pickle_basic_roundtrip(self):
        data = {"name": "Carol", "score": 99.5, "active": True}
        result = self.tester.test_pickle(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded, data)

    def test_pickle_format_enum(self):
        result = self.tester.test_pickle({"x": 1})
        self.assertEqual(result.format, SerializationFormat.PICKLE)

    def test_pickle_encoded_is_bytes(self):
        result = self.tester.test_pickle({"a": 1})
        self.assertIsInstance(result.encoded, bytes)

    def test_pickle_tuple_preserved(self):
        data = {"t": (1, 2, 3)}
        result = self.tester.test_pickle(data)
        self.assertIsInstance(result.decoded["t"], tuple)
        self.assertNotIn("t", result.lossy_fields)

    def test_pickle_none_lossless(self):
        data = {"n": None}
        result = self.tester.test_pickle(data)
        self.assertIsNone(result.decoded["n"])
        self.assertNotIn("n", result.lossy_fields)

    def test_pickle_nan_lossless(self):
        data = {"v": float("nan")}
        result = self.tester.test_pickle(data)
        self.assertTrue(math.isnan(result.decoded["v"]))
        self.assertNotIn("v", result.lossy_fields)

    def test_pickle_inf_lossless(self):
        data = {"v": float("inf")}
        result = self.tester.test_pickle(data)
        self.assertTrue(math.isinf(result.decoded["v"]))
        self.assertNotIn("v", result.lossy_fields)

    def test_pickle_nested_dict_lossless(self):
        data = {"outer": {"inner": [1, 2, 3]}}
        result = self.tester.test_pickle(data)
        self.assertEqual(result.decoded["outer"]["inner"], [1, 2, 3])

    def test_pickle_empty_collections(self):
        data = {"empty_list": [], "empty_dict": {}, "empty_tuple": ()}
        result = self.tester.test_pickle(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["empty_list"], [])
        self.assertEqual(result.decoded["empty_dict"], {})
        self.assertEqual(result.decoded["empty_tuple"], ())

    def test_pickle_unicode_lossless(self):
        data = {"u": "日本語テスト"}
        result = self.tester.test_pickle(data)
        self.assertEqual(result.decoded["u"], "日本語テスト")
        self.assertNotIn("u", result.lossy_fields)


# ===========================================================================
# FormatTester — XML tests
# ===========================================================================

class TestFormatTesterXML(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def test_xml_basic_roundtrip(self):
        data = {"tag": "hello", "count": "5"}
        result = self.tester.test_xml(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["tag"], "hello")
        self.assertEqual(result.decoded["count"], "5")

    def test_xml_format_enum(self):
        result = self.tester.test_xml({"x": "1"})
        self.assertEqual(result.format, SerializationFormat.XML)

    def test_xml_encoded_is_string(self):
        result = self.tester.test_xml({"a": "1"})
        self.assertIsInstance(result.encoded, str)
        self.assertIn("<root>", result.encoded)

    def test_xml_int_becomes_string(self):
        data = {"n": 42}
        result = self.tester.test_xml(data)
        self.assertIn("n", result.lossy_fields)
        self.assertEqual(result.decoded["n"], "42")

    def test_xml_float_becomes_string(self):
        data = {"f": 3.14}
        result = self.tester.test_xml(data)
        self.assertIn("f", result.lossy_fields)

    def test_xml_bool_becomes_string(self):
        data = {"b": True}
        result = self.tester.test_xml(data)
        self.assertIn("b", result.lossy_fields)

    def test_xml_none_encoded_decoded(self):
        data = {"n": None}
        result = self.tester.test_xml(data)
        self.assertTrue(result.passed)
        self.assertIn("n", result.lossy_fields)

    def test_xml_unicode_string(self):
        data = {"u": "héllo"}
        result = self.tester.test_xml(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["u"], "héllo")

    def test_xml_nan_encoded(self):
        data = {"v": float("nan")}
        result = self.tester.test_xml(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["v"], "nan")

    def test_xml_inf_encoded(self):
        data = {"v": float("inf")}
        result = self.tester.test_xml(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["v"], "inf")

    def test_xml_empty_dict(self):
        result = self.tester.test_xml({})
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded, {})


# ===========================================================================
# FormatTester — INI tests
# ===========================================================================

class TestFormatTesterINI(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def test_ini_basic_roundtrip(self):
        data = {"host": "localhost", "port": "8080"}
        result = self.tester.test_ini(data)
        self.assertTrue(result.passed)
        self.assertIn("host", result.decoded)
        self.assertEqual(result.decoded["host"], "localhost")

    def test_ini_format_enum(self):
        result = self.tester.test_ini({"x": "1"})
        self.assertEqual(result.format, SerializationFormat.INI)

    def test_ini_encoded_is_string(self):
        result = self.tester.test_ini({"a": "b"})
        self.assertIsInstance(result.encoded, str)

    def test_ini_int_becomes_string(self):
        data = {"count": 10}
        result = self.tester.test_ini(data)
        self.assertIn("count", result.lossy_fields)

    def test_ini_float_becomes_string(self):
        data = {"rate": 0.5}
        result = self.tester.test_ini(data)
        self.assertIn("rate", result.lossy_fields)

    def test_ini_bool_becomes_string(self):
        data = {"enabled": True}
        result = self.tester.test_ini(data)
        self.assertIn("enabled", result.lossy_fields)

    def test_ini_none_becomes_empty(self):
        data = {"val": None}
        result = self.tester.test_ini(data)
        self.assertTrue(result.passed)
        self.assertIn("val", result.lossy_fields)

    def test_ini_string_preserved(self):
        data = {"name": "myapp"}
        result = self.tester.test_ini(data)
        self.assertNotIn("name", result.lossy_fields)

    def test_ini_keys_lowercased(self):
        data = {"MyKey": "value"}
        result = self.tester.test_ini(data)
        self.assertTrue(result.passed)
        # ConfigParser lowercases keys
        self.assertIn("mykey", result.decoded)

    def test_ini_custom_section(self):
        data = {"timeout": "30"}
        result = self.tester.test_ini(data, section="network")
        self.assertTrue(result.passed)

    def test_ini_empty_dict(self):
        result = self.tester.test_ini({})
        self.assertTrue(result.passed)


# ===========================================================================
# RoundtripRunner tests
# ===========================================================================

class TestRoundtripRunner(unittest.TestCase):

    def setUp(self):
        self.runner = RoundtripRunner()

    def test_run_all_returns_report(self):
        data = {"name": "test", "value": 1, "int_val": 5, "double_val": 2.0,
                "float_val": 1.0, "bool_val": False}
        report = self.runner.run_all(data)
        self.assertIsInstance(report, SerializationReport)

    def test_run_all_has_six_results(self):
        data = {"int_val": 1, "double_val": 1.0, "float_val": 1.0, "bool_val": True}
        report = self.runner.run_all(data)
        self.assertEqual(len(report.results), 6)

    def test_run_all_formats_present(self):
        data = {"name": "x", "int_val": 0, "double_val": 0.0, "float_val": 0.0, "bool_val": False}
        report = self.runner.run_all(data)
        fmts = {r.format for r in report.results}
        self.assertIn(SerializationFormat.JSON, fmts)
        self.assertIn(SerializationFormat.CSV, fmts)
        self.assertIn(SerializationFormat.BINARY, fmts)
        self.assertIn(SerializationFormat.PICKLE, fmts)
        self.assertIn(SerializationFormat.XML, fmts)
        self.assertIn(SerializationFormat.INI, fmts)

    def test_run_format_json(self):
        result = self.runner.run_format(SerializationFormat.JSON, {"a": 1})
        self.assertEqual(result.format, SerializationFormat.JSON)
        self.assertTrue(result.passed)

    def test_run_format_pickle(self):
        result = self.runner.run_format(SerializationFormat.PICKLE, {"x": (1, 2)})
        self.assertEqual(result.format, SerializationFormat.PICKLE)
        self.assertTrue(result.passed)

    def test_run_format_xml(self):
        result = self.runner.run_format(SerializationFormat.XML, {"tag": "hello"})
        self.assertEqual(result.format, SerializationFormat.XML)
        self.assertTrue(result.passed)

    def test_run_format_csv(self):
        result = self.runner.run_format(SerializationFormat.CSV, {"col": "val"})
        self.assertEqual(result.format, SerializationFormat.CSV)
        self.assertTrue(result.passed)

    def test_run_format_ini(self):
        result = self.runner.run_format(SerializationFormat.INI, {"key": "val"})
        self.assertEqual(result.format, SerializationFormat.INI)
        self.assertTrue(result.passed)

    def test_run_format_binary(self):
        data = {"int_val": 1, "double_val": 1.0, "float_val": 1.0, "bool_val": True}
        result = self.runner.run_format(SerializationFormat.BINARY, data)
        self.assertEqual(result.format, SerializationFormat.BINARY)
        self.assertTrue(result.passed)

    def test_run_format_invalid_raises(self):
        with self.assertRaises((ValueError, AttributeError, KeyError)):
            self.runner.run_format("NOTAFORMAT", {})


# ===========================================================================
# Mock HTTP Server tests
# ===========================================================================

class TestMockServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = _start_server()
        cls.base = cls.server.base_url()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}") as resp:
            return json.loads(resp.read().decode())

    def _post(self, path, payload):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())

    def test_health_endpoint(self):
        data = self._get("/health")
        self.assertEqual(data["status"], "ok")

    def test_formats_endpoint(self):
        data = self._get("/formats")
        self.assertIn("formats", data)
        self.assertIn("JSON", data["formats"])
        self.assertIn("CSV", data["formats"])
        self.assertIn("PICKLE", data["formats"])
        self.assertIn("XML", data["formats"])
        self.assertIn("INI", data["formats"])
        self.assertIn("BINARY", data["formats"])

    def test_roundtrip_json_endpoint(self):
        data = self._get("/roundtrip/json")
        self.assertEqual(data["format"], "JSON")
        self.assertTrue(data["passed"])

    def test_roundtrip_csv_endpoint(self):
        data = self._get("/roundtrip/csv")
        self.assertEqual(data["format"], "CSV")
        self.assertTrue(data["passed"])

    def test_roundtrip_pickle_endpoint(self):
        data = self._get("/roundtrip/pickle")
        self.assertEqual(data["format"], "PICKLE")
        self.assertTrue(data["passed"])

    def test_roundtrip_xml_endpoint(self):
        data = self._get("/roundtrip/xml")
        self.assertEqual(data["format"], "XML")
        self.assertTrue(data["passed"])

    def test_roundtrip_ini_endpoint(self):
        data = self._get("/roundtrip/ini")
        self.assertEqual(data["format"], "INI")
        self.assertTrue(data["passed"])

    def test_report_endpoint(self):
        data = self._get("/report")
        self.assertIn("lossless_count", data)
        self.assertIn("lossy_count", data)
        self.assertIn("passed_count", data)
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 6)

    def test_post_roundtrip_json(self):
        payload = {"format": "JSON", "data": {"a": 1, "b": "hello"}}
        data = self._post("/roundtrip", payload)
        self.assertEqual(data["format"], "JSON")
        self.assertTrue(data["passed"])

    def test_post_roundtrip_pickle(self):
        payload = {"format": "PICKLE", "data": {"key": "val"}}
        data = self._post("/roundtrip", payload)
        self.assertEqual(data["format"], "PICKLE")
        self.assertTrue(data["passed"])

    def test_not_found_endpoint(self):
        try:
            urllib.request.urlopen(f"{self.base}/nonexistent")
            self.fail("Expected HTTPError 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


# ===========================================================================
# Edge-case / integration tests
# ===========================================================================

class TestEdgeCases(unittest.TestCase):

    def setUp(self):
        self.tester = FormatTester()

    def test_json_empty_string_key(self):
        data = {"": "empty_key"}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)

    def test_json_large_integer(self):
        data = {"big": 10 ** 18}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["big"], 10 ** 18)

    def test_json_nested_list_of_dicts(self):
        data = {"items": [{"a": 1}, {"b": 2}]}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)

    def test_pickle_set_lossless(self):
        data = {"s": {1, 2, 3}}
        result = self.tester.test_pickle(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["s"], {1, 2, 3})

    def test_pickle_bytes_lossless(self):
        data = {"b": b"\x00\x01\x02\xff"}
        result = self.tester.test_pickle(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["b"], b"\x00\x01\x02\xff")

    def test_xml_special_chars_in_value(self):
        # XML must escape < > &
        data = {"code": "<b>&amp;</b>"}
        result = self.tester.test_xml(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["code"], "<b>&amp;</b>")

    def test_csv_comma_in_value(self):
        data = {"address": "123 Main St, Suite 4"}
        result = self.tester.test_csv(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["address"], "123 Main St, Suite 4")

    def test_report_all_passed(self):
        data = {
            "name": "test",
            "int_val": 42,
            "double_val": 3.14159,
            "float_val": 1.0,
            "bool_val": True,
        }
        runner = RoundtripRunner()
        report = runner.run_all(data)
        self.assertEqual(report.passed_count, 6)

    def test_binary_negative_int(self):
        data = {"int_val": -999, "double_val": 0.0, "float_val": 0.0, "bool_val": False}
        result = self.tester.test_binary(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["int_val"], -999)

    def test_json_deeply_nested(self):
        data = {"a": {"b": {"c": {"d": 42}}}}
        result = self.tester.test_json(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.decoded["a"]["b"]["c"]["d"], 42)


if __name__ == "__main__":
    unittest.main(verbosity=2)
