"""
Tests for i18n / Unicode / Encoding Test Harness (Harness 30 of 36)

122 tests covering:
- Encoding round-trips and mojibake detection
- BOM detection and stripping
- Surrogate pairs and astral plane
- Grapheme cluster estimation
- NFC/NFD normalization
- Casefolding (German ß, Turkish dotless-ı)
- Byte-safe truncation
- East-Asian display width
- RTL/bidi detection
- Mock HTTP server endpoints
- Dataclass fields and report aggregation
"""

from __future__ import annotations

import json
import socket
import time
import unittest
import urllib.error
import urllib.request
from typing import Any

from harnesses.core.i18n_test_harness import (
    BIDI_OVERRIDE_CHARS,
    BOM_UTF8,
    BOM_UTF16_BE,
    BOM_UTF16_LE,
    BidiDetector,
    BOMDetector,
    CasefoldTester,
    DisplayWidthCalculator,
    EncodingResult,
    EncodingTester,
    GraphemeResult,
    I18nAnalyzer,
    I18nReport,
    MOJIBAKE_CAFE,
    NormalizationResult,
    NormalizationTester,
    SurrogateTester,
    estimate_grapheme_clusters,
    looks_like_mojibake,
    safe_truncate_bytes,
    start_server,
    stop_server,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fetch(url: str, timeout: float = 5.0) -> Any:
    """Fetch JSON from URL."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ServerTestCase(unittest.TestCase):
    """Base class that spins up the mock server for HTTP tests."""

    @classmethod
    def setUpClass(cls):
        cls.port = _get_free_port()
        cls.server = start_server(cls.port)
        cls.base = f"http://127.0.0.1:{cls.port}"
        # Give server a moment to bind
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.server)


# ===========================================================================
# 1. EncodingResult dataclass
# ===========================================================================

class TestEncodingResultDataclass(unittest.TestCase):

    def test_fields_exist(self):
        r = EncodingResult(
            encoding="utf-8",
            original_text="hello",
            encoded_bytes=b"hello",
            decoded_text="hello",
            round_trip_ok=True,
        )
        self.assertEqual(r.encoding, "utf-8")
        self.assertEqual(r.original_text, "hello")
        self.assertEqual(r.encoded_bytes, b"hello")
        self.assertEqual(r.decoded_text, "hello")
        self.assertTrue(r.round_trip_ok)

    def test_default_is_mojibake_false(self):
        r = EncodingResult(
            encoding="utf-8",
            original_text="x",
            encoded_bytes=b"x",
            decoded_text="x",
            round_trip_ok=True,
        )
        self.assertFalse(r.is_mojibake)

    def test_default_error_none(self):
        r = EncodingResult(
            encoding="utf-8",
            original_text="x",
            encoded_bytes=b"x",
            decoded_text="x",
            round_trip_ok=True,
        )
        self.assertIsNone(r.error)

    def test_can_set_is_mojibake(self):
        r = EncodingResult(
            encoding="utf-8→latin-1",
            original_text="café",
            encoded_bytes=b"caf\xc3\xa9",
            decoded_text="cafÃ©",
            round_trip_ok=False,
            is_mojibake=True,
        )
        self.assertTrue(r.is_mojibake)

    def test_can_set_error(self):
        r = EncodingResult(
            encoding="ascii",
            original_text="café",
            encoded_bytes=b"",
            decoded_text="",
            round_trip_ok=False,
            error="codec can't encode character",
        )
        self.assertIsNotNone(r.error)
        self.assertIn("encode", r.error)


# ===========================================================================
# 2. NormalizationResult dataclass
# ===========================================================================

class TestNormalizationResultDataclass(unittest.TestCase):

    def test_fields(self):
        r = NormalizationResult(
            form="NFC",
            original="é",
            normalized="\xe9",
            changed=True,
            byte_length_original=3,
            byte_length_normalized=2,
        )
        self.assertEqual(r.form, "NFC")
        self.assertTrue(r.changed)
        self.assertEqual(r.byte_length_original, 3)
        self.assertEqual(r.byte_length_normalized, 2)

    def test_unchanged_nfc(self):
        # Already NFC — should not change
        text = "café"
        r = NormalizationResult(
            form="NFC",
            original=text,
            normalized=text,
            changed=False,
            byte_length_original=len(text.encode("utf-8")),
            byte_length_normalized=len(text.encode("utf-8")),
        )
        self.assertFalse(r.changed)


# ===========================================================================
# 3. GraphemeResult dataclass
# ===========================================================================

class TestGraphemeResultDataclass(unittest.TestCase):

    def test_fields(self):
        gr = GraphemeResult(
            text="🎉",
            python_len=1,
            grapheme_count=1,
            utf16_units=2,
            utf8_bytes=4,
            has_zwj=False,
        )
        self.assertEqual(gr.python_len, 1)
        self.assertEqual(gr.grapheme_count, 1)
        self.assertEqual(gr.utf16_units, 2)
        self.assertEqual(gr.utf8_bytes, 4)
        self.assertFalse(gr.has_zwj)

    def test_default_has_lone_surrogate_false(self):
        gr = GraphemeResult(
            text="a",
            python_len=1,
            grapheme_count=1,
            utf16_units=1,
            utf8_bytes=1,
            has_zwj=False,
        )
        self.assertFalse(gr.has_lone_surrogate)


# ===========================================================================
# 4. I18nReport dataclass
# ===========================================================================

class TestI18nReportDataclass(unittest.TestCase):

    def test_empty_report(self):
        report = I18nReport()
        self.assertEqual(report.encoding_ok_count, 0)
        self.assertEqual(report.encoding_fail_count, 0)
        self.assertEqual(report.mojibake_count, 0)

    def test_encoding_ok_count(self):
        report = I18nReport()
        report.encoding_results.append(
            EncodingResult("utf-8", "a", b"a", "a", True)
        )
        report.encoding_results.append(
            EncodingResult("latin-1", "a", b"a", "a", True)
        )
        self.assertEqual(report.encoding_ok_count, 2)

    def test_encoding_fail_count(self):
        report = I18nReport()
        report.encoding_results.append(
            EncodingResult("ascii", "café", b"", "", False, error="err")
        )
        self.assertEqual(report.encoding_fail_count, 1)

    def test_mojibake_count(self):
        report = I18nReport()
        report.encoding_results.append(
            EncodingResult("utf-8→latin-1", "café", b"", "cafÃ©", False, is_mojibake=True)
        )
        self.assertEqual(report.mojibake_count, 1)

    def test_lists_are_independent(self):
        r1 = I18nReport()
        r2 = I18nReport()
        r1.warnings.append("test")
        self.assertEqual(len(r2.warnings), 0)


# ===========================================================================
# 5. Encoding Round-Trip Tests
# ===========================================================================

class TestEncodingRoundTrip(unittest.TestCase):

    def setUp(self):
        self.tester = EncodingTester()

    def test_ascii_roundtrip_utf8(self):
        r = self.tester.test_roundtrip("Hello, World!", "utf-8")
        self.assertTrue(r.round_trip_ok)

    def test_unicode_roundtrip_utf8(self):
        r = self.tester.test_roundtrip("café résumé", "utf-8")
        self.assertTrue(r.round_trip_ok)

    def test_emoji_roundtrip_utf8(self):
        r = self.tester.test_roundtrip("Hello 🎉", "utf-8")
        self.assertTrue(r.round_trip_ok)

    def test_japanese_roundtrip_utf8(self):
        r = self.tester.test_roundtrip("こんにちは", "utf-8")
        self.assertTrue(r.round_trip_ok)

    def test_ascii_roundtrip_utf16(self):
        r = self.tester.test_roundtrip("Hello", "utf-16")
        self.assertTrue(r.round_trip_ok)

    def test_unicode_roundtrip_utf16(self):
        r = self.tester.test_roundtrip("café", "utf-16")
        self.assertTrue(r.round_trip_ok)

    def test_latin1_roundtrip(self):
        r = self.tester.test_roundtrip("café", "latin-1")
        self.assertTrue(r.round_trip_ok)

    def test_ascii_roundtrip(self):
        r = self.tester.test_ascii_only("Hello, World!")
        self.assertTrue(r.round_trip_ok)

    def test_ascii_fails_on_non_ascii(self):
        r = self.tester.test_ascii_only("café")
        self.assertFalse(r.round_trip_ok)
        self.assertIsNotNone(r.error)

    def test_latin1_fails_on_emoji(self):
        r = self.tester.test_roundtrip("🎉", "latin-1")
        self.assertFalse(r.round_trip_ok)

    def test_roundtrip_preserves_encoding_field(self):
        r = self.tester.test_roundtrip("hello", "utf-8")
        self.assertEqual(r.encoding, "utf-8")

    def test_roundtrip_preserves_original(self):
        text = "Héllo"
        r = self.tester.test_roundtrip(text, "utf-8")
        self.assertEqual(r.original_text, text)

    def test_roundtrip_bytes_not_empty_on_success(self):
        r = self.tester.test_roundtrip("hello", "utf-8")
        self.assertGreater(len(r.encoded_bytes), 0)

    def test_roundtrip_decoded_equals_original_on_success(self):
        text = "Héllo Wörld"
        r = self.tester.test_roundtrip(text, "utf-8")
        self.assertEqual(r.decoded_text, text)


# ===========================================================================
# 6. Mojibake Detection Tests
# ===========================================================================

class TestMojibakeDetection(unittest.TestCase):

    def setUp(self):
        self.tester = EncodingTester()

    def test_mojibake_detected_for_accented(self):
        r = self.tester.detect_mojibake("café")
        self.assertTrue(r.is_mojibake)

    def test_mojibake_text_differs_from_original(self):
        r = self.tester.detect_mojibake("café")
        self.assertNotEqual(r.decoded_text, r.original_text)

    def test_no_mojibake_for_ascii(self):
        r = self.tester.detect_mojibake("Hello")
        # ASCII roundtrips cleanly through both encodings
        self.assertFalse(r.is_mojibake)

    def test_corrupted_text_looks_like_mojibake(self):
        self.assertTrue(looks_like_mojibake(MOJIBAKE_CAFE))

    def test_clean_accented_text_not_flagged_as_mojibake_artifact(self):
        self.assertFalse(looks_like_mojibake("café"))

    def test_mojibake_encoding_label(self):
        r = self.tester.detect_mojibake("café")
        self.assertIn("latin-1", r.encoding)

    def test_mojibake_not_round_trip_ok(self):
        r = self.tester.detect_mojibake("café")
        self.assertFalse(r.round_trip_ok)

    def test_mojibake_bytes_are_utf8(self):
        text = "résumé"
        r = self.tester.detect_mojibake(text)
        # Encoded bytes should be valid UTF-8 of the original text
        self.assertEqual(r.encoded_bytes, text.encode("utf-8"))

    def test_mojibake_japanese(self):
        r = self.tester.detect_mojibake("こんにちは")
        self.assertTrue(r.is_mojibake)


# ===========================================================================
# 7. BOM Detection Tests
# ===========================================================================

class TestBOMDetection(unittest.TestCase):

    def test_detect_utf8_bom(self):
        data = BOM_UTF8 + b"hello"
        self.assertEqual(BOMDetector.detect(data), "utf-8-sig")

    def test_detect_utf16_le_bom(self):
        data = BOM_UTF16_LE + "hello".encode("utf-16-le")
        self.assertEqual(BOMDetector.detect(data), "utf-16-le")

    def test_detect_utf16_be_bom(self):
        data = BOM_UTF16_BE + "hello".encode("utf-16-be")
        self.assertEqual(BOMDetector.detect(data), "utf-16-be")

    def test_detect_no_bom(self):
        data = b"hello world"
        self.assertIsNone(BOMDetector.detect(data))

    def test_strip_utf8_bom(self):
        data = BOM_UTF8 + b"hello"
        stripped, bom_type = BOMDetector.strip(data)
        self.assertEqual(stripped, b"hello")
        self.assertEqual(bom_type, "utf-8-sig")

    def test_strip_utf16_le_bom(self):
        content = "hello".encode("utf-16-le")
        data = BOM_UTF16_LE + content
        stripped, bom_type = BOMDetector.strip(data)
        self.assertEqual(stripped, content)
        self.assertEqual(bom_type, "utf-16-le")

    def test_strip_utf16_be_bom(self):
        content = "hello".encode("utf-16-be")
        data = BOM_UTF16_BE + content
        stripped, bom_type = BOMDetector.strip(data)
        self.assertEqual(stripped, content)
        self.assertEqual(bom_type, "utf-16-be")

    def test_strip_no_bom(self):
        data = b"hello"
        stripped, bom_type = BOMDetector.strip(data)
        self.assertEqual(stripped, b"hello")
        self.assertIsNone(bom_type)

    def test_decode_with_utf8_bom(self):
        data = BOM_UTF8 + b"hello"
        text, bom_type = BOMDetector.decode_with_bom(data)
        self.assertEqual(text, "hello")
        self.assertEqual(bom_type, "utf-8-sig")

    def test_decode_with_utf16_le_bom(self):
        data = BOM_UTF16_LE + "hello".encode("utf-16-le")
        text, bom_type = BOMDetector.decode_with_bom(data)
        self.assertEqual(text, "hello")
        self.assertEqual(bom_type, "utf-16-le")

    def test_decode_with_utf16_be_bom(self):
        data = BOM_UTF16_BE + "hello".encode("utf-16-be")
        text, bom_type = BOMDetector.decode_with_bom(data)
        self.assertEqual(text, "hello")
        self.assertEqual(bom_type, "utf-16-be")

    def test_decode_no_bom(self):
        data = b"hello"
        text, bom_type = BOMDetector.decode_with_bom(data)
        self.assertEqual(text, "hello")
        self.assertIsNone(bom_type)

    def test_bom_constants_correct(self):
        self.assertEqual(BOM_UTF8, b"\xef\xbb\xbf")
        self.assertEqual(BOM_UTF16_LE, b"\xff\xfe")
        self.assertEqual(BOM_UTF16_BE, b"\xfe\xff")


# ===========================================================================
# 8. Surrogate Pair / Astral Plane Tests
# ===========================================================================

class TestSurrogatePairs(unittest.TestCase):

    def setUp(self):
        self.tester = SurrogateTester()

    def test_emoji_python_len_is_1(self):
        gr = self.tester.analyze("🎉")
        self.assertEqual(gr.python_len, 1)

    def test_emoji_utf16_units_is_2(self):
        gr = self.tester.analyze("🎉")
        self.assertEqual(gr.utf16_units, 2)

    def test_emoji_utf8_bytes_is_4(self):
        gr = self.tester.analyze("🎉")
        self.assertEqual(gr.utf8_bytes, 4)

    def test_emoji_grapheme_count_is_1(self):
        gr = self.tester.analyze("🎉")
        self.assertEqual(gr.grapheme_count, 1)

    def test_ascii_char_utf16_units_is_1(self):
        gr = self.tester.analyze("A")
        self.assertEqual(gr.utf16_units, 1)

    def test_ascii_char_utf8_bytes_is_1(self):
        gr = self.tester.analyze("A")
        self.assertEqual(gr.utf8_bytes, 1)

    def test_no_zwj_in_simple_emoji(self):
        gr = self.tester.analyze("🎉")
        self.assertFalse(gr.has_zwj)

    def test_zwj_detected_in_family_emoji(self):
        family = "👨‍👩‍👧"
        gr = self.tester.analyze(family)
        self.assertTrue(gr.has_zwj)

    def test_family_emoji_python_len_greater_than_1(self):
        family = "👨‍👩‍👧"
        gr = self.tester.analyze(family)
        # Python len counts code points, not grapheme clusters
        self.assertGreater(gr.python_len, 1)

    def test_family_emoji_grapheme_count_is_1(self):
        family = "👨‍👩‍👧"
        gr = self.tester.analyze(family)
        self.assertEqual(gr.grapheme_count, 1)

    def test_no_lone_surrogate_in_normal_text(self):
        gr = self.tester.analyze("Hello 🎉")
        self.assertFalse(gr.has_lone_surrogate)

    def test_lone_surrogate_detected(self):
        lone = self.tester.make_lone_surrogate_string()
        gr = self.tester.analyze(lone)
        self.assertTrue(gr.has_lone_surrogate)

    def test_multiple_emoji_python_len(self):
        text = "🎉🎊🎈"
        gr = self.tester.analyze(text)
        self.assertEqual(gr.python_len, 3)
        self.assertEqual(gr.grapheme_count, 3)

    def test_cjk_char_utf16_units_is_1(self):
        # CJK chars are in BMP (Basic Multilingual Plane), not surrogates
        gr = self.tester.analyze("日")
        self.assertEqual(gr.utf16_units, 1)
        self.assertEqual(gr.utf8_bytes, 3)


# ===========================================================================
# 9. Grapheme Cluster Estimation Tests
# ===========================================================================

class TestGraphemeClusters(unittest.TestCase):

    def test_simple_ascii(self):
        self.assertEqual(estimate_grapheme_clusters("hello"), 5)

    def test_empty_string(self):
        self.assertEqual(estimate_grapheme_clusters(""), 0)

    def test_single_char(self):
        self.assertEqual(estimate_grapheme_clusters("a"), 1)

    def test_single_emoji(self):
        self.assertEqual(estimate_grapheme_clusters("🎉"), 1)

    def test_combining_accent(self):
        # e + combining acute accent = 1 grapheme cluster
        combined = "é"  # e + ́
        self.assertEqual(estimate_grapheme_clusters(combined), 1)

    def test_zwj_family_is_one_grapheme(self):
        family = "👨‍👩‍👧"
        count = estimate_grapheme_clusters(family)
        self.assertEqual(count, 1)

    def test_two_separate_emoji(self):
        self.assertEqual(estimate_grapheme_clusters("🎉🎊"), 2)

    def test_cjk_chars(self):
        self.assertEqual(estimate_grapheme_clusters("日本語"), 3)

    def test_mixed_ascii_emoji(self):
        self.assertEqual(estimate_grapheme_clusters("A🎉B"), 3)

    def test_python_len_vs_grapheme_differ_for_family(self):
        family = "👨‍👩‍👧"
        self.assertGreater(len(family), estimate_grapheme_clusters(family))


# ===========================================================================
# 10. NFC/NFD Normalization Tests
# ===========================================================================

class TestNormalization(unittest.TestCase):

    def setUp(self):
        self.tester = NormalizationTester()
        # é as U+00E9 (precomposed NFC)
        self.e_precomposed = "é"
        # é as U+0065 U+0301 (decomposed NFD)
        self.e_decomposed = "é"

    def test_nfc_precomposed_unchanged(self):
        r = self.tester.normalize(self.e_precomposed, "NFC")
        self.assertFalse(r.changed)

    def test_nfd_decomposes_precomposed(self):
        r = self.tester.normalize(self.e_precomposed, "NFD")
        self.assertTrue(r.changed)
        self.assertEqual(r.normalized, self.e_decomposed)

    def test_nfc_composes_decomposed(self):
        r = self.tester.normalize(self.e_decomposed, "NFC")
        self.assertTrue(r.changed)
        self.assertEqual(r.normalized, self.e_precomposed)

    def test_byte_unequal_before_normalization(self):
        # The two forms are byte-unequal
        self.assertNotEqual(self.e_precomposed, self.e_decomposed)

    def test_normalize_equal_after_nfc(self):
        self.assertTrue(self.tester.are_equivalent(self.e_precomposed, self.e_decomposed))

    def test_are_equivalent_same_string(self):
        self.assertTrue(self.tester.are_equivalent("café", "café"))

    def test_are_not_equivalent_different_strings(self):
        self.assertFalse(self.tester.are_equivalent("café", "cafe"))

    def test_dedup_bug_demonstrated(self):
        dedup = self.tester.demonstrate_dedup_bug(self.e_precomposed, self.e_decomposed)
        self.assertTrue(dedup["dedup_bug_demonstrated"])

    def test_dedup_raw_set_has_two_items(self):
        dedup = self.tester.demonstrate_dedup_bug(self.e_precomposed, self.e_decomposed)
        self.assertEqual(dedup["raw_set_size"], 2)

    def test_dedup_norm_set_has_one_item(self):
        dedup = self.tester.demonstrate_dedup_bug(self.e_precomposed, self.e_decomposed)
        self.assertEqual(dedup["norm_set_size"], 1)

    def test_dedup_byte_equal_is_false(self):
        dedup = self.tester.demonstrate_dedup_bug(self.e_precomposed, self.e_decomposed)
        self.assertFalse(dedup["byte_equal"])

    def test_dedup_normalize_equal_is_true(self):
        dedup = self.tester.demonstrate_dedup_bug(self.e_precomposed, self.e_decomposed)
        self.assertTrue(dedup["normalize_equal"])

    def test_nfd_byte_length_greater(self):
        # NFD is longer because combining marks are separate code points
        r = self.tester.normalize(self.e_precomposed, "NFD")
        self.assertGreater(r.byte_length_normalized, r.byte_length_original)

    def test_nfc_byte_length_shorter(self):
        r = self.tester.normalize(self.e_decomposed, "NFC")
        self.assertLess(r.byte_length_normalized, r.byte_length_original)

    def test_form_field_stored(self):
        r = self.tester.normalize("café", "NFC")
        self.assertEqual(r.form, "NFC")


# ===========================================================================
# 11. Casefolding Tests
# ===========================================================================

class TestCasefolding(unittest.TestCase):

    def setUp(self):
        self.tester = CasefoldTester()

    def test_casefold_compare_equal(self):
        self.assertTrue(self.tester.casefold_compare("Hello", "hello"))

    def test_casefold_compare_not_equal(self):
        self.assertFalse(self.tester.casefold_compare("hello", "world"))

    def test_german_sharp_s_casefold_is_ss(self):
        result = self.tester.demonstrate_german_sharp_s()
        self.assertEqual(result["casefold"], "ss")

    def test_german_sharp_s_lower_is_sharp_s(self):
        result = self.tester.demonstrate_german_sharp_s()
        self.assertEqual(result["lower"], "ß")

    def test_german_strasse_casefold_match(self):
        result = self.tester.demonstrate_german_sharp_s()
        self.assertTrue(result["strass_casefold_match"])

    def test_german_strasse_lower_no_match(self):
        result = self.tester.demonstrate_german_sharp_s()
        # "Straße".lower() = "straße", not "strasse"
        self.assertNotEqual(result["strass_lower"], "strasse")

    def test_turkish_capital_i_lower_is_i(self):
        result = self.tester.demonstrate_turkish_dotless_i()
        # Python uses C locale: I.lower() = 'i', NOT Turkish dotless 'ı'
        self.assertEqual(result["capital_I_lower_python"], "i")

    def test_turkish_trap_python_is_not_turkish(self):
        result = self.tester.demonstrate_turkish_dotless_i()
        # The trap: Python's I.lower() != Turkish dotless ı
        self.assertTrue(result["turkish_trap"])

    def test_dotless_i_is_different_from_i(self):
        result = self.tester.demonstrate_turkish_dotless_i()
        self.assertTrue(result["dotless_i_is_different"])

    def test_dotless_i_codepoint(self):
        result = self.tester.demonstrate_turkish_dotless_i()
        self.assertEqual(result["dotless_i_codepoint"], "0x131")

    def test_casefold_normalize(self):
        text = "Café"
        normalized = self.tester.casefold_normalize(text)
        self.assertEqual(normalized, "café")

    def test_casefold_normalize_equivalence(self):
        # NFC + casefold should make these equal
        e_pre = "é"  # é precomposed
        e_dec = "é"  # e + combining accent
        a = self.tester.casefold_normalize("caf" + e_pre)
        b = self.tester.casefold_normalize("caf" + e_dec)
        self.assertEqual(a, b)


# ===========================================================================
# 12. Byte-Safe Truncation Tests
# ===========================================================================

class TestSafeTruncation(unittest.TestCase):

    def test_ascii_truncation(self):
        result = safe_truncate_bytes("Hello, World!", 5)
        self.assertEqual(result, "Hello")

    def test_no_truncation_needed(self):
        text = "Hi"
        result = safe_truncate_bytes(text, 100)
        self.assertEqual(result, text)

    def test_exact_fit(self):
        text = "Hello"
        result = safe_truncate_bytes(text, 5)
        self.assertEqual(result, "Hello")

    def test_multibyte_not_split(self):
        # 'é' is 2 bytes in UTF-8; truncating to 1 byte should drop it
        text = "é"  # 2 bytes
        result = safe_truncate_bytes(text, 1)
        # Cannot fit the 2-byte é; result should be empty
        self.assertEqual(result, "")

    def test_multibyte_fits(self):
        text = "é"  # 2 bytes
        result = safe_truncate_bytes(text, 2)
        self.assertEqual(result, "é")

    def test_emoji_not_split(self):
        # 🎉 is 4 bytes; truncating to 3 should not produce garbage
        text = "🎉"
        result = safe_truncate_bytes(text, 3)
        # Should not include the emoji (would be garbled)
        result.encode("utf-8")  # must be valid UTF-8
        self.assertEqual(result, "")

    def test_emoji_fits(self):
        text = "🎉"
        result = safe_truncate_bytes(text, 4)
        self.assertEqual(result, "🎉")

    def test_mixed_content_truncation(self):
        text = "AB🎉CD"
        # 'AB' = 2 bytes, '🎉' = 4 bytes
        result = safe_truncate_bytes(text, 3)
        # Should keep 'AB' but not the partial emoji
        self.assertEqual(result, "AB")

    def test_result_is_valid_utf8(self):
        text = "Hello 🎉 café"
        for n in range(0, 20):
            result = safe_truncate_bytes(text, n)
            # Must be encodable back to UTF-8 without errors
            result.encode("utf-8")

    def test_truncation_respects_max_bytes(self):
        text = "Hello 🎉 café"
        for n in range(0, 20):
            result = safe_truncate_bytes(text, n)
            self.assertLessEqual(len(result.encode("utf-8")), n)

    def test_zero_max_bytes(self):
        result = safe_truncate_bytes("Hello", 0)
        self.assertEqual(result, "")

    def test_japanese_truncation(self):
        # Each CJK char is 3 bytes in UTF-8
        text = "日本語"
        result = safe_truncate_bytes(text, 3)
        self.assertEqual(result, "日")

    def test_japanese_partial_char(self):
        text = "日本語"
        result = safe_truncate_bytes(text, 5)  # 3+2, can't fit second char fully
        self.assertEqual(result, "日")

    def test_japanese_two_chars(self):
        text = "日本語"
        result = safe_truncate_bytes(text, 6)
        self.assertEqual(result, "日本")


# ===========================================================================
# 13. East-Asian Display Width Tests
# ===========================================================================

class TestDisplayWidth(unittest.TestCase):

    def setUp(self):
        self.calc = DisplayWidthCalculator()

    def test_ascii_width_one(self):
        self.assertEqual(self.calc.display_width("A"), 1)

    def test_cjk_width_two(self):
        self.assertEqual(self.calc.display_width("日"), 2)

    def test_ascii_string_width(self):
        self.assertEqual(self.calc.display_width("Hello"), 5)

    def test_cjk_string_width(self):
        self.assertEqual(self.calc.display_width("日本語"), 6)

    def test_mixed_width(self):
        # "A日B" = 1 + 2 + 1 = 4
        self.assertEqual(self.calc.display_width("A日B"), 4)

    def test_fullwidth_ascii(self):
        # Fullwidth A: Ａ (U+FF21)
        fw_a = "Ａ"
        self.assertEqual(self.calc.display_width(fw_a), 2)

    def test_char_width_ascii(self):
        self.assertEqual(self.calc.char_width("A"), 1)

    def test_char_width_cjk(self):
        self.assertEqual(self.calc.char_width("日"), 2)

    def test_truncate_to_width_ascii(self):
        result = self.calc.truncate_to_width("Hello, World!", 5)
        self.assertEqual(result, "Hello")

    def test_truncate_to_width_cjk(self):
        result = self.calc.truncate_to_width("日本語", 4)
        # 日(2) + 本(2) = 4, fits exactly
        self.assertEqual(result, "日本")

    def test_truncate_to_width_mixed(self):
        # "A日" = 1+2 = 3 columns; max_width=2: only "A" fits (日 would need 2 more)
        result = self.calc.truncate_to_width("A日B", 2)
        # A(1) + no more (日 needs 2 but only 1 remaining)
        self.assertEqual(result, "A")

    def test_analyze_returns_dict(self):
        result = self.calc.analyze("A日")
        self.assertIn("python_len", result)
        self.assertIn("display_width", result)
        self.assertIn("wide_char_count", result)

    def test_analyze_wide_char_count(self):
        result = self.calc.analyze("A日B語")
        self.assertEqual(result["wide_char_count"], 2)

    def test_empty_string_width(self):
        self.assertEqual(self.calc.display_width(""), 0)


# ===========================================================================
# 14. RTL / Bidi Detection Tests
# ===========================================================================

class TestBidiDetection(unittest.TestCase):

    def setUp(self):
        self.detector = BidiDetector()

    def test_safe_text_no_override(self):
        self.assertFalse(self.detector.contains_bidi_override("Hello World"))

    def test_rtl_override_detected(self):
        # U+202E RIGHT-TO-LEFT OVERRIDE
        text = "Hello‮World"
        self.assertTrue(self.detector.contains_bidi_override(text))

    def test_rle_detected(self):
        # U+202B RIGHT-TO-LEFT EMBEDDING
        text = "Hello‫World"
        self.assertTrue(self.detector.contains_bidi_override(text))

    def test_find_overrides_empty(self):
        overrides = self.detector.find_bidi_overrides("Hello World")
        self.assertEqual(overrides, [])

    def test_find_overrides_finds_position(self):
        text = "Hello‮World"
        overrides = self.detector.find_bidi_overrides(text)
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0][0], 5)

    def test_find_overrides_returns_char(self):
        text = "Hello‮World"
        overrides = self.detector.find_bidi_overrides(text)
        self.assertEqual(overrides[0][1], "‮")

    def test_scan_trojan_source_safe(self):
        result = self.detector.scan_for_trojan_source("Hello World")
        self.assertFalse(result["is_unsafe"])
        self.assertEqual(result["override_count"], 0)

    def test_scan_trojan_source_unsafe(self):
        text = "Hello‮World"
        result = self.detector.scan_for_trojan_source(text)
        self.assertTrue(result["is_unsafe"])
        self.assertGreater(result["override_count"], 0)

    def test_arabic_is_rtl(self):
        # Arabic text
        arabic = "مرحبا"
        self.assertTrue(self.detector.is_rtl_text(arabic))

    def test_english_is_not_rtl(self):
        self.assertFalse(self.detector.is_rtl_text("Hello World"))

    def test_bidi_override_chars_set_not_empty(self):
        self.assertGreater(len(BIDI_OVERRIDE_CHARS), 0)

    def test_rtl_override_u202e_in_set(self):
        self.assertIn("‮", BIDI_OVERRIDE_CHARS)

    def test_scan_long_text_truncated_preview(self):
        # Text longer than 80 chars
        text = "A" * 90 + "‮" + "B" * 10
        result = self.detector.scan_for_trojan_source(text)
        self.assertLessEqual(len(result["text_preview"]), 80)


# ===========================================================================
# 15. I18nAnalyzer Integration Tests
# ===========================================================================

class TestI18nAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = I18nAnalyzer()

    def test_full_report_returns_report(self):
        report = self.analyzer.full_report(["Hello", "café"])
        self.assertIsInstance(report, I18nReport)

    def test_full_report_has_encoding_results(self):
        report = self.analyzer.full_report(["Hello"])
        self.assertGreater(len(report.encoding_results), 0)

    def test_full_report_has_normalization_results(self):
        report = self.analyzer.full_report(["Hello"])
        self.assertGreater(len(report.normalization_results), 0)

    def test_full_report_has_grapheme_results(self):
        report = self.analyzer.full_report(["Hello"])
        self.assertGreater(len(report.grapheme_results), 0)

    def test_full_report_mojibake_for_non_ascii(self):
        report = self.analyzer.full_report(["café"])
        self.assertGreater(report.mojibake_count, 0)

    def test_full_report_no_mojibake_for_ascii(self):
        report = self.analyzer.full_report(["Hello"])
        self.assertEqual(report.mojibake_count, 0)

    def test_full_report_bidi_unsafe_detected(self):
        text = "Hello‮World"
        report = self.analyzer.full_report([text])
        self.assertIn(text, report.bidi_unsafe_texts)

    def test_full_report_empty_text_list(self):
        report = self.analyzer.full_report([])
        self.assertEqual(len(report.encoding_results), 0)

    def test_full_report_multiple_texts(self):
        texts = ["Hello", "café", "こんにちは"]
        report = self.analyzer.full_report(texts)
        # Each text gets at least 3 encoding checks (utf-8, utf-16, latin-1)
        self.assertGreaterEqual(len(report.encoding_results), len(texts) * 2)


# ===========================================================================
# 16. Mock HTTP Server Tests
# ===========================================================================

class TestMockServer(ServerTestCase):

    def test_health_endpoint(self):
        data = _fetch(f"{self.base}/health")
        self.assertEqual(data["status"], "ok")

    def test_health_has_harness_name(self):
        data = _fetch(f"{self.base}/health")
        self.assertEqual(data["harness"], "i18n")

    def test_encoding_roundtrip_endpoint(self):
        data = _fetch(f"{self.base}/encoding/roundtrip")
        self.assertIn("results", data)
        self.assertIsInstance(data["results"], list)

    def test_encoding_roundtrip_has_utf8(self):
        data = _fetch(f"{self.base}/encoding/roundtrip")
        encodings = [r["encoding"] for r in data["results"]]
        self.assertIn("utf-8", encodings)

    def test_mojibake_endpoint(self):
        data = _fetch(f"{self.base}/encoding/mojibake")
        self.assertIn("is_mojibake", data)
        self.assertTrue(data["is_mojibake"])

    def test_mojibake_original_differs_from_result(self):
        data = _fetch(f"{self.base}/encoding/mojibake")
        self.assertNotEqual(data["original"], data["mojibake"])

    def test_bom_detect_endpoint(self):
        data = _fetch(f"{self.base}/bom/detect")
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 3)

    def test_bom_detect_utf8_sig(self):
        data = _fetch(f"{self.base}/bom/detect")
        bom_names = [r["bom_name"] for r in data["results"]]
        self.assertIn("utf-8-sig", bom_names)

    def test_grapheme_analyze_endpoint(self):
        data = _fetch(f"{self.base}/grapheme/analyze")
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 2)

    def test_grapheme_emoji_utf16_units(self):
        data = _fetch(f"{self.base}/grapheme/analyze")
        emoji_result = next(r for r in data["results"] if r["text"] == "🎉")
        self.assertEqual(emoji_result["utf16_units"], 2)

    def test_grapheme_family_is_one_grapheme(self):
        data = _fetch(f"{self.base}/grapheme/analyze")
        family_result = next(r for r in data["results"] if "👨" in r["text"])
        self.assertEqual(family_result["grapheme_count"], 1)

    def test_normalization_demo_endpoint(self):
        data = _fetch(f"{self.base}/normalization/demo")
        self.assertIn("dedup_bug_demonstrated", data)
        self.assertTrue(data["dedup_bug_demonstrated"])

    def test_normalization_raw_set_size_two(self):
        data = _fetch(f"{self.base}/normalization/demo")
        self.assertEqual(data["raw_set_size"], 2)

    def test_normalization_norm_set_size_one(self):
        data = _fetch(f"{self.base}/normalization/demo")
        self.assertEqual(data["norm_set_size"], 1)

    def test_casefold_german_endpoint(self):
        data = _fetch(f"{self.base}/casefold/german")
        self.assertIn("casefold", data)
        self.assertEqual(data["casefold"], "ss")

    def test_casefold_turkish_endpoint(self):
        data = _fetch(f"{self.base}/casefold/turkish")
        self.assertIn("turkish_trap", data)
        self.assertTrue(data["turkish_trap"])

    def test_truncate_endpoint(self):
        data = _fetch(f"{self.base}/truncate")
        self.assertIn("truncated", data)

    def test_truncate_results_are_valid_strings(self):
        data = _fetch(f"{self.base}/truncate")
        for _key, val in data["truncated"].items():
            self.assertIsInstance(val, str)

    def test_east_asian_width_endpoint(self):
        data = _fetch(f"{self.base}/width/eastasian")
        self.assertIn("results", data)

    def test_east_asian_cjk_width(self):
        data = _fetch(f"{self.base}/width/eastasian")
        cjk_result = next(r for r in data["results"] if "こ" in r["text"])
        self.assertGreater(cjk_result["display_width"], cjk_result["python_len"])

    def test_bidi_detect_endpoint(self):
        data = _fetch(f"{self.base}/bidi/detect")
        self.assertIn("safe", data)
        self.assertIn("unsafe", data)

    def test_bidi_safe_not_flagged(self):
        data = _fetch(f"{self.base}/bidi/detect")
        self.assertFalse(data["safe"]["is_unsafe"])

    def test_bidi_unsafe_flagged(self):
        data = _fetch(f"{self.base}/bidi/detect")
        self.assertTrue(data["unsafe"]["is_unsafe"])

    def test_report_endpoint(self):
        data = _fetch(f"{self.base}/report")
        self.assertIn("encoding_ok", data)
        self.assertIn("encoding_fail", data)
        self.assertIn("mojibake_count", data)

    def test_report_has_normalization_count(self):
        data = _fetch(f"{self.base}/report")
        self.assertIn("normalization_count", data)
        self.assertGreater(data["normalization_count"], 0)

    def test_404_unknown_path(self):
        try:
            _fetch(f"{self.base}/nonexistent")
            self.fail("Expected HTTP error")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


# ===========================================================================
# 17. Edge Cases and Boundary Conditions
# ===========================================================================

class TestEdgeCases(unittest.TestCase):

    def test_empty_string_normalization(self):
        tester = NormalizationTester()
        r = tester.normalize("", "NFC")
        self.assertEqual(r.normalized, "")
        self.assertFalse(r.changed)

    def test_empty_string_encode_roundtrip(self):
        tester = EncodingTester()
        r = tester.test_roundtrip("", "utf-8")
        self.assertTrue(r.round_trip_ok)
        self.assertEqual(r.encoded_bytes, b"")

    def test_null_byte_in_string_utf8(self):
        text = "Hello\x00World"
        tester = EncodingTester()
        r = tester.test_roundtrip(text, "utf-8")
        self.assertTrue(r.round_trip_ok)

    def test_very_long_string_truncation(self):
        text = "A" * 10000
        result = safe_truncate_bytes(text, 100)
        self.assertEqual(result, "A" * 100)

    def test_all_bom_bytes_correct(self):
        self.assertEqual(BOM_UTF8, bytes([0xEF, 0xBB, 0xBF]))
        self.assertEqual(BOM_UTF16_LE, bytes([0xFF, 0xFE]))
        self.assertEqual(BOM_UTF16_BE, bytes([0xFE, 0xFF]))

    def test_surrogate_pair_analyze_preserves_text(self):
        text = "Hello 🎉"
        tester = SurrogateTester()
        gr = tester.analyze(text)
        self.assertEqual(gr.text, text)

    def test_display_width_empty_string(self):
        calc = DisplayWidthCalculator()
        self.assertEqual(calc.display_width(""), 0)

    def test_display_width_newline(self):
        calc = DisplayWidthCalculator()
        # newline is not wide
        self.assertEqual(calc.char_width("\n"), 1)

    def test_bidi_multiple_overrides(self):
        text = "A‮B‫C"
        detector = BidiDetector()
        overrides = detector.find_bidi_overrides(text)
        self.assertEqual(len(overrides), 2)

    def test_casefold_empty_string(self):
        tester = CasefoldTester()
        result = tester.casefold_normalize("")
        self.assertEqual(result, "")

    def test_nfkc_collapses_fullwidth(self):
        # Fullwidth A (U+FF21) should NFKC-normalize to regular A
        fullwidth_a = "Ａ"
        tester = NormalizationTester()
        r = tester.normalize(fullwidth_a, "NFKC")
        self.assertEqual(r.normalized, "A")

    def test_grapheme_variation_selector(self):
        # Variation selector 16 (U+FE0F) attaches to preceding char
        text = "#️"  # # with emoji presentation
        count = estimate_grapheme_clusters(text)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
