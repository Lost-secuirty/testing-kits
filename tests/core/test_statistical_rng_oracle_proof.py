import unittest

from harnesses.core import statistical_rng_oracle_test_harness as harness


class TestStatisticalRngOracleProof(unittest.TestCase):
    def test_proof_biased_rng_fails_distribution(self):
        report = harness.evaluate_distribution(harness.sample(rng=harness.BiasedRng()))
        self.assertFalse(report.ok)
        self.assertGreater(report.observed["common"], 0.95)


if __name__ == "__main__":
    unittest.main()
