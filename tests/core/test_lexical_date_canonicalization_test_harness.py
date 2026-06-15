import dataclasses
import subprocess
import sys
import unittest
import unittest.mock

from harnesses._teeth import verify
from harnesses.core import lexical_date_canonicalization_test_harness as harness
from harnesses.core.lexical_date_canonicalization_test_harness import (
    CANONICALIZE_CORPUS,
    TEETH,
    canonicalize_iso,
    prove,
)


class TestLexicalDateCanonicalizationHarness(unittest.TestCase):
    def test_case_catalog_has_canonical_and_noncanonical(self):
        self.assertGreaterEqual(len(harness.CANON_CASES), 6)
        self.assertTrue(any(c.is_canonical for c in harness.CANON_CASES))
        self.assertTrue(any(not c.is_canonical and c.parses for c in harness.CANON_CASES))

    def test_each_canon_case_matches_oracle(self):
        results = [harness.run_canon_case(c) for c in harness.CANON_CASES]
        self.assertTrue(all(r.ok for r in results), [r.name for r in results if not r.ok])

    def test_each_sort_case_matches_oracle(self):
        results = [harness.run_sort_case(c) for c in harness.SORT_CASES]
        self.assertTrue(all(r.ok for r in results), [r.name for r in results if not r.ok])

    def test_canonicalize_zero_pads(self):
        self.assertEqual(harness.canonicalize("2026-5-9"), "2026-05-09")
        self.assertEqual(harness.canonicalize("2026-05-09"), "2026-05-09")

    def test_is_canonical_distinguishes_padding(self):
        self.assertTrue(harness.is_canonical("2026-05-09"))
        self.assertFalse(harness.is_canonical("2026-5-9"))
        self.assertFalse(harness.is_canonical("not-a-date"))

    def test_canonical_dates_lexical_equals_chronological(self):
        self.assertTrue(harness.lexical_matches_chronological(harness.ALL_CANONICAL_DATES))
        self.assertEqual(
            harness.lexical_sort(harness.ALL_CANONICAL_DATES),
            harness.chronological_sort(harness.ALL_CANONICAL_DATES),
        )

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_cli_list_scenarios(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--list-scenarios"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("noncanonical_diverges", proc.stdout)


class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted canonicalization bug (teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct canonicalizer must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(canonicalize_iso))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(CANONICALIZE_CORPUS))

    def test_oracle_matches_frozen_literals(self):
        # The frozen expectations are non-circular constants the oracle must
        # reproduce exactly.
        for case in CANONICALIZE_CORPUS:
            self.assertEqual(canonicalize_iso(case.raw), case.expected, case.name)

    def test_noncircular_corpus(self):
        # Corrupting one frozen literal must make prove(oracle) flip False -> True.
        # If it does not flip, the corpus is circular (re-derived from the oracle).
        self.assertFalse(prove(canonicalize_iso))  # clean baseline
        idx = next(i for i, c in enumerate(CANONICALIZE_CORPUS)
                   if c.name == "us_slash_ambiguous")
        original = CANONICALIZE_CORPUS[idx]
        # 3/4/2026 -> the SWAPPED (wrong) literal April 3 instead of March 4.
        corrupted = dataclasses.replace(original, expected="2026-04-03")
        patched = tuple(
            corrupted if i == idx else c for i, c in enumerate(CANONICALIZE_CORPUS)
        )
        with unittest.mock.patch.object(harness, "CANONICALIZE_CORPUS", patched):
            self.assertTrue(prove(canonicalize_iso),
                            "prove(oracle) did not flip when a literal was corrupted "
                            "-> corpus is circular")
        # Restored after the patch: the real corpus is clean again.
        self.assertFalse(prove(canonicalize_iso))


if __name__ == "__main__":
    unittest.main()
