import unittest

from harnesses.core import mutation_test_harness as harness


class TestMutationProof(unittest.TestCase):
    def test_oracle_matches_frozen_mutation_summaries(self):
        for case in harness.MUTATION_ANALYSIS_CORPUS:
            actual = harness.oracle_mutation_analyze(case)
            self.assertEqual(actual.total, case.expected_total, case.name)
            self.assertEqual(actual.killed, case.expected_killed, case.name)
            self.assertEqual(actual.survived, case.expected_survived, case.name)
            self.assertEqual(actual.errors, 0, case.name)
            self.assertEqual(actual.timeouts, 0, case.name)
            self.assertEqual(actual.mutation_score, case.expected_score, case.name)
            self.assertEqual(actual.descriptions, case.expected_descriptions, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_mutation_analyze))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.MUTATION_ANALYSIS_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_surviving_mutant_case_is_not_counted_as_killed(self):
        case = next(
            case
            for case in harness.MUTATION_ANALYSIS_CORPUS
            if case.name == "survivor_from_untested_function"
        )
        actual = harness.oracle_mutation_analyze(case)
        buggy = harness.survivors_counted_as_killed(case)
        self.assertEqual(actual.survived, 1)
        self.assertEqual(buggy.survived, 0)
        self.assertGreater(buggy.killed, actual.killed)

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.MUTATION_ANALYSIS_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
