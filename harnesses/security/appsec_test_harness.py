"""
App-Security Test Harness (Harness 36/36)
Covers OWASP classes most over-represented in AI-generated code.
Pure stdlib, zero external dependencies.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import pickle
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# ---------------------------------------------------------------------------
# SecFinding & AppSecReport
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class SecFinding:
    check_name: str
    severity: str  # CRITICAL / HIGH / MEDIUM / LOW
    description: str
    evidence: str = ""

    def __post_init__(self):
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"Invalid severity: {self.severity}")


@dataclass
class AppSecReport:
    findings: list[SecFinding] = field(default_factory=list)

    def add(self, finding: SecFinding) -> None:
        self.findings.append(finding)

    def counts_by_severity(self) -> dict[str, int]:
        counts = {s: 0 for s in SEVERITY_ORDER}
        for f in self.findings:
            counts[f.severity] += 1
        return counts

    def is_clean(self) -> bool:
        return len(self.findings) == 0

    def __len__(self) -> int:
        return len(self.findings)


# ---------------------------------------------------------------------------
# SSRFChecker
# ---------------------------------------------------------------------------

_BLOCKED_SCHEMES = {"file", "gopher", "dict", "ftp", "sftp", "ldap", "ldaps"}

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local IPv4
    ipaddress.ip_network("fe80::/10"),          # link-local IPv6
    ipaddress.ip_network("fc00::/7"),           # unique-local IPv6
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),      # carrier-grade NAT
    ipaddress.ip_network("198.18.0.0/15"),      # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),        # reserved
]

# Cloud metadata endpoints
_METADATA_HOSTS = {
    "169.254.169.254",       # AWS / GCP / Azure IMDSv1
    "metadata.google.internal",
    "fd00:ec2::254",          # AWS IMDSv6
}


class SSRFChecker:
    """
    Validates URLs against an allowlist.
    check(url) -> (allowed: bool, reason: str)
    """

    def __init__(self, allowed_hosts: list[str] | None = None,
                 allowed_schemes: list[str] | None = None):
        self.allowed_hosts: list[str] = [h.lower() for h in (allowed_hosts or [])]
        self.allowed_schemes: list[str] = [s.lower() for s in (allowed_schemes or ["https", "http"])]

    # ------------------------------------------------------------------
    def check(self, url: str) -> tuple[bool, str]:
        if not url or not url.strip():
            return False, "Empty URL"

        # Protocol-relative URLs are treated as HTTP — block them explicitly
        stripped = url.strip()
        if stripped.startswith("//"):
            return False, "Protocol-relative URL not allowed"

        try:
            parsed = urllib.parse.urlparse(url)
        except Exception as exc:
            return False, f"URL parse error: {exc}"

        scheme = (parsed.scheme or "").lower()

        # Scheme check
        if scheme in _BLOCKED_SCHEMES:
            return False, f"Blocked scheme: {scheme}"

        if scheme not in self.allowed_schemes:
            return False, f"Scheme '{scheme}' not in allowed list"

        hostname = (parsed.hostname or "").lower().strip(".")

        if not hostname:
            return False, "No hostname in URL"

        # Metadata endpoint check (hostname)
        if hostname in _METADATA_HOSTS:
            return False, f"Metadata endpoint blocked: {hostname}"

        # Allowlist check (if configured)
        if self.allowed_hosts:
            allowed = False
            for ah in self.allowed_hosts:
                if hostname == ah or hostname.endswith("." + ah):
                    allowed = True
                    break
            if not allowed:
                return False, f"Host '{hostname}' not in allowlist"

        # IP address checks
        try:
            addr = ipaddress.ip_address(hostname)
            for net in _PRIVATE_NETS:
                if addr in net:
                    return False, f"IP {hostname} is in blocked range {net}"
            # Check metadata IP
            if hostname == "169.254.169.254":
                return False, "Metadata endpoint IP blocked"
        except ValueError:
            # Not an IP address — hostname, continue
            pass

        return True, "OK"


# ---------------------------------------------------------------------------
# DeserializationChecker
# ---------------------------------------------------------------------------

# Dangerous pickle opcodes (single-byte)
_PICKLE_DANGEROUS_OPCODES = {
    ord(b'R'),  # REDUCE
    ord(b'c'),  # GLOBAL
    ord(b'b'),  # BUILD
    ord(b'i'),  # INST
    ord(b'o'),  # OBJ
}

_JAVA_MAGIC = b'\xac\xed'
_PYYAML_DANGEROUS = re.compile(r'!!python/(object|apply|module|name|new)', re.IGNORECASE)


class DeserializationChecker:
    """
    Detects dangerous deserialization patterns without executing payloads.
    """

    def check_pickle(self, data: bytes) -> tuple[bool, str]:
        """Returns (dangerous, reason)."""
        if not isinstance(data, bytes):
            return False, "Not bytes"
        for i, byte in enumerate(data):
            if byte in _PICKLE_DANGEROUS_OPCODES:
                opcode_name = {
                    ord(b'R'): 'REDUCE',
                    ord(b'c'): 'GLOBAL',
                    ord(b'b'): 'BUILD',
                    ord(b'i'): 'INST',
                    ord(b'o'): 'OBJ',
                }.get(byte, f'0x{byte:02x}')
                return True, f"Dangerous pickle opcode '{opcode_name}' at offset {i}"
        return False, "No dangerous opcodes found"

    def check_yaml(self, yaml_str: str) -> tuple[bool, str]:
        """Returns (dangerous, reason)."""
        m = _PYYAML_DANGEROUS.search(yaml_str)
        if m:
            return True, f"Dangerous PyYAML tag: {m.group(0)}"
        return False, "No dangerous YAML tags found"

    def check_java(self, data: bytes) -> tuple[bool, str]:
        """Returns (dangerous, reason)."""
        if not isinstance(data, bytes):
            return False, "Not bytes"
        if data[:2] == _JAVA_MAGIC:
            return True, "Java serialization magic bytes detected (0xACED)"
        return False, "No Java serialization magic bytes"

    def check(self, data: Any) -> tuple[bool, str]:
        """
        Auto-detect format and check.
        Accepts bytes (pickle/java check) or str (yaml check).
        """
        if isinstance(data, bytes):
            # Check Java first
            java_danger, java_reason = self.check_java(data)
            if java_danger:
                return True, java_reason
            # Then pickle
            return self.check_pickle(data)
        elif isinstance(data, str):
            return self.check_yaml(data)
        return False, "Unknown data type"


# ---------------------------------------------------------------------------
# JWTChecker
# ---------------------------------------------------------------------------

def _b64url_decode(s: str) -> bytes:
    """Decode base64url without padding."""
    s = s.replace("-", "+").replace("_", "/")
    # Add padding
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.b64decode(s)


def _b64url_encode(data: bytes) -> str:
    """Encode base64url without padding."""
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


class JWTChecker:
    """
    Parses and validates JWT tokens without external libraries.
    """

    def __init__(self, allowed_algorithms: list[str] | None = None,
                 allowed_issuers: list[str] | None = None,
                 allowed_audiences: list[str] | None = None):
        self.allowed_algorithms = [a.upper() for a in (allowed_algorithms or ["HS256"])]
        self.allowed_issuers = allowed_issuers  # None = no check
        self.allowed_audiences = allowed_audiences  # None = no check

    def decode_token(self, token: str) -> tuple[dict | None, dict | None, str | None]:
        """
        Returns (header, payload, signature_b64) or raises ValueError.
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError(f"JWT must have 3 parts, got {len(parts)}")
        try:
            header = json.loads(_b64url_decode(parts[0]))
            payload = json.loads(_b64url_decode(parts[1]))
            signature = parts[2]
        except Exception as exc:
            raise ValueError(f"JWT decode error: {exc}")
        return header, payload, signature

    def check_alg_none(self, token: str) -> tuple[bool, str]:
        """Returns (vulnerable, reason)."""
        try:
            header, _, _ = self.decode_token(token)
        except ValueError as e:
            return False, str(e)
        alg = str(header.get("alg", "")).lower()
        if alg in ("none", "", "null"):
            return True, f"Algorithm 'none' attack detected: alg={header.get('alg')!r}"
        return False, "Algorithm is not 'none'"

    def check_algorithm_confusion(self, token: str) -> tuple[bool, str]:
        """
        Detect HS/RS algorithm confusion: token claims RS* but we'd verify with HS*.
        Returns (confused, reason).
        """
        try:
            header, _, _ = self.decode_token(token)
        except ValueError as e:
            return False, str(e)
        alg = str(header.get("alg", "")).upper()
        if alg not in self.allowed_algorithms:
            return True, f"Algorithm '{alg}' not in allowed list {self.allowed_algorithms}"
        return False, "Algorithm is allowed"

    def check_expiry(self, token: str) -> tuple[bool, str]:
        """Returns (expired, reason)."""
        try:
            _, payload, _ = self.decode_token(token)
        except ValueError as e:
            return True, str(e)
        exp = payload.get("exp")
        if exp is None:
            return True, "Missing 'exp' claim — token has no expiry"
        now = time.time()
        if now > exp:
            return True, f"Token expired at {exp}, current time {now:.0f}"
        return False, f"Token valid until {exp}"

    def check_issuer(self, token: str) -> tuple[bool, str]:
        """Returns (invalid, reason)."""
        if self.allowed_issuers is None:
            return False, "Issuer check not configured"
        try:
            _, payload, _ = self.decode_token(token)
        except ValueError as e:
            return True, str(e)
        iss = payload.get("iss")
        if iss is None:
            return True, "Missing 'iss' claim"
        if iss not in self.allowed_issuers:
            return True, f"Issuer '{iss}' not in allowed list"
        return False, f"Issuer '{iss}' is allowed"

    def check_audience(self, token: str) -> tuple[bool, str]:
        """Returns (invalid, reason)."""
        if self.allowed_audiences is None:
            return False, "Audience check not configured"
        try:
            _, payload, _ = self.decode_token(token)
        except ValueError as e:
            return True, str(e)
        aud = payload.get("aud")
        if aud is None:
            return True, "Missing 'aud' claim"
        # aud can be string or list
        if isinstance(aud, str):
            aud_list = [aud]
        else:
            aud_list = list(aud)
        # At least one must match
        for a in aud_list:
            if a in self.allowed_audiences:
                return False, f"Audience '{a}' is allowed"
        return True, f"Audience {aud_list} not in allowed list"

    def sign_hs256(self, payload: dict, secret: str, extra_header: dict | None = None) -> str:
        """Create a signed HS256 JWT."""
        header = {"alg": "HS256", "typ": "JWT"}
        if extra_header:
            header.update(extra_header)
        header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        sig_b64 = _b64url_encode(sig)
        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def verify_hs256(self, token: str, secret: str) -> tuple[bool, str]:
        """Returns (valid, reason)."""
        parts = token.split(".")
        if len(parts) != 3:
            return False, "JWT must have 3 parts"
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        try:
            expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        except Exception as exc:
            return False, f"HMAC error: {exc}"
        try:
            actual_sig = _b64url_decode(parts[2])
        except Exception as exc:
            return False, f"Signature decode error: {exc}"
        if hmac.compare_digest(expected_sig, actual_sig):
            return True, "Signature valid"
        return False, "Signature mismatch"

    def validate(self, token: str, secret: str | None = None) -> AppSecReport:
        """Full validation — returns AppSecReport."""
        report = AppSecReport()

        # alg:none
        vuln, reason = self.check_alg_none(token)
        if vuln:
            report.add(SecFinding("JWTChecker", "CRITICAL", "alg:none attack", reason))

        # Algorithm confusion
        confused, reason = self.check_algorithm_confusion(token)
        if confused:
            report.add(SecFinding("JWTChecker", "HIGH", "Algorithm confusion", reason))

        # Expiry
        expired, reason = self.check_expiry(token)
        if expired:
            report.add(SecFinding("JWTChecker", "HIGH", "Token expired or no expiry", reason))

        # Issuer
        invalid_iss, reason = self.check_issuer(token)
        if invalid_iss and self.allowed_issuers is not None:
            report.add(SecFinding("JWTChecker", "MEDIUM", "Invalid issuer", reason))

        # Audience
        invalid_aud, reason = self.check_audience(token)
        if invalid_aud and self.allowed_audiences is not None:
            report.add(SecFinding("JWTChecker", "MEDIUM", "Invalid audience", reason))

        # Signature
        if secret:
            valid_sig, reason = self.verify_hs256(token, secret)
            if not valid_sig:
                report.add(SecFinding("JWTChecker", "CRITICAL", "Invalid signature", reason))

        return report


