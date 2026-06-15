"""
Tests for Configuration Validation Test Harness (Harness 16 of 36)
~52 tests covering all validators.
"""

import json
import os
import time
import unittest
from urllib.request import urlopen, Request
from urllib.error import URLError

from harnesses._teeth import verify
from harnesses.core.config_test_harness import (
    FieldSchema,
    ConfigSchema,
    ConfigValidator,
    EnvOverrideChecker,
    CrossFieldValidator,
    SensitiveValueDetector,
    ConfigReport,
    MockConfigServer,
    TEETH,
    prove,
    load_config,
)


# ---------------------------------------------------------------------------
# FieldSchema tests
# ---------------------------------------------------------------------------

class TestFieldSchema(unittest.TestCase):
    """Tests for FieldSchema dataclass."""

    def test_default_values(self):
        fs = FieldSchema()
        self.assertEqual(fs.type, "str")
        self.assertFalse(fs.required)
        self.assertIsNone(fs.default)
        self.assertIsNone(fs.min_val)
        self.assertIsNone(fs.max_val)
        self.assertIsNone(fs.enum)
        self.assertIsNone(fs.regex)
        self.assertEqual(fs.description, "")

    def test_custom_values(self):
        fs = FieldSchema(
            type="int",
            required=True,
            default=42,
            min_val=0,
            max_val=100,
            enum=[1, 2, 42],
            regex=r"\d+",
            description="A test field",
        )
        self.assertEqual(fs.type, "int")
        self.assertTrue(fs.required)
        self.assertEqual(fs.default, 42)
        self.assertEqual(fs.min_val, 0)
        self.assertEqual(fs.max_val, 100)
        self.assertEqual(fs.enum, [1, 2, 42])
        self.assertEqual(fs.regex, r"\d+")
        self.assertEqual(fs.description, "A test field")

    def test_is_dataclass(self):
        import dataclasses
        self.assertTrue(dataclasses.is_dataclass(FieldSchema))


# ---------------------------------------------------------------------------
# ConfigSchema tests
# ---------------------------------------------------------------------------

class TestConfigSchema(unittest.TestCase):
    """Tests for ConfigSchema."""

    def test_add_and_get_field(self):
        schema = ConfigSchema()
        fs = FieldSchema(type="str", required=True)
        schema.add_field("hostname", fs)
        retrieved = schema.get_field("hostname")
        self.assertIs(retrieved, fs)

    def test_missing_field_returns_none(self):
        schema = ConfigSchema()
        self.assertIsNone(schema.get_field("nonexistent"))

    def test_init_with_fields(self):
        fields = {"host": FieldSchema(type="str"), "port": FieldSchema(type="int")}
        schema = ConfigSchema(fields=fields)
        self.assertEqual(set(schema.all_keys()), {"host", "port"})

    def test_all_keys(self):
        schema = ConfigSchema()
        schema.add_field("a", FieldSchema())
        schema.add_field("b", FieldSchema())
        schema.add_field("c.d", FieldSchema())
        self.assertIn("c.d", schema.all_keys())
        self.assertEqual(len(schema.all_keys()), 3)


# ---------------------------------------------------------------------------
# ConfigReport tests
# ---------------------------------------------------------------------------

class TestConfigReport(unittest.TestCase):
    """Tests for ConfigReport."""

    def test_initially_valid(self):
        report = ConfigReport()
        self.assertTrue(report.is_valid)

    def test_add_error_makes_invalid(self):
        report = ConfigReport()
        report.add_error("field1", "Something is wrong")
        self.assertFalse(report.is_valid)
        self.assertIn("field1", report.errors)
        self.assertEqual(report.errors["field1"], ["Something is wrong"])

    def test_add_warning_stays_valid(self):
        report = ConfigReport()
        report.add_warning("field1", "Just a note")
        self.assertTrue(report.is_valid)
        self.assertIn("field1", report.warnings)

    def test_multiple_errors_same_field(self):
        report = ConfigReport()
        report.add_error("f", "err1")
        report.add_error("f", "err2")
        self.assertEqual(len(report.errors["f"]), 2)


# ---------------------------------------------------------------------------
# ConfigValidator tests
# ---------------------------------------------------------------------------

