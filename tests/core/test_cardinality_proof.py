import unittest

from harnesses.core import cardinality_test_harness as harness


class TestCardinalityProof(unittest.TestCase):
    def test_oracle_matches_frozen_cardinality_streams(self):
        for case in harness.CARDINALITY_AUDIT_CORPUS:
            self.assertEqual(
                harness.oracle_cardinality_audit(case),
                case.expected_reports,
                case.name,
            )

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_cardinality_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.CARDINALITY_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_cardinality_defects_have_traps(self):
        cases = {case.name: case for case in harness.CARDINALITY_AUDIT_CORPUS}
        bounded_case = cases["bounded_user_tier_metric_label"]
        unbounded_case = cases["unbounded_request_id_metric_label"]
        mixed_case = cases["mixed_route_template_and_session_id"]

        self.assertNotEqual(
            harness.first_value_only_cardinality_auditor(unbounded_case),
            unbounded_case.expected_reports,
        )
        self.assertNotEqual(
            harness.sample_count_cardinality_auditor(bounded_case),
            bounded_case.expected_reports,
        )
        self.assertNotEqual(
            harness.first_dimension_only_cardinality_auditor(mixed_case),
            mixed_case.expected_reports,
        )


if __name__ == "__main__":
    unittest.main()
