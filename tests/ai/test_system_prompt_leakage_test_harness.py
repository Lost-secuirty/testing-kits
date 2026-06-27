"""Test suite for system_prompt_leakage_test_harness (OWASP LLM07:2025)."""

import unittest

from harnesses.ai.system_prompt_leakage_test_harness import (
    CORPUS,
    FROZEN_EXPECTED,
    TEETH,
    LeakageCase,
    _bug_filter_case_sensitive,
    _bug_persona_overzealous,
    _bug_role_marker_overfit,
    _bug_rule_keyword_too_narrow,
    _bug_secret_requires_assignment,
    _has_internal_rule,
    _has_role,
    _has_secret,
    _run_self_test,
    list_scenarios,
    oracle_leakage_audit,
    prove,
)


class TestDetectors(unittest.TestCase):
    def test_secret_in_prose(self):
        self.assertTrue(_has_secret("use the key AKIAIOSFODNN7EXAMPLE now"))  # allowlist secret (synthetic fixture)
        self.assertTrue(_has_secret("postgres://u:p@host:5432/db"))
        self.assertFalse(_has_secret("a perfectly benign sentence"))

    def test_internal_rule_threshold(self):
        self.assertTrue(_has_internal_rule("approve transactions under $10,000 automatically"))
        self.assertFalse(_has_internal_rule("be helpful and concise"))

    def test_role_phrased_differently(self):
        self.assertTrue(_has_role("the superuser role can modify any user record"))
        self.assertFalse(_has_role("you are a travel assistant"))


class TestOracle(unittest.TestCase):
    def test_corpus_expectations_match_oracle(self):
        for case in CORPUS:
            with self.subTest(case=case.name):
                self.assertEqual(oracle_leakage_audit(case), case.expected)

    def test_clean_cases_have_no_flags(self):
        for name in ("output_clean_answer", "sysprompt_clean_policy"):
            case = next(c for c in CORPUS if c.name == name)
            self.assertEqual(oracle_leakage_audit(case), ())

    def test_leaked_instruction_is_output_only(self):
        # The same persona phrasing in a sysprompt is design, not a leak.
        sysprompt = LeakageCase("x", "sysprompt", "You are an internal bot.", ())
        output = LeakageCase("y", "output", "You are an internal bot.", ())
        self.assertNotIn("leaked_instruction", oracle_leakage_audit(sysprompt))
        self.assertIn("leaked_instruction", oracle_leakage_audit(output))

    def test_multi_failure_sorted(self):
        case = next(c for c in CORPUS if c.name == "sysprompt_multi_failure")
        out = oracle_leakage_audit(case)
        self.assertEqual(list(out), sorted(out))
        self.assertEqual(len(out), 3)

    def test_frozen_witness_matches(self):
        for name, expected in FROZEN_EXPECTED.items():
            case = next(c for c in CORPUS if c.name == name)
            self.assertEqual(oracle_leakage_audit(case), expected)


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(oracle_leakage_audit))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_secret_requires_assignment, _bug_rule_keyword_too_narrow,
                    _bug_filter_case_sensitive, _bug_role_marker_overfit,
                    _bug_persona_overzealous):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_persona_mutant_false_positives_on_clean(self):
        clean = next(c for c in CORPUS if c.name == "output_clean_answer")
        self.assertIn("leaked_instruction", _bug_persona_overzealous(clean))

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("sysprompt_secret_in_prose", scenarios)
        self.assertIn("persona_overzealous", scenarios)
        self.assertGreaterEqual(len(scenarios), 9)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
