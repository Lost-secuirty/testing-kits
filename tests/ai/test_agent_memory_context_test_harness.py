import subprocess
import sys
import unittest

from harnesses.ai import agent_memory_context_test_harness as harness


class TestAgentMemoryContextHarness(unittest.TestCase):
    def test_expected_scenarios_exist(self):
        names = {scenario.name for scenario in harness.SCENARIOS}
        self.assertIn("trusted_read", names)
        self.assertIn("spoofed_system", names)
        self.assertIn("poisoned_memory", names)

    def test_all_builtin_scenarios_match_expectation(self):
        results = harness.run_all()
        self.assertTrue(all(result.ok for result in results), [result.scenario.name for result in results if not result.ok])

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
