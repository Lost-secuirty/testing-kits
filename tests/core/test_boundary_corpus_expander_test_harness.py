"""Test suite for boundary_corpus_expander_test_harness."""

import contextlib
import io
import json
import unittest

from harnesses.core.boundary_corpus_expander_test_harness import (
    _BOUNDARY_VALUES,
    _USEP,
    ANCHORS,
    CORPUS,
    TEETH,
    _bug_counts_duplicates,
    _bug_drops_anchor,
    _bug_invents_value,
    _bug_skips_boundary_class,
    _run_self_test,
    expand,
    list_scenarios,
    main,
    prove,
)


class TestExpand(unittest.TestCase):
    def test_corpus_expectations_match_oracle(self):
        for name, base, expected in CORPUS:
            with self.subTest(case=name):
                self.assertEqual(expand(base, _BOUNDARY_VALUES), expected)

    def test_preserves_anchors(self):
        base = ("x", "PLANTED_BAD", "y")
        out = expand(base, _BOUNDARY_VALUES)
        for anchor in ANCHORS:
            self.assertIn(anchor, out)

    def test_dedups_existing_boundary_value(self):
        # Base already has 0; expansion must not add a second 0.
        out = expand(("0-holder", 0), _BOUNDARY_VALUES)
        self.assertEqual(sum(1 for v in out if type(v) is int and v == 0), 1)

    def test_adds_every_boundary_class(self):
        out = expand((), _BOUNDARY_VALUES)
        for value in _BOUNDARY_VALUES:
            self.assertIn(value, out)

    def test_unicode_separator_is_u2028(self):
        self.assertEqual(_USEP, chr(0x2028))
        self.assertIn(_USEP, expand((), _BOUNDARY_VALUES))

    def test_idempotent(self):
        once = expand(("a", "PLANTED_BAD"), _BOUNDARY_VALUES)
        twice = expand(once, _BOUNDARY_VALUES)
        self.assertEqual(once, twice)

    def test_preserves_base_order(self):
        out = expand(("first", "second"), _BOUNDARY_VALUES)
        self.assertEqual(out[:2], ("first", "second"))


class TestTeeth(unittest.TestCase):
    def test_oracle_is_clean(self):
        self.assertFalse(prove(expand))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl))

    def test_named_mutants_caught(self):
        for bug in (_bug_drops_anchor, _bug_skips_boundary_class,
                    _bug_invents_value, _bug_counts_duplicates):
            with self.subTest(bug=bug.__name__):
                self.assertTrue(prove(bug))

    def test_drops_anchor_actually_drops(self):
        out = _bug_drops_anchor(("PLANTED_BAD", "x"), _BOUNDARY_VALUES)
        self.assertNotIn("PLANTED_BAD", out)

    def test_corpus_size_matches(self):
        self.assertEqual(TEETH.corpus_size, len(CORPUS))


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("basic", scenarios)
        self.assertIn("drops_anchor", scenarios)
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
            self.assertEqual(payload["harness"], "core/boundary_corpus_expander")
            self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
