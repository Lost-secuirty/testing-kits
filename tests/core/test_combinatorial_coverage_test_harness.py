"""Test suite for combinatorial_coverage_test_harness."""

import contextlib
import io
import json
import unittest

from harnesses.core.combinatorial_coverage_test_harness import (
    CORPUS,
    MODEL,
    TEETH,
    _bug_always_covered,
    _bug_collapse_values,
    _bug_first_row_only,
    _bug_ignore_param,
    _run_self_test,
    covered_pairs,
    list_scenarios,
    main,
    missing_pairs,
    pairwise,
    prove,
    required_pairs,
)


class TestModelAndRequiredPairs(unittest.TestCase):
    def test_model_is_finite_and_nonempty(self):
        self.assertGreaterEqual(len(MODEL), 2)
        for values in MODEL.values():
            self.assertGreaterEqual(len(values), 2)

    def test_required_pairs_count(self):
        # 3 parameter-pairs, each 2x2 values -> 12 required 2-way pairs.
        self.assertEqual(len(required_pairs(MODEL)), 12)

    def test_required_pairs_are_cross_parameter(self):
        for pair in required_pairs(MODEL):
            params = {p for p, _v in pair}
            self.assertEqual(len(params), 2, "a pair must join two distinct parameters")


class TestOracle(unittest.TestCase):
    def test_complete_set_has_no_missing(self):
        complete = next(ts for name, ts, _e in CORPUS if name == "complete")
        self.assertEqual(missing_pairs(complete, MODEL), frozenset())

    def test_empty_set_misses_everything(self):
        self.assertEqual(missing_pairs((), MODEL), required_pairs(MODEL))

    def test_corpus_expectations_match_oracle(self):
        for name, test_set, expected in CORPUS:
            with self.subTest(case=name):
                self.assertEqual(missing_pairs(test_set, MODEL), expected)

    def test_covered_pairs_subset_of_required(self):
        complete = next(ts for name, ts, _e in CORPUS if name == "complete")
        self.assertLessEqual(covered_pairs(complete), required_pairs(MODEL))


class TestGenerator(unittest.TestCase):
    def test_pairwise_achieves_full_coverage(self):
        self.assertEqual(missing_pairs(pairwise(MODEL), MODEL), frozenset())

    def test_pairwise_is_deterministic(self):
        self.assertEqual(pairwise(MODEL), pairwise(MODEL))

    def test_pairwise_is_not_larger_than_cartesian(self):
        cartesian = 1
        for values in MODEL.values():
            cartesian *= len(values)
        self.assertLessEqual(len(pairwise(MODEL)), cartesian)


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(missing_pairs))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_collapse_values, _bug_ignore_param,
                    _bug_always_covered, _bug_first_row_only):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("complete", scenarios)
        self.assertIn("pairwise_generator_full_coverage", scenarios)
        self.assertGreaterEqual(len(scenarios), 5)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)

    def test_json_mode_is_machine_readable(self):
        # --json stdout must be parseable JSON with no human-readable prefix lines.
        for run in (lambda: _run_self_test(as_json=True), lambda: main(["--json"])):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["harness"], "core/combinatorial_coverage")
            self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
