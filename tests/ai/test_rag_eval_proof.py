import unittest

from harnesses.ai import rag_eval_test_harness as harness
from tests.proof_helpers import assert_mutants_differ, assert_oracle_matches, assert_teeth_contract


class TestRagEvalProof(unittest.TestCase):
    def test_oracle_matches_frozen_rag_cases(self):
        assert_oracle_matches(self, harness.RAG_AUDIT_CORPUS, harness.oracle_rag_audit)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        assert_teeth_contract(self, harness, harness.RAG_AUDIT_CORPUS)

    def test_planted_rag_defects_have_traps(self):
        assert_mutants_differ(self, harness.RAG_AUDIT_CORPUS, (
            ("retrieval_pipeline_meets_rag_floors", harness.keyword_only_rag_auditor),
            ("citation_fabrication_is_counted", harness.citation_fabrication_blind_rag_auditor),
            ("overflow_drops_tail_claim_context", harness.overflow_blind_rag_auditor),
            ("empty_retrieval_scores_zero_recall", harness.empty_retrieval_invents_hit_rag_auditor),
        ))


if __name__ == "__main__":
    unittest.main()
