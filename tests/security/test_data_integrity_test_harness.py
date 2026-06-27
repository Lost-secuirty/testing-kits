"""Test suite for data_integrity_test_harness (OWASP A08:2025)."""

import unittest

from harnesses.security.data_integrity_test_harness import (
    CORPUS,
    TEETH,
    IntegrityCase,
    _autoupdate_unverified,
    _bug_autoupdate_blind,
    _bug_ignores_unsafe_deserialization,
    _bug_present_checksum_counts_as_strong,
    _bug_skip_signature_gate,
    _bug_trusts_all_sources,
    _is_blank,
    _run_self_test,
    _untrusted_source,
    list_scenarios,
    oracle_integrity_audit,
    prove,
)


class TestHelpers(unittest.TestCase):
    def test_is_blank(self):
        self.assertTrue(_is_blank(None))
        self.assertTrue(_is_blank(""))
        self.assertTrue(_is_blank("   "))
        self.assertFalse(_is_blank("ed25519:x"))

    def test_untrusted_source(self):
        self.assertTrue(_untrusted_source("http://1.2.3.4/x"))
        self.assertTrue(_untrusted_source("https://evil.example.test/x"))
        self.assertTrue(_untrusted_source("not-a-url"))
        self.assertFalse(_untrusted_source("https://updates.example-trusted.test/v2"))

    def test_autoupdate_unverified(self):
        self.assertTrue(_autoupdate_unverified({"auto_update": True, "autoupdate_verify": False}))
        self.assertFalse(_autoupdate_unverified({"auto_update": True, "autoupdate_verify": True}))
        self.assertFalse(_autoupdate_unverified({"auto_update": False}))


class TestOracle(unittest.TestCase):
    def test_corpus_expectations_match_oracle(self):
        for case in CORPUS:
            with self.subTest(case=case.name):
                self.assertEqual(oracle_integrity_audit(case), case.expected_codes)

    def test_clean_record_has_no_findings(self):
        clean = next(c for c in CORPUS if c.name == "signed_strong_trusted_clean")
        self.assertEqual(oracle_integrity_audit(clean), ())

    def test_compound_findings_are_sorted(self):
        compound = next(c for c in CORPUS if c.name == "all_failures_compound")
        out = oracle_integrity_audit(compound)
        self.assertEqual(list(out), sorted(out))
        self.assertEqual(len(out), 4)

    def test_does_not_raise_on_garbage_record(self):
        # Malformed/partial records must degrade to findings, never raise.
        garbage = IntegrityCase("garbage", '{"kind":"update"}', ())
        self.assertIsInstance(oracle_integrity_audit(garbage), tuple)


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(oracle_integrity_audit))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_present_checksum_counts_as_strong, _bug_skip_signature_gate,
                    _bug_trusts_all_sources, _bug_ignores_unsafe_deserialization,
                    _bug_autoupdate_blind):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("signed_strong_trusted_clean", scenarios)
        self.assertIn("skip_signature_gate", scenarios)
        self.assertGreaterEqual(len(scenarios), 7)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
