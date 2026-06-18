import unittest

from harnesses.core import a11y_test_harness as harness


class TestA11yProof(unittest.TestCase):
    def test_oracle_matches_frozen_a11y_events(self):
        for case in harness.A11Y_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_a11y_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_a11y_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.A11Y_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_a11y_defects_have_traps(self):
        cases = {case.name: case for case in harness.A11Y_AUDIT_CORPUS}
        self.assertNotEqual(
            harness.alt_blind_auditor(cases["missing_alt"]),
            cases["missing_alt"].expected_events,
        )
        self.assertNotEqual(
            harness.label_blind_auditor(cases["unlabeled_input"]),
            cases["unlabeled_input"].expected_events,
        )
        self.assertNotEqual(
            harness.contrast_blind_auditor(cases["low_contrast"]),
            cases["low_contrast"].expected_events,
        )
        self.assertNotEqual(
            harness.aria_blind_auditor(cases["invalid_aria_and_hidden_focus"]),
            cases["invalid_aria_and_hidden_focus"].expected_events,
        )

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.A11Y_AUDIT_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
