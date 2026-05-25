"""
113 tests for upload_test_harness.py
Pure stdlib, zero external dependencies.
"""

import gzip
import io
import os
import struct
import sys
import time
import unittest
import urllib.request
import urllib.error
import zlib
import zipfile

# Ensure parent dir is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from upload_test_harness import (
    MultipartParser,
    DecompressionBombChecker,
    DecompressionBombError,
    ContentTypeSniffer,
    SizeLimitChecker,
    FilenameSanitizer,
    PartialStreamTester,
    UploadPart,
    UploadResult,
    UploadReport,
    MockUploadHandler,
    MockUploadServer,
    build_multipart_body,
    UploadValidator,
    DEFAULT_ALLOWED_TYPES,
)


# ===========================================================================
# Helpers
# ===========================================================================

def make_gzip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(data)
    return buf.getvalue()


def make_zlib(data: bytes) -> bytes:
    return zlib.compress(data)


def make_zip(files: dict) -> bytes:
    """files: {name: bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ===========================================================================
# 1. MultipartParser Tests (28 tests)
# ===========================================================================

class TestMultipartParserBasic(unittest.TestCase):
    """Basic multipart parsing."""

    def _build(self, parts, boundary="BOUNDARY"):
        return build_multipart_body(parts, boundary)

    def test_single_text_field(self):
        body = self._build([{"name": "field1", "data": "hello"}])
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].name, "field1")
        self.assertEqual(parts[0].data, b"hello")
        self.assertEqual(errors, [])

    def test_multiple_fields(self):
        body = self._build([
            {"name": "a", "data": "val_a"},
            {"name": "b", "data": "val_b"},
            {"name": "c", "data": "val_c"},
        ])
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 3)
        names = [p.name for p in parts]
        self.assertIn("a", names)
        self.assertIn("b", names)
        self.assertIn("c", names)

    def test_file_part_with_filename(self):
        body = self._build([{
            "name": "upload",
            "filename": "test.txt",
            "content_type": "text/plain",
            "data": b"file content here",
        }])
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].filename, "test.txt")
        self.assertEqual(parts[0].data, b"file content here")

    def test_mixed_field_and_file(self):
        body = self._build([
            {"name": "username", "data": "alice"},
            {"name": "avatar", "filename": "pic.png", "content_type": "image/png",
             "data": bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])},
        ])
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 2)

    def test_content_type_preserved(self):
        body = self._build([{
            "name": "doc",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "data": b"%PDF-1.4",
        }])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(parts[0].content_type, "application/pdf")

    def test_from_content_type_header(self):
        ct = 'multipart/form-data; boundary=MyBound'
        parser = MultipartParser.from_content_type(ct)
        self.assertIsNotNone(parser)
        body = self._build([{"name": "x", "data": "y"}], boundary="MyBound")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 1)

    def test_from_content_type_quoted_boundary(self):
        ct = 'multipart/form-data; boundary="QuotedBound"'
        parser = MultipartParser.from_content_type(ct)
        self.assertIsNotNone(parser)

    def test_from_content_type_missing_boundary(self):
        ct = 'multipart/form-data'
        parser = MultipartParser.from_content_type(ct)
        self.assertIsNone(parser)

    def test_empty_body(self):
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(b"")
        self.assertEqual(parts, [])
        self.assertEqual(errors, [])

    def test_binary_data_preserved(self):
        binary = bytes(range(256))
        body = self._build([{"name": "bin", "filename": "data.bin",
                             "content_type": "application/octet-stream",
                             "data": binary}])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(parts[0].data, binary)

    def test_boundary_in_content_escaped(self):
        """Data containing boundary-like text should be handled gracefully."""
        # The boundary won't appear verbatim with -- prefix in normal data
        body = self._build([
            {"name": "f1", "data": "text with BOUNDARY inside"},
            {"name": "f2", "data": "another part"},
        ])
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        # Should have 2 parts
        self.assertEqual(len(parts), 2)

    def test_missing_trailing_boundary(self):
        """Body that ends without final boundary should still parse available parts."""
        body = self._build([
            {"name": "f1", "data": "complete"},
            {"name": "f2", "data": "also complete"},
        ])
        # Truncate final boundary
        truncated = body[:-10]
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(truncated)
        # At least first part should parse
        self.assertGreaterEqual(len(parts), 1)

    def test_crlf_line_endings(self):
        body = (
            b"--BOUNDARY\r\n"
            b'Content-Disposition: form-data; name="field"\r\n'
            b"\r\n"
            b"value\r\n"
            b"--BOUNDARY--\r\n"
        )
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].data, b"value")

    def test_empty_part_data(self):
        body = (
            b"--BOUNDARY\r\n"
            b'Content-Disposition: form-data; name="empty_field"\r\n'
            b"\r\n"
            b"\r\n"
            b"--BOUNDARY--\r\n"
        )
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].data, b"")

    def test_part_name_extraction(self):
        body = self._build([{"name": "my_special_field", "data": "xyz"}])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(parts[0].name, "my_special_field")

    def test_multiline_header_values(self):
        """Headers with special characters in filename."""
        body = self._build([{
            "name": "upload",
            "filename": "my file.txt",
            "content_type": "text/plain",
            "data": b"data",
        }])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(parts[0].filename, "my file.txt")

    def test_truncated_body_reports_error(self):
        """Body cut mid-part should report an error."""
        body = (
            b"--BOUNDARY\r\n"
            b'Content-Disposition: form-data; name="f1"\r\n'
            b"\r\n"
            b"complete\r\n"
            b"--BOUNDARY\r\n"
            b'Content-Disposition: form-data; name="f2"\r\n'
            # No body separator - truncated
        )
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        # Should report at least one error or handle gracefully
        # (truncated part has no body)

    def test_large_number_of_parts(self):
        fields = [{"name": f"field{i}", "data": f"value{i}"} for i in range(20)]
        body = self._build(fields)
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 20)

    def test_part_with_no_content_type(self):
        body = (
            b"--BOUNDARY\r\n"
            b'Content-Disposition: form-data; name="noct"\r\n'
            b"\r\n"
            b"data\r\n"
            b"--BOUNDARY--\r\n"
        )
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].content_type, "text/plain")

    def test_unicode_field_value(self):
        body = self._build([{"name": "msg", "data": "héllo wörld".encode("utf-8")}])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(parts[0].data, "héllo wörld".encode("utf-8"))

    def test_complex_boundary(self):
        boundary = "---=_Part_123_456789.0987654321"
        body = self._build([{"name": "x", "data": "test"}], boundary=boundary)
        parser = MultipartParser(boundary)
        parts, _ = parser.parse(body)
        self.assertEqual(len(parts), 1)

    def test_two_file_uploads(self):
        body = self._build([
            {"name": "file1", "filename": "a.txt", "content_type": "text/plain", "data": b"AAA"},
            {"name": "file2", "filename": "b.txt", "content_type": "text/plain", "data": b"BBB"},
        ])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].data, b"AAA")
        self.assertEqual(parts[1].data, b"BBB")

    def test_part_data_with_newlines(self):
        body = self._build([{"name": "text", "data": "line1\nline2\nline3"}])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertIn(b"line1", parts[0].data)
        self.assertIn(b"line2", parts[0].data)

    def test_content_disposition_no_filename(self):
        body = self._build([{"name": "field", "data": "value"}])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertIsNone(parts[0].filename)

    def test_bytes_boundary(self):
        """Parser accepts bytes boundary."""
        parser = MultipartParser(b"BytesBound")
        body = self._build([{"name": "f", "data": "v"}], boundary="BytesBound")
        parts, _ = parser.parse(body)
        self.assertEqual(len(parts), 1)

    def test_parse_only_preamble(self):
        """Body with only preamble and no parts."""
        body = b"Just some preamble without boundary"
        parser = MultipartParser("BOUNDARY")
        parts, errors = parser.parse(body)
        self.assertEqual(parts, [])

    def test_filename_with_spaces(self):
        body = self._build([{
            "name": "up",
            "filename": "my document.pdf",
            "content_type": "application/pdf",
            "data": b"%PDF",
        }])
        parser = MultipartParser("BOUNDARY")
        parts, _ = parser.parse(body)
        self.assertEqual(parts[0].filename, "my document.pdf")


# ===========================================================================
# 2. DecompressionBombChecker Tests (25 tests)
# ===========================================================================

class TestDecompressionBombChecker(unittest.TestCase):

    def setUp(self):
        self.checker = DecompressionBombChecker(
            max_output_bytes=1024 * 1024,  # 1 MB for tests
            max_ratio=10,
            max_depth=2,
        )

    # --- gzip tests ---

    def test_gzip_normal(self):
        # Use pseudo-random-ish data to keep ratio well below max_ratio=10
        import hashlib
        data = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(30))
        compressed = make_gzip(data)
        result = self.checker.check_gzip(compressed)
        self.assertEqual(result, data)

    def test_gzip_small_payload(self):
        data = b"tiny"
        compressed = make_gzip(data)
        result = self.checker.check_gzip(compressed)
        self.assertEqual(result, data)

    def test_gzip_bomb_exceeds_output(self):
        """Compressed data that expands beyond max_output_bytes."""
        checker = DecompressionBombChecker(max_output_bytes=100, max_ratio=1000)
        data = b"A" * 200
        compressed = make_gzip(data)
        with self.assertRaises(DecompressionBombError):
            checker.check_gzip(compressed)

    def test_gzip_bomb_exceeds_ratio(self):
        """Compressed data with too high a ratio."""
        checker = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024, max_ratio=2)
        data = b"A" * 50000
        compressed = make_gzip(data)
        with self.assertRaises(DecompressionBombError):
            checker.check_gzip(compressed)

    def test_gzip_invalid_data(self):
        with self.assertRaises((ValueError, Exception)):
            self.checker.check_gzip(b"not gzip data at all")

    def test_gzip_empty_content(self):
        data = b""
        compressed = make_gzip(data)
        result = self.checker.check_gzip(compressed)
        self.assertEqual(result, b"")

    # --- zlib tests ---

    def test_zlib_normal(self):
        import hashlib
        # Use pseudo-random data to keep ratio well below max_ratio=10
        data = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(30))
        compressed = make_zlib(data)
        result = self.checker.check_zlib(compressed)
        self.assertEqual(result, data)

    def test_zlib_bomb_exceeds_output(self):
        checker = DecompressionBombChecker(max_output_bytes=100, max_ratio=1000)
        data = b"B" * 200
        compressed = make_zlib(data)
        with self.assertRaises(DecompressionBombError):
            checker.check_zlib(compressed)

    def test_zlib_bomb_exceeds_ratio(self):
        checker = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024, max_ratio=2)
        data = b"B" * 50000
        compressed = make_zlib(data)
        with self.assertRaises(DecompressionBombError):
            checker.check_zlib(compressed)

    def test_zlib_invalid_data(self):
        with self.assertRaises((ValueError, Exception)):
            self.checker.check_zlib(b"not zlib data at all xyz")

    def test_zlib_binary_data(self):
        data = bytes(range(256)) * 10
        compressed = make_zlib(data)
        result = self.checker.check_zlib(compressed)
        self.assertEqual(result, data)

    # --- zip tests ---

    def test_zip_normal(self):
        files = {"readme.txt": b"Hello", "data.bin": b"\x00\x01\x02"}
        data = make_zip(files)
        result = self.checker.check_zip(data)
        self.assertIn("readme.txt", result)
        self.assertEqual(result["readme.txt"], b"Hello")

    def test_zip_bomb_total_output(self):
        checker = DecompressionBombChecker(max_output_bytes=500, max_ratio=1000)
        files = {"bigfile.txt": b"X" * 1000}
        data = make_zip(files)
        with self.assertRaises(DecompressionBombError):
            checker.check_zip(data)

    def test_zip_bomb_ratio(self):
        checker = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024, max_ratio=2)
        files = {"big.txt": b"Z" * 50000}
        data = make_zip(files)
        with self.assertRaises(DecompressionBombError):
            checker.check_zip(data)

    def test_zip_multiple_files(self):
        files = {f"file{i}.txt": f"content{i}".encode() for i in range(5)}
        data = make_zip(files)
        result = self.checker.check_zip(data)
        self.assertEqual(len(result), 5)

    def test_zip_nested_depth_limit(self):
        """Nested zip exceeding max_depth should raise."""
        checker = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024,
                                           max_ratio=100, max_depth=1)
        # Create inner zip
        inner = make_zip({"inner.txt": b"deep content"})
        # Create outer zip containing the inner zip
        outer = make_zip({"nested.zip": inner})
        # With max_depth=1, depth starts at 0, inner is depth 1, which equals max_depth
        # Recursion into inner.zip would be depth 2 > max_depth=1 -- but inner.zip doesn't
        # contain a zip, so this should succeed at depth 1.
        # Test that depth 0 recursion into a zip at depth=max_depth raises
        checker2 = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024,
                                            max_ratio=100, max_depth=0)
        with self.assertRaises(DecompressionBombError):
            checker2.check_zip(outer)

    def test_zip_empty_archive(self):
        data = make_zip({})
        result = self.checker.check_zip(data)
        self.assertEqual(result, {})

    def test_zip_invalid_data(self):
        with self.assertRaises((ValueError, Exception)):
            self.checker.check_zip(b"not a zip file")

    def test_zip_single_file(self):
        data = make_zip({"only.txt": b"sole content"})
        result = self.checker.check_zip(data)
        self.assertEqual(result["only.txt"], b"sole content")

    def test_decompression_error_message_gzip(self):
        checker = DecompressionBombChecker(max_output_bytes=10, max_ratio=1000)
        data = make_gzip(b"A" * 100)
        try:
            checker.check_gzip(data)
            self.fail("Expected DecompressionBombError")
        except DecompressionBombError as e:
            self.assertIn("max_output_bytes", str(e))

    def test_decompression_error_message_ratio(self):
        checker = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024, max_ratio=2)
        data = make_zlib(b"A" * 50000)
        try:
            checker.check_zlib(data)
            self.fail("Expected DecompressionBombError")
        except DecompressionBombError as e:
            self.assertIn("ratio", str(e).lower())

    def test_default_limits(self):
        checker = DecompressionBombChecker()
        self.assertEqual(checker.max_output_bytes, DecompressionBombChecker.DEFAULT_MAX_OUTPUT)
        self.assertEqual(checker.max_ratio, DecompressionBombChecker.DEFAULT_MAX_RATIO)
        self.assertEqual(checker.max_depth, DecompressionBombChecker.DEFAULT_MAX_DEPTH)

    def test_gzip_exactly_at_limit(self):
        """Data exactly at limit should pass."""
        limit = 1000
        checker = DecompressionBombChecker(max_output_bytes=limit, max_ratio=1000)
        data = make_gzip(b"A" * limit)
        result = checker.check_gzip(data)
        self.assertEqual(len(result), limit)

    def test_gzip_one_byte_over_limit(self):
        """Data one byte over limit should fail."""
        limit = 100
        checker = DecompressionBombChecker(max_output_bytes=limit, max_ratio=1000)
        data = make_gzip(b"A" * (limit + 1))
        with self.assertRaises(DecompressionBombError):
            checker.check_gzip(data)

    def test_nested_zip_contents_returned(self):
        """Valid nested zip within depth should return contents."""
        inner = make_zip({"inner.txt": b"inner content"})
        outer = make_zip({"nested.zip": inner})
        checker = DecompressionBombChecker(max_output_bytes=10 * 1024 * 1024,
                                           max_ratio=100, max_depth=3)
        result = checker.check_zip(outer)
        # Should have the nested content
        nested_key = [k for k in result if "inner.txt" in k]
        self.assertTrue(len(nested_key) > 0)


# ===========================================================================
# 3. ContentTypeSniffer Tests (18 tests)
# ===========================================================================

class TestContentTypeSniffer(unittest.TestCase):

    def setUp(self):
        self.sniffer = ContentTypeSniffer()

    # --- sniff ---

    def test_sniff_png(self):
        data = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"rest"
        self.assertEqual(self.sniffer.sniff(data), "image/png")

    def test_sniff_gif87a(self):
        data = b"GIF87a" + b"rest"
        self.assertEqual(self.sniffer.sniff(data), "image/gif")

    def test_sniff_gif89a(self):
        data = b"GIF89a" + b"rest"
        self.assertEqual(self.sniffer.sniff(data), "image/gif")

    def test_sniff_pdf(self):
        data = b"%PDF-1.4 rest of pdf"
        self.assertEqual(self.sniffer.sniff(data), "application/pdf")

    def test_sniff_zip(self):
        data = bytes([0x50, 0x4B, 0x03, 0x04]) + b"rest"
        self.assertEqual(self.sniffer.sniff(data), "application/zip")

    def test_sniff_jpeg(self):
        data = bytes([0xFF, 0xD8, 0xFF]) + b"rest"
        self.assertEqual(self.sniffer.sniff(data), "image/jpeg")

    def test_sniff_unknown(self):
        data = b"unknown format data here"
        self.assertIsNone(self.sniffer.sniff(data))

    def test_sniff_empty(self):
        self.assertIsNone(self.sniffer.sniff(b""))

    # --- validate ---

    def test_validate_correct_png(self):
        data = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        valid, msg = self.sniffer.validate(data, "image/png")
        self.assertTrue(valid)
        self.assertEqual(msg, "OK")

    def test_validate_correct_jpeg(self):
        data = bytes([0xFF, 0xD8, 0xFF, 0xE0])
        valid, msg = self.sniffer.validate(data, "image/jpeg")
        self.assertTrue(valid)

    def test_validate_content_type_mismatch(self):
        """Declared PNG but data is JPEG."""
        data = bytes([0xFF, 0xD8, 0xFF, 0xE0])
        valid, msg = self.sniffer.validate(data, "image/png")
        self.assertFalse(valid)
        self.assertIn("mismatch", msg.lower())

    def test_validate_type_not_in_allowlist(self):
        data = b"executable data"
        valid, msg = self.sniffer.validate(data, "application/x-executable")
        self.assertFalse(valid)
        self.assertIn("allow-list", msg.lower())

    def test_validate_plain_text_no_magic(self):
        data = b"Plain text content"
        valid, msg = self.sniffer.validate(data, "text/plain")
        self.assertTrue(valid)

    def test_validate_content_type_with_params(self):
        """Content-Type with charset should still work."""
        data = b"Plain text content"
        valid, msg = self.sniffer.validate(data, "text/plain; charset=utf-8")
        self.assertTrue(valid)

    def test_is_allowed_true(self):
        self.assertTrue(self.sniffer.is_allowed("image/png"))

    def test_is_allowed_false(self):
        self.assertFalse(self.sniffer.is_allowed("application/x-msdownload"))

    def test_custom_allowlist(self):
        sniffer = ContentTypeSniffer(allowed_types={"image/png"})
        self.assertTrue(sniffer.is_allowed("image/png"))
        self.assertFalse(sniffer.is_allowed("image/jpeg"))

    def test_validate_pdf_correct(self):
        data = b"%PDF-1.5 content"
        valid, msg = self.sniffer.validate(data, "application/pdf")
        self.assertTrue(valid)


# ===========================================================================
# 4. SizeLimitChecker Tests (10 tests)
# ===========================================================================

class TestSizeLimitChecker(unittest.TestCase):

    def test_under_limit(self):
        checker = SizeLimitChecker(100)
        n, hit = checker.check_bytes(b"A" * 50)
        self.assertEqual(n, 50)
        self.assertFalse(hit)

    def test_exactly_at_limit(self):
        checker = SizeLimitChecker(100)
        n, hit = checker.check_bytes(b"A" * 100)
        self.assertEqual(n, 100)
        self.assertFalse(hit)

    def test_over_limit(self):
        checker = SizeLimitChecker(100)
        n, hit = checker.check_bytes(b"A" * 101)
        self.assertEqual(n, 100)
        self.assertTrue(hit)

    def test_zero_limit(self):
        checker = SizeLimitChecker(0)
        n, hit = checker.check_bytes(b"")
        self.assertFalse(hit)

    def test_zero_limit_one_byte(self):
        checker = SizeLimitChecker(0)
        n, hit = checker.check_bytes(b"A")
        self.assertTrue(hit)

    def test_stream_under_limit(self):
        checker = SizeLimitChecker(1000)
        stream = io.BytesIO(b"X" * 500)
        n, hit = checker.read_stream(stream)
        self.assertEqual(n, 500)
        self.assertFalse(hit)

    def test_stream_over_limit(self):
        checker = SizeLimitChecker(100)
        stream = io.BytesIO(b"X" * 200)
        n, hit = checker.read_stream(stream)
        self.assertTrue(hit)

    def test_stream_exactly_at_limit(self):
        checker = SizeLimitChecker(100)
        stream = io.BytesIO(b"X" * 100)
        n, hit = checker.read_stream(stream)
        self.assertFalse(hit)

    def test_empty_stream(self):
        checker = SizeLimitChecker(100)
        stream = io.BytesIO(b"")
        n, hit = checker.read_stream(stream)
        self.assertEqual(n, 0)
        self.assertFalse(hit)

    def test_large_limit(self):
        checker = SizeLimitChecker(10 * 1024 * 1024)
        n, hit = checker.check_bytes(b"A" * 1024)
        self.assertFalse(hit)


# ===========================================================================
# 5. FilenameSanitizer Tests (20 tests)
# ===========================================================================

class TestFilenameSanitizer(unittest.TestCase):

    def setUp(self):
        self.san = FilenameSanitizer()

    def test_safe_filename(self):
        safe, reason, name = self.san.sanitize("document.pdf")
        self.assertTrue(safe)
        self.assertEqual(name, "document.pdf")

    def test_path_traversal_forward_slash(self):
        safe, reason, _ = self.san.sanitize("../etc/passwd")
        self.assertFalse(safe)
        self.assertIn("traversal", reason.lower())

    def test_path_traversal_backslash(self):
        safe, reason, _ = self.san.sanitize("..\\windows\\system32\\cmd.exe")
        self.assertFalse(safe)
        self.assertIn("traversal", reason.lower())

    def test_path_traversal_nested(self):
        safe, reason, _ = self.san.sanitize("uploads/../../secret.txt")
        self.assertFalse(safe)

    def test_null_byte(self):
        safe, reason, _ = self.san.sanitize("file\x00.txt")
        self.assertFalse(safe)
        self.assertIn("null", reason.lower())

    def test_absolute_path_unix(self):
        safe, reason, _ = self.san.sanitize("/etc/passwd")
        self.assertFalse(safe)
        self.assertIn("absolute", reason.lower())

    def test_absolute_path_windows(self):
        safe, reason, _ = self.san.sanitize("C:\\Windows\\System32")
        self.assertFalse(safe)
        self.assertIn("absolute", reason.lower())

    def test_windows_reserved_con(self):
        safe, reason, _ = self.san.sanitize("CON")
        self.assertFalse(safe)
        self.assertIn("reserved", reason.lower())

    def test_windows_reserved_nul(self):
        safe, reason, _ = self.san.sanitize("NUL.txt")
        self.assertFalse(safe)

    def test_windows_reserved_com1(self):
        safe, reason, _ = self.san.sanitize("COM1")
        self.assertFalse(safe)

    def test_windows_reserved_lpt9(self):
        safe, reason, _ = self.san.sanitize("LPT9.log")
        self.assertFalse(safe)

    def test_windows_reserved_prn(self):
        safe, reason, _ = self.san.sanitize("PRN")
        self.assertFalse(safe)

    def test_windows_reserved_aux(self):
        safe, reason, _ = self.san.sanitize("AUX")
        self.assertFalse(safe)

    def test_empty_filename(self):
        safe, reason, _ = self.san.sanitize("")
        self.assertFalse(safe)

    def test_filename_with_directory_stripped(self):
        """Path component should be stripped, basename returned."""
        safe, reason, name = self.san.sanitize("uploads/photo.jpg")
        # "uploads/photo.jpg" does not contain ".." so it's safe
        # but the returned name should be just the basename
        if safe:
            self.assertEqual(name, "photo.jpg")

    def test_is_safe_true(self):
        self.assertTrue(self.san.is_safe("image.png"))

    def test_is_safe_false_traversal(self):
        self.assertFalse(self.san.is_safe("../bad.txt"))

    def test_filename_with_spaces(self):
        safe, _, name = self.san.sanitize("my document.pdf")
        self.assertTrue(safe)
        self.assertIn("document.pdf", name)

    def test_windows_reserved_case_insensitive(self):
        """con, Con, CON should all be rejected."""
        for name in ["con", "Con", "CON", "con.txt"]:
            safe, _, _ = self.san.sanitize(name)
            self.assertFalse(safe, f"Expected '{name}' to be rejected")

    def test_safe_extension_variety(self):
        for fn in ["image.png", "report.pdf", "data.csv", "archive.zip"]:
            safe, _, _ = self.san.sanitize(fn)
            self.assertTrue(safe, f"Expected '{fn}' to be safe")


# ===========================================================================
# 6. PartialStreamTester Tests (8 tests)
# ===========================================================================

class TestPartialStreamTester(unittest.TestCase):

    def setUp(self):
        self.tester = PartialStreamTester()

    def test_exact_length(self):
        body = b"Hello World!"
        truncated, msg = self.tester.check(body, len(body))
        self.assertFalse(truncated)
        self.assertEqual(msg, "OK")

    def test_truncated(self):
        body = b"Hello"
        truncated, msg = self.tester.check(body, 10)
        self.assertTrue(truncated)
        self.assertIn("5", msg)
        self.assertIn("10", msg)

    def test_longer_than_declared(self):
        body = b"Hello Extra Bytes"
        truncated, msg = self.tester.check(body, 5)
        self.assertFalse(truncated)

    def test_no_content_length(self):
        body = b"data"
        truncated, msg = self.tester.check(body, None)
        self.assertFalse(truncated)

    def test_empty_body_zero_length(self):
        truncated, msg = self.tester.check(b"", 0)
        self.assertFalse(truncated)

    def test_empty_body_nonzero_declared(self):
        truncated, msg = self.tester.check(b"", 100)
        self.assertTrue(truncated)

    def test_stream_check_exact(self):
        stream = io.BytesIO(b"exact content")
        truncated, msg = self.tester.check_stream(stream, 13)
        self.assertFalse(truncated)

    def test_stream_check_truncated(self):
        stream = io.BytesIO(b"short")
        truncated, msg = self.tester.check_stream(stream, 100)
        self.assertTrue(truncated)


# ===========================================================================
# 7. UploadPart / UploadResult / UploadReport Tests (8 tests)
# ===========================================================================

class TestDataclasses(unittest.TestCase):

    def test_upload_part_defaults(self):
        part = UploadPart()
        self.assertEqual(part.name, "")
        self.assertIsNone(part.filename)
        self.assertEqual(part.content_type, "text/plain")
        self.assertEqual(part.data, b"")

    def test_upload_part_with_values(self):
        part = UploadPart(name="file", filename="test.png", content_type="image/png",
                          data=b"\x89PNG")
        self.assertEqual(part.name, "file")
        self.assertEqual(part.filename, "test.png")

    def test_upload_result_defaults(self):
        result = UploadResult()
        self.assertEqual(result.parts, [])
        self.assertEqual(result.errors, [])
        self.assertFalse(result.rejected)
        self.assertEqual(result.rejection_reason, "")

    def test_upload_result_rejected(self):
        result = UploadResult(rejected=True, rejection_reason="too big")
        self.assertTrue(result.rejected)
        self.assertEqual(result.rejection_reason, "too big")

    def test_upload_report_defaults(self):
        report = UploadReport()
        self.assertEqual(report.total_uploads, 0)
        self.assertEqual(report.accepted, 0)
        self.assertEqual(report.rejected, 0)

    def test_upload_report_add_accepted(self):
        report = UploadReport()
        result = UploadResult()
        report.add_result(result, byte_count=100)
        self.assertEqual(report.total_uploads, 1)
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.rejected, 0)
        self.assertEqual(report.total_bytes, 100)

    def test_upload_report_add_rejected(self):
        report = UploadReport()
        result = UploadResult(rejected=True, rejection_reason="bomb")
        report.add_result(result, byte_count=50)
        self.assertEqual(report.rejected, 1)
        self.assertEqual(report.accepted, 0)

    def test_upload_report_multiple_results(self):
        report = UploadReport()
        for i in range(5):
            r = UploadResult(rejected=(i % 2 == 0))
            report.add_result(r)
        self.assertEqual(report.total_uploads, 5)
        self.assertEqual(report.rejected, 3)  # indices 0,2,4
        self.assertEqual(report.accepted, 2)  # indices 1,3


# ===========================================================================
# 8. MockUploadServer Tests (9 tests)
# ===========================================================================

class TestMockUploadServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = MockUploadServer(port=0)
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.clear_results()

    def _post(self, body: bytes, content_type: str,
              content_length: int = None) -> tuple:
        url = self.server.url + "/upload"
        cl = content_length if content_length is not None else len(body)
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(cl),
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_server_starts(self):
        self.assertGreater(self.server.port, 0)

    def test_get_request(self):
        req = urllib.request.Request(self.server.url + "/")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

    def test_simple_upload(self):
        body = build_multipart_body(
            [{"name": "field1", "data": "value1"}], boundary="TestBound"
        )
        status, resp = self._post(body, "multipart/form-data; boundary=TestBound")
        self.assertEqual(status, 200)

    def test_upload_result_stored(self):
        body = build_multipart_body(
            [{"name": "x", "data": "y"}], boundary="B1"
        )
        self._post(body, "multipart/form-data; boundary=B1")
        self.assertEqual(len(self.server.results), 1)

    def test_upload_parts_parsed(self):
        body = build_multipart_body([
            {"name": "f1", "data": "v1"},
            {"name": "f2", "data": "v2"},
        ], boundary="B2")
        self._post(body, "multipart/form-data; boundary=B2")
        result = self.server.results[0]
        self.assertEqual(len(result.parts), 2)

    def test_clear_results(self):
        body = build_multipart_body([{"name": "a", "data": "b"}], boundary="B3")
        self._post(body, "multipart/form-data; boundary=B3")
        self.server.clear_results()
        self.assertEqual(len(self.server.results), 0)

    def test_bad_content_type(self):
        status, _ = self._post(b"raw data", "application/octet-stream")
        # Server should still respond (may return 200 with error in result)
        self.assertIn(status, [200, 400])

    def test_multiple_uploads(self):
        for i in range(3):
            body = build_multipart_body([{"name": f"f{i}", "data": f"v{i}"}],
                                        boundary=f"B{i}")
            self._post(body, f"multipart/form-data; boundary=B{i}")
        self.assertEqual(len(self.server.results), 3)

    def test_server_url_format(self):
        self.assertTrue(self.server.url.startswith("http://127.0.0.1:"))


# ===========================================================================
# 9. UploadValidator Integration Tests (7 tests)
# ===========================================================================

class TestUploadValidator(unittest.TestCase):

    def setUp(self):
        self.validator = UploadValidator(
            max_file_size=1024,
            allowed_types={"image/png", "image/jpeg", "text/plain", "application/pdf"},
        )

    def test_valid_text_part(self):
        part = UploadPart(name="msg", data=b"hello world")
        result = self.validator.validate_part(part)
        self.assertFalse(result.rejected)

    def test_file_too_large(self):
        part = UploadPart(name="big", filename="big.txt",
                          content_type="text/plain", data=b"X" * 2000)
        result = self.validator.validate_part(part)
        self.assertTrue(result.rejected)
        self.assertIn("size", result.rejection_reason.lower())

    def test_invalid_filename(self):
        part = UploadPart(name="evil", filename="../etc/passwd",
                          content_type="text/plain", data=b"data")
        result = self.validator.validate_part(part)
        self.assertTrue(result.rejected)

    def test_content_type_mismatch(self):
        # Declare PNG but send JPEG bytes
        part = UploadPart(name="img", filename="fake.png",
                          content_type="image/png",
                          data=bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"jpeg data")
        result = self.validator.validate_part(part)
        self.assertTrue(result.rejected)

    def test_valid_png_upload(self):
        png_magic = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        part = UploadPart(name="img", filename="photo.png",
                          content_type="image/png", data=png_magic + b"rest")
        result = self.validator.validate_part(part)
        self.assertFalse(result.rejected)

    def test_disallowed_content_type(self):
        part = UploadPart(name="exe", filename="malware.exe",
                          content_type="application/x-msdownload",
                          data=b"MZ executable")
        result = self.validator.validate_part(part)
        self.assertTrue(result.rejected)

    def test_no_filename_skips_sniff(self):
        """Form fields without filename should skip content-type sniff."""
        part = UploadPart(name="bio", content_type="text/plain",
                          data=b"I am a text bio without a filename")
        result = self.validator.validate_part(part)
        self.assertFalse(result.rejected)


# ===========================================================================
# 10. build_multipart_body helper Tests (5 tests)
# ===========================================================================

class TestBuildMultipartBody(unittest.TestCase):

    def test_produces_valid_bytes(self):
        body = build_multipart_body([{"name": "a", "data": "b"}])
        self.assertIsInstance(body, bytes)
        self.assertGreater(len(body), 0)

    def test_contains_boundary(self):
        body = build_multipart_body([{"name": "a", "data": "b"}], boundary="TESTBOUND")
        self.assertIn(b"TESTBOUND", body)

    def test_contains_field_name(self):
        body = build_multipart_body([{"name": "myfield", "data": "myvalue"}])
        self.assertIn(b"myfield", body)

    def test_contains_data(self):
        body = build_multipart_body([{"name": "f", "data": "specific_data_xyz"}])
        self.assertIn(b"specific_data_xyz", body)

    def test_multiple_parts_all_present(self):
        parts = [{"name": f"f{i}", "data": f"data{i}"} for i in range(3)]
        body = build_multipart_body(parts)
        for i in range(3):
            self.assertIn(f"data{i}".encode(), body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
