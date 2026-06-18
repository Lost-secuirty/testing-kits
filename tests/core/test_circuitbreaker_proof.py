import unittest

from harnesses.core import circuitbreaker_test_harness as harness
from tests.proof_helpers import assert_mutants_differ, assert_oracle_matches, assert_teeth_contract


class TestCircuitBreakerProof(unittest.TestCase):
    def test_oracle_matches_frozen_event_logs(self):
        assert_oracle_matches(self, harness.CIRCUIT_BREAKER_AUDIT_CORPUS, harness.oracle_circuitbreaker_audit)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        assert_teeth_contract(self, harness, harness.CIRCUIT_BREAKER_AUDIT_CORPUS)

    def test_planted_transition_defects_have_traps(self):
        assert_mutants_differ(self, harness.CIRCUIT_BREAKER_AUDIT_CORPUS, (
            ("opens_at_failure_threshold", harness.threshold_one_late_circuit_auditor),
            ("success_resets_consecutive_failures", harness.success_reset_blind_circuit_auditor),
            ("half_open_failure_retrips_open", harness.half_open_failure_closes_circuit_auditor),
            ("half_open_trial_cap_blocks_second_probe", harness.half_open_cap_ignored_circuit_auditor),
            ("open_state_rejects_before_timeout", harness.open_window_blind_circuit_auditor),
        ))


if __name__ == "__main__":
    unittest.main()
