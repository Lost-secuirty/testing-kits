import unittest

from harnesses.security import ci_workflow_hardening_test_harness as harness


class TestCiWorkflowHardeningProof(unittest.TestCase):
    def test_proof_each_unsafe_fixture_produces_its_finding(self):
        unsafe = [case for case in harness.CASES if not case.should_pass]
        self.assertTrue(unsafe)
        for case in unsafe:
            codes = [f.code for f in harness.audit_workflow(case.build_workflow())]
            for expected in case.expected_findings:
                self.assertIn(
                    expected, codes,
                    f"{case.name}: expected {expected}, got {codes}",
                )

    def test_proof_hardened_fixture_has_no_findings(self):
        hardened = [case for case in harness.CASES if case.should_pass]
        self.assertTrue(hardened)
        for case in hardened:
            findings = harness.audit_workflow(case.build_workflow())
            self.assertEqual(
                findings, [],
                f"{case.name} should be clean, got {[f.code for f in findings]}",
            )

    def test_proof_naive_auditor_misses_unpinned_action(self):
        # The planted bug: the naive auditor skips the action-pin SHA check.
        # The real auditor must flag the unpinned action; the naive one must not.
        unpinned = next(
            case for case in harness.CASES if case.name == "unpinned_action"
        )
        data = unpinned.build_workflow()
        real_codes = [f.code for f in harness.audit_workflow(data)]
        naive_codes = [f.code for f in harness.audit_workflow_naive(data)]
        self.assertIn("action-pin", real_codes)
        self.assertNotIn("action-pin", naive_codes)


if __name__ == "__main__":
    unittest.main()
