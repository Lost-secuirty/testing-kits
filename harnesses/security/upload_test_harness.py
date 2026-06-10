"""
File Upload / Decompression-Bomb Test Harness (harness 35 of 36)
Pure stdlib, zero external dependencies.
"""

import gzip
import hashlib
import io
import ipaddress
import os
import re
import socket
import struct
import threading
import time
import zlib
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UploadPart:
    """Represents one part of a multipart/form-data upload."""
    name: str = ""
    filename: Optional[str] = None
    content_type: str = "text/plain"
    data: bytes = b""


@dataclass
class UploadResult:
    """Result of processing a single upload."""
    parts: List[UploadPart] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""


@dataclass
class UploadReport:
    """Aggregate validation results across multiple uploads."""
    total_uploads: int = 0
    accepted: int = 0
    rejected: int = 0
    total_bytes: int = 0
    errors: List[str] = field(default_factory=list)
    results: List[UploadResult] = field(default_factory=list)

    def add_result(self, result: UploadResult, byte_count: int = 0) -> None:
        self.total_uploads += 1
        self.total_bytes += byte_count
        self.results.append(result)
        if result.rejected:
            self.rejected += 1
        else:
            self.accepted += 1
        self.errors.extend(result.errors)


# ---------------------------------------------------------------------------
# MultipartParser
# ---------------------------------------------------------------------------

class MultipartParser:
    """
    Parses multipart/form-data from raw bytes.

    Handles:
    - Multiple fields and file parts
    - Boundary in content
    - Missing trailing boundary (partial/truncated body)
    - CRLF line endings
    - Empty parts
    - Truncated bodies
    """

    def __init__(self, boundary: str):
        if isinstance(boundary, str):
            boundary = boundary.encode("latin-1")
        self.boundary = boundary
        self._delimiter = b"--" + boundary
        self._final_delimiter = b"--" + boundary + b"--"

    @classmethod
    def from_content_type(cls, content_type: str) -> Optional["MultipartParser"]:
        """Create parser by extracting boundary from Content-Type header."""
        match = re.search(r'boundary=([^\s;]+)', content_type, re.IGNORECASE)
        if not match:
            return None
        boundary = match.group(1).strip('"')
        return cls(boundary)

    def parse(self, body: bytes) -> Tuple[List[UploadPart], List[str]]:
        """
        Parse multipart body. Returns (parts, errors).
        Tolerates truncated bodies and missing final boundary.
        """
        parts: List[UploadPart] = []
        errors: List[str] = []

        if not body:
            return parts, errors

        # Split on delimiter
        delimiter = self._delimiter
        segments = body.split(delimiter)

        # segments[0] is preamble (before first boundary), skip it
        for i, segment in enumerate(segments[1:], 1):
            # Check for final boundary marker
            if segment.startswith(b"--"):
                # This is the closing boundary, we're done
                break

            # Strip leading CRLF after boundary
            if segment.startswith(b"\r\n"):
                segment = segment[2:]
            elif segment.startswith(b"\n"):
                segment = segment[1:]

            # Strip trailing CRLF before next boundary
            if segment.endswith(b"\r\n"):
                segment = segment[:-2]
            elif segment.endswith(b"\n"):
                segment = segment[:-1]

            if not segment:
                # Empty part
                parts.append(UploadPart(name=f"part_{i}", data=b""))
                continue

            # Split headers from body on double CRLF
            if b"\r\n\r\n" in segment:
                header_block, data = segment.split(b"\r\n\r\n", 1)
            elif b"\n\n" in segment:
                header_block, data = segment.split(b"\n\n", 1)
            else:
                # No header/body separator found - truncated part
                errors.append(f"Part {i}: missing header/body separator (truncated)")
                parts.append(UploadPart(name=f"part_{i}", data=segment))
                continue

            # Parse headers
            headers = self._parse_headers(header_block)
            part = self._build_part(headers, data, i, errors)
            parts.append(part)

        return parts, errors

    def _parse_headers(self, header_block: bytes) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        lines = header_block.replace(b"\r\n", b"\n").split(b"\n")
        for line in lines:
            if b":" in line:
                key, _, val = line.partition(b":")
                headers[key.strip().lower().decode("latin-1")] = val.strip().decode("latin-1")
        return headers

    def _build_part(self, headers: Dict[str, str], data: bytes, idx: int,
                    errors: List[str]) -> UploadPart:
        part = UploadPart()
        part.data = data

        disp = headers.get("content-disposition", "")
        # Extract name
        name_match = re.search(r'name="([^"]*)"', disp)
        part.name = name_match.group(1) if name_match else f"part_{idx}"

        # Extract filename (optional)
        fn_match = re.search(r'filename="([^"]*)"', disp)
        part.filename = fn_match.group(1) if fn_match else None

        part.content_type = headers.get("content-type", "text/plain")
        return part


