"""test_prompt_ab_test_harness.py — unittest suite."""

import unittest

from harnesses.ai.prompt_ab_test_harness import (
    ALL_PROMPTS,
    EXTRA_PROMPTS,
    NEUTRAL,
    SECURE_PERSONA,
    CannedModelAdapter,
    CodegenScorer,
    SecureCodegenEval,
    generations_from_adapter,
    list_scenarios,
    run_ab,
    run_all_scenarios,
)


class TestExtraPrompts(unittest.TestCase):
    def test_extra_safe_secure_pass(self):
        scorer = CodegenScorer()
        for case in EXTRA_PROMPTS:
            self.assertTrue(scorer.score(case.reference_safe, case).secure_pass, case.id)

    def test_extra_bad_correct_but_insecure(self):
        scorer = CodegenScorer()
        for case in EXTRA_PROMPTS:
            res = scorer.score(case.reference_bad, case)
            self.assertTrue(res.correct, case.id)
            self.assertFalse(res.secure, case.id)

    def test_all_prompts_expanded(self):
        self.assertGreaterEqual(len(ALL_PROMPTS), 6)


class TestAB(unittest.TestCase):
    def test_persona_beats_neutral(self):
        ab = run_ab(ALL_PROMPTS, NEUTRAL, SECURE_PERSONA, k=1)
        self.assertEqual(ab.pass_a, 0.0)
        self.assertEqual(ab.pass_b, 1.0)
        self.assertEqual(ab.delta, 1.0)

    def test_identical_strategies_zero_delta(self):
        ab = run_ab(ALL_PROMPTS, NEUTRAL, NEUTRAL, k=1)
        self.assertEqual(ab.delta, 0.0)


class TestModelAdapter(unittest.TestCase):
    def test_adapter_generations(self):
        adapter = CannedModelAdapter(lambda case, fb: case.reference_safe)
        gens = generations_from_adapter(adapter, ALL_PROMPTS, k=1)
        rep = SecureCodegenEval(ALL_PROMPTS).run(gens, k=1)
        self.assertEqual(rep.secure_pass_at_k, 1.0)


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 12)


if __name__ == "__main__":
    unittest.main()
