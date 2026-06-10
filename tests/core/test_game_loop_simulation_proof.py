import unittest

from harnesses.core import game_loop_simulation_test_harness as harness


class TestGameLoopSimulationProof(unittest.TestCase):
    def test_proof_sticky_input_bug_fails(self):
        result = harness.check_input_release(harness.EngineBugs(sticky_input=True))
        self.assertFalse(result.ok)

    def test_proof_pause_bug_fails(self):
        result = harness.check_pause_freeze(harness.EngineBugs(moves_while_paused=True))
        self.assertFalse(result.ok)

    def test_proof_progression_gate_bug_fails(self):
        result = harness.check_progression_gate(harness.EngineBugs(skips_boss_gate=True))
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