# ---------------------------------------------------------------------------
# DecompressionBombChecker
# ---------------------------------------------------------------------------

class DecompressionBombError(Exception):
    """Raised when decompression would exceed safety limits."""
    pass


class DecompressionBombChecker:
    """
    Safely decompresses gzip/zlib/zip data under hard output caps.

    Prevents decompression bombs by:
    - Enforcing max_output_bytes absolute limit
    - Enforcing max_ratio compression ratio limit
    - Limiting nested-zip depth
    """

    DEFAULT_MAX_OUTPUT = 50 * 1024 * 1024   # 50 MB
    DEFAULT_MAX_RATIO = 100                  # 100:1 compression ratio
    DEFAULT_MAX_DEPTH = 3                    # max nested zip depth

    def __init__(
        self,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT,
        max_ratio: float = DEFAULT_MAX_RATIO,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ):
        self.max_output_bytes = max_output_bytes
        self.max_ratio = max_ratio
        self.max_depth = max_depth

    def check_gzip(self, data: bytes) -> bytes:
        """Decompress gzip data with bomb protection."""
        return self._decompress_stream(gzip.decompress, data, "gzip")

    def check_zlib(self, data: bytes) -> bytes:
        """Decompress zlib data with bomb protection."""
        return self._decompress_stream(zlib.decompress, data, "zlib")

    def _decompress_stream(self, decompress_fn, data: bytes, fmt: str) -> bytes:
        """Decompress with streaming size check."""
        input_size = len(data)

        # Use chunked decompression to detect bombs early
        try:
            if fmt == "gzip":
                buf = io.BytesIO(data)
                result = bytearray()
                with gzip.GzipFile(fileobj=buf) as gz:
                    while True:
                        chunk = gz.read(65536)
                        if not chunk:
                            break
                        result.extend(chunk)
                        if len(result) > self.max_output_bytes:
                            raise DecompressionBombError(
                                f"gzip output exceeds max_output_bytes={self.max_output_bytes}"
                            )
                        if input_size > 0 and len(result) / input_size > self.max_ratio:
                            raise DecompressionBombError(
                                f"gzip compression ratio {len(result)/input_size:.1f} "
                                f"exceeds max_ratio={self.max_ratio}"
                            )
                output = bytes(result)
            else:
                # zlib: decompress then check
                output = decompress_fn(data)
                if len(output) > self.max_output_bytes:
                    raise DecompressionBombError(
                        f"{fmt} output exceeds max_output_bytes={self.max_output_bytes}"
                    )
                if input_size > 0 and len(output) / input_size > self.max_ratio:
                    raise DecompressionBombError(
                        f"{fmt} compression ratio {len(output)/input_size:.1f} "
                        f"exceeds max_ratio={self.max_ratio}"
                    )
        except DecompressionBombError:
            raise
        except Exception as exc:
            raise ValueError(f"Failed to decompress {fmt} data: {exc}") from exc

        return output

    def check_zip(self, data: bytes, depth: int = 0) -> Dict[str, bytes]:
        """
        Extract zip contents with bomb protection.
        Returns {filename: contents} dict.
        Recursively checks nested zips up to max_depth.
        """
        if depth > self.max_depth:
            raise DecompressionBombError(
                f"Nested zip depth {depth} exceeds max_depth={self.max_depth}"
            )

        input_size = len(data)
        total_output = 0
        result: Dict[str, bytes] = {}

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    # Check uncompressed size declared in header
                    if info.file_size > self.max_output_bytes:
                        raise DecompressionBombError(
                            f"ZIP entry '{info.filename}' declares size "
                            f"{info.file_size} > max_output_bytes={self.max_output_bytes}"
                        )

                    # Read with streaming check
                    entry_data = bytearray()
                    with zf.open(info) as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            entry_data.extend(chunk)
                            total_output += len(chunk)
                            if total_output > self.max_output_bytes:
                                raise DecompressionBombError(
                                    f"ZIP total output exceeds "
                                    f"max_output_bytes={self.max_output_bytes}"
                                )
                            if input_size > 0 and total_output / input_size > self.max_ratio:
                                raise DecompressionBombError(
                                    f"ZIP compression ratio "
                                    f"{total_output/input_size:.1f} "
                                    f"exceeds max_ratio={self.max_ratio}"
                                )

                    entry_bytes = bytes(entry_data)

                    # Recurse into nested zips (depth guard at top of function)
                    if info.filename.lower().endswith(".zip"):
                        nested = self.check_zip(entry_bytes, depth=depth + 1)
                        for k, v in nested.items():
                            result[f"{info.filename}/{k}"] = v
                    else:
                        result[info.filename] = entry_bytes

        except DecompressionBombError:
            raise
        except Exception as exc:
            raise ValueError(f"Failed to read ZIP data: {exc}") from exc

        return result


