import subprocess
import sys
import unittest

from harnesses.core import lexical_date_canonicalization_test_harness as harness


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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_cli_list_scenarios(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--list-scenarios"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("noncanonical_diverges", proc.stdout)


if __name__ == "__main__":
    unittest.main()
