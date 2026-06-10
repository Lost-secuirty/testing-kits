"""Test suite for search_relevance_test_harness."""

import unittest

from harnesses.core.search_relevance_test_harness import (
    ANALYZER_CASES,
    DOCS,
    QUERY_SETS,
    SCENARIOS,
    Doc,
    RelevanceReport,
    SearchConfig,
    _run_self_test,
    analyze,
    analyzer_failures,
    evaluate,
    list_scenarios,
    mrr,
    ndcg_at_k,
    no_fold_analyze,
    precision_at_k,
    recall_at_k,
    reversed_search,
    search,
)


class TestFixtures(unittest.TestCase):
    def test_at_least_20_docs(self):
        self.assertGreaterEqual(len(DOCS), 20)

    def test_doc_ids_unique(self):
        ids = [d.doc_id for d in DOCS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_query_sets_present_with_graded_judgments(self):
        self.assertGreaterEqual(len(QUERY_SETS), 6)
        grades = {j.grade for qs in QUERY_SETS for j in qs.judgments}
        self.assertTrue(grades <= {0, 1, 2, 3})
        self.assertIn(3, grades)


class TestAnalyzer(unittest.TestCase):
    def test_casefold(self):
        self.assertIn("cat", analyze("Cat"))

    def test_accent_fold(self):
        self.assertEqual(analyze("café"), analyze("Cafe"))

    def test_plural_stem(self):
        self.assertEqual(analyze("models"), analyze("model"))

    def test_stopword_dropped(self):
        self.assertEqual(analyze("the"), [])

    def test_nfkc_fullwidth(self):
        self.assertEqual(analyze("ＡＢＣ"), ["abc"])

    def test_cjk_segmentation(self):
        self.assertIn("猫", analyze("我有一只猫"))

    def test_deterministic(self):
        self.assertEqual(analyze("Running 猫 café"), analyze("Running 猫 café"))


class TestMetricOracles(unittest.TestCase):
    def test_recall(self):
        self.assertEqual(recall_at_k(["r1", "r2"], {"r1", "r2"}, 10), 1.0)
        self.assertEqual(recall_at_k(["r1", "x"], {"r1", "r2"}, 10), 0.5)

    def test_precision(self):
        self.assertAlmostEqual(precision_at_k(["r1", "x", "r2"], {"r1", "r2"}, 3), 2 / 3)

    def test_mrr_rank_3(self):
        self.assertAlmostEqual(mrr(["x", "y", "r"], {"r"}), 1 / 3)

    def test_mrr_no_relevant(self):
        self.assertEqual(mrr(["x", "y"], {"r"}), 0.0)

    def test_ndcg_ideal_is_one(self):
        self.assertAlmostEqual(ndcg_at_k(["a", "b", "c"], {"a": 3, "b": 2, "c": 1}, 10), 1.0)

    def test_ndcg_non_ideal_below_one(self):
        self.assertLess(ndcg_at_k(["c", "b", "a"], {"a": 3, "b": 2, "c": 1}, 10), 1.0)

    def test_ndcg_graded_differs_from_binary(self):
        grades = {"a": 1, "b": 3, "c": 2}
        self.assertNotAlmostEqual(
            ndcg_at_k(["a", "b", "c"], grades, 10, binary=False),
            ndcg_at_k(["a", "b", "c"], grades, 10, binary=True),
        )

    def test_empty_query_returns_nothing(self):
        self.assertEqual(search("", DOCS), [])

    def test_tie_break_stable_by_doc_id(self):
        docs = [Doc("zeta", "alpha"), Doc("alpha", "alpha")]
        self.assertEqual(search("alpha", docs), ["alpha", "zeta"])


class TestOracleAndBuggy(unittest.TestCase):
    def test_oracle_meets_floors(self):
        cfg = SearchConfig()
        rep = evaluate(QUERY_SETS, DOCS, cfg)
        self.assertTrue(rep.meets_floors(cfg))
        self.assertGreaterEqual(rep.mean_recall_at_k, cfg.recall_floor)
        self.assertGreaterEqual(rep.mean_ndcg_at_k, cfg.ndcg_floor)
        self.assertEqual(rep.analyzer_failures, 0)

    def test_reversed_ranker_below_ndcg_floor(self):
        cfg = SearchConfig()
        rep = evaluate(QUERY_SETS, DOCS, cfg, search_fn=reversed_search)
        self.assertLess(rep.mean_ndcg_at_k, cfg.ndcg_floor)

    def test_no_fold_analyzer_has_failures(self):
        self.assertGreaterEqual(analyzer_failures(ANALYZER_CASES, no_fold_analyze), 1)


class TestRelevanceReport(unittest.TestCase):
    def test_meets_floors_logic(self):
        cfg = SearchConfig()
        good = RelevanceReport(6, 1.0, 1.0, 1.0, 1.0, 0)
        self.assertTrue(good.meets_floors(cfg))
        self.assertFalse(RelevanceReport(6, 1.0, 1.0, 1.0, 0.5, 0).meets_floors(cfg))
        self.assertFalse(RelevanceReport(6, 1.0, 1.0, 1.0, 1.0, 1).meets_floors(cfg))


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
