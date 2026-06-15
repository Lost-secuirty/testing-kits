import subprocess
import sys
import unittest

from harnesses.security import ci_workflow_hardening_test_harness as harness


class TestCiWorkflowHardeningHarness(unittest.TestCase):
    def test_case_catalog_has_hardened_and_unsafe_fixtures(self):
        self.assertGreaterEqual(len(harness.CASES), 8)
        self.assertTrue(any(case.should_pass for case in harness.CASES))
        self.assertTrue(any(not case.should_pass for case in harness.CASES))

    def test_each_case_matches_expected_findings(self):
        results = harness.run_all()
        self.assertTrue(
            all(result.ok for result in results),
            [result.case.name for result in results if not result.ok],
        )

    def test_hardened_fixture_yields_zero_findings(self):
        hardened = next(case for case in harness.CASES if case.name == "hardened")
        findings = harness.audit_workflow(hardened.build_workflow())
        self.assertEqual(findings, [], [f.code for f in findings])

    def test_on_as_true_key_quirk_is_detected(self):
        case = next(case for case in harness.CASES if case.name == "on_as_true_key")
        codes = [f.code for f in harness.audit_workflow(case.build_workflow())]
        self.assertIn("pull-request-target", codes)

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_cli_list_scenarios(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--list-scenarios"],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("hardened", proc.stdout)


if __name__ == "__main__":
    unittest.main()
