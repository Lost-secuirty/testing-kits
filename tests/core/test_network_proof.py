import unittest

from harnesses.core import network_test_harness as harness


class TestNetworkProof(unittest.TestCase):
    def test_oracle_matches_frozen_network_events(self):
        for case in harness.NETWORK_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_network_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_network_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.NETWORK_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_network_defects_have_traps(self):
        cases = {case.name: case for case in harness.NETWORK_AUDIT_CORPUS}
        self.assertNotEqual(
            harness.ignores_content_length(cases["content_length_mismatch"]),
            cases["content_length_mismatch"].expected_events,
        )
        self.assertNotEqual(
            harness.linear_backoff_planner(cases["retry_backoff_capped"]),
            cases["retry_backoff_capped"].expected_events,
        )
        self.assertNotEqual(
            harness.unbounded_pool_planner(cases["pool_denies_over_capacity"]),
            cases["pool_denies_over_capacity"].expected_events,
        )

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.NETWORK_AUDIT_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
