"""
Security Test Harness (Harness 6 of 36)
========================================
Pure stdlib, zero external dependencies.
Scans for common web security vulnerabilities using a built-in mock HTTP server.

Covered vulnerability classes:
  - SQL Injection
  - Cross-Site Scripting (XSS)
  - Command Injection
  - Path Traversal
  - Header Injection (CRLF)
  - Authentication Bypass
  - Sensitive Data Exposure
"""

from __future__ import annotations

import html
import http.client
import http.server
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Enumerations and result types
# ---------------------------------------------------------------------------

class Severity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ScanStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


@dataclass
class ScanResult:
    """Result of a single security scan."""
    test_name: str
    status: ScanStatus
    severity: Severity
    description: str
    endpoint: str = ""
    payload: str = ""
    evidence: str = ""
    remediation: str = ""

    def is_vulnerable(self) -> bool:
        return self.status == ScanStatus.FAIL

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "status": self.status.value,
            "severity": self.severity.value,
            "description": self.description,
            "endpoint": self.endpoint,
            "payload": self.payload,
            "evidence": self.evidence,
            "remediation": self.remediation,
        }


@dataclass
class SecurityReport:
    """Aggregated report of all security scan results."""
    target_url: str
    scan_time: float = 0.0
    results: list[ScanResult] = field(default_factory=list)

    def add_result(self, result: ScanResult) -> None:
        self.results.append(result)

    def vulnerabilities(self) -> list[ScanResult]:
        return [r for r in self.results if r.is_vulnerable()]

    def passed(self) -> list[ScanResult]:
        return [r for r in self.results if r.status == ScanStatus.PASS]

    def errors(self) -> list[ScanResult]:
        return [r for r in self.results if r.status == ScanStatus.ERROR]

    def critical_count(self) -> int:
        return sum(1 for r in self.vulnerabilities() if r.severity == Severity.CRITICAL)

    def high_count(self) -> int:
        return sum(1 for r in self.vulnerabilities() if r.severity == Severity.HIGH)

    def summary(self) -> dict[str, Any]:
        return {
            "target_url": self.target_url,
            "scan_time_seconds": round(self.scan_time, 3),
            "total_tests": len(self.results),
            "vulnerabilities_found": len(self.vulnerabilities()),
            "passed": len(self.passed()),
            "errors": len(self.errors()),
            "critical": self.critical_count(),
            "high": self.high_count(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def is_clean(self) -> bool:
        return len(self.vulnerabilities()) == 0


# ---------------------------------------------------------------------------
# Mock HTTP server handler
# ---------------------------------------------------------------------------

# Shared token for auth tests
VALID_TOKEN = "Bearer valid-token-12345"
SECRET_DATA = {"password": "supersecret123", "api_key": "key-abcdef"}


class MockSecurityHandler(http.server.BaseHTTPRequestHandler):
    """
    Mock HTTP request handler with both VULNERABLE and SAFE endpoint variants.

    Endpoints:
      SQL Injection:
        GET /sql-safe?q=...   — safe (parameterised, does not reflect SQL)
        GET /sql-vuln?q=...   — vulnerable (reflects q directly in fake SQL)

      XSS:
        GET /xss-safe?q=...   — safe (HTML-escapes output)
        GET /xss-vuln?q=...   — vulnerable (reflects raw HTML)

      Command Injection:
        GET /cmd-safe?file=...  — safe (sanitises argument)
        GET /cmd-vuln?file=...  — vulnerable (reflects unsanitised arg in response)

      Path Traversal:
        GET /file-safe?path=... — safe (rejects traversal sequences)
        GET /file-vuln?path=... — vulnerable (reflects path literally)

      Header Injection:
        GET /redirect-safe?url=... — safe (strips CRLF)
        GET /redirect-vuln?url=... — vulnerable (echoes Location header directly)

      Authentication:
        GET /protected         — requires valid Authorization header
        GET /admin             — requires valid Authorization header

      Sensitive Data:
        GET /profile-safe      — returns user data without secrets
        GET /profile-vuln      — leaks password/api_key in response
    """

    def log_message(self, fmt: str, *args: Any) -> None:  # silence logs
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        routes: dict[str, Any] = {
            "/sql-safe": self._sql_safe,
            "/sql-vuln": self._sql_vuln,
            "/xss-safe": self._xss_safe,
            "/xss-vuln": self._xss_vuln,
            "/cmd-safe": self._cmd_safe,
            "/cmd-vuln": self._cmd_vuln,
            "/file-safe": self._file_safe,
            "/file-vuln": self._file_vuln,
            "/redirect-safe": self._redirect_safe,
            "/redirect-vuln": self._redirect_vuln,
            "/protected": self._protected,
            "/admin": self._admin,
            "/profile-safe": self._profile_safe,
            "/profile-vuln": self._profile_vuln,
            "/health": self._health,
        }

        handler = routes.get(path)
        if handler is None:
            self._send_json(404, {"error": "Not found"})
            return

        handler(params)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""
        try:
            post_params = urllib.parse.parse_qs(body.decode(), keep_blank_values=True)
        except Exception:
            post_params = {}

        if path == "/login":
            self._login(post_params)
        else:
            self._send_json(404, {"error": "Not found"})

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    def _send_response_body(
        self,
        code: int,
        body: bytes,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        code: int,
        data: Any,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(data).encode()
        self._send_response_body(code, body, "application/json", extra_headers)

    def _send_html(self, code: int, content: str) -> None:
        body = content.encode()
        self._send_response_body(code, body, "text/html")

    def _get_param(self, params: dict[str, list[str]], key: str, default: str = "") -> str:
        return params.get(key, [default])[0]

    def _is_authenticated(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == VALID_TOKEN

    # ------------------------------------------------------------------
    # SQL Injection endpoints
    # ------------------------------------------------------------------

    def _sql_safe(self, params: dict[str, list[str]]) -> None:
        q = self._get_param(params, "q")
        # Safe: does not reflect user input into SQL-like string
        result = {"status": "ok", "query": "SELECT * FROM items WHERE id = ?", "rows": []}
        self._send_json(200, result)

    def _sql_vuln(self, params: dict[str, list[str]]) -> None:
        q = self._get_param(params, "q")
        # Vulnerable: directly interpolates user input into SQL string
        fake_sql = f"SELECT * FROM users WHERE name = '{q}'"
        result = {"status": "ok", "executed_query": fake_sql, "rows": []}
        self._send_json(200, result)

    # ------------------------------------------------------------------
    # XSS endpoints
    # ------------------------------------------------------------------

    def _xss_safe(self, params: dict[str, list[str]]) -> None:
        q = self._get_param(params, "q")
        escaped = html.escape(q)
        content = f"<html><body><p>Search results for: {escaped}</p></body></html>"
        self._send_html(200, content)

    def _xss_vuln(self, params: dict[str, list[str]]) -> None:
        q = self._get_param(params, "q")
        # Vulnerable: reflects raw user input without escaping
        content = f"<html><body><p>Search results for: {q}</p></body></html>"
        self._send_html(200, content)

    # ------------------------------------------------------------------
    # Command Injection endpoints
    # ------------------------------------------------------------------

    def _cmd_safe(self, params: dict[str, list[str]]) -> None:
        file_arg = self._get_param(params, "file")
        # Safe: strips shell metacharacters
        sanitised = re.sub(r"[;&|`$<>\\\n\r]", "", file_arg)
        result = {"status": "ok", "file": sanitised, "content": "file content here"}
        self._send_json(200, result)

    def _cmd_vuln(self, params: dict[str, list[str]]) -> None:
        file_arg = self._get_param(params, "file")
        # Vulnerable: reflects unsanitised argument as if it were passed to shell
        result = {
            "status": "ok",
            "executed": f"cat {file_arg}",
            "content": "file content here",
        }
        self._send_json(200, result)

    # ------------------------------------------------------------------
    # Path Traversal endpoints
    # ------------------------------------------------------------------

    def _file_safe(self, params: dict[str, list[str]]) -> None:
        path = self._get_param(params, "path")
        # Safe: rejects traversal sequences
        if ".." in path or path.startswith("/"):
            self._send_json(400, {"error": "Invalid path"})
            return
        result = {"status": "ok", "path": path, "content": "safe file content"}
        self._send_json(200, result)

    def _file_vuln(self, params: dict[str, list[str]]) -> None:
        path = self._get_param(params, "path")
        # Vulnerable: reflects literal path, including traversal sequences
        result = {"status": "ok", "path": path, "content": f"contents of {path}"}
        self._send_json(200, result)

    # ------------------------------------------------------------------
    # Header Injection / CRLF endpoints
    # ------------------------------------------------------------------

    def _redirect_safe(self, params: dict[str, list[str]]) -> None:
        url = self._get_param(params, "url", "http://example.com")
        # Safe: strip CRLF characters before setting Location header
        safe_url = url.replace("\r", "").replace("\n", "")
        self._send_json(302, {"redirect": safe_url}, extra_headers={"Location": safe_url})

    def _redirect_vuln(self, params: dict[str, list[str]]) -> None:
        url = self._get_param(params, "url", "http://example.com")
        # Vulnerable: echoes url directly into Location header (CRLF injection possible)
        # We can't actually inject CRLF via http.server (it sanitises), so we
        # reflect the raw url in the response body to prove vulnerability.
        self._send_json(
            302,
            {"redirect": url, "raw_location": url},
            extra_headers={"Location": url.split("\n")[0]},
        )

    # ------------------------------------------------------------------
    # Authentication endpoints
    # ------------------------------------------------------------------

    def _protected(self, params: dict[str, list[str]]) -> None:
        if not self._is_authenticated():
            self._send_json(401, {"error": "Unauthorized"})
            return
        self._send_json(200, {"status": "ok", "data": "protected resource"})

    def _admin(self, params: dict[str, list[str]]) -> None:
        if not self._is_authenticated():
            self._send_json(403, {"error": "Forbidden"})
            return
        self._send_json(200, {"status": "ok", "data": "admin resource"})

    def _login(self, params: dict[str, list[str]]) -> None:
        username = self._get_param(params, "username")
        password = self._get_param(params, "password")
        if username == "admin" and password == "correct-password":
            self._send_json(200, {"token": VALID_TOKEN})
        else:
            self._send_json(401, {"error": "Invalid credentials"})

    # ------------------------------------------------------------------
    # Sensitive data exposure endpoints
    # ------------------------------------------------------------------

    def _profile_safe(self, params: dict[str, list[str]]) -> None:
        # Safe: only returns non-sensitive fields
        result = {"user_id": 42, "username": "alice", "email": "alice@example.com"}
        self._send_json(200, result)

    def _profile_vuln(self, params: dict[str, list[str]]) -> None:
        # Vulnerable: leaks sensitive fields
        result = {
            "user_id": 42,
            "username": "alice",
            "email": "alice@example.com",
            "password": SECRET_DATA["password"],
            "api_key": SECRET_DATA["api_key"],
        }
        self._send_json(200, result)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def _health(self, params: dict[str, list[str]]) -> None:
        self._send_json(200, {"status": "healthy"})


# ---------------------------------------------------------------------------
# Mock server lifecycle
# ---------------------------------------------------------------------------

class MockSecurityServer:
    """Manages the lifecycle of the mock HTTP security test server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start the server and return the base URL."""
        self._server = http.server.HTTPServer((self.host, self.port), MockSecurityHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Wait until actually accepting connections
        for _ in range(50):
            try:
                with socket.create_connection((self.host, self.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        return self.base_url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self) -> MockSecurityServer:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# HTTP utility
# ---------------------------------------------------------------------------

def _http_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a GET request; return (status_code, headers_dict, body_bytes)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except Exception as exc:
        raise RuntimeError(f"HTTP GET {url} failed: {exc}") from exc


def _http_post(
    url: str,
    data: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a POST request with form-encoded data."""
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except Exception as exc:
        raise RuntimeError(f"HTTP POST {url} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# SQL Injection Scanner
# ---------------------------------------------------------------------------

SQL_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1--",
    "'; DROP TABLE users;--",
    "1' AND SLEEP(0)--",
    "\" OR \"1\"=\"1",
]

SQL_REFLECTION_PATTERNS = [
    re.compile(r"OR\s+'1'='1", re.IGNORECASE),
    re.compile(r"OR\s+1=1", re.IGNORECASE),
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"AND\s+SLEEP", re.IGNORECASE),
    re.compile(r"SELECT\s+\*\s+FROM.*WHERE.*'[^']*'", re.IGNORECASE),
]


class InjectionScanner:
    """Scans endpoints for SQL injection vulnerabilities."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def scan_endpoint(self, path: str, param: str = "q") -> ScanResult:
        """Scan a single endpoint for SQL injection."""
        endpoint = f"{self.base_url}{path}"
        for payload in SQL_PAYLOADS:
            encoded_payload = urllib.parse.quote(payload)
            url = f"{endpoint}?{param}={encoded_payload}"
            try:
                status, _, body = _http_get(url)
                body_str = body.decode(errors="replace")
                for pattern in SQL_REFLECTION_PATTERNS:
                    if pattern.search(body_str):
                        return ScanResult(
                            test_name=f"sql_injection:{path}",
                            status=ScanStatus.FAIL,
                            severity=Severity.CRITICAL,
                            description="SQL injection payload reflected in response",
                            endpoint=endpoint,
                            payload=payload,
                            evidence=body_str[:300],
                            remediation="Use parameterised queries / prepared statements",
                        )
            except Exception as exc:
                return ScanResult(
                    test_name=f"sql_injection:{path}",
                    status=ScanStatus.ERROR,
                    severity=Severity.HIGH,
                    description=str(exc),
                    endpoint=endpoint,
                )
        return ScanResult(
            test_name=f"sql_injection:{path}",
            status=ScanStatus.PASS,
            severity=Severity.CRITICAL,
            description="No SQL injection payloads reflected",
            endpoint=endpoint,
            remediation="Use parameterised queries / prepared statements",
        )

    def scan_all(self) -> list[ScanResult]:
        return [
            self.scan_endpoint("/sql-safe"),
            self.scan_endpoint("/sql-vuln"),
        ]


# ---------------------------------------------------------------------------
# XSS Scanner
# ---------------------------------------------------------------------------

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    '"><script>alert(1)</script>',
]


class XSSScan:
    """Scans endpoints for Cross-Site Scripting (XSS) vulnerabilities."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _payload_reflected_raw(self, payload: str, body: str) -> bool:
        """Return True if the raw (unescaped) payload appears in the body.

        For payloads containing HTML-special characters (< > &) we require that
        those characters are present unescaped.  Payloads without HTML-special
        characters (e.g. javascript: URIs) are only flagged when the full payload
        string appears literally in an HTML tag attribute context.
        """
        if "<" in payload or ">" in payload:
            # Only flag if the angle-bracket form is present (not &lt; escaped)
            return payload in body
        # For non-HTML-tag payloads skip reflection-based detection
        # (javascript: URIs need attribute context to be exploitable)
        return False

    def scan_endpoint(self, path: str, param: str = "q") -> ScanResult:
        endpoint = f"{self.base_url}{path}"
        for payload in XSS_PAYLOADS:
            encoded_payload = urllib.parse.quote(payload)
            url = f"{endpoint}?{param}={encoded_payload}"
            try:
                _, _, body = _http_get(url)
                body_str = body.decode(errors="replace")
                if self._payload_reflected_raw(payload, body_str):
                    return ScanResult(
                        test_name=f"xss:{path}",
                        status=ScanStatus.FAIL,
                        severity=Severity.HIGH,
                        description="XSS payload reflected unescaped in response",
                        endpoint=endpoint,
                        payload=payload,
                        evidence=body_str[:300],
                        remediation="HTML-encode all user-supplied output",
                    )
            except Exception as exc:
                return ScanResult(
                    test_name=f"xss:{path}",
                    status=ScanStatus.ERROR,
                    severity=Severity.HIGH,
                    description=str(exc),
                    endpoint=endpoint,
                )
        return ScanResult(
            test_name=f"xss:{path}",
            status=ScanStatus.PASS,
            severity=Severity.HIGH,
            description="No XSS payloads reflected unescaped",
            endpoint=endpoint,
            remediation="HTML-encode all user-supplied output",
        )

    def scan_all(self) -> list[ScanResult]:
        return [
            self.scan_endpoint("/xss-safe"),
            self.scan_endpoint("/xss-vuln"),
        ]


# ---------------------------------------------------------------------------
# Command Injection Scanner
# ---------------------------------------------------------------------------

CMD_PAYLOADS = [
    "; ls",
    "| cat /etc/passwd",
    "`id`",
    "$(whoami)",
    "&& id",
    "; id #",
]

CMD_REFLECTION_PATTERNS = [
    re.compile(r";\s*(ls|id|whoami|cat)", re.IGNORECASE),
    re.compile(r"\|\s*(cat|ls|id)", re.IGNORECASE),
    re.compile(r"`id`"),
    re.compile(r"\$\(whoami\)"),
    re.compile(r"&&\s*id", re.IGNORECASE),
]


class CommandInjectionScan:
    """Scans endpoints for OS command injection vulnerabilities."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def scan_endpoint(self, path: str, param: str = "file") -> ScanResult:
        endpoint = f"{self.base_url}{path}"
        for payload in CMD_PAYLOADS:
            encoded_payload = urllib.parse.quote(payload)
            url = f"{endpoint}?{param}={encoded_payload}"
            try:
                _, _, body = _http_get(url)
                body_str = body.decode(errors="replace")
                for pattern in CMD_REFLECTION_PATTERNS:
                    if pattern.search(body_str):
                        return ScanResult(
                            test_name=f"cmd_injection:{path}",
                            status=ScanStatus.FAIL,
                            severity=Severity.CRITICAL,
                            description="Command injection payload reflected in response",
                            endpoint=endpoint,
                            payload=payload,
                            evidence=body_str[:300],
                            remediation="Avoid shell calls; use safe APIs; sanitise inputs",
                        )
            except Exception as exc:
                return ScanResult(
                    test_name=f"cmd_injection:{path}",
                    status=ScanStatus.ERROR,
                    severity=Severity.CRITICAL,
                    description=str(exc),
                    endpoint=endpoint,
                )
        return ScanResult(
            test_name=f"cmd_injection:{path}",
            status=ScanStatus.PASS,
            severity=Severity.CRITICAL,
            description="No command injection payloads reflected unsanitised",
            endpoint=endpoint,
            remediation="Avoid shell calls; use safe APIs; sanitise inputs",
        )

    def scan_all(self) -> list[ScanResult]:
        return [
            self.scan_endpoint("/cmd-safe"),
            self.scan_endpoint("/cmd-vuln"),
        ]


# ---------------------------------------------------------------------------
# Path Traversal Scanner
# ---------------------------------------------------------------------------

PATH_TRAVERSAL_PAYLOADS = [
    "../etc/passwd",
    "../../etc/passwd",
    "../../../etc/shadow",
    "..%2Fetc%2Fpasswd",
    "%2e%2e%2fetc%2fpasswd",
]


class PathTraversalScan:
    """Scans endpoints for path traversal vulnerabilities."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _is_traversal_reflected(self, payload: str, body: str) -> bool:
        """Return True if traversal path appears in the response."""
        decoded_payload = urllib.parse.unquote(payload)
        return decoded_payload in body or ".." in body

    def scan_endpoint(self, path: str, param: str = "path") -> ScanResult:
        endpoint = f"{self.base_url}{path}"
        for payload in PATH_TRAVERSAL_PAYLOADS:
            # Use decoded payload in URL to avoid double-encoding
            url = f"{endpoint}?{param}={payload}"
            try:
                status, _, body = _http_get(url)
                body_str = body.decode(errors="replace")
                if status == 200 and self._is_traversal_reflected(payload, body_str):
                    return ScanResult(
                        test_name=f"path_traversal:{path}",
                        status=ScanStatus.FAIL,
                        severity=Severity.HIGH,
                        description="Path traversal sequence accepted and reflected",
                        endpoint=endpoint,
                        payload=payload,
                        evidence=body_str[:300],
                        remediation="Validate and normalise file paths; use a whitelist",
                    )
            except Exception as exc:
                return ScanResult(
                    test_name=f"path_traversal:{path}",
                    status=ScanStatus.ERROR,
                    severity=Severity.HIGH,
                    description=str(exc),
                    endpoint=endpoint,
                )
        return ScanResult(
            test_name=f"path_traversal:{path}",
            status=ScanStatus.PASS,
            severity=Severity.HIGH,
            description="Path traversal sequences rejected",
            endpoint=endpoint,
            remediation="Validate and normalise file paths; use a whitelist",
        )

    def scan_all(self) -> list[ScanResult]:
        return [
            self.scan_endpoint("/file-safe"),
            self.scan_endpoint("/file-vuln"),
        ]


# ---------------------------------------------------------------------------
# Header Security Audit (includes CRLF / Header Injection)
# ---------------------------------------------------------------------------

# CRLF payloads — stored as raw strings; the scanner percent-encodes them itself
# so that urllib.request never sees literal control characters.
# We check for the injected marker string appearing in the *response body* JSON,
# which the /redirect-vuln endpoint reflects back via "raw_location".
CRLF_MARKER = "X-Injected-Marker"
CRLF_PAYLOADS = [
    f"http://example.com\r\n{CRLF_MARKER}: hacked",
    "http://example.com\r\nSet-Cookie: malicious=1",
    f"http://example.com%0d%0a{CRLF_MARKER}: hacked",
]


def _http_get_raw(
    host: str,
    port: int,
    path_and_query: str,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """Low-level HTTP GET using http.client (bypasses urllib URL validation)."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path_and_query)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


class HeaderSecurityAudit:
    """
    Checks HTTP response headers for security best-practices and
    tests for CRLF / header-injection vulnerabilities.
    """

    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": None,  # any non-empty value is acceptable
        "Content-Security-Policy": None,
        "Strict-Transport-Security": None,
    }

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 80

    # ------------------------------------------------------------------
    # CRLF / Header Injection
    # ------------------------------------------------------------------

    def scan_crlf_endpoint(self, path: str, param: str = "url") -> ScanResult:
        """Test whether CRLF characters in a parameter lead to header injection.

        Uses http.client directly so that percent-encoded control characters in
        the query string are transmitted verbatim without urllib stripping them.
        """
        endpoint = f"{self.base_url}{path}"
        for payload in CRLF_PAYLOADS:
            # Percent-encode the raw payload completely (including CR, LF, colons)
            encoded = urllib.parse.quote(payload, safe="")
            path_and_query = f"{path}?{param}={encoded}"
            try:
                status, resp_headers, body = _http_get_raw(
                    self._host, self._port, path_and_query
                )
                body_str = body.decode(errors="replace")
                # Vulnerable indicator 1: raw CRLF sequence still present in body
                # (safe endpoint strips \r\n; vuln endpoint reflects them verbatim)
                if "\r\n" in body_str and CRLF_MARKER in body_str:
                    return ScanResult(
                        test_name=f"header_injection:{path}",
                        status=ScanStatus.FAIL,
                        severity=Severity.HIGH,
                        description="CRLF payload reflected verbatim in response body",
                        endpoint=endpoint,
                        payload=payload,
                        evidence=repr(body_str[:300]),
                        remediation="Strip CR/LF from user input before using in headers",
                    )
                # Vulnerable indicator 2: injected header actually appears in response
                if CRLF_MARKER.lower() in resp_headers:
                    return ScanResult(
                        test_name=f"header_injection:{path}",
                        status=ScanStatus.FAIL,
                        severity=Severity.HIGH,
                        description="CRLF injection succeeded — injected header in response",
                        endpoint=endpoint,
                        payload=payload,
                        evidence=str(resp_headers)[:300],
                        remediation="Strip CR/LF from user input before using in headers",
                    )
            except Exception as exc:
                return ScanResult(
                    test_name=f"header_injection:{path}",
                    status=ScanStatus.ERROR,
                    severity=Severity.HIGH,
                    description=str(exc),
                    endpoint=endpoint,
                )
        return ScanResult(
            test_name=f"header_injection:{path}",
            status=ScanStatus.PASS,
            severity=Severity.HIGH,
            description="No CRLF injection detected",
            endpoint=endpoint,
            remediation="Strip CR/LF from user input before using in headers",
        )

    # ------------------------------------------------------------------
    # Security headers presence check
    # ------------------------------------------------------------------

    def check_security_headers(self, path: str = "/health") -> list[ScanResult]:
        endpoint = f"{self.base_url}{path}"
        results: list[ScanResult] = []
        try:
            _, headers, _ = _http_get(endpoint)
            lower_headers = {k.lower(): v for k, v in headers.items()}
            for header, expected_value in self.SECURITY_HEADERS.items():
                present = header.lower() in lower_headers
                if not present:
                    results.append(
                        ScanResult(
                            test_name=f"security_header:{header}",
                            status=ScanStatus.FAIL,
                            severity=Severity.MEDIUM,
                            description=f"Missing security header: {header}",
                            endpoint=endpoint,
                            remediation=f"Add '{header}' response header",
                        )
                    )
                else:
                    if expected_value and lower_headers[header.lower()] != expected_value:
                        results.append(
                            ScanResult(
                                test_name=f"security_header:{header}",
                                status=ScanStatus.FAIL,
                                severity=Severity.LOW,
                                description=f"Security header '{header}' has unexpected value",
                                endpoint=endpoint,
                                evidence=lower_headers[header.lower()],
                                remediation=f"Set '{header}: {expected_value}'",
                            )
                        )
                    else:
                        results.append(
                            ScanResult(
                                test_name=f"security_header:{header}",
                                status=ScanStatus.PASS,
                                severity=Severity.MEDIUM,
                                description=f"Security header '{header}' present",
                                endpoint=endpoint,
                            )
                        )
        except Exception as exc:
            results.append(
                ScanResult(
                    test_name="security_headers",
                    status=ScanStatus.ERROR,
                    severity=Severity.MEDIUM,
                    description=str(exc),
                    endpoint=endpoint,
                )
            )
        return results

    def scan_all(self) -> list[ScanResult]:
        return [
            self.scan_crlf_endpoint("/redirect-safe"),
            self.scan_crlf_endpoint("/redirect-vuln"),
        ] + self.check_security_headers()


# ---------------------------------------------------------------------------
# Authentication Bypass Scanner
# ---------------------------------------------------------------------------

class AuthBypassScan:
    """Tests that protected endpoints correctly enforce authentication."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _check_endpoint_rejects_unauthenticated(
        self, path: str, expected_codes: tuple[int, ...] = (401, 403)
    ) -> ScanResult:
        endpoint = f"{self.base_url}{path}"
        bypass_attempts = [
            {},                                    # no token
            {"Authorization": ""},                 # empty token
            {"Authorization": "Bearer invalid"},   # bad token
            {"Authorization": "null"},             # null string
            {"Authorization": "undefined"},        # JS-leak artefact
        ]
        for headers in bypass_attempts:
            try:
                status, _, body = _http_get(endpoint, headers=headers)
                if status not in expected_codes:
                    return ScanResult(
                        test_name=f"auth_bypass:{path}",
                        status=ScanStatus.FAIL,
                        severity=Severity.CRITICAL,
                        description=f"Endpoint returned {status} without valid auth",
                        endpoint=endpoint,
                        payload=str(headers),
                        evidence=body.decode(errors="replace")[:200],
                        remediation="Enforce authentication checks on all protected routes",
                    )
            except Exception as exc:
                return ScanResult(
                    test_name=f"auth_bypass:{path}",
                    status=ScanStatus.ERROR,
                    severity=Severity.CRITICAL,
                    description=str(exc),
                    endpoint=endpoint,
                )
        return ScanResult(
            test_name=f"auth_bypass:{path}",
            status=ScanStatus.PASS,
            severity=Severity.CRITICAL,
            description="Endpoint correctly rejects unauthenticated requests",
            endpoint=endpoint,
            remediation="Enforce authentication checks on all protected routes",
        )

    def _check_valid_auth_accepted(self, path: str) -> ScanResult:
        endpoint = f"{self.base_url}{path}"
        try:
            status, _, _ = _http_get(endpoint, headers={"Authorization": VALID_TOKEN})
            if status == 200:
                return ScanResult(
                    test_name=f"auth_valid:{path}",
                    status=ScanStatus.PASS,
                    severity=Severity.CRITICAL,
                    description="Valid token correctly accepted",
                    endpoint=endpoint,
                )
            return ScanResult(
                test_name=f"auth_valid:{path}",
                status=ScanStatus.FAIL,
                severity=Severity.HIGH,
                description=f"Valid token rejected with status {status}",
                endpoint=endpoint,
                remediation="Ensure valid tokens are accepted",
            )
        except Exception as exc:
            return ScanResult(
                test_name=f"auth_valid:{path}",
                status=ScanStatus.ERROR,
                severity=Severity.HIGH,
                description=str(exc),
                endpoint=endpoint,
            )

    def scan_all(self) -> list[ScanResult]:
        results = []
        for path in ["/protected", "/admin"]:
            results.append(self._check_endpoint_rejects_unauthenticated(path))
            results.append(self._check_valid_auth_accepted(path))
        return results


# ---------------------------------------------------------------------------
# Sensitive Data Exposure Scanner
# ---------------------------------------------------------------------------

SENSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("password", re.compile(r'"password"\s*:\s*"[^"]+"', re.IGNORECASE)),
    ("api_key", re.compile(r'"api_key"\s*:\s*"[^"]+"', re.IGNORECASE)),
    ("secret", re.compile(r'"secret"\s*:\s*"[^"]+"', re.IGNORECASE)),
    ("private_key", re.compile(r'"private_key"\s*:\s*"[^"]+"', re.IGNORECASE)),
    ("access_token", re.compile(r'"access_token"\s*:\s*"[^"]+"', re.IGNORECASE)),
    ("credit_card", re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b')),
    ("ssn", re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
]


class SensitiveDataExposureScan:
    """Checks API responses for accidental sensitive data exposure."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def scan_endpoint(self, path: str) -> ScanResult:
        endpoint = f"{self.base_url}{path}"
        try:
            _, _, body = _http_get(endpoint)
            body_str = body.decode(errors="replace")
            for label, pattern in SENSITIVE_PATTERNS:
                if pattern.search(body_str):
                    return ScanResult(
                        test_name=f"sensitive_data:{path}",
                        status=ScanStatus.FAIL,
                        severity=Severity.HIGH,
                        description=f"Sensitive field '{label}' leaked in response",
                        endpoint=endpoint,
                        evidence=body_str[:300],
                        remediation="Remove sensitive fields from API responses",
                    )
        except Exception as exc:
            return ScanResult(
                test_name=f"sensitive_data:{path}",
                status=ScanStatus.ERROR,
                severity=Severity.HIGH,
                description=str(exc),
                endpoint=endpoint,
            )
        return ScanResult(
            test_name=f"sensitive_data:{path}",
            status=ScanStatus.PASS,
            severity=Severity.HIGH,
            description="No sensitive data patterns detected",
            endpoint=endpoint,
            remediation="Remove sensitive fields from API responses",
        )

    def scan_all(self) -> list[ScanResult]:
        return [
            self.scan_endpoint("/profile-safe"),
            self.scan_endpoint("/profile-vuln"),
        ]


# ---------------------------------------------------------------------------
# Full security scan runner
# ---------------------------------------------------------------------------

class SecurityScanner:
    """Orchestrates all security scans against a target base URL."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.report = SecurityReport(target_url=base_url)

    def run(self) -> SecurityReport:
        start = time.monotonic()

        scanners = [
            InjectionScanner(self.base_url),
            XSSScan(self.base_url),
            CommandInjectionScan(self.base_url),
            PathTraversalScan(self.base_url),
            HeaderSecurityAudit(self.base_url),
            AuthBypassScan(self.base_url),
            SensitiveDataExposureScan(self.base_url),
        ]

        for scanner in scanners:
            for result in scanner.scan_all():
                self.report.add_result(result)

        self.report.scan_time = time.monotonic() - start
        return self.report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_self_test(verbose: bool = False) -> int:
    """Scan the bundled vulnerable mock server; the scanner must find planted vulns."""
    server = MockSecurityServer(port=0)
    base_url = server.start()
    try:
        s = SecurityScanner(base_url).run().summary()
    finally:
        server.stop()
    checks = [
        ("scanner ran tests", s["total_tests"] >= 1, f"tests={s['total_tests']}"),
        ("found planted vulnerabilities", s["vulnerabilities_found"] >= 1,
         f"vulns={s['vulnerabilities_found']}"),
    ]
    failures = [n for n, ok, _ in checks if not ok]
    for n, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}  ({d})")
    print(f"\n  {len(checks) - len(failures)}/{len(checks)} checks passed")
    return 0 if not failures else 1


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Security Test Harness")
    parser.add_argument(
        "--port",
        type=int,
        default=18920,
        help="Port for the mock security server (default 18920; 0 = dynamic)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in scenarios and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_run_self_test())

    server = MockSecurityServer(port=args.port)
    base_url = server.start()
    print(f"Mock security server running at {base_url}")

    try:
        scanner = SecurityScanner(base_url)
        report = scanner.run()

        if args.json:
            print(report.to_json())
        else:
            summary = report.summary()
            print(f"\n{'='*60}")
            print("SECURITY SCAN REPORT")
            print(f"{'='*60}")
            print(f"Target : {summary['target_url']}")
            print(f"Time   : {summary['scan_time_seconds']}s")
            print(f"Tests  : {summary['total_tests']}")
            print(f"Passed : {summary['passed']}")
            print(f"Vulns  : {summary['vulnerabilities_found']}")
            print(f"  Critical: {summary['critical']}")
            print(f"  High    : {summary['high']}")
            print()

            for result in report.results:
                icon = "PASS" if result.status == ScanStatus.PASS else result.status.value
                print(f"[{icon}] {result.test_name} ({result.severity.value})")
                if result.is_vulnerable():
                    print(f"       {result.description}")
                    if result.evidence:
                        print(f"       Evidence: {result.evidence[:80]}...")
            print(f"{'='*60}")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
