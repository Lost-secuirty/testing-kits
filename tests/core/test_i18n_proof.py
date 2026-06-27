import unittest

from harnesses.core import i18n_test_harness as harness


class TestI18nProof(unittest.TestCase):
    def test_oracle_matches_frozen_i18n_events(self):
        for case in harness.I18N_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_i18n_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_i18n_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.I18N_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_i18n_defects_have_traps(self):
        cases = {case.name: case for case in harness.I18N_AUDIT_CORPUS}
        self.assertNotEqual(
            harness.raw_normalization_auditor(cases["nfc_nfd_canonical_equivalence"]),
            cases["nfc_nfd_canonical_equivalence"].expected_events,
        )
        self.assertNotEqual(
            harness.generated_mojibake_auditor(cases["mojibake_clean_sample_not_flagged"]),
            cases["mojibake_clean_sample_not_flagged"].expected_events,
        )
        self.assertNotEqual(
            harness.naive_grapheme_auditor(cases["zwj_family_single_grapheme"]),
            cases["zwj_family_single_grapheme"].expected_events,
        )
        self.assertNotEqual(
            harness.byte_slice_truncation_auditor(cases["utf8_truncation_safe"]),
            cases["utf8_truncation_safe"].expected_events,
        )
        self.assertNotEqual(
            harness.bidi_blind_auditor(cases["bidi_override_detected"]),
            cases["bidi_override_detected"].expected_events,
        )

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.I18N_AUDIT_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
