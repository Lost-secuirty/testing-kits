"""test_secure_codegen_eval_test_harness.py — unittest suite."""

import unittest

from harnesses.ai.secure_codegen_eval_test_harness import (
    PROMPTS,
    CodegenScorer,
    RepairLoop,
    SecureCodegenEval,
    list_scenarios,
    run_all_scenarios,
)


class TestScorer(unittest.TestCase):
    def setUp(self):
        self.scorer = CodegenScorer()

    def test_reference_safe_is_secure_pass(self):
        for case in PROMPTS:
            res = self.scorer.score(case.reference_safe, case)
            self.assertTrue(res.secure_pass, f"{case.id}: correct={res.correct} secure={res.secure}")

    def test_reference_bad_is_correct_but_insecure(self):
        for case in PROMPTS:
            res = self.scorer.score(case.reference_bad, case)
            self.assertTrue(res.correct, f"{case.id} should be functionally correct")
            self.assertFalse(res.secure, f"{case.id} should be flagged insecure")

    def test_malformed_not_correct(self):
        self.assertFalse(self.scorer.score("def x( bad", PROMPTS[0]).correct)


class TestEval(unittest.TestCase):
    def test_all_safe_pass_at_1(self):
        gens = {c.id: [c.reference_safe] for c in PROMPTS}
        self.assertEqual(SecureCodegenEval().run(gens, k=1).secure_pass_at_k, 1.0)

    def test_all_bad_zero_at_1(self):
        gens = {c.id: [c.reference_bad] for c in PROMPTS}
        self.assertEqual(SecureCodegenEval().run(gens, k=1).secure_pass_at_k, 0.0)

    def test_pass_at_k_picks_best_of_k(self):
        # one bad then one safe -> at k=2 the case passes
        gens = {PROMPTS[0].id: [PROMPTS[0].reference_bad, PROMPTS[0].reference_safe]}
        report = SecureCodegenEval(cases=[PROMPTS[0]]).run(gens, k=2)
        self.assertEqual(report.secure_pass_at_k, 1.0)


class TestRepairLoop(unittest.TestCase):
    def test_repair_lifts_bad_to_fixed(self):
        def gen(case, feedback):
            return case.reference_safe if feedback else case.reference_bad
        result = RepairLoop().run(PROMPTS[0], gen, max_iters=4)
        self.assertFalse(result.before_pass)
        self.assertTrue(result.after_pass)

    def test_repair_gives_up_if_never_fixed(self):
        def gen(case, feedback):
            return case.reference_bad
        result = RepairLoop().run(PROMPTS[0], gen, max_iters=3)
        self.assertFalse(result.after_pass)
        self.assertEqual(result.iterations, 3)


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 12)


if __name__ == "__main__":
    unittest.main()
