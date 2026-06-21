"""test_misconfig_test_harness.py — unittest suite."""

import unittest

from harnesses.security.misconfig_test_harness import (
    CookieFlagChecker,
    CORSChecker,
    DebugModeChecker,
    DefaultCredChecker,
    FilePermissionChecker,
    list_scenarios,
    run_all_scenarios,
)


class TestDebugModeChecker(unittest.TestCase):
    def setUp(self):
        self.c = DebugModeChecker()

    def test_debug_off_clean(self):
        self.assertEqual(self.c.check({"DEBUG": False}), [])

    def test_debug_on_flagged(self):
        self.assertTrue(self.c.check({"DEBUG": True}))

    def test_dev_env_flagged(self):
        self.assertTrue(self.c.check({"ENV": "development"}))


class TestCORSChecker(unittest.TestCase):
    def setUp(self):
        self.c = CORSChecker()

    def test_explicit_origin_accepted(self):
        self.assertFalse(self.c.check("https://app.example.com", True)[0])

    def test_wildcard_with_credentials_flagged(self):
        flagged, _ = self.c.check("*", True)
        self.assertTrue(flagged)
        self.assertEqual(self.c.severity("*", True), "CRITICAL")

    def test_wildcard_alone_flagged(self):
        self.assertTrue(self.c.check("*")[0])

    def test_null_origin_flagged(self):
        self.assertTrue(self.c.check("null")[0])


class TestDefaultCredChecker(unittest.TestCase):
    def setUp(self):
        self.c = DefaultCredChecker()

    def test_strong_creds_clean(self):
        self.assertEqual(self.c.scan({"admin": "S3cur3!longpw"}), [])

    def test_default_creds_flagged(self):
        self.assertTrue(self.c.scan({"admin": "admin"}))

    def test_uppercase_user_default_flagged(self):
        self.assertTrue(self.c.scan({"Admin": "admin"}))

    def test_empty_password_flagged(self):
        self.assertTrue(self.c.scan({"svc": ""}))


class TestCookieFlagChecker(unittest.TestCase):
    def setUp(self):
        self.c = CookieFlagChecker()

    def test_hardened_clean(self):
        self.assertEqual(self.c.check("sid=abc; Secure; HttpOnly; SameSite=Strict"), [])

    def test_flagless_flagged(self):
        self.assertTrue(self.c.check("sid=abc; Path=/"))


class TestFilePermissionChecker(unittest.TestCase):
    def setUp(self):
        self.c = FilePermissionChecker()

    def test_600_secret_accepted(self):
        self.assertFalse(self.c.check(0o600, is_secret=True)[0])

    def test_world_writable_flagged(self):
        self.assertTrue(self.c.check(0o666)[0])

    def test_secret_readable_flagged(self):
        self.assertTrue(self.c.check(0o644, is_secret=True)[0])

    def test_secret_group_readable_flagged(self):
        self.assertTrue(self.c.check(0o640, is_secret=True)[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 14)


if __name__ == "__main__":
    unittest.main()
