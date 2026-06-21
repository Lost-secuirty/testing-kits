"""test_crypto_test_harness.py — unittest suite for crypto_test_harness."""

import unittest

from harnesses.security.crypto_test_harness import (
    CipherChecker,
    HardcodedSecretScanner,
    PasswordHashChecker,
    RandomnessChecker,
    TLSConfigChecker,
    list_scenarios,
    run_all_scenarios,
)


class TestPasswordHashChecker(unittest.TestCase):
    def setUp(self):
        self.c = PasswordHashChecker()

    def test_argon2_accepted(self):
        weak, _ = self.c.check("argon2id", salted=True)
        self.assertFalse(weak)

    def test_bcrypt_accepted(self):
        self.assertFalse(self.c.check("bcrypt", salted=True)[0])

    def test_md5_flagged(self):
        weak, reason = self.c.check("md5", salted=False)
        self.assertTrue(weak)
        self.assertIn("CWE-327", reason)

    def test_unsalted_sha256_flagged(self):
        weak, reason = self.c.check("sha256", salted=False)
        self.assertTrue(weak)
        self.assertIn("unsalted", reason)

    def test_pbkdf2_low_iterations_flagged(self):
        self.assertTrue(self.c.check("pbkdf2", iterations=1000)[0])

    def test_pbkdf2_high_iterations_accepted(self):
        self.assertFalse(self.c.check("pbkdf2", iterations=200_000)[0])

    def test_unknown_algorithm_is_unsafe(self):
        self.assertTrue(self.c.check("rot13")[0])


class TestRandomnessChecker(unittest.TestCase):
    def setUp(self):
        self.c = RandomnessChecker()

    def test_secrets_accepted(self):
        self.assertFalse(self.c.check_source("secrets.token_hex")[0])

    def test_os_urandom_accepted(self):
        self.assertFalse(self.c.check_source("os.urandom")[0])

    def test_random_flagged(self):
        self.assertTrue(self.c.check_source("random.randint")[0])

    def test_time_based_flagged(self):
        self.assertTrue(self.c.check_source("time.time")[0])

    def test_short_token_flagged(self):
        self.assertTrue(self.c.check_token_entropy(b"\x01\x02", min_bits=128)[0])

    def test_adequate_token_accepted(self):
        self.assertFalse(self.c.check_token_entropy(bytes(range(20)), min_bits=128)[0])

    def test_constant_token_flagged(self):
        self.assertTrue(self.c.check_token_entropy(b"\x00" * 32, min_bits=128)[0])


class TestCipherChecker(unittest.TestCase):
    def setUp(self):
        self.c = CipherChecker()

    def test_aes_gcm_accepted(self):
        self.assertFalse(self.c.check("AES", "GCM")[0])

    def test_ecb_flagged(self):
        self.assertTrue(self.c.check("AES", "ECB")[0])

    def test_des_flagged(self):
        self.assertTrue(self.c.check("DES", "CBC")[0])

    def test_rc4_flagged(self):
        self.assertTrue(self.c.check("RC4")[0])

    def test_reused_iv_flagged(self):
        self.assertTrue(self.c.check("AES", "CTR", iv_reused=True)[0])


class TestHardcodedSecretScanner(unittest.TestCase):
    def setUp(self):
        self.c = HardcodedSecretScanner()

    def test_aws_key_flagged(self):
        findings = self.c.scan('API_KEY = "AKIAIOSFODNN7EXAMPLE"')
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, "CRITICAL")

    def test_private_key_flagged(self):
        findings = self.c.scan("-----BEGIN RSA PRIVATE KEY-----\nMIIxxx\n")
        self.assertTrue(findings)

    def test_password_literal_flagged(self):
        self.assertTrue(self.c.scan('password = "hunter2hunter2"'))

    def test_env_sourced_clean(self):
        self.assertEqual(self.c.scan('API_KEY = os.environ["API_KEY"]'), [])

    def test_empty_clean(self):
        self.assertEqual(self.c.scan(""), [])


class TestTLSConfigChecker(unittest.TestCase):
    def setUp(self):
        self.c = TLSConfigChecker()

    def test_verified_accepted(self):
        self.assertFalse(self.c.check(verify=True, check_hostname=True)[0])

    def test_verify_false_flagged(self):
        self.assertTrue(self.c.check(verify=False)[0])

    def test_cert_none_flagged(self):
        self.assertTrue(self.c.check(cert_reqs="CERT_NONE")[0])

    def test_hostname_off_flagged(self):
        self.assertTrue(self.c.check(check_hostname=False)[0])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 18)


if __name__ == "__main__":
    unittest.main()
