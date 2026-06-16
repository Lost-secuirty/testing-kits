import subprocess
import sys
import unittest

from harnesses.security import diff_secret_gate_test_harness as harness


class TestDiffSecretGateHarness(unittest.TestCase):
    def test_case_catalog_has_added_removed_and_clean_scenarios(self):
        self.assertGreaterEqual(len(harness.CASES), 6)
        # at least one case that expects a finding, and one that expects none
        self.assertTrue(any(case.expected for case in harness.CASES))
        self.assertTrue(any(not case.expected for case in harness.CASES))

    def test_each_case_matches_expected_findings(self):
        results = harness.run_all()
        self.assertTrue(
            all(result.ok for result in results),
            [result.case.name for result in results if not result.ok],
        )

    def test_scan_line_detects_each_secret_label(self):
        labels = {
            "AWS_ACCESS_KEY_ID": "AKIA" + "IOSFODNN7EXAMPLE",
            "GITHUB_TOKEN": "ghp_" + ("a" * 36),
            "PRIVATE_KEY_BLOCK": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "SLACK_TOKEN": "xoxb-" + "1234567890-abcdefXYZ",
            "GOOGLE_API_KEY": "AIza" + ("b" * 35),
            "GENERIC_SECRET_ASSIGNMENT": "secret" + " = " + "'" + ("A" * 20) + "'",
        }
        for label, sample in labels.items():
            self.assertIn(label, harness.scan_line(sample), label)

    def test_scan_line_allowlist_escape_hatch(self):
        secret = "AKIA" + "IOSFODNN7EXAMPLE"
        self.assertEqual(harness.scan_line(secret + "  # allowlist secret"), [])

    def test_scan_line_clean_inputs_have_no_hits(self):
        self.assertEqual(harness.scan_line("this mentions an api_key in passing"), [])
        self.assertEqual(harness.scan_line("token: ${{ secrets.GITHUB_TOKEN }}"), [])

    def test_scope_no_pii_detectors(self):
        # PII is owned by pii_redaction_test_harness; this gate must not flag it.
        self.assertEqual(harness.scan_line("email alice@example.com"), [])
        self.assertEqual(harness.scan_line("ssn 123-45-6789"), [])

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
        self.assertIn("removed_secret_only", proc.stdout)


if __name__ == "__main__":
    unittest.main()
