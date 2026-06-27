"""test_security_logging_proof.py — planted-bad proof (teeth)."""

import unittest

from harnesses.security.security_logging_test_harness import (
    CASES,
    TEETH,
    oracle_security_logging_audit,
    prove,
    run_case,
)


class TestProof(unittest.TestCase):
    def test_oracle_not_caught(self):
        # The correct auditor matches every frozen should_flag literal.
        self.assertFalse(prove(oracle_security_logging_audit))

    def test_every_planted_mutant_caught(self):
        self.assertTrue(TEETH.mutants)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_bad_cases_flagged(self):
        bad = [c for c in CASES if c.should_flag]
        self.assertTrue(bad)
        self.assertTrue(all(run_case(c) for c in bad))

    def test_safe_cases_clean(self):
        safe = [c for c in CASES if not c.should_flag]
        self.assertTrue(safe)
        self.assertTrue(all(not run_case(c) for c in safe))


if __name__ == "__main__":
    unittest.main()
