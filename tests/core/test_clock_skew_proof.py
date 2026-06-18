import unittest

from harnesses.core import clock_skew_test_harness as harness


class TestClockSkewProof(unittest.TestCase):
    def test_oracle_matches_frozen_clock_skew_events(self):
        for case in harness.CLOCK_SKEW_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_clock_skew_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_clock_skew_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.CLOCK_SKEW_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_clock_skew_defects_have_traps(self):
        cases = {case.name: case for case in harness.CLOCK_SKEW_AUDIT_CORPUS}
        self.assertNotEqual(
            harness.wall_clock_ttl_auditor(cases["ttl_jump_forward_safe_vs_unsafe"]),
            cases["ttl_jump_forward_safe_vs_unsafe"].expected_events,
        )
        self.assertNotEqual(
            harness.monotonic_blind_auditor(cases["monotonic_regression_detected"]),
            cases["monotonic_regression_detected"].expected_events,
        )
        self.assertNotEqual(
            harness.trusts_lww_outlier_auditor(cases["lww_implausible_skew_rejected"]),
            cases["lww_implausible_skew_rejected"].expected_events,
        )

    def test_list_teeth_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_teeth_scenarios(),
            [case.name for case in harness.CLOCK_SKEW_AUDIT_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
