import unittest

from harnesses.core import complexity_test_harness as harness
from tests.proof_helpers import assert_mutants_differ, assert_oracle_matches, assert_teeth_contract


class TestComplexityProof(unittest.TestCase):
    def test_oracle_matches_frozen_complexity_cases(self):
        assert_oracle_matches(self, harness.COMPLEXITY_AUDIT_CORPUS, harness.oracle_complexity_audit)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        assert_teeth_contract(self, harness, harness.COMPLEXITY_AUDIT_CORPUS)

    def test_planted_complexity_defects_have_traps(self):
        assert_mutants_differ(self, harness.COMPLEXITY_AUDIT_CORPUS, (
            ("cognitive_and_nesting_thresholds_flag", harness.cyclomatic_only_complexity_auditor),
            ("length_threshold_flags_bloat", harness.length_blind_complexity_auditor),
            ("parameter_threshold_flags_wide_signature", harness.params_blind_complexity_auditor),
            ("nested_code_is_cognitively_harder_than_flat_code", harness.nesting_blind_complexity_auditor),
        ))


if __name__ == "__main__":
    unittest.main()