# ---------------------------------------------------------------------------
# OpenRedirectChecker
# ---------------------------------------------------------------------------

class OpenRedirectChecker:
    """
    Validates redirect URLs against allowed domains.
    """

    def __init__(self, allowed_domains: list[str] | None = None):
        self.allowed_domains: list[str] = [d.lower() for d in (allowed_domains or [])]

    def check(self, redirect_url: str) -> tuple[bool, str]:
        """Returns (safe, reason)."""
        if not redirect_url or not redirect_url.strip():
            return False, "Empty redirect URL"

        stripped = redirect_url.strip()

        # Protocol-relative (//evil.com) — always block
        if stripped.startswith("//"):
            return False, "Protocol-relative redirect blocked"

        # Relative URLs starting with / are safe (same domain)
        if stripped.startswith("/") and not stripped.startswith("//"):
            return True, "Relative redirect (same domain)"

        try:
            parsed = urllib.parse.urlparse(stripped)
        except Exception as exc:
            return False, f"URL parse error: {exc}"

        # No scheme = might be relative or dangerous
        scheme = (parsed.scheme or "").lower()
        if not scheme:
            # Could be a relative URL without leading slash — allow cautiously
            return True, "Relative redirect (no scheme)"

        if scheme not in ("http", "https"):
            return False, f"Non-HTTP redirect scheme blocked: {scheme}"

        hostname = (parsed.hostname or "").lower().strip(".")
        if not hostname:
            return False, "No hostname in redirect URL"

        if not self.allowed_domains:
            return False, "No allowed domains configured"

        for domain in self.allowed_domains:
            if hostname == domain or hostname.endswith("." + domain):
                return True, f"Host '{hostname}' is in allowed domains"

        return False, f"Host '{hostname}' not in allowed domains — open redirect risk"


