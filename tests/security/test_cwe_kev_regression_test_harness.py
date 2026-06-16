import dataclasses
import subprocess
import sys
import unittest

from harnesses._teeth import verify
from harnesses.security import cwe_kev_regression_test_harness as harness


class TestCweKevRegressionHarness(unittest.TestCase):
    def test_case_catalog_has_safe_and_bad_controls(self):
        self.assertGreaterEqual(len(harness.CASES), 20)
        self.assertTrue(any(case.should_block for case in harness.CASES))
        self.assertTrue(any(not case.should_block for case in harness.CASES))

    def test_each_case_matches_expected_block_decision(self):
        results = harness.run_all()
        self.assertTrue(all(result.ok for result in results), [result.case.name for result in results if not result.ok])

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)


class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted auditor defect (teeth contract)."""

    def test_teeth_verified(self):
        result = verify(harness.TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct auditor must NOT be flagged by prove.
        self.assertFalse(harness.TEETH.prove(harness.TEETH.oracle))
        self.assertFalse(harness.prove(harness.oracle_audit))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(harness.TEETH.mutants), 3)
        for mutant in harness.TEETH.mutants:
            self.assertTrue(
                harness.TEETH.prove(mutant.impl),
                f"mutant not caught: {mutant.name}",
            )

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(harness.TEETH.corpus_size, 1)
        self.assertEqual(harness.TEETH.corpus_size, len(harness.AUDIT_CORPUS))

    def test_oracle_matches_frozen_literals(self):
        # The frozen expectations are non-circular constants the oracle must
        # reproduce exactly.
        for case in harness.AUDIT_CORPUS:
            self.assertEqual(
                harness.oracle_audit(case.cwe, case.payload),
                case.should_flag,
                case.name,
            )

    def test_noncircular_corpus(self):
        """Corrupt ONE frozen literal and assert prove(oracle) flips to True.

        If prove re-derived the answer from the oracle (circular) rather than
        comparing against the frozen literal, flipping a literal would have no
        effect and prove(oracle) would stay False. This proves it has teeth.
        """
        original = harness.AUDIT_CORPUS
        self.assertFalse(harness.prove(harness.oracle_audit))
        corrupted = tuple(
            dataclasses.replace(case, should_flag=not case.should_flag)
            if case.name == "sqli_union_select"
            else case
            for case in original
        )
        harness.AUDIT_CORPUS = corrupted
        try:
            self.assertTrue(
                harness.prove(harness.oracle_audit),
                "prove(oracle) did not flip after corrupting a frozen literal — "
                "the corpus is circular",
            )
        finally:
            harness.AUDIT_CORPUS = original
        # Restoration must return prove(oracle) to clean.
        self.assertFalse(harness.prove(harness.oracle_audit))


if __name__ == "__main__":
    unittest.main()
