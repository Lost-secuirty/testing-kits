import subprocess
import sys
import unittest

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
