"""Test suite for rag_eval_test_harness."""

import unittest

from harnesses.ai.rag_eval_test_harness import (
    CASES,
    CORPUS,
    SCENARIOS,
    Passage,
    RagConfig,
    RagReport,
    _run_self_test,
    citation_audit,
    context_overflow,
    evaluate,
    grounding_audit,
    keyword_only_retrieve,
    list_scenarios,
    recall_at_k,
    retrieve,
    truncating_retrieve,
)


class TestCorpusShape(unittest.TestCase):
    def test_corpus_at_least_20(self):
        self.assertGreaterEqual(len(CORPUS), 20)

    def test_cases_present(self):
        self.assertGreaterEqual(len(CASES), 6)

    def test_gold_ids_exist_in_corpus(self):
        ids = {p.doc_id for p in CORPUS}
        for case in CASES:
            for g in case.gold_doc_ids:
                self.assertIn(g, ids)

    def test_passage_token_count_autofills(self):
        self.assertEqual(Passage("x", "one two three").tokens, 3)


class TestRetrieverDeterminism(unittest.TestCase):
    def test_retrieve_deterministic(self):
        self.assertEqual(retrieve("ocean current", CORPUS, 5),
                         retrieve("ocean current", CORPUS, 5))

    def test_retrieve_dedups_by_id(self):
        dup = [Passage("d", "neural network"), Passage("d", "neural network")]
        self.assertEqual(len(retrieve("neural network", dup, 5)), 1)

    def test_empty_on_no_overlap(self):
        self.assertEqual(retrieve("zzz qqq", CORPUS, 5), [])


class TestMetricOracles(unittest.TestCase):
    def test_recall(self):
        self.assertEqual(recall_at_k(["a", "b"], {"a", "b"}, 5), 1.0)
        self.assertEqual(recall_at_k(["a"], {"a", "b"}, 5), 0.5)

    def test_citation_faithful(self):
        cmap = {p.doc_id: p for p in CORPUS}
        f, fab = citation_audit(("p0_a",), ["p0_a"], cmap, ("neural network training",))
        self.assertEqual(f, 1.0)
        self.assertEqual(fab, 0)

    def test_citation_fabricated(self):
        cmap = {p.doc_id: p for p in CORPUS}
        f, fab = citation_audit(("ghost",), [], cmap, ("neural network training",))
        self.assertEqual(fab, 1)
        self.assertLess(f, 1.0)

    def test_grounding_full_and_partial(self):
        cmap = {p.doc_id: p for p in CORPUS}
        g, _ = grounding_audit(("neural network training",), [cmap["p0_a"]])
        self.assertEqual(g, 1.0)
        g2, ung = grounding_audit(("neural network training", "moon cheese"), [cmap["p0_a"]])
        self.assertEqual(g2, 0.5)
        self.assertEqual(ung, 1)

    def test_grounding_hallucinated_is_zero(self):
        cmap = {p.doc_id: p for p in CORPUS}
        g, ung = grounding_audit(("wizard dragon",), [cmap["p0_a"]])
        self.assertEqual(g, 0.0)
        self.assertEqual(ung, 1)


class TestOverflow(unittest.TestCase):
    def test_no_truncation_under_window(self):
        ps = [Passage("a", "x", tokens=10), Passage("b", "y", tokens=10)]
        kept, dropped = context_overflow(ps, 512)
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 2)

    def test_tail_dropped_over_window(self):
        ps = [Passage("a", "x", tokens=400), Passage("b", "y", tokens=400)]
        kept, dropped = context_overflow(ps, 512)
        self.assertEqual(dropped, ["b"])


class TestBuggyImplsCaught(unittest.TestCase):
    def test_oracle_meets_floors(self):
        cfg = RagConfig()
        self.assertTrue(evaluate(CASES, CORPUS, cfg).meets_floors(cfg))

    def test_keyword_only_below_recall_floor(self):
        cfg = RagConfig()
        self.assertLess(evaluate(CASES, CORPUS, cfg, retriever=keyword_only_retrieve)
                        .mean_recall_at_k, cfg.recall_floor)

    def test_truncating_retriever_degrades(self):
        cfg = RagConfig()
        rep = evaluate(CASES, CORPUS, cfg, retriever=truncating_retrieve)
        self.assertTrue(rep.mean_grounding < cfg.grounding_floor
                        or rep.mean_recall_at_k < cfg.recall_floor)

    def test_fabricator_below_faithfulness_floor(self):
        cfg = RagConfig()
        rep = evaluate(CASES, CORPUS, cfg,
                       cited_fn=lambda case, rids: case.cited_doc_ids + ("ghost",))
        self.assertGreaterEqual(rep.fabricated_citations, len(CASES))
        self.assertLess(rep.mean_faithfulness, cfg.faithfulness_floor)


class TestRagReport(unittest.TestCase):
    def test_meets_floors_logic(self):
        cfg = RagConfig()
        self.assertTrue(RagReport(6, 1.0, 1.0, 1.0, 0, 0, 0).meets_floors(cfg))
        self.assertFalse(RagReport(6, 0.5, 1.0, 1.0, 0, 0, 0).meets_floors(cfg))
        self.assertFalse(RagReport(6, 1.0, 0.5, 1.0, 0, 0, 0).meets_floors(cfg))


class TestSelfTest(unittest.TestCase):
    def test_at_least_16_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 16)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
