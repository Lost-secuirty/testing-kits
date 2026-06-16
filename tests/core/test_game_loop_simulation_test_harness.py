import dataclasses
import subprocess
import sys
import unittest

from harnesses._teeth import verify
from harnesses.core import game_loop_simulation_test_harness as harness
from harnesses.core.game_loop_simulation_test_harness import (
    TEETH,
    TICK_CORPUS,
    oracle_simulate,
    prove,
)


class TestGameLoopSimulationHarness(unittest.TestCase):
    def test_good_engine_passes_all_invariants(self):
        results = harness.run_all()
        self.assertTrue(all(result.ok for result in results), [result.detail for result in results if not result.ok])

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)


# ===========================================================================
# Teeth — the harness must catch a real planted tick-loop bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct fixed-step loop must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_simulate))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(TICK_CORPUS))

    def test_oracle_matches_frozen_literals(self):
        # The frozen expectations are non-circular constants the oracle must
        # reproduce exactly for every corpus case.
        for case in TICK_CORPUS:
            end = oracle_simulate(case.x0, case.y0, case.pressed, case.n_ticks)
            self.assertEqual(tuple(end), case.expected, case.name)

    def test_noncircular_corpus(self):
        # Corrupt one frozen literal and assert prove(oracle) flips to True.
        # If it does not flip, the corpus is being derived from the oracle
        # (circular) rather than judged against frozen ground truth.
        original = harness.TICK_CORPUS
        corrupted = list(original)
        corrupted[0] = dataclasses.replace(corrupted[0],
                                           expected=(corrupted[0].expected[0] + 1,
                                                     corrupted[0].expected[1]))
        harness.TICK_CORPUS = tuple(corrupted)
        try:
            self.assertTrue(prove(oracle_simulate),
                            "prove(oracle) must flip True when a frozen literal is corrupted")
        finally:
            harness.TICK_CORPUS = original
        # Sanity: the restored corpus is clean again.
        self.assertFalse(prove(oracle_simulate))


if __name__ == "__main__":
    unittest.main()
