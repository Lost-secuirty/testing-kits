import unittest

from harnesses.core import search_relevance_test_harness as harness


class TestSearchRelevanceProof(unittest.TestCase):
    def test_oracle_matches_frozen_search_relevance_cases(self):
        for case in harness.SEARCH_RELEVANCE_AUDIT_CORPUS:
            self.assertEqual(
                harness.oracle_search_relevance_audit(case),
                case.expected_events,
                case.name,
            )

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_search_relevance_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.SEARCH_RELEVANCE_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_search_relevance_defects_have_traps(self):
        cases = {case.name: case for case in harness.SEARCH_RELEVANCE_AUDIT_CORPUS}
        benchmark_case = cases["lexical_ranker_meets_relevance_floors"]
        analyzer_case = cases["analyzer_handles_fold_and_segmentation_cases"]
        tiebreak_case = cases["tie_break_orders_by_doc_id"]
        empty_case = cases["empty_query_returns_no_results"]

        self.assertNotEqual(
            harness.reversed_ranker_search_auditor(benchmark_case),
            benchmark_case.expected_events,
        )
        self.assertNotEqual(
            harness.no_fold_search_auditor(analyzer_case),
            analyzer_case.expected_events,
        )
        self.assertNotEqual(
            harness.unstable_tiebreak_search_auditor(tiebreak_case),
            tiebreak_case.expected_events,
        )
        self.assertNotEqual(
            harness.empty_query_returns_all_auditor(empty_case),
            empty_case.expected_events,
        )


if __name__ == "__main__":
    unittest.main()
