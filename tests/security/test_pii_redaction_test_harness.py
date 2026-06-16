"""test_pii_redaction_test_harness.py â€” unittest suite for pii_redaction_test_harness (45)."""

import unittest

from harnesses.security.pii_redaction_test_harness import (
    RedactionOracle,
    Redactor,
    _luhn_ok,
    run_all_scenarios,
)


class TestDetection(unittest.TestCase):

    def setUp(self):
        self.r = Redactor(mode="label")

    def test_ssn(self):
        self.assertIn("[SSN]", self.r.redact("ssn 123-45-6789"))

    def test_email(self):
        self.assertIn("[EMAIL]", self.r.redact("mail a.b@c.io"))

    def test_phone_formats(self):
        for s in ["(555) 123-4567", "555-123-4567", "+1 555 123 4567"]:
            self.assertIn("[PHONE]", self.r.redact("call " + s), s)

    def test_valid_card(self):
        self.assertIn("[CREDIT_CARD]", self.r.redact("4111 1111 1111 1111"))

    def test_mrn(self):
        self.assertIn("[MRN]", self.r.redact("chart MRN-000123"))

    def test_dob_iso_and_us(self):
        out = self.r.redact("1980-04-12 04/12/1980")
        self.assertEqual(out.count("[DOB]"), 2)


class TestOverRedactionGuards(unittest.TestCase):

    def setUp(self):
        self.r = Redactor(mode="label")

    def test_non_luhn_card_not_flagged(self):
        self.assertNotIn("[CREDIT_CARD]", self.r.redact("1234 5678 9012 3456"))

    def test_zip_untouched(self):
        out = self.r.redact("ZIP 90210 here")
        self.assertIn("90210", out)
        self.assertNotIn("[", out)

    def test_clean_text_unchanged(self):
        clean = "Nothing private: 7 dogs, 3 cats."
        self.assertEqual(self.r.redact(clean), clean)

    def test_luhn_helper(self):
        self.assertTrue(_luhn_ok("4111111111111111"))
        self.assertFalse(_luhn_ok("1234567890123456"))


class TestUnderRedactionAndIdempotency(unittest.TestCase):

    def setUp(self):
        self.r = Redactor(mode="label")

    def test_ssn_digits_gone(self):
        out = self.r.redact("123-45-6789")
        self.assertFalse(RedactionOracle.digit_run_survives("123-45-6789", out))

    def test_card_digits_gone(self):
        out = self.r.redact("4111 1111 1111 1111")
        self.assertFalse(RedactionOracle.digit_run_survives("4111111111111111", out))

    def test_idempotent(self):
        src = "SSN 123-45-6789 mail a@b.co ph 555-123-4567"
        once = self.r.redact(src)
        self.assertEqual(once, self.r.redact(once))

    def test_no_secret_survives(self):
        secrets = ["987-65-4321", "bob@x.org", "4111 1111 1111 1111"]
        para = f"{secrets[0]} {secrets[1]} {secrets[2]}"
        red = self.r.redact(para)
        for s in secrets:
            self.assertFalse(RedactionOracle.secret_survives(s, red), s)

    def test_adjacent_entities(self):
        out = self.r.redact("123-45-6789,jane@x.io")
        self.assertIn("[SSN]", out)
        self.assertIn("[EMAIL]", out)


class TestMaskMode(unittest.TestCase):

    def setUp(self):
        self.r = Redactor(mode="mask")

    def test_ssn_last4_kept(self):
        out = self.r.redact("123-45-6789")
        self.assertEqual(out, "***-**-6789")

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            Redactor(mode="nope")


class TestCounts(unittest.TestCase):

    def test_counts(self):
        r = Redactor()
        c = r.counts("jane@x.io 555-123-4567 123-45-6789 MRN-9999")
        self.assertEqual(c.get("EMAIL"), 1)
        self.assertEqual(c.get("PHONE"), 1)
        self.assertEqual(c.get("SSN"), 1)
        self.assertEqual(c.get("MRN"), 1)


class TestSelfTest(unittest.TestCase):

    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(run_all_scenarios(verbose=False)), 14)


if __name__ == "__main__":
    unittest.main()
