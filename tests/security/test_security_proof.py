import unittest

from harnesses.security import security_test_harness as harness


class TestSecurityProof(unittest.TestCase):
    def test_oracle_matches_frozen_security_decisions(self):
        self.assertTrue(any(case.should_flag for case in harness.SECURITY_AUDIT_CORPUS))
        self.assertTrue(any(not case.should_flag for case in harness.SECURITY_AUDIT_CORPUS))
        for case in harness.SECURITY_AUDIT_CORPUS:
            self.assertEqual(harness.oracle_security_audit(case), case.should_flag, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_security_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.SECURITY_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_security_defects_have_traps(self):
        cases = {case.name: case for case in harness.SECURITY_AUDIT_CORPUS}
        self.assertTrue(harness.status_only_auditor(cases["path_safe_rejected"]))
        self.assertFalse(harness.secret_blind_auditor(cases["profile_leaks_secret"]))
        self.assertTrue(harness.escape_blind_xss_auditor(cases["xss_escaped_script"]))

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.SECURITY_AUDIT_CORPUS],
        )


if __name__ == "__main__":
    unittest.main()
