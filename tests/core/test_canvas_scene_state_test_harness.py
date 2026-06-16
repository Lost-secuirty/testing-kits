import dataclasses
import subprocess
import sys
import unittest

from harnesses._teeth import verify
from harnesses.core import canvas_scene_state_test_harness as harness
from harnesses.core.canvas_scene_state_test_harness import (
    SCENE_CORPUS,
    TEETH,
    oracle_analyze,
    prove,
)


class TestCanvasSceneStateHarness(unittest.TestCase):
    def test_good_scene_passes(self):
        report = harness.analyze_scene(harness.GOOD_SCENE)
        self.assertTrue(report.ok, report.issues)

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
# Teeth — the harness must catch a real planted scene-reducer bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct analyzer must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_analyze))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertGreaterEqual(len(TEETH.mutants), 1)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(SCENE_CORPUS))

    def test_noncircular_corpus(self):
        """Corrupting a frozen literal must make prove(oracle) flip False->True.

        This proves prove() judges against the frozen corpus constants, not by
        re-deriving the answer from the oracle at runtime (which would be
        circular and could never be caught by the external gate).
        """
        self.assertFalse(prove(oracle_analyze))  # baseline: oracle is clean
        # Corrupt one frozen expectation: claim the clean scene should be NOT ok.
        original = SCENE_CORPUS[0]
        corrupted = dataclasses.replace(original, expected_ok=False)
        patched = (corrupted,) + SCENE_CORPUS[1:]
        harness.SCENE_CORPUS = patched
        try:
            self.assertTrue(prove(oracle_analyze),
                            "prove did not flip on a corrupted literal -> corpus is circular")
        finally:
            harness.SCENE_CORPUS = SCENE_CORPUS


if __name__ == "__main__":
    unittest.main()
