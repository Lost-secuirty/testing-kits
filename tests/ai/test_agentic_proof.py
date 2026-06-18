import unittest

from harnesses.ai import agentic_test_harness as harness


class TestAgenticProof(unittest.TestCase):
    def test_oracle_matches_frozen_agentic_events(self):
        for case in harness.AGENTIC_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_agentic_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_agentic_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.AGENTIC_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_agentic_defects_have_traps(self):
        cases = {case.name: case for case in harness.AGENTIC_AUDIT_CORPUS}
        self.assertNotEqual(
            harness.name_only_fidelity_auditor(cases["missing_required_arg"]),
            cases["missing_required_arg"].expected_events,
        )
        self.assertNotEqual(
            harness.loop_blind_auditor(cases["repeated_loop"]),
            cases["repeated_loop"].expected_events,
        )
        self.assertNotEqual(
            harness.schema_drift_blind_auditor(cases["schema_drift"]),
            cases["schema_drift"].expected_events,
        )
        self.assertNotEqual(
            harness.unsafe_blind_auditor(cases["unsafe_without_guard"]),
            cases["unsafe_without_guard"].expected_events,
        )

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.AGENTIC_AUDIT_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
