import unittest

from harnesses.ai import agent_memory_context_test_harness as harness


class TestAgentMemoryContextProof(unittest.TestCase):
    def test_proof_untrusted_authority_spoof_is_blocked(self):
        scenario = next(item for item in harness.SCENARIOS if item.name == "spoofed_system")
        result = harness.evaluate_scenario(scenario)
        self.assertFalse(result.allowed)
        self.assertIn("untrusted authority spoof", result.reasons)

    def test_proof_dangerous_cascade_is_blocked(self):
        scenario = next(item for item in harness.SCENARIOS if item.name == "cascade_after_failure")
        result = harness.evaluate_scenario(scenario)
        self.assertFalse(result.allowed)
        self.assertIn("dangerous cascade after failed tool", result.reasons)


if __name__ == "__main__":
    unittest.main()
