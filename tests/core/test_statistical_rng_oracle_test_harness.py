import subprocess
import sys
import unittest

from harnesses.core import statistical_rng_oracle_test_harness as harness


class TestStatisticalRngOracleHarness(unittest.TestCase):
    def test_seed_replay_is_deterministic(self):
        self.assertTrue(harness.check_seed_replay())

    def test_good_distribution_passes(self):
        report = harness.evaluate_distribution(harness.sample(draws=20_000))
        self.assertTrue(report.ok, report.detail)

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
