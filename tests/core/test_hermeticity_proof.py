import unittest

from harnesses.core import hermeticity_test_harness as harness


class TestHermeticityProof(unittest.TestCase):
    def test_oracle_matches_frozen_hermeticity_observations(self):
        for case in harness.HERMETICITY_AUDIT_CORPUS:
            self.assertEqual(
                harness.oracle_hermeticity_audit(case),
                case.expected_contaminating,
                case.name,
            )

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_hermeticity_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.HERMETICITY_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_hermeticity_defects_have_traps(self):
        cases = {case.name: case for case in harness.HERMETICITY_AUDIT_CORPUS}
        clean_case = cases["pure_test_has_stable_observations"]
        mixed_case = cases["random_env_and_home_dependencies_change_outcome"]

        self.assertNotEqual(
            harness.baseline_only_hermeticity_auditor(mixed_case),
            mixed_case.expected_contaminating,
        )
        self.assertNotEqual(
            harness.time_random_only_hermeticity_auditor(mixed_case),
            mixed_case.expected_contaminating,
        )
        self.assertNotEqual(
            harness.noisy_hermeticity_auditor(clean_case),
            clean_case.expected_contaminating,
        )


if __name__ == "__main__":
    unittest.main()
