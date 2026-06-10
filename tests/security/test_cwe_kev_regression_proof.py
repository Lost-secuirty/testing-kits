import unittest

from harnesses.security import cwe_kev_regression_test_harness as harness


class TestCweKevRegressionProof(unittest.TestCase):
    def test_proof_bad_controls_are_rejected(self):
        bad_results = [harness.run_case(case) for case in harness.CASES if case.should_block]
        self.assertTrue(bad_results)
        self.assertTrue(all(result.blocked for result in bad_results))

    def test_proof_safe_controls_are_allowed(self):
        safe_results = [harness.run_case(case) for case in harness.CASES if not case.should_block]
        self.assertTrue(safe_results)
        self.assertTrue(all(not result.blocked for result in safe_results))


if __name__ == "__main__":
    unittest.main()
