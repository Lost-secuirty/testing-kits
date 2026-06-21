"""test_secure_codegen_eval_proof.py — planted-bad proof (teeth).

The correct auditor matches every frozen should_flag literal; every planted
mutant misclassifies at least one corpus case. Each reference_safe must score
secure-pass (not flagged) and each reference_bad must be detected insecure —
proving the scorer detects what it claims before any real model is plugged in.
"""

import unittest

from harnesses.ai.secure_codegen_eval_test_harness import (
    CASES,
    PROMPTS,
    TEETH,
    CodegenScorer,
    oracle_secure_codegen_audit,
    prove,
    run_case,
)


class TestProof(unittest.TestCase):
    def test_oracle_not_caught(self):
        # The correct auditor matches every frozen should_flag literal.
        self.assertFalse(prove(oracle_secure_codegen_audit))

    def test_every_planted_mutant_caught(self):
        self.assertTrue(TEETH.mutants)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_bad_cases_flagged(self):
        bad = [c for c in CASES if c.should_flag]
        self.assertTrue(bad)
        self.assertTrue(all(run_case(c) for c in bad),
                        "a planted-bad candidate was NOT detected insecure")

    def test_safe_cases_clean(self):
        safe = [c for c in CASES if not c.should_flag]
        self.assertTrue(safe)
        self.assertTrue(all(not run_case(c) for c in safe),
                        "a safe candidate was incorrectly flagged")

    def test_safe_references_are_secure_pass(self):
        scorer = CodegenScorer()
        for case in PROMPTS:
            self.assertTrue(scorer.score(case.reference_safe, case).secure_pass,
                            f"{case.id} reference_safe is not secure-pass")


if __name__ == "__main__":
    unittest.main()
