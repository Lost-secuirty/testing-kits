"""
Configuration Validation Test Harness (Harness 16 of 36)
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Callable
from urllib.request import urlopen
from urllib.error import URLError


# ---------------------------------------------------------------------------
# FieldSchema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class FieldSchema:
    """Describes validation rules for a single configuration field."""
    type: str = "str"           # "str", "int", "float", "bool", "list", "dict"
    required: bool = False
    default: Any = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    enum: Optional[List[Any]] = None
    regex: Optional[str] = None
    description: str = ""


# ---------------------------------------------------------------------------
# ConfigSchema
# ---------------------------------------------------------------------------

class ConfigSchema:
    """
    Collection of FieldSchema definitions.
    Keys can be dotted paths for nested fields (e.g. "db.host").
    """

    def __init__(self, fields: Optional[Dict[str, FieldSchema]] = None):
        self.fields: Dict[str, FieldSchema] = fields or {}

    def add_field(self, key: str, schema: FieldSchema) -> None:
        self.fields[key] = schema

    def get_field(self, key: str) -> Optional[FieldSchema]:
        return self.fields.get(key)

    def all_keys(self) -> List[str]:
        return list(self.fields.keys())


# ---------------------------------------------------------------------------
# ConfigReport
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ConfigReport:
    """Holds validation results."""
    errors: Dict[str, List[str]] = dataclasses.field(default_factory=dict)
    warnings: Dict[str, List[str]] = dataclasses.field(default_factory=dict)

    def add_error(self, field: str, message: str) -> None:
        self.errors.setdefault(field, []).append(message)

    def add_warning(self, field: str, message: str) -> None:
        self.warnings.setdefault(field, []).append(message)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def __repr__(self) -> str:
        return f"ConfigReport(valid={self.is_valid}, errors={self.errors}, warnings={self.warnings})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_nested(config: Dict[str, Any], dotted_key: str) -> tuple[bool, Any]:
    """
    Retrieve a value using a dotted key from a nested dict.
    Returns (found: bool, value).
    """
    parts = dotted_key.split(".")
    node: Any = config
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set_nested(config: Dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set a value using a dotted key in a nested dict (creates intermediates)."""
    parts = dotted_key.split(".")
    node = config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _coerce(value: Any, target_type: str) -> tuple[bool, Any]:
    """
    Try to coerce *value* to *target_type*.
    Returns (success: bool, coerced_value).
    """
    type_map = {
        "str": str,
        "int": int,
        "float": float,
        "bool": None,  # handled separately
        "list": list,
        "dict": dict,
    }

    if target_type == "bool":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes", "on"):
                return True, True
            if value.lower() in ("false", "0", "no", "off"):
                return True, False
        if isinstance(value, int):
            return True, bool(value)
        return False, value

    if target_type not in type_map:
        return False, value

    target_cls = type_map[target_type]
    if isinstance(value, target_cls):
        return True, value

    # Allow str → int / float coercion
    if target_type in ("int", "float") and isinstance(value, str):
        try:
            return True, target_cls(value)
        except (ValueError, TypeError):
            return False, value

    # Allow int → float
    if target_type == "float" and isinstance(value, int):
        return True, float(value)

    return False, value


# ---------------------------------------------------------------------------
# ConfigValidator
# ---------------------------------------------------------------------------

class ConfigValidator:
    """Validates a config dict against a ConfigSchema."""

    def validate(
        self,
        config: Dict[str, Any],
        schema: ConfigSchema,
        report: Optional[ConfigReport] = None,
    ) -> ConfigReport:
        if report is None:
            report = ConfigReport()

        # Flatten nested config to dotted keys for easier lookup later,
        # but we work with _get_nested so we don't need to flatten.
        for key, field_schema in schema.fields.items():
            found, raw_value = _get_nested(config, key)

            if not found:
                if field_schema.required:
                    report.add_error(key, f"Required field '{key}' is missing.")
                # Apply default (no further validation needed for missing optional)
                continue

            # Type coercion / check
            ok, coerced = _coerce(raw_value, field_schema.type)
            if not ok:
                report.add_error(
                    key,
                    f"Field '{key}' expected type '{field_schema.type}', "
                    f"got '{type(raw_value).__name__}' value {raw_value!r}.",
                )
                continue  # Can't do further checks without valid type

            value = coerced

            # Range checks (only for numeric types)
            if field_schema.min_val is not None:
                try:
                    if float(value) < field_schema.min_val:
                        report.add_error(
                            key,
                            f"Field '{key}' value {value!r} is below minimum {field_schema.min_val}.",
                        )
                except (TypeError, ValueError):
                    pass

            if field_schema.max_val is not None:
                try:
                    if float(value) > field_schema.max_val:
                        report.add_error(
                            key,
                            f"Field '{key}' value {value!r} exceeds maximum {field_schema.max_val}.",
                        )
                except (TypeError, ValueError):
                    pass

            # Enum membership
            if field_schema.enum is not None:
                if value not in field_schema.enum:
                    report.add_error(
                        key,
                        f"Field '{key}' value {value!r} is not in allowed values {field_schema.enum}.",
                    )

            # Regex pattern
            if field_schema.regex is not None:
                str_value = str(value)
                if not re.fullmatch(field_schema.regex, str_value):
                    report.add_error(
                        key,
                        f"Field '{key}' value {value!r} does not match pattern '{field_schema.regex}'.",
                    )

        return report


