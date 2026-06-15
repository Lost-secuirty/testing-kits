"""
Configuration Validation Test Harness (Harness 16 of 36)
Pure stdlib, zero external dependencies.

GOLD shape (hardened TEETH gate): exposes a frozen oracle corpus of layered
configuration sources (defaults < file < env) with literal expected outcomes,
a correct ORACLE load/merge/validate pipeline, faithful planted MUTANTs that
model real-world config bugs, a non-circular ``prove(impl) -> bool``, a
module-level ``TEETH``, and a Report-based ``--self-test``.

Run:
  python harnesses/core/config_test_harness.py --self-test
  python harnesses/core/config_test_harness.py --json
  python harnesses/core/config_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# FieldSchema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class FieldSchema:
    """Describes validation rules for a single configuration field."""
    type: str = "str"           # "str", "int", "float", "bool", "list", "dict"
    required: bool = False
    default: Any = None
    min_val: float | None = None
    max_val: float | None = None
    enum: list[Any] | None = None
    regex: str | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# ConfigSchema
# ---------------------------------------------------------------------------

class ConfigSchema:
    """
    Collection of FieldSchema definitions.
    Keys can be dotted paths for nested fields (e.g. "db.host").
    """

    def __init__(self, fields: dict[str, FieldSchema] | None = None):
        self.fields: dict[str, FieldSchema] = fields or {}

    def add_field(self, key: str, schema: FieldSchema) -> None:
        self.fields[key] = schema

    def get_field(self, key: str) -> FieldSchema | None:
        return self.fields.get(key)

    def all_keys(self) -> list[str]:
        return list(self.fields.keys())


# ---------------------------------------------------------------------------
# ConfigReport
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ConfigReport:
    """Holds validation results."""
    errors: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    warnings: dict[str, list[str]] = dataclasses.field(default_factory=dict)

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

def _get_nested(config: dict[str, Any], dotted_key: str) -> tuple[bool, Any]:
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


def _set_nested(config: dict[str, Any], dotted_key: str, value: Any) -> None:
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
        config: dict[str, Any],
        schema: ConfigSchema,
        report: ConfigReport | None = None,
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
        self, config: dict[str, Any], env: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Return a new config dict with env-var overrides applied.
        If *env* is None, os.environ is used.
        """
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
        base_config: dict[str, Any],
        env: dict[str, str],
        expected_config: dict[str, Any],
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
        self._rules: list[tuple[str, Callable[[dict[str, Any]], str | None]]] = []

    def add_rule(
        self,
        name: str,
        rule: Callable[[dict[str, Any]], str | None],
    ) -> None:
        self._rules.append((name, rule))

    def validate(
        self, config: dict[str, Any], report: ConfigReport | None = None
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

    def __init__(self, key_patterns: list[str] | None = None):
        """
        key_patterns: additional regex patterns for sensitive key names.
        """
        self._extra_patterns: list[re.Pattern] = []
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
        self, config: dict[str, Any], report: ConfigReport | None = None, _prefix: str = ""
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

    _config_store: dict[str, Any] = {}
    _schema_store: dict[str, Any] = {}

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

    def __init__(self, port: int = 0, initial_config: dict[str, Any] | None = None):
        self._port = port
        self._initial_config = initial_config or {}
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

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
        server = self._server
        if server:
            server.shutdown()
            server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> MockConfigServer:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# TEETH: a layered config load/merge/validate ORACLE + frozen corpus.
#
# The networked MockConfigHandler/Server above is exercised over a real socket
# by the paired unittest. The teeth, by contrast, run a PURE in-process model of
# the canonical "load a config from layered sources" contract so the gate can
# verify "this harness catches a real config bug" with zero clock/network/
# filesystem I/O and full determinism.
#
# A loader impl maps a frozen ConfigSources triple (defaults, file, env) plus a
# ConfigSchema to a LoadOutcome: either a fully merged + coerced config dict, or
# a validation error keyed by field. The oracle is the correct loader; each
# Mutant is a faithful real-world config defect. prove() judges each impl's
# outcome against the case's FROZEN expected outcome (a literal constant) -- it
# never compares an impl to the oracle object, so the check is non-circular.
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ConfigSources:
    """The three precedence layers of a configuration load (lowest -> highest)."""
    defaults: tuple[tuple[str, Any], ...] = ()   # built-in defaults (dotted keys)
    file: tuple[tuple[str, Any], ...] = ()       # values from a config file (dotted keys)
    env: tuple[tuple[str, str], ...] = ()        # raw environment variables (PREFIX_*)


@dataclasses.dataclass(frozen=True)
class LoadOutcome:
    """The result of a layered load: either an effective config or an error set.

    ``ok`` is True iff validation passed. ``config`` holds the merged, coerced,
    validated dotted-key->value mapping (only meaningful when ok). ``error_keys``
    holds the set of fields that failed validation (only meaningful when not ok).
    Both are stored canonically (sorted tuples) so two outcomes compare by value.
    """
    ok: bool
    config: tuple[tuple[str, Any], ...] = ()
    error_keys: tuple[str, ...] = ()

    @staticmethod
    def valid(config: dict[str, Any]) -> LoadOutcome:
        return LoadOutcome(ok=True, config=tuple(sorted(config.items())))

    @staticmethod
    def invalid(error_keys: list[str]) -> LoadOutcome:
        return LoadOutcome(ok=False, error_keys=tuple(sorted(set(error_keys))))


# Convention: env var PREFIX_DB_HOST overrides dotted key db.host (the harness's
# EnvOverrideChecker convention). The teeth use this same prefix.
_TEETH_ENV_PREFIX = "MYAPP"


def _env_to_dotted(env_key: str, prefix: str = _TEETH_ENV_PREFIX) -> str | None:
    """Map a PREFIX_DB_HOST env var name to the dotted key db.host, or None."""
    up = env_key.upper()
    pre = prefix.upper() + "_"
    if not up.startswith(pre):
        return None
    return env_key[len(pre):].lower().replace("_", ".")


def load_config(sources: ConfigSources, schema: ConfigSchema) -> LoadOutcome:
    """Correct ORACLE: merge layered sources by precedence, then coerce+validate.

    Precedence (lowest to highest): defaults < file < env. A present env var
    ALWAYS overrides a file value for the same key. Each merged value is coerced
    to its schema type; required fields must be present after the merge; type
    coercion failures and missing-required fields produce a non-ok outcome whose
    error_keys name the offending fields.
    """
    # 1. Merge by precedence into a flat dotted-key map.
    merged: dict[str, Any] = {}
    for key, value in sources.defaults:
        merged[key] = value
    for key, value in sources.file:
        merged[key] = value
    for env_key, env_val in sources.env:
        dotted = _env_to_dotted(env_key)
        if dotted is not None:
            merged[dotted] = env_val  # env overrides file/defaults

    # 2. Coerce + validate against the schema.
    effective: dict[str, Any] = {}
    errors: list[str] = []
    for key, field_schema in schema.fields.items():
        if key not in merged:
            if field_schema.required:
                errors.append(key)
            elif field_schema.default is not None:
                effective[key] = field_schema.default
            continue
        ok, coerced = _coerce(merged[key], field_schema.type)
        if not ok:
            errors.append(key)
            continue
        effective[key] = coerced

    if errors:
        return LoadOutcome.invalid(errors)
    return LoadOutcome.valid(effective)


# --- Planted buggy twins (each models a real, common configuration bug) -----

def load_config_env_ignored(sources: ConfigSources, schema: ConfigSchema) -> LoadOutcome:
    """BUG: environment variables do NOT override file values.

    A very common precedence regression -- the loader reads env vars but applies
    them BEFORE the file layer (or not at all for already-set keys), so a
    deploy-time ``MYAPP_DB_HOST`` is silently shadowed by the checked-in config
    file. Operators think they overrode a setting; they did not.
    """
    merged: dict[str, Any] = {}
    for key, value in sources.defaults:
        merged[key] = value
    # BUG: env applied first, then the file clobbers it back.
    for env_key, env_val in sources.env:
        dotted = _env_to_dotted(env_key)
        if dotted is not None:
            merged[dotted] = env_val
    for key, value in sources.file:
        merged[key] = value  # file wrongly wins over env

    effective: dict[str, Any] = {}
    errors: list[str] = []
    for key, field_schema in schema.fields.items():
        if key not in merged:
            if field_schema.required:
                errors.append(key)
            elif field_schema.default is not None:
                effective[key] = field_schema.default
            continue
        ok, coerced = _coerce(merged[key], field_schema.type)
        if not ok:
            errors.append(key)
            continue
        effective[key] = coerced
    if errors:
        return LoadOutcome.invalid(errors)
    return LoadOutcome.valid(effective)


def load_config_missing_required_ok(sources: ConfigSources, schema: ConfigSchema) -> LoadOutcome:
    """BUG: a missing required field is silently accepted (no error).

    Models the classic "the app booted in prod with no database URL" failure --
    the loader forgets to enforce ``required``, so an absent mandatory field
    passes validation and the service starts mis-configured instead of failing
    fast at load time.
    """
    merged: dict[str, Any] = {}
    for key, value in sources.defaults:
        merged[key] = value
    for key, value in sources.file:
        merged[key] = value
    for env_key, env_val in sources.env:
        dotted = _env_to_dotted(env_key)
        if dotted is not None:
            merged[dotted] = env_val

    effective: dict[str, Any] = {}
    errors: list[str] = []
    for key, field_schema in schema.fields.items():
        if key not in merged:
            # BUG: required-ness never checked; missing fields just skipped.
            if field_schema.default is not None:
                effective[key] = field_schema.default
            continue
        ok, coerced = _coerce(merged[key], field_schema.type)
        if not ok:
            errors.append(key)
            continue
        effective[key] = coerced
    if errors:
        return LoadOutcome.invalid(errors)
    return LoadOutcome.valid(effective)


def load_config_no_coercion(sources: ConfigSources, schema: ConfigSchema) -> LoadOutcome:
    """BUG: values are stored raw, with no type coercion.

    Env vars (and many file formats) deliver everything as strings. A loader that
    skips coercion leaves ``port`` as the string ``"5432"`` instead of the int
    ``5432`` -- arithmetic/comparison on it then fails far from the config layer,
    or a string sneaks past where an int was contractually required. The merged
    value differs from the correctly-coerced expected value.
    """
    merged: dict[str, Any] = {}
    for key, value in sources.defaults:
        merged[key] = value
    for key, value in sources.file:
        merged[key] = value
    for env_key, env_val in sources.env:
        dotted = _env_to_dotted(env_key)
        if dotted is not None:
            merged[dotted] = env_val

    effective: dict[str, Any] = {}
    errors: list[str] = []
    for key, field_schema in schema.fields.items():
        if key not in merged:
            if field_schema.required:
                errors.append(key)
            elif field_schema.default is not None:
                effective[key] = field_schema.default
            continue
        effective[key] = merged[key]  # BUG: no _coerce — keep the raw value
    if errors:
        return LoadOutcome.invalid(errors)
    return LoadOutcome.valid(effective)


# --- Frozen corpus: (sources, schema) -> expected LoadOutcome ---------------

@dataclasses.dataclass(frozen=True)
class ConfigCase:
    name: str
    sources: ConfigSources
    schema: ConfigSchema
    expected: LoadOutcome
    note: str = ""


def _schema(fields: dict[str, FieldSchema]) -> ConfigSchema:
    return ConfigSchema(dict(fields))


ORACLE_CASES: tuple[ConfigCase, ...] = (
    # Precedence: env MUST override file MUST override defaults.
    # This is the teeth case for the env-ignored mutant.
    ConfigCase(
        "env_overrides_file",
        ConfigSources(
            defaults=(("db.host", "localhost"),),
            file=(("db.host", "file-db"),),
            env=(("MYAPP_DB_HOST", "env-db"),),
        ),
        _schema({"db.host": FieldSchema(type="str", required=True)}),
        LoadOutcome.valid({"db.host": "env-db"}),
        note="precedence defaults<file<env: env must win",
    ),
    # File overrides defaults when no env var is present.
    ConfigCase(
        "file_overrides_defaults",
        ConfigSources(
            defaults=(("db.host", "localhost"),),
            file=(("db.host", "file-db"),),
        ),
        _schema({"db.host": FieldSchema(type="str", required=True)}),
        LoadOutcome.valid({"db.host": "file-db"}),
        note="file layer overrides built-in defaults",
    ),
    # Type coercion: an env string "5432" must become int 5432.
    # This is the teeth case for the no-coercion mutant.
    ConfigCase(
        "env_string_coerced_to_int",
        ConfigSources(
            defaults=(("db.port", 5432),),
            env=(("MYAPP_DB_PORT", "6543"),),
        ),
        _schema({"db.port": FieldSchema(type="int", required=True)}),
        LoadOutcome.valid({"db.port": 6543}),
        note="env arrives as a string and must be coerced to int",
    ),
    # bool coercion from a file string.
    ConfigCase(
        "bool_coerced_from_string",
        ConfigSources(file=(("debug", "true"),)),
        _schema({"debug": FieldSchema(type="bool", required=True)}),
        LoadOutcome.valid({"debug": True}),
        note="string 'true' must coerce to boolean True",
    ),
    # Missing required field -> error.
    # This is the teeth case for the missing-required mutant.
    ConfigCase(
        "missing_required_is_error",
        ConfigSources(file=(("app.name", "svc"),)),
        _schema({
            "app.name": FieldSchema(type="str", required=True),
            "db.host": FieldSchema(type="str", required=True),
        }),
        LoadOutcome.invalid(["db.host"]),
        note="a required field absent from every layer must fail the load",
    ),
    # Optional missing field falls back to its schema default.
    ConfigCase(
        "optional_uses_default",
        ConfigSources(file=(("app.name", "svc"),)),
        _schema({
            "app.name": FieldSchema(type="str", required=True),
            "workers": FieldSchema(type="int", required=False, default=4),
        }),
        LoadOutcome.valid({"app.name": "svc", "workers": 4}),
        note="missing optional field takes the schema default",
    ),
    # A value that cannot be coerced to its declared type -> error.
    ConfigCase(
        "uncoercible_type_is_error",
        ConfigSources(env=(("MYAPP_PORT", "not-a-number"),)),
        _schema({"port": FieldSchema(type="int", required=True)}),
        LoadOutcome.invalid(["port"]),
        note="non-numeric env value for an int field fails type validation",
    ),
)


def list_scenarios() -> list[str]:
    return [c.name for c in ORACLE_CASES]


def prove(impl: Callable[[ConfigSources, ConfigSchema], LoadOutcome]) -> bool:
    """True iff loader ``impl`` diverges from any frozen corpus expectation.

    Non-circular + deterministic: each impl outcome is compared to the case's
    FROZEN expected LoadOutcome (a literal constant), never to the oracle object.
    No clock/network/filesystem I/O; no RNG. A loader that raises on a corpus
    case counts as caught.
    """
    for case in ORACLE_CASES:
        try:
            outcome = impl(case.sources, case.schema)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if outcome != case.expected:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=load_config,
    mutants=(
        Mutant("env_not_overriding_file", load_config_env_ignored,
               "env vars do not override file values (file wrongly wins over env)"),
        Mutant("missing_required_accepted", load_config_missing_required_ok,
               "a missing required field is silently accepted instead of erroring"),
        Mutant("no_type_coercion", load_config_no_coercion,
               "values are stored raw with no type coercion (env '5432' stays a string)"),
    ),
    corpus_size=len(ORACLE_CASES),
    kind="oracle_swap",
    notes="precedence is defaults<file<env, required fields are enforced, and types are coerced",
)


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/config")

    # 1. The correct oracle loader matches every frozen expected outcome.
    for case in ORACLE_CASES:
        report.add(f"oracle:{case.name}", case.expected, load_config(case.sources, case.schema),
                   detail=case.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    # 3. Harness-specific spot checks of the underlying validators (kept alive).
    schema = ConfigSchema({"port": FieldSchema(type="int", required=True, min_val=1, max_val=65535)})
    rep = ConfigValidator().validate({"port": "8080"}, schema)
    report.record("validator_coerces_str_port", rep.is_valid,
                  detail="ConfigValidator coerces '8080' -> 8080 within range")
    over = ConfigValidator().validate({"port": "70000"}, schema)
    report.record("validator_flags_out_of_range", not over.is_valid,
                  detail="port above max must be rejected")
    det = SensitiveValueDetector().scan({"db": {"password": "hunter2"}})
    report.record("detector_flags_plaintext_secret", "db.password" in det.warnings,
                  detail="plaintext secret under a sensitive key is warned")

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configuration validation controls")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen oracle corpus case names")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


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
    "ConfigSources",
    "LoadOutcome",
    "load_config",
    "prove",
    "TEETH",
    "ORACLE_CASES",
    "list_scenarios",
]


if __name__ == "__main__":
    sys.exit(main())
