"""Test suite for counterexample_replay_test_harness."""

import unittest

from harnesses.core.counterexample_replay_test_harness import (
    _REC_A,
    _REC_A_LATER,
    _REC_A_OTHER_SEED,
    _REC_B,
    CORPUS,
    KEY_A,
    TEETH,
    _bug_accepts_empty_class,
    _bug_constant_key,
    _bug_drops_seed,
    _bug_includes_volatile,
    _run_self_test,
    canonical_record,
    list_scenarios,
    prove,
    replay_key,
)


class TestCanonicalRecord(unittest.TestCase):
    def test_drops_volatile_fields(self):
        canon = canonical_record(_REC_A)
        self.assertNotIn("timestamp", canon)
        self.assertNotIn("run_id", canon)
        self.assertIn("seed", canon)

    def test_rejects_empty_failure_class(self):
        with self.assertRaises(ValueError):
            canonical_record({"input": [1], "seed": 1, "failure_class": "",
                              "expected_verdict": "fail"})

    def test_rejects_missing_seed(self):
        with self.assertRaises(ValueError):
            canonical_record({"input": [1], "failure_class": "X", "expected_verdict": "fail"})


class TestReplayKey(unittest.TestCase):
    def test_exact_frozen_key(self):
        self.assertEqual(replay_key(_REC_A), KEY_A)

    def test_stable_across_volatile_fields(self):
        self.assertEqual(replay_key(_REC_A), replay_key(_REC_A_LATER))

    def test_distinct_on_seed(self):
        self.assertNotEqual(replay_key(_REC_A), replay_key(_REC_A_OTHER_SEED))

    def test_distinct_on_input_and_class(self):
        self.assertNotEqual(replay_key(_REC_A), replay_key(_REC_B))

    def test_deterministic(self):
        self.assertEqual(replay_key(_REC_A), replay_key(dict(_REC_A)))


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(replay_key))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_includes_volatile, _bug_drops_seed,
                    _bug_accepts_empty_class, _bug_constant_key):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("stable_across_volatile", scenarios)
        self.assertIn("constant_key", scenarios)
        self.assertGreaterEqual(len(scenarios), 5)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
