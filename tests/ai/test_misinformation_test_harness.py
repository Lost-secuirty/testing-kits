"""Test suite for misinformation_test_harness (OWASP LLM09:2025)."""

import unittest

from harnesses.ai.misinformation_test_harness import (
    CORPUS,
    TEETH,
    AnswerCase,
    _bug_first_claim_only,
    _bug_flags_clean_as_unsupported,
    _bug_ignores_refutation,
    _bug_loose_overlap_support,
    _bug_trusts_citation_presence,
    _contradicts,
    _run_self_test,
    _supported,
    list_scenarios,
    oracle_misinformation_audit,
    prove,
)


class TestSupportPredicates(unittest.TestCase):
    def test_supported_allows_paraphrase(self):
        self.assertTrue(_supported("lake trout spawn in autumn",
                                   "in autumn, lake trout spawn in cold streams"))
        self.assertFalse(_supported("lake trout survive boiling water",
                                    "lake trout prefer cold deep water"))

    def test_contradicts_requires_negation_and_coverage(self):
        self.assertTrue(_contradicts("lake trout tolerate warm water",
                                     "lake trout do not tolerate warm water"))
        self.assertFalse(_contradicts("lake trout tolerate warm water",
                                      "lake trout tolerate warm water"))


class TestOracle(unittest.TestCase):
    def test_corpus_expectations_match_oracle(self):
        for case in CORPUS:
            with self.subTest(case=case.name):
                self.assertEqual(oracle_misinformation_audit(case), case.expected_findings)

    def test_fully_grounded_is_clean(self):
        case = next(c for c in CORPUS if c.name == "fully_grounded_answer")
        self.assertEqual(oracle_misinformation_audit(case), ())

    def test_uncited_claim_is_unsupported(self):
        case = AnswerCase("u", (("a wild claim", ""),), (), ("UNSUPPORTED_CLAIM",))
        self.assertEqual(oracle_misinformation_audit(case), ("UNSUPPORTED_CLAIM",))

    def test_findings_sorted_and_deduped(self):
        out = oracle_misinformation_audit(
            next(c for c in CORPUS if c.name == "contradicted_by_context"))
        self.assertEqual(list(out), sorted(out))


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(oracle_misinformation_audit))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_trusts_citation_presence, _bug_loose_overlap_support,
                    _bug_ignores_refutation, _bug_first_claim_only,
                    _bug_flags_clean_as_unsupported):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_clean_paraphrase_mutant_false_positives(self):
        clean = next(c for c in CORPUS if c.name == "fully_grounded_answer")
        self.assertIn("UNSUPPORTED_CLAIM", _bug_flags_clean_as_unsupported(clean))

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("fabricated_source_citation", scenarios)
        self.assertIn("ignores_refutation", scenarios)
        self.assertGreaterEqual(len(scenarios), 5)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
