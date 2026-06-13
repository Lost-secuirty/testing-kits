import unittest

from harnesses.core import lexical_date_canonicalization_test_harness as harness


class TestLexicalDateCanonicalizationProof(unittest.TestCase):
    def test_proof_lenient_validator_accepts_what_strict_rejects(self):
        # The planted bug: the lenient (strptime-based) validator accepts a
        # parseable-but-non-canonical date that the strict oracle rejects.
        trap = "2026-5-9"
        self.assertTrue(harness.lenient_is_valid(trap))
        self.assertFalse(harness.strict_is_valid(trap))

    def test_proof_noncanonical_dataset_sorts_wrong_lexically(self):
        # Lexical (TEXT-column) order diverges from chronological order when a
        # non-canonical date is present.
        self.assertFalse(harness.lexical_matches_chronological(harness.DIVERGENT_DATES))
        lexical = harness.lexical_sort(harness.DIVERGENT_DATES)
        chrono = harness.chronological_sort(harness.DIVERGENT_DATES)
        self.assertNotEqual(
            [harness.parse_date(d) for d in lexical],
            [harness.parse_date(d) for d in chrono],
        )
        # Concretely: '2026-5-9' (May) lexically sorts last, after '2026-10-01'.
        self.assertEqual(lexical[-1], "2026-5-9")

    def test_proof_canonicalization_restores_chronological_order(self):
        canon_sorted = harness.canonical_then_lexical_sort(harness.DIVERGENT_DATES)
        chrono_sorted = [
            harness.canonicalize(d) for d in harness.chronological_sort(harness.DIVERGENT_DATES)
        ]
        self.assertEqual(canon_sorted, chrono_sorted)
        self.assertTrue(harness.lexical_matches_chronological(harness.CANONICALIZED_DIVERGENT))

    def test_proof_safe_canonical_cases_pass_strict(self):
        safe = [c for c in harness.CANON_CASES if c.is_canonical]
        self.assertTrue(safe)
        self.assertTrue(all(harness.strict_is_valid(c.raw) for c in safe))

    def test_proof_self_test_reports_ok(self):
        self.assertEqual(harness._run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
