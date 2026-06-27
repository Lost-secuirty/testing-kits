import unittest

from harnesses.security import jwt_test_harness as harness
from tests.proof_helpers import assert_mutants_differ, assert_oracle_matches, assert_teeth_contract


class TestJwtProof(unittest.TestCase):
    def test_oracle_matches_frozen_jwt_cases(self):
        assert_oracle_matches(self, harness.JWT_AUDIT_CORPUS, harness.oracle_jwt_audit)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        assert_teeth_contract(self, harness, harness.JWT_AUDIT_CORPUS)

    def test_planted_jwt_bypasses_have_traps(self):
        assert_mutants_differ(self, harness.JWT_AUDIT_CORPUS, (
            ("alg_none_token_is_rejected", harness.alg_none_accepting_jwt_auditor),
            ("alg_swap_outside_allowlist_is_rejected", harness.algorithm_allowlist_blind_jwt_auditor),
            ("tampered_payload_is_signature_mismatch", harness.signature_blind_jwt_auditor),
            ("expired_token_is_rejected", harness.time_claim_blind_jwt_auditor),
            ("missing_required_claim_is_rejected", harness.required_claim_blind_jwt_auditor),
        ))


if __name__ == "__main__":
    unittest.main()
