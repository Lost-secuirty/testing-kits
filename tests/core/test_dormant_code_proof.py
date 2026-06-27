import unittest

from harnesses.core import dormant_code_test_harness as harness


class TestDormantCodeProof(unittest.TestCase):
    def test_oracle_matches_frozen_dormant_cases(self):
        for case in harness.DORMANT_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_dormant_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_dormant_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.DORMANT_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_dormant_code_defects_have_traps(self):
        cases = {case.name: case for case in harness.DORMANT_AUDIT_CORPUS}
        crash_case = cases["synthetic_input_reaches_crashing_branch"]
        missed_case = cases["synthetic_driver_misses_remaining_branch"]

        self.assertNotEqual(
            harness.baseline_only_dormant_auditor(crash_case),
            crash_case.expected_events,
        )
        self.assertNotEqual(
            harness.crash_blind_dormant_auditor(crash_case),
            crash_case.expected_events,
        )
        self.assertNotEqual(
            harness.overcovered_dormant_auditor(missed_case),
            missed_case.expected_events,
        )


if __name__ == "__main__":
    unittest.main()
