"""test_supplychain_depth_proof.py — planted-bad proof (teeth)."""

import unittest

from harnesses.security.supplychain_depth_test_harness import (
    CASES,
    TEETH,
    oracle_sbom_audit,
    prove,
    run_case,
)


class TestProof(unittest.TestCase):
    def test_oracle_not_caught(self):
        # The correct auditor matches every frozen expected_codes literal.
        self.assertFalse(prove(oracle_sbom_audit))

    def test_every_planted_mutant_caught(self):
        self.assertTrue(TEETH.mutants)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_bad_cases_flagged(self):
        # Every case that expects at least one finding code is flagged by the oracle.
        bad = [c for c in CASES if c.expected_codes]
        self.assertTrue(bad)
        for case in bad:
            self.assertEqual(run_case(case), case.expected_codes, case.name)

    def test_safe_cases_clean(self):
        # Every case expecting no findings yields an empty finding set.
        safe = [c for c in CASES if not c.expected_codes]
        self.assertTrue(safe)
        for case in safe:
            self.assertEqual(run_case(case), (), case.name)


if __name__ == "__main__":
    unittest.main()