# ---------------------------------------------------------------------------
# MassAssignmentChecker
# ---------------------------------------------------------------------------

_DEFAULT_SENSITIVE_FIELDS = {
    "role", "is_admin", "admin", "is_superuser", "superuser",
    "permissions", "privilege", "privileges", "group", "groups",
    "password", "password_hash", "hashed_password",
    "email_verified", "verified", "active", "is_active",
    "credit", "balance", "credits", "points",
    "internal_id", "created_at", "updated_at", "deleted_at",
    "csrf_token", "api_key", "secret", "token",
}


class MassAssignmentChecker:
    """
    Filters incoming data to only allowed fields.
    Detects attempted assignment of sensitive fields.
    """

    def __init__(self, allowed_fields: list[str],
                 sensitive_fields: list[str] | None = None):
        self.allowed_fields: set = set(allowed_fields)
        self.sensitive_fields: set = (
            set(sensitive_fields) if sensitive_fields is not None
            else _DEFAULT_SENSITIVE_FIELDS
        )

    def filter(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return only the allowed fields from data."""
        return {k: v for k, v in data.items() if k in self.allowed_fields}

    def detect_violations(self, data: dict[str, Any]) -> list[str]:
        """Return list of field names that are not in allowed list."""
        return [k for k in data if k not in self.allowed_fields]

    def detect_sensitive_violations(self, data: dict[str, Any]) -> list[str]:
        """Return list of sensitive field names attempted in data."""
        return [k for k in data if k in self.sensitive_fields and k not in self.allowed_fields]

    def check(self, data: dict[str, Any]) -> tuple[dict[str, Any], AppSecReport]:
        """
        Filter data and return (filtered_data, report).
        Report contains findings for any sensitive field violations.
        """
        report = AppSecReport()
        sensitive_violations = self.detect_sensitive_violations(data)
        all_violations = self.detect_violations(data)

        for field_name in sensitive_violations:
            report.add(SecFinding(
                "MassAssignmentChecker",
                "HIGH",
                f"Attempted mass assignment of sensitive field '{field_name}'",
                f"Field '{field_name}' is in sensitive list and not in allowlist"
            ))

        # Report non-sensitive violations too
        for field_name in all_violations:
            if field_name not in sensitive_violations:
                report.add(SecFinding(
                    "MassAssignmentChecker",
                    "MEDIUM",
                    f"Attempted assignment of non-allowed field '{field_name}'",
                    f"Field '{field_name}' not in allowlist"
                ))

        filtered = self.filter(data)
        return filtered, report


# ---------------------------------------------------------------------------
# XXEChecker
# ---------------------------------------------------------------------------

_DOCTYPE_RE = re.compile(r'<!DOCTYPE', re.IGNORECASE)
_ENTITY_RE = re.compile(r'<!ENTITY', re.IGNORECASE)
_SYSTEM_RE = re.compile(r'\bSYSTEM\b', re.IGNORECASE)
_PUBLIC_RE = re.compile(r'\bPUBLIC\b', re.IGNORECASE)
_PARAM_ENTITY_RE = re.compile(r'<!ENTITY\s+%', re.IGNORECASE)


class XXEChecker:
    """
    Scans XML strings for XXE patterns without parsing/resolving them.
    """

    def check(self, xml_string: str) -> tuple[bool, str]:
        """Returns (dangerous, reason)."""
        if not isinstance(xml_string, str):
            xml_string = xml_string.decode("utf-8", errors="replace")

        if _DOCTYPE_RE.search(xml_string):
            if _ENTITY_RE.search(xml_string):
                if _PARAM_ENTITY_RE.search(xml_string):
                    return True, "XXE/XEE: Parameter entity in DOCTYPE detected (blind XXE risk)"
                if _SYSTEM_RE.search(xml_string):
                    return True, "XXE: DOCTYPE with SYSTEM entity detected"
                if _PUBLIC_RE.search(xml_string):
                    return True, "XXE: DOCTYPE with PUBLIC entity detected"
                return True, "XXE: DOCTYPE with ENTITY definition detected"
            return True, "XXE: DOCTYPE declaration detected (potential XXE vector)"

        return False, "No DOCTYPE or ENTITY definitions found"

    def check_bytes(self, xml_bytes: bytes) -> tuple[bool, str]:
        return self.check(xml_bytes.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# MockAppSecHandler (HTTP server)
# ---------------------------------------------------------------------------

class MockAppSecHandler(BaseHTTPRequestHandler):
    """
    HTTP server with both safe and vulnerable demo endpoints.
    """

    def log_message(self, format, *args):
        pass  # Suppress logs

    def send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/health":
            self.send_json(200, {"status": "ok"})

        elif path == "/safe/redirect":
            target = params.get("to", "/")
            checker = OpenRedirectChecker(allowed_domains=["example.com"])
            safe, reason = checker.check(target)
            if safe:
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
            else:
                self.send_json(400, {"error": "Open redirect blocked", "reason": reason})

        elif path == "/vuln/redirect":
            target = params.get("to", "/")
            # Vulnerable: no validation
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        elif path == "/safe/user":
            # Mass-assignment safe endpoint
            self.send_json(200, {"allowed_fields": ["username", "email", "bio"]})

        elif path == "/safe/ssrf":
            url = params.get("url", "")
            checker = SSRFChecker(allowed_hosts=["example.com", "api.example.com"])
            allowed, reason = checker.check(url)
            self.send_json(200 if allowed else 403, {"allowed": allowed, "reason": reason})

        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self.read_body()

        if path == "/safe/user":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_json(400, {"error": "Invalid JSON"})
                return
            checker = MassAssignmentChecker(allowed_fields=["username", "email", "bio"])
            filtered, report = checker.check(data)
            violations = [f.description for f in report.findings]
            self.send_json(200, {
                "filtered": filtered,
                "violations": violations,
                "clean": report.is_clean()
            })

        elif path == "/vuln/user":
            # Vulnerable: accepts all fields
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_json(400, {"error": "Invalid JSON"})
                return
            self.send_json(200, {"saved": data})

        elif path == "/safe/xml":
            xml_str = body.decode("utf-8", errors="replace")
            checker = XXEChecker()
            dangerous, reason = checker.check(xml_str)
            self.send_json(200 if not dangerous else 400, {
                "dangerous": dangerous,
                "reason": reason
            })

        elif path == "/safe/jwt":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_json(400, {"error": "Invalid JSON"})
                return
            token = data.get("token", "")
            secret = data.get("secret", "")
            checker = JWTChecker(allowed_algorithms=["HS256"])
            report = checker.validate(token, secret=secret or None)
            self.send_json(200, {
                "clean": report.is_clean(),
                "findings": [
                    {"name": f.check_name, "severity": f.severity, "desc": f.description}
                    for f in report.findings
                ]
            })

        else:
            self.send_json(404, {"error": "Not found"})


# ---------------------------------------------------------------------------
# Server start/stop helpers
# ---------------------------------------------------------------------------

def start_mock_server(port: int = 0) -> tuple[HTTPServer, int]:
    """Start the mock server. Returns (server, port)."""
    server = HTTPServer(("127.0.0.1", port), MockAppSecHandler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


def stop_mock_server(server: HTTPServer) -> None:
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("AppSec Test Harness — self test")

    # Quick smoke test
    ssrf = SSRFChecker(allowed_hosts=["example.com"])
    assert ssrf.check("https://example.com/path") == (True, "OK")
    assert ssrf.check("http://127.0.0.1/")[0] is False
    assert ssrf.check("file:///etc/passwd")[0] is False
    print("SSRFChecker OK")

    deser = DeserializationChecker()
    safe_pickle = pickle.dumps({"key": "value"})
    danger_pickle = pickle.dumps({"key": "value"})  # safe object
    # Craft a payload with REDUCE opcode
    bad_bytes = b'\x80\x04\x95' + b'R'  # contains REDUCE
    assert deser.check_pickle(bad_bytes)[0] is True
    print("DeserializationChecker OK")

    jwt_checker = JWTChecker()
    token = jwt_checker.sign_hs256({"sub": "1234", "exp": int(time.time()) + 3600}, "secret")
    valid, _ = jwt_checker.verify_hs256(token, "secret")
    assert valid
    print("JWTChecker OK")

    redir = OpenRedirectChecker(allowed_domains=["example.com"])
    assert redir.check("https://example.com/dashboard")[0] is True
    assert redir.check("https://evil.com/phish")[0] is False
    print("OpenRedirectChecker OK")

    mass = MassAssignmentChecker(allowed_fields=["username", "email"])
    filtered, report = mass.check({"username": "alice", "role": "admin"})
    assert "role" not in filtered
    assert not report.is_clean()
    print("MassAssignmentChecker OK")

    xxe = XXEChecker()
    assert xxe.check('<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>')[0] is True
    assert xxe.check('<root><data>hello</data></root>')[0] is False
    print("XXEChecker OK")

    server, port = start_mock_server()
    import urllib.request
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
    assert json.loads(resp.read())["status"] == "ok"
    stop_mock_server(server)
    print(f"MockServer OK (port {port})")

    print("All self-tests passed!")
