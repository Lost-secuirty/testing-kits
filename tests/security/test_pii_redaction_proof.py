import unittest

from harnesses.security import pii_redaction_test_harness as harness
from tests.proof_helpers import assert_mutants_differ, assert_oracle_matches, assert_teeth_contract


class TestPiiRedactionProof(unittest.TestCase):
    def test_oracle_matches_frozen_pii_cases(self):
        assert_oracle_matches(self, harness.PII_AUDIT_CORPUS, harness.oracle_pii_audit)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        assert_teeth_contract(self, harness, harness.PII_AUDIT_CORPUS)

    def test_planted_redaction_defects_have_traps(self):
        assert_mutants_differ(self, harness.PII_AUDIT_CORPUS, (
            ("mixed_entities_are_detected_and_removed", harness.ssn_blind_pii_auditor),
            ("mask_mode_hides_full_ssn_digit_run", harness.digit_leak_pii_auditor),
            ("non_luhn_order_number_is_not_card_redacted", harness.luhn_blind_overredacts_pii_auditor),
            ("mixed_entities_are_detected_and_removed", harness.non_idempotent_pii_auditor),
        ))


if __name__ == "__main__":
    unittest.main()
