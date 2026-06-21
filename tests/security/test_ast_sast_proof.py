"""test_ast_sast_proof.py — planted-bad proof (teeth)."""

import unittest

from harnesses.security.ast_sast_test_harness import (
    SAST_CORPUS,
    TEETH,
    oracle_ast_sast_audit,
    prove,
)


class TestProof(unittest.TestCase):
    def test_oracle_not_caught(self):
        # The correct auditor matches every frozen should_flag literal.
        self.assertFalse(prove(oracle_ast_sast_audit))

    def test_every_planted_mutant_caught(self):
        self.assertTrue(TEETH.mutants)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_bad_cases_flagged(self):
        bad = [c for c in SAST_CORPUS if c.should_flag]
        self.assertTrue(bad)
        self.assertTrue(all(oracle_ast_sast_audit(c) for c in bad),
                        "a known-vulnerable snippet was NOT flagged")

    def test_safe_cases_clean(self):
        safe = [c for c in SAST_CORPUS if not c.should_flag]
        self.assertTrue(safe)
        self.assertTrue(all(not oracle_ast_sast_audit(c) for c in safe),
                        "a known-safe snippet was incorrectly flagged")


if __name__ == "__main__":
    unittest.main()
