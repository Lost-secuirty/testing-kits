import subprocess
import sys
import unittest

from harnesses.core import game_loop_simulation_test_harness as harness


class TestGameLoopSimulationHarness(unittest.TestCase):
    def test_good_engine_passes_all_invariants(self):
        results = harness.run_all()
        self.assertTrue(all(result.ok for result in results), [result.detail for result in results if not result.ok])

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