class TestConfigValidator(unittest.TestCase):
    """Tests for ConfigValidator."""

    def setUp(self):
        self.validator = ConfigValidator()

    def test_valid_simple_config(self):
        schema = ConfigSchema({"host": FieldSchema(type="str", required=True)})
        report = self.validator.validate({"host": "localhost"}, schema)
        self.assertTrue(report.is_valid)

    def test_required_field_missing(self):
        schema = ConfigSchema({"host": FieldSchema(type="str", required=True)})
        report = self.validator.validate({}, schema)
        self.assertFalse(report.is_valid)
        self.assertIn("host", report.errors)

    def test_optional_field_missing_ok(self):
        schema = ConfigSchema({"host": FieldSchema(type="str", required=False)})
        report = self.validator.validate({}, schema)
        self.assertTrue(report.is_valid)

    def test_type_mismatch_str_for_int(self):
        schema = ConfigSchema({"port": FieldSchema(type="int", required=True)})
        report = self.validator.validate({"port": "notanumber"}, schema)
        self.assertFalse(report.is_valid)
        self.assertIn("port", report.errors)

    def test_coerce_str_to_int(self):
        schema = ConfigSchema({"port": FieldSchema(type="int", required=True)})
        report = self.validator.validate({"port": "8080"}, schema)
        self.assertTrue(report.is_valid)

    def test_coerce_str_to_float(self):
        schema = ConfigSchema({"ratio": FieldSchema(type="float", required=True)})
        report = self.validator.validate({"ratio": "3.14"}, schema)
        self.assertTrue(report.is_valid)

    def test_coerce_str_to_bool_true(self):
        schema = ConfigSchema({"debug": FieldSchema(type="bool", required=True)})
        for truthy in ("true", "True", "1", "yes", "on"):
            with self.subTest(val=truthy):
                report = self.validator.validate({"debug": truthy}, schema)
                self.assertTrue(report.is_valid, f"Expected valid for {truthy!r}")

    def test_coerce_str_to_bool_false(self):
        schema = ConfigSchema({"debug": FieldSchema(type="bool", required=True)})
        for falsy in ("false", "False", "0", "no", "off"):
            with self.subTest(val=falsy):
                report = self.validator.validate({"debug": falsy}, schema)
                self.assertTrue(report.is_valid, f"Expected valid for {falsy!r}")

    def test_min_val_violation(self):
        schema = ConfigSchema({"workers": FieldSchema(type="int", required=True, min_val=1)})
        report = self.validator.validate({"workers": 0}, schema)
        self.assertFalse(report.is_valid)
        self.assertIn("workers", report.errors)

    def test_max_val_violation(self):
        schema = ConfigSchema({"workers": FieldSchema(type="int", required=True, max_val=10)})
        report = self.validator.validate({"workers": 11}, schema)
        self.assertFalse(report.is_valid)

    def test_min_max_in_range(self):
        schema = ConfigSchema({"workers": FieldSchema(type="int", required=True, min_val=1, max_val=10)})
        report = self.validator.validate({"workers": 5}, schema)
        self.assertTrue(report.is_valid)

    def test_enum_valid(self):
        schema = ConfigSchema({"env": FieldSchema(type="str", required=True, enum=["dev", "staging", "prod"])})
        report = self.validator.validate({"env": "dev"}, schema)
        self.assertTrue(report.is_valid)

    def test_enum_invalid(self):
        schema = ConfigSchema({"env": FieldSchema(type="str", required=True, enum=["dev", "staging", "prod"])})
        report = self.validator.validate({"env": "production"}, schema)
        self.assertFalse(report.is_valid)
        self.assertIn("env", report.errors)

    def test_regex_match(self):
        schema = ConfigSchema({"ip": FieldSchema(type="str", required=True, regex=r"\d{1,3}(\.\d{1,3}){3}")})
        report = self.validator.validate({"ip": "192.168.1.1"}, schema)
        self.assertTrue(report.is_valid)

    def test_regex_no_match(self):
        schema = ConfigSchema({"ip": FieldSchema(type="str", required=True, regex=r"\d{1,3}(\.\d{1,3}){3}")})
        report = self.validator.validate({"ip": "not-an-ip"}, schema)
        self.assertFalse(report.is_valid)

    def test_nested_key_valid(self):
        schema = ConfigSchema({"db.host": FieldSchema(type="str", required=True)})
        config = {"db": {"host": "localhost"}}
        report = self.validator.validate(config, schema)
        self.assertTrue(report.is_valid)

    def test_nested_key_missing(self):
        schema = ConfigSchema({"db.host": FieldSchema(type="str", required=True)})
        config = {"db": {}}
        report = self.validator.validate(config, schema)
        self.assertFalse(report.is_valid)
        self.assertIn("db.host", report.errors)

    def test_nested_port_type_coercion(self):
        schema = ConfigSchema({"db.port": FieldSchema(type="int", required=True, min_val=1, max_val=65535)})
        config = {"db": {"port": "5432"}}
        report = self.validator.validate(config, schema)
        self.assertTrue(report.is_valid)

    def test_multiple_errors(self):
        schema = ConfigSchema({
            "host": FieldSchema(type="str", required=True),
            "port": FieldSchema(type="int", required=True),
        })
        report = self.validator.validate({}, schema)
        self.assertIn("host", report.errors)
        self.assertIn("port", report.errors)

    def test_existing_report_accumulates(self):
        schema = ConfigSchema({"x": FieldSchema(type="int", required=True)})
        report = ConfigReport()
        report.add_error("pre", "pre-existing")
        self.validator.validate({}, schema, report=report)
        self.assertIn("pre", report.errors)
        self.assertIn("x", report.errors)


