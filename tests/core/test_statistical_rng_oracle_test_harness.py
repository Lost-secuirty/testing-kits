import subprocess
import sys
import unittest

from harnesses._teeth import verify
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
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)


# ---------------------------------------------------------------------------
# Teeth — the harness must catch a real planted RNG-bias bug.
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(harness.TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct seeded sampler must NOT be flagged as biased.
        self.assertFalse(harness.TEETH.prove(harness.TEETH.oracle))
        self.assertFalse(harness.prove(harness.oracle_sampler))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertGreaterEqual(len(harness.TEETH.mutants), 1)
        for mutant in harness.TEETH.mutants:
            self.assertTrue(
                harness.TEETH.prove(mutant.impl),
                f"mutant not caught: {mutant.name}",
            )

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(harness.TEETH.corpus_size, 1)

    def test_noncircular_corpus(self):
        """Corrupting one frozen literal must flip prove(oracle) False->True.

        If it does not flip, the corpus is not actually driving the verdict
        (it would be circular / re-deriving the answer from the oracle)."""
        # Baseline: the oracle is clean against the true frozen literals.
        self.assertFalse(harness.prove(harness.oracle_sampler))
        original = dict(harness.FAIR_PROPORTIONS)
        try:
            # Corrupt the jackpot expectation to an impossible 0.40; the oracle's
            # ~0.05 realized jackpot rate now diverges far beyond tolerance.
            harness.FAIR_PROPORTIONS["jackpot"] = 0.40
            self.assertTrue(
                harness.prove(harness.oracle_sampler),
                "prove(oracle) did not flip after corrupting a frozen literal "
                "-> corpus is circular",
            )
        finally:
            harness.FAIR_PROPORTIONS.clear()
            harness.FAIR_PROPORTIONS.update(original)
        # Restored: the oracle is clean again.
        self.assertFalse(harness.prove(harness.oracle_sampler))


if __name__ == "__main__":
    unittest.main()
