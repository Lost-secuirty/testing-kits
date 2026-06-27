"""Test suite for stateful_sequence_budget_test_harness."""

import contextlib
import io
import json
import unittest

from harnesses.core.stateful_sequence_budget_test_harness import (
    CORPUS,
    MACHINE,
    TEETH,
    Exploration,
    _as_tuple,
    _bug_action_names_only,
    _bug_expand_terminal,
    _bug_ignore_budget,
    _bug_skip_visited,
    _run_self_test,
    explore,
    list_scenarios,
    main,
    prove,
)


class TestMachine(unittest.TestCase):
    def test_initial_and_terminal(self):
        self.assertEqual(MACHINE.initial, "start")
        self.assertTrue(MACHINE.is_terminal("done"))
        self.assertFalse(MACHINE.is_terminal("start"))

    def test_actions_from_are_ordered(self):
        self.assertEqual(MACHINE.actions_from("start"), ("go_a", "go_b"))

    def test_next_transition(self):
        self.assertEqual(MACHINE.next("a", "finish"), "done")

    def test_next_unknown_action_raises(self):
        with self.assertRaises(KeyError):
            MACHINE.next("start", "finish")

    def test_all_actions_sorted_unique(self):
        self.assertEqual(MACHINE.all_actions(), ("back", "finish", "go_a", "go_b", "noop"))


class TestExplore(unittest.TestCase):
    def test_full_exploration(self):
        result = explore(MACHINE, 10)
        self.assertEqual(result.stop_reason, "exhausted")
        self.assertEqual(result.steps, 6)
        self.assertEqual(len(result.edges), 6)

    def test_budget_caps_steps(self):
        for budget in (0, 1, 2, 3, 5):
            with self.subTest(budget=budget):
                self.assertLessEqual(explore(MACHINE, budget).steps, budget)

    def test_no_edge_from_terminal(self):
        for state, _action in explore(MACHINE, 10).edges:
            self.assertFalse(MACHINE.is_terminal(state))

    def test_corpus_expectations_match_oracle(self):
        for budget, expected in CORPUS:
            with self.subTest(budget=budget):
                self.assertEqual(_as_tuple(explore(MACHINE, budget)), expected)

    def test_deterministic(self):
        self.assertEqual(explore(MACHINE, 10), explore(MACHINE, 10))


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(explore))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_ignore_budget, _bug_expand_terminal,
                    _bug_skip_visited, _bug_action_names_only):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_mutants_terminate(self):
        # Each mutant must return an Exploration (no infinite loop) on every case.
        for budget, _exp in CORPUS:
            for mutant in TEETH.mutants:
                with self.subTest(budget=budget, mutant=mutant.name):
                    self.assertIsInstance(mutant.impl(MACHINE, budget), Exploration)

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("budget_10", scenarios)
        self.assertIn("ignore_budget", scenarios)
        self.assertGreaterEqual(len(scenarios), 5)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)

    def test_json_mode_is_machine_readable(self):
        for run in (lambda: _run_self_test(as_json=True), lambda: main(["--json"])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["harness"], "core/stateful_sequence_budget")
            self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