# ---------------------------------------------------------------------------
# ContentTypeSniffer
# ---------------------------------------------------------------------------

# Magic byte signatures: (offset, magic_bytes, mime_type)
MAGIC_SIGNATURES = [
    (0, bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), "image/png"),
    (0, bytes([0x47, 0x49, 0x46, 0x38, 0x37, 0x61]), "image/gif"),  # GIF87a
    (0, bytes([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]), "image/gif"),  # GIF89a
    (0, bytes([0x25, 0x50, 0x44, 0x46]),              "application/pdf"),
    (0, bytes([0x50, 0x4B, 0x03, 0x04]),              "application/zip"),
    (0, bytes([0xFF, 0xD8, 0xFF]),                    "image/jpeg"),
    (0, b"GIF",                                       "image/gif"),
]

# Default allow-list of content types
DEFAULT_ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/json",
}


class ContentTypeSniffer:
    """
    Sniffs magic bytes to detect actual content type.
    Compares against declared Content-Type.
    Enforces an allow-list.
    """

    def __init__(self, allowed_types: Optional[set] = None):
        self.allowed_types = allowed_types if allowed_types is not None else set(DEFAULT_ALLOWED_TYPES)

    def sniff(self, data: bytes) -> Optional[str]:
        """Detect content type from magic bytes. Returns MIME type or None."""
        if not data:
            return None
        for offset, magic, mime in MAGIC_SIGNATURES:
            if data[offset:offset + len(magic)] == magic:
                return mime
        return None

    def validate(self, data: bytes, declared_type: str) -> Tuple[bool, str]:
        """
        Validate data against declared content type.

        Returns (valid: bool, message: str).
        - Rejects if declared type not in allow-list
        - Warns/rejects if detected type differs from declared
        """
        # Normalise declared type (strip parameters like ; charset=utf-8)
        base_declared = declared_type.split(";")[0].strip().lower()

        if base_declared not in self.allowed_types:
            return False, f"Content-Type '{base_declared}' not in allow-list"

        detected = self.sniff(data)
        if detected is not None and detected != base_declared:
            return False, (
                f"Content-Type mismatch: declared '{base_declared}' "
                f"but detected '{detected}'"
            )

        return True, "OK"

    def is_allowed(self, content_type: str) -> bool:
        base = content_type.split(";")[0].strip().lower()
        return base in self.allowed_types


# ---------------------------------------------------------------------------
# SizeLimitChecker
# ---------------------------------------------------------------------------

class SizeLimitChecker:
    """
    Reads from a stream or bytes, stopping early at a byte limit.
    Returns (bytes_read, limit_hit).
    """

    def __init__(self, limit: int):
        self.limit = limit

    def check_bytes(self, data: bytes) -> Tuple[int, bool]:
        """Check if bytes data exceeds the limit."""
        if len(data) > self.limit:
            return self.limit, True
        return len(data), False

    def read_stream(self, stream, chunk_size: int = 8192) -> Tuple[int, bool]:
        """
        Read from a file-like stream up to the limit.
        Returns (total_bytes_read, limit_hit).
        """
        total = 0
        while True:
            remaining = self.limit - total + 1  # +1 to detect overflow
            to_read = min(chunk_size, remaining)
            chunk = stream.read(to_read)
            if not chunk:
                break
            total += len(chunk)
            if total > self.limit:
                return self.limit, True
        return total, False


# ---------------------------------------------------------------------------
# FilenameSanitizer
# ---------------------------------------------------------------------------