# ---------------------------------------------------------------------------
# EnvOverrideChecker tests
# ---------------------------------------------------------------------------

class TestEnvOverrideChecker(unittest.TestCase):
    """Tests for EnvOverrideChecker."""

    def setUp(self):
        self.checker = EnvOverrideChecker(prefix="MYAPP")

    def test_env_key_for_simple(self):
        self.assertEqual(self.checker.env_key_for("host"), "MYAPP_HOST")

    def test_env_key_for_nested(self):
        self.assertEqual(self.checker.env_key_for("db.host"), "MYAPP_DB_HOST")

    def test_apply_override_simple(self):
        base = {"host": "localhost"}
        env = {"MYAPP_HOST": "remotehost"}
        result = self.checker.apply_overrides(base, env)
        self.assertEqual(result["host"], "remotehost")

    def test_apply_override_nested(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        env = {"MYAPP_DB_HOST": "db.example.com"}
        result = self.checker.apply_overrides(base, env)
        self.assertEqual(result["db"]["host"], "db.example.com")

    def test_apply_override_does_not_mutate_original(self):
        base = {"host": "localhost"}
        env = {"MYAPP_HOST": "other"}
        self.checker.apply_overrides(base, env)
        self.assertEqual(base["host"], "localhost")

    def test_check_reports_no_errors_on_match(self):
        base = {"host": "localhost"}
        env = {"MYAPP_HOST": "newhost"}
        expected = {"host": "newhost"}
        report = self.checker.check(base, env, expected)
        self.assertTrue(report.is_valid)

    def test_check_reports_error_on_mismatch(self):
        base = {"host": "localhost"}
        env = {"MYAPP_HOST": "newhost"}
        expected = {"host": "wronghost"}
        report = self.checker.check(base, env, expected)
        self.assertFalse(report.is_valid)

    def test_prefix_with_trailing_underscore_handled(self):
        checker = EnvOverrideChecker(prefix="APP_")
        self.assertEqual(checker.env_key_for("host"), "APP_HOST")


# ---------------------------------------------------------------------------
# CrossFieldValidator tests
# ---------------------------------------------------------------------------

class TestCrossFieldValidator(unittest.TestCase):
    """Tests for CrossFieldValidator."""

    def setUp(self):
        self.cfv = CrossFieldValidator()

    def test_no_rules_valid(self):
        report = self.cfv.validate({"ssl": True, "cert_path": "/etc/ssl/cert.pem"})
        self.assertTrue(report.is_valid)

    def test_rule_passes(self):
        def ssl_cert_rule(cfg):
            if cfg.get("ssl") and not cfg.get("cert_path"):
                return "ssl=True requires cert_path to be set"
            return None

        self.cfv.add_rule("ssl_cert", ssl_cert_rule)
        report = self.cfv.validate({"ssl": True, "cert_path": "/etc/ssl/cert.pem"})
        self.assertTrue(report.is_valid)

    def test_rule_fails(self):
        def ssl_cert_rule(cfg):
            if cfg.get("ssl") and not cfg.get("cert_path"):
                return "ssl=True requires cert_path to be set"
            return None

        self.cfv.add_rule("ssl_cert", ssl_cert_rule)
        report = self.cfv.validate({"ssl": True})
        self.assertFalse(report.is_valid)
        self.assertIn("ssl_cert", report.errors)

    def test_rule_exception_captured(self):
        def bad_rule(cfg):
            raise ValueError("explosion")

        self.cfv.add_rule("bad", bad_rule)
        report = self.cfv.validate({})
        self.assertFalse(report.is_valid)
        self.assertIn("bad", report.errors)
        self.assertIn("explosion", report.errors["bad"][0])

    def test_multiple_rules_all_evaluated(self):
        self.cfv.add_rule("r1", lambda c: "err1" if not c.get("a") else None)
        self.cfv.add_rule("r2", lambda c: "err2" if not c.get("b") else None)
        report = self.cfv.validate({})
        self.assertIn("r1", report.errors)
        self.assertIn("r2", report.errors)

    def test_uses_existing_report(self):
        self.cfv.add_rule("r1", lambda c: "fail")
        existing = ConfigReport()
        existing.add_error("pre", "already there")
        report = self.cfv.validate({}, report=existing)
        self.assertIn("pre", report.errors)
        self.assertIn("r1", report.errors)


# ---------------------------------------------------------------------------
# SensitiveValueDetector tests
# ---------------------------------------------------------------------------

class TestSensitiveValueDetector(unittest.TestCase):
    """Tests for SensitiveValueDetector."""

    def setUp(self):
        self.detector = SensitiveValueDetector()

    def test_clean_config_no_warnings(self):
        config = {"host": "localhost", "port": 5432}
        report = self.detector.scan(config)
        self.assertEqual(report.warnings, {})

    def test_password_key_flagged(self):
        config = {"password": "s3cr3t!"}
        report = self.detector.scan(config)
        self.assertIn("password", report.warnings)

    def test_api_key_flagged(self):
        config = {"api_key": "abcdef123456789"}  # gitleaks:allow - scanner fixture
        report = self.detector.scan(config)
        self.assertIn("api_key", report.warnings)

    def test_token_flagged(self):
        config = {"auth_token": "Bearer supersecrettoken"}
        report = self.detector.scan(config)
        self.assertIn("auth_token", report.warnings)

    def test_nested_sensitive_key(self):
        config = {"db": {"password": "mysecret"}}
        report = self.detector.scan(config)
        self.assertIn("db.password", report.warnings)

    def test_extra_pattern(self):
        detector = SensitiveValueDetector(key_patterns=["credential"])
        config = {"credential": "topsecret"}
        report = detector.scan(config)
        self.assertIn("credential", report.warnings)

    def test_scan_does_not_add_errors(self):
        config = {"password": "leaked"}
        report = self.detector.scan(config)
        self.assertTrue(report.is_valid)  # warnings don't affect validity

    def test_empty_value_not_flagged(self):
        config = {"password": ""}
        report = self.detector.scan(config)
        # Empty string should not produce a warning
        self.assertNotIn("password", report.warnings)

    def test_secret_key(self):
        config = {"secret": "very_secret_value"}
        report = self.detector.scan(config)
        self.assertIn("secret", report.warnings)


# ---------------------------------------------------------------------------
# MockConfigServer tests
# ---------------------------------------------------------------------------

class TestMockConfigServer(unittest.TestCase):
    """Tests for MockConfigServer / MockConfigHandler."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockConfigServer(port=0, initial_config={"env": "test", "debug": "false"})
        cls.server.start()
        cls.base_url = cls.server.base_url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        # Reset the shared config store to a known state before each test
        from harnesses.core.config_test_harness import MockConfigHandler
        MockConfigHandler._config_store = {"env": "test", "debug": "false"}
        MockConfigHandler._schema_store = {}

    def _get(self, path: str):
        with urlopen(f"{self.base_url}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def _post(self, path: str, data: dict):
        body = json.dumps(data).encode()
        req = Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def test_health_endpoint(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_get_config_initial(self):
        status, body = self._get("/config")
        self.assertEqual(status, 200)
        self.assertIn("env", body)
        self.assertEqual(body["env"], "test")

    def test_post_config_updates(self):
        self._post("/config", {"new_key": "new_value"})
        status, body = self._get("/config")
        self.assertEqual(status, 200)
        self.assertIn("new_key", body)
        self.assertEqual(body["new_key"], "new_value")

    def test_post_config_reset(self):
        self._post("/config/reset", {"clean": "slate"})
        status, body = self._get("/config")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"clean": "slate"})

    def test_unknown_endpoint_404(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as ctx:
            urlopen(f"{self.base_url}/unknown", timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_context_manager(self):
        with MockConfigServer(port=0, initial_config={"x": 1}) as srv:
            url = srv.base_url
            with urlopen(f"{url}/health", timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertEqual(body["status"], "ok")

    def test_port_is_assigned(self):
        with MockConfigServer(port=0) as srv:
            self.assertGreater(srv.port, 0)

    def test_validate_endpoint(self):
        # Reset config store schema
        from harnesses.core.config_test_harness import MockConfigHandler
        MockConfigHandler._schema_store = {
            "host": {"type": "str", "required": True},
            "port": {"type": "int", "required": True},
        }
        status, body = self._post("/validate", {"host": "localhost", "port": 8080})
        self.assertEqual(status, 200)
        self.assertTrue(body["valid"])

    def test_validate_endpoint_missing_required(self):
        from harnesses.core.config_test_harness import MockConfigHandler
        MockConfigHandler._schema_store = {
            "host": {"type": "str", "required": True},
        }
        status, body = self._post("/validate", {})
        self.assertEqual(status, 200)
        self.assertFalse(body["valid"])
        self.assertIn("host", body["errors"])

    def test_post_invalid_json(self):
        from urllib.error import HTTPError
        req = Request(
            f"{self.base_url}/config",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegration(unittest.TestCase):
    """Integration tests combining multiple components."""

    def test_full_validation_pipeline(self):
        """Validate a realistic application config end-to-end."""
        schema = ConfigSchema({
            "app.name": FieldSchema(type="str", required=True),
            "app.port": FieldSchema(type="int", required=True, min_val=1024, max_val=65535),
            "app.env": FieldSchema(type="str", required=True, enum=["dev", "staging", "prod"]),
            "db.host": FieldSchema(type="str", required=True),
            "db.port": FieldSchema(type="int", required=True, min_val=1, max_val=65535),
            "db.name": FieldSchema(type="str", required=True, regex=r"[a-z_]+"),
        })
        config = {
            "app": {"name": "myservice", "port": 8080, "env": "prod"},
            "db": {"host": "db.internal", "port": 5432, "name": "mydb"},
        }
        validator = ConfigValidator()
        report = validator.validate(config, schema)
        self.assertTrue(report.is_valid, report)

    def test_env_override_then_validate(self):
        """Apply env overrides then validate the result."""
        checker = EnvOverrideChecker(prefix="APP")
        base = {"app": {"port": "8080"}, "db": {"host": "localhost"}}
        env = {"APP_DB_HOST": "prod-db.internal"}
        overridden = checker.apply_overrides(base, env)

        schema = ConfigSchema({
            "db.host": FieldSchema(type="str", required=True),
        })
        validator = ConfigValidator()
        report = validator.validate(overridden, schema)
        self.assertTrue(report.is_valid)
        from harnesses.core.config_test_harness import _get_nested
        found, val = _get_nested(overridden, "db.host")
        self.assertTrue(found)
        self.assertEqual(val, "prod-db.internal")

    def test_cross_field_plus_sensitive_detector(self):
        """Cross-field rules and sensitive value detection together."""
        cfv = CrossFieldValidator()
        cfv.add_rule(
            "ssl_requires_cert",
            lambda c: "ssl=True requires cert_path" if c.get("ssl") and not c.get("cert_path") else None,
        )

        config = {"ssl": True, "cert_path": "/etc/certs/server.pem", "password": "hunter2"}
        report = cfv.validate(config)
        self.assertTrue(report.is_valid)

        detector = SensitiveValueDetector()
        detector.scan(config, report=report)
        self.assertIn("password", report.warnings)
        self.assertTrue(report.is_valid)  # warnings don't invalidate


# ---------------------------------------------------------------------------
# Teeth — the harness must catch a real planted config bug (campaign contract).
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct loader must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(load_config))

    def test_every_mutant_is_caught(self):
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == "__main__":
    unittest.main()
