import subprocess
import sys
import unittest

from harnesses._teeth import verify
from harnesses.ai import agent_memory_context_test_harness as harness
from harnesses.ai.agent_memory_context_test_harness import (
    TEETH,
    EXPECTED_RETRIEVED,
    MEMORY_CORPUS,
    MemoryQuery,
    oracle_retrieve,
    prove,
)


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


# ---------------------------------------------------------------------------
# Memory-retrieval invariants exercised directly (session isolation, pinned
# survival, budget keeps the required item).
# ---------------------------------------------------------------------------

class TestMemoryRetrieval(unittest.TestCase):
    def test_session_isolation_beta_query_excludes_alpha(self):
        q = MemoryQuery("x", "beta", "billing", capacity=10, token_budget=400)
        ids = oracle_retrieve(q)
        self.assertFalse([i for i in ids if i.startswith("a_")], ids)
        self.assertIn("b_secret", ids)

    def test_session_isolation_alpha_query_excludes_beta(self):
        q = MemoryQuery("x", "alpha", "billing", capacity=10, token_budget=400)
        ids = oracle_retrieve(q)
        self.assertNotIn("b_secret", ids)

    def test_pinned_survives_over_capacity_eviction(self):
        q = MemoryQuery("x", "alpha", "billing", capacity=2, token_budget=400)
        self.assertIn("a_pin", oracle_retrieve(q))

    def test_tight_budget_keeps_required_pinned_item(self):
        q = MemoryQuery("x", "alpha", "billing", capacity=10, token_budget=80)
        self.assertIn("a_pin", oracle_retrieve(q))

    def test_oracle_matches_every_frozen_expectation(self):
        for q in MEMORY_CORPUS:
            self.assertEqual(tuple(oracle_retrieve(q)), EXPECTED_RETRIEVED[q.name], q.name)


# ---------------------------------------------------------------------------
# Teeth: the harness must catch a real planted agent-memory bug.
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct retriever must NOT be flagged by prove.
        self.assertFalse(prove(oracle_retrieve))
        self.assertFalse(TEETH.prove(TEETH.oracle))

    def test_every_mutant_is_caught(self):
        # Each planted agent-memory defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == "__main__":
    unittest.main()