# Windows reserved device names
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
}


class FilenameSanitizer:
    """
    Sanitizes and validates uploaded filenames.

    Rejects:
    - Path traversal (../, ..\\)
    - Null bytes (\\x00)
    - Absolute paths
    - Windows reserved names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    """

    def sanitize(self, filename: str) -> Tuple[bool, str, str]:
        """
        Validate and sanitize a filename.

        Returns (safe: bool, reason: str, sanitized_name: str).
        - safe=False means the filename should be rejected outright.
        - sanitized_name is a cleaned version (basename only) when safe=True.
        """
        if not filename:
            return False, "Empty filename", ""

        # Check for null bytes
        if "\x00" in filename:
            return False, "Filename contains null byte", ""

        # Check for path traversal sequences
        # Normalise to forward slashes for check
        normalised = filename.replace("\\", "/")
        parts = normalised.split("/")
        for part in parts:
            if part == "..":
                return False, f"Path traversal detected in '{filename}'", ""

        # Also catch encoded forms or mixed separators
        if ".." in filename.split("/") or ".." in filename.split("\\"):
            return False, f"Path traversal detected in '{filename}'", ""

        # Check absolute paths
        if filename.startswith("/") or filename.startswith("\\"):
            return False, f"Absolute path not allowed: '{filename}'", ""

        # Windows drive letters (e.g. C:\...)
        if len(filename) >= 2 and filename[1] == ":" and filename[0].isalpha():
            return False, f"Absolute Windows path not allowed: '{filename}'", ""

        # Extract basename
        basename = os.path.basename(filename.replace("\\", "/").replace("\\", "/"))
        # Remove any remaining path components
        basename = basename.split("/")[-1].split("\\")[-1]

        if not basename:
            return False, "Empty basename after sanitization", ""

        # Check Windows reserved names (without extension)
        stem = basename.split(".")[0].upper()
        if stem in WINDOWS_RESERVED:
            return False, f"Windows reserved filename: '{basename}'", ""

        return True, "OK", basename

    def is_safe(self, filename: str) -> bool:
        safe, _, _ = self.sanitize(filename)
        return safe


# ---------------------------------------------------------------------------
# PartialStreamTester
# ---------------------------------------------------------------------------

class PartialStreamTester:
    """
    Detects truncated uploads by comparing actual body length
    against the declared Content-Length header.
    """

    def check(self, body: bytes, declared_content_length: Optional[int]) -> Tuple[bool, str]:
        """
        Check if body is truncated.

        Returns (truncated: bool, message: str).
        """
        if declared_content_length is None:
            return False, "No Content-Length header; cannot check truncation"

        actual = len(body)
        if actual < declared_content_length:
            return True, (
                f"Truncated upload: received {actual} bytes, "
                f"expected {declared_content_length}"
            )
        if actual > declared_content_length:
            return False, (
                f"Body longer than Content-Length: "
                f"got {actual}, declared {declared_content_length}"
            )
        return False, "OK"

    def check_stream(self, stream, declared_content_length: int,
                     chunk_size: int = 8192) -> Tuple[bool, str]:
        """
        Read from stream and check against declared length.
        Returns (truncated: bool, message: str).
        """
        total = 0
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)

        if total < declared_content_length:
            return True, (
                f"Truncated upload: received {total} bytes, "
                f"expected {declared_content_length}"
            )
        return False, "OK"


# ---------------------------------------------------------------------------
# MockUploadHandler / MockUploadServer
# ---------------------------------------------------------------------------

class MockUploadHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP handler that accepts multipart/form-data POST uploads.
    Stores results in server.upload_results list.
    """

    def log_message(self, fmt, *args):
        # Suppress default logging
        pass

    def do_POST(self):
        content_type = self.headers.get("Content-Type", "")
        content_length = self.headers.get("Content-Length")

        result = UploadResult()

        try:
            cl = int(content_length) if content_length else None
        except (ValueError, TypeError):
            cl = None

        # Read body
        if cl is not None:
            body = self.rfile.read(cl)
        else:
            body = self.rfile.read(65536)

        # Check truncation
        pst = PartialStreamTester()
        truncated, trunc_msg = pst.check(body, cl)
        if truncated:
            result.errors.append(trunc_msg)
            result.rejected = True
            result.rejection_reason = trunc_msg

        # Parse multipart
        if "multipart/form-data" in content_type:
            parser = MultipartParser.from_content_type(content_type)
            if parser:
                parts, parse_errors = parser.parse(body)
                result.parts = parts
                result.errors.extend(parse_errors)
            else:
                result.errors.append("Could not extract boundary from Content-Type")
                result.rejected = True
                result.rejection_reason = "No boundary"
        else:
            result.errors.append(f"Unexpected Content-Type: {content_type}")

        # Store result
        if hasattr(self.server, "upload_results"):
            self.server.upload_results.append(result)

        # Respond
        if result.rejected:
            self.send_response(400)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        parts_count = len(result.parts)
        errors_count = len(result.errors)
        body_resp = (
            f'{{"parts": {parts_count}, "errors": {errors_count}, '
            f'"rejected": {str(result.rejected).lower()}}}'
        ).encode()
        self.wfile.write(body_resp)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"MockUploadServer OK")


class MockUploadServer:
    """Wraps HTTPServer for easy use in tests."""

    DEFAULT_PORT = 19210

    def __init__(self, port: int = 0):
        # port=0 lets OS pick an available port
        self.server = HTTPServer(("127.0.0.1", port), MockUploadHandler)
        self.server.upload_results: List[UploadResult] = []
        self.port = self.server.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def clear_results(self) -> None:
        self.server.upload_results.clear()

    @property
    def results(self) -> List[UploadResult]:
        return self.server.upload_results


# ---------------------------------------------------------------------------
# Multipart builder helpers (for tests and internal use)
# ---------------------------------------------------------------------------

def build_multipart_body(parts: List[dict], boundary: str = "TestBoundary123") -> bytes:
    """
    Build a multipart/form-data body from a list of part dicts.

    Each dict may have: name, filename, content_type, data (bytes or str).
    """
    buf = bytearray()
    bound_bytes = boundary.encode("latin-1")

    for p in parts:
        buf += b"--" + bound_bytes + b"\r\n"

        # Content-Disposition
        disp = f'Content-Disposition: form-data; name="{p.get("name", "field")}"'
        if "filename" in p and p["filename"]:
            disp += f'; filename="{p["filename"]}"'
        buf += disp.encode("latin-1") + b"\r\n"

        # Content-Type (optional)
        ct = p.get("content_type", "")
        if ct:
            buf += f"Content-Type: {ct}\r\n".encode("latin-1")

        buf += b"\r\n"

        data = p.get("data", b"")
        if isinstance(data, str):
            data = data.encode("utf-8")
        buf += data + b"\r\n"

    buf += b"--" + bound_bytes + b"--\r\n"
    return bytes(buf)


# ---------------------------------------------------------------------------
# Convenience / integration helpers
# ---------------------------------------------------------------------------

class UploadValidator:
    """
    Combines all checks into one pipeline for an uploaded file part.
    """

    def __init__(
        self,
        max_file_size: int = 10 * 1024 * 1024,
        allowed_types: Optional[set] = None,
        max_output_bytes: int = DecompressionBombChecker.DEFAULT_MAX_OUTPUT,
        max_ratio: float = DecompressionBombChecker.DEFAULT_MAX_RATIO,
    ):
        self.size_checker = SizeLimitChecker(max_file_size)
        self.sniffer = ContentTypeSniffer(allowed_types)
        self.filename_sanitizer = FilenameSanitizer()
        self.bomb_checker = DecompressionBombChecker(max_output_bytes, max_ratio)

    def validate_part(self, part: UploadPart) -> UploadResult:
        result = UploadResult(parts=[part])

        # 1. Filename check
        if part.filename:
            safe, reason, _ = self.filename_sanitizer.sanitize(part.filename)
            if not safe:
                result.rejected = True
                result.rejection_reason = reason
                result.errors.append(reason)
                return result

        # 2. Size check
        bytes_read, limit_hit = self.size_checker.check_bytes(part.data)
        if limit_hit:
            msg = f"File exceeds size limit ({self.size_checker.limit} bytes)"
            result.rejected = True
            result.rejection_reason = msg
            result.errors.append(msg)
            return result

        # 3. Content-type validation
        if part.filename:  # only sniff file uploads
            valid, msg = self.sniffer.validate(part.data, part.content_type)
            if not valid:
                result.rejected = True
                result.rejection_reason = msg
                result.errors.append(msg)
                return result

        return result
