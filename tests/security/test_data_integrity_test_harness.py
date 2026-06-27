"""Test suite for data_integrity_test_harness (OWASP A08:2025)."""

import contextlib
import io
import json
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
    main,
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

    def test_untrusted_source_resists_authority_spoof(self):
        # Real host is evil.com, not the trusted name in the userinfo segment.
        self.assertTrue(
            _untrusted_source("https://updates.example-trusted.test:8443@evil.com/x"))
        # Mixed-case scheme on a genuinely trusted host stays trusted.
        self.assertFalse(_untrusted_source("HTTPS://updates.example-trusted.test/v2"))

    def test_autoupdate_unverified(self):
        self.assertTrue(_autoupdate_unverified({"auto_update": True, "autoupdate_verify": False}))
        self.assertFalse(_autoupdate_unverified({"auto_update": True, "autoupdate_verify": True}))
        self.assertFalse(_autoupdate_unverified({"auto_update": False}))

    def test_autoupdate_requires_dedicated_control(self):
        # The dedicated autoupdate_verify control is REQUIRED: a missing field is
        # unverified, and verify_signature on the artifact does not substitute for it.
        self.assertTrue(_autoupdate_unverified({"auto_update": True}))
        self.assertTrue(
            _autoupdate_unverified({"auto_update": True, "verify_signature": True}))


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
        self.assertEqual(
            oracle_integrity_audit(garbage),
            (
                "integrity-no-checksum",
                "integrity-unsigned",
                "integrity-untrusted-source",
            ),
        )

    def test_autoupdate_missing_control_flags(self):
        # A signed/checksummed/trusted artifact with auto_update on but no
        # autoupdate_verify must still flag the unverified update channel.
        case = next(c for c in CORPUS if c.name == "autoupdate_missing_channel_control")
        self.assertEqual(oracle_integrity_audit(case), ("integrity-autoupdate-unverified",))


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(oracle_integrity_audit))

    def test_mutants_wired_exactly(self):
        # Pin the wiring: dropping a planted mutant from TEETH must fail here, not
        # silently shrink the proof surface.
        self.assertEqual(
            {mutant.impl for mutant in TEETH.mutants},
            {
                _bug_present_checksum_counts_as_strong,
                _bug_skip_signature_gate,
                _bug_trusts_all_sources,
                _bug_ignores_unsafe_deserialization,
                _bug_autoupdate_blind,
            },
        )

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

    def test_json_mode_is_machine_readable(self):
        for run in (lambda: _run_self_test(as_json=True), lambda: main(["--json"])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["harness"], "security/data_integrity")
            self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