# ---------------------------------------------------------------------------
# EnvOverrideChecker
# ---------------------------------------------------------------------------

class EnvOverrideChecker:
    """
    Checks that environment variable overrides correctly override config values.
    Convention: PREFIX_DB_HOST overrides db.host (dots → underscores, uppercased).
    """

    def __init__(self, prefix: str = "MYAPP"):
        self.prefix = prefix.upper().rstrip("_")

    def env_key_for(self, config_key: str) -> str:
        """Return the expected environment variable name for a config key."""
        return f"{self.prefix}_{config_key.upper().replace('.', '_')}"

    def apply_overrides(
        self, config: Dict[str, Any], env: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Return a new config dict with env-var overrides applied.
        If *env* is None, os.environ is used.
        """
        import copy
        result = copy.deepcopy(config)
        env_source = env if env is not None else os.environ

        prefix_len = len(self.prefix) + 1  # e.g. "MYAPP_"
        for env_key, env_val in env_source.items():
            if not env_key.upper().startswith(self.prefix + "_"):
                continue
            remainder = env_key[prefix_len:]           # e.g. "DB_HOST"
            config_key = remainder.lower().replace("_", ".", 1)  # "db.host"
            # Also try direct underscore-to-dot replacement for multi-segment keys
            # Use a more complete approach: replace all underscores with dots
            config_key_full = remainder.lower().replace("_", ".")
            # Try both: single replacement and full replacement
            # We prefer the longest matching key we can find.
            # For simplicity, store under the full-dot version.
            _set_nested(result, config_key_full, env_val)

        return result

    def check(
        self,
        base_config: Dict[str, Any],
        env: Dict[str, str],
        expected_config: Dict[str, Any],
    ) -> ConfigReport:
        """
        Apply env overrides to base_config and compare against expected_config.
        Returns a report with errors for any mismatches.
        """
        report = ConfigReport()
        actual = self.apply_overrides(base_config, env)

        def _compare(actual_node: Any, expected_node: Any, path: str) -> None:
            if isinstance(expected_node, dict):
                for k, v in expected_node.items():
                    child_path = f"{path}.{k}" if path else k
                    if not isinstance(actual_node, dict) or k not in actual_node:
                        report.add_error(child_path, f"Key '{child_path}' missing in overridden config.")
                    else:
                        _compare(actual_node[k], v, child_path)
            else:
                if str(actual_node) != str(expected_node):
                    report.add_error(
                        path,
                        f"Expected '{expected_node}', got '{actual_node}' at '{path}'.",
                    )

        _compare(actual, expected_config, "")
        return report


# ---------------------------------------------------------------------------
# CrossFieldValidator
# ---------------------------------------------------------------------------

class CrossFieldValidator:
    """
    Validates relationships between fields.
    Rules are registered as callables (config) -> Optional[str] (error message or None).
    """

    def __init__(self) -> None:
        self._rules: List[tuple[str, Callable[[Dict[str, Any]], Optional[str]]]] = []

    def add_rule(
        self,
        name: str,
        rule: Callable[[Dict[str, Any]], Optional[str]],
    ) -> None:
        self._rules.append((name, rule))

    def validate(
        self, config: Dict[str, Any], report: Optional[ConfigReport] = None
    ) -> ConfigReport:
        if report is None:
            report = ConfigReport()
        for name, rule in self._rules:
            try:
                msg = rule(config)
                if msg:
                    report.add_error(name, msg)
            except Exception as exc:  # noqa: BLE001
                report.add_error(name, f"Rule '{name}' raised exception: {exc}")
        return report


# ---------------------------------------------------------------------------
# SensitiveValueDetector
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS = [
    # key-based heuristics
    re.compile(r"(password|passwd|secret|api[_-]?key|token|private[_-]?key|auth[_-]?key|access[_-]?key)", re.I),
    # value-based heuristics (looks like a random secret)
    re.compile(r"^[A-Za-z0-9+/=_\-]{20,}$"),
    # JWT-like
    re.compile(r"^eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$"),
]

_KEY_SENSITIVE = re.compile(
    r"(password|passwd|secret|api[_-]?key|token|private[_-]?key|auth[_-]?key|access[_-]?key)", re.I
)


class SensitiveValueDetector:
    """
    Scans a config dict for plaintext passwords, tokens, and API keys.
    Reports findings as warnings (not errors, since presence is expected
    but should be noted).
    """

    def __init__(self, key_patterns: Optional[List[str]] = None):
        """
        key_patterns: additional regex patterns for sensitive key names.
        """
        self._extra_patterns: List[re.Pattern] = []
        for p in (key_patterns or []):
            self._extra_patterns.append(re.compile(p, re.I))

    def _is_sensitive_key(self, key: str) -> bool:
        if _KEY_SENSITIVE.search(key):
            return True
        for pat in self._extra_patterns:
            if pat.search(key):
                return True
        return False

    def _looks_like_plaintext_secret(self, value: str) -> bool:
        """Heuristic: non-empty string that isn't a path, URL, hostname, or short word."""
        if not isinstance(value, str):
            return False
        if len(value) < 6:
            return False
        # Exclude typical non-secret values
        if re.match(r"https?://", value):
            return False
        if re.match(r"^[\w\-\.]+$", value) and len(value) < 20:
            return False
        return True

    def scan(
        self, config: Dict[str, Any], report: Optional[ConfigReport] = None, _prefix: str = ""
    ) -> ConfigReport:
        if report is None:
            report = ConfigReport()
        self._scan_node(config, report, _prefix)
        return report

    def _scan_node(
        self, node: Any, report: ConfigReport, prefix: str
    ) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if self._is_sensitive_key(k) and isinstance(v, str) and v:
                    report.add_warning(
                        full_key,
                        f"Sensitive key '{full_key}' contains a plaintext value.",
                    )
                self._scan_node(v, report, full_key)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                self._scan_node(item, report, f"{prefix}[{i}]")


# ---------------------------------------------------------------------------
# MockConfigHandler  (HTTP server)
# ---------------------------------------------------------------------------

class MockConfigHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP handler that serves config JSON and accepts PUT/POST updates.

    The server stores a mutable config dict at the class level so that
    all handler instances share state.
    """

    _config_store: Dict[str, Any] = {}
    _schema_store: Dict[str, Any] = {}

    def log_message(self, *args: Any) -> None:  # suppress access logs
        pass

    def _send_json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/config":
            self._send_json(200, MockConfigHandler._config_store)
        elif self.path == "/schema":
            self._send_json(200, MockConfigHandler._schema_store)
        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return

        if self.path == "/config":
            MockConfigHandler._config_store.update(data)
            self._send_json(200, {"status": "updated", "config": MockConfigHandler._config_store})
        elif self.path == "/config/reset":
            MockConfigHandler._config_store = data
            self._send_json(200, {"status": "reset", "config": MockConfigHandler._config_store})
        elif self.path == "/validate":
            # Validate the posted config against the stored schema.
            schema = ConfigSchema()
            for field_key, field_def in MockConfigHandler._schema_store.items():
                fs = FieldSchema(**{k: v for k, v in field_def.items() if k in FieldSchema.__dataclass_fields__})
                schema.add_field(field_key, fs)
            validator = ConfigValidator()
            report = validator.validate(data, schema)
            self._send_json(200, {"valid": report.is_valid, "errors": report.errors, "warnings": report.warnings})
        else:
            self._send_json(404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        self.do_POST()


class MockConfigServer:
    """Context-manager wrapper around the mock HTTP server."""

    DEFAULT_PORT = 19020

    def __init__(self, port: int = 0, initial_config: Optional[Dict[str, Any]] = None):
        self._port = port
        self._initial_config = initial_config or {}
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("Server not started.")
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        MockConfigHandler._config_store = dict(self._initial_config)
        self._server = HTTPServer(("127.0.0.1", self._port), MockConfigHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    def __enter__(self) -> "MockConfigServer":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Public convenience re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "FieldSchema",
    "ConfigSchema",
    "ConfigValidator",
    "EnvOverrideChecker",
    "CrossFieldValidator",
    "SensitiveValueDetector",
    "ConfigReport",
    "MockConfigHandler",
    "MockConfigServer",
]
