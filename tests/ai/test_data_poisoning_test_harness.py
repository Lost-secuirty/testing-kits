"""Test suite for data_poisoning_test_harness (OWASP LLM04:2025)."""

import contextlib
import io
import json
import unittest

from harnesses.ai.data_poisoning_test_harness import (
    CORPUS,
    TEETH,
    IngestCase,
    _bug_blank_signature_counts_as_signed,
    _bug_dedup_off_by_one,
    _bug_label_mismatch_one_direction,
    _bug_trigger_exact_equality,
    _bug_trust_waives_content_scan,
    _has_trigger,
    _is_flood,
    _label_mismatch,
    _run_self_test,
    _unsigned,
    _untrusted_source,
    list_scenarios,
    main,
    oracle_poison_audit,
    prove,
)


class TestPredicates(unittest.TestCase):
    def test_untrusted_source(self):
        self.assertFalse(_untrusted_source({"source": "curated-internal"}))
        self.assertTrue(_untrusted_source({"source": "anon-forum-dump"}))

    def test_unsigned_treats_blank_as_missing(self):
        self.assertTrue(_unsigned({"signature": "   "}))
        self.assertTrue(_unsigned({}))
        self.assertFalse(_unsigned({"signature": "ed25519:x"}))

    def test_has_trigger_is_embedded_not_full_string(self):
        self.assertTrue(_has_trigger("hello cf-activate-7 world"))
        self.assertFalse(_has_trigger("hello world"))

    def test_label_mismatch_both_directions(self):
        self.assertTrue(_label_mismatch({"label": "safe", "content": "what a scam"}))
        self.assertTrue(_label_mismatch({"label": "toxic", "content": "I love this"}))
        self.assertFalse(_label_mismatch({"label": "safe", "content": "plain fact"}))

    def test_is_flood_threshold(self):
        self.assertTrue(_is_flood({"dup_cluster": 4}))
        self.assertFalse(_is_flood({"dup_cluster": 3}))


class TestOracle(unittest.TestCase):
    def test_corpus_expectations_match_oracle(self):
        for case in CORPUS:
            with self.subTest(case=case.name):
                self.assertEqual(oracle_poison_audit(case), case.expected_codes)

    def test_trust_does_not_waive_content_scan(self):
        trusted_trigger = next(c for c in CORPUS
                               if c.name == "trusted_signed_with_backdoor_trigger")
        self.assertIn("poison-backdoor-trigger", oracle_poison_audit(trusted_trigger))

    def test_mixed_case_accumulates_sorted(self):
        mixed = next(c for c in CORPUS if c.name == "untrusted_unsigned_trigger_flood_mixed")
        out = oracle_poison_audit(mixed)
        self.assertEqual(list(out), sorted(out))
        self.assertEqual(len(out), 4)

    def test_no_raise_on_garbage(self):
        self.assertIsInstance(oracle_poison_audit(IngestCase("g", "{}", ())), tuple)


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(oracle_poison_audit))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_trust_waives_content_scan, _bug_trigger_exact_equality,
                    _bug_label_mismatch_one_direction, _bug_dedup_off_by_one,
                    _bug_blank_signature_counts_as_signed):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("clean_trusted_signed", scenarios)
        self.assertIn("trust_waives_content_scan", scenarios)
        self.assertGreaterEqual(len(scenarios), 9)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)

    def test_json_mode_is_machine_readable(self):
        for run in (lambda: _run_self_test(as_json=True), lambda: main(["--json"])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["harness"], "ai/data_poisoning")
            self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
