"""Unittest wrapper for tools/dead_expr_checker.py — the dead-expression gate.

This gate is ADVISORY (DP3): it never red-locks main, so — unlike the purity/circularity
tests — there is intentionally NO assertion that the live repo is dead-expr-clean. The tests
here verify the CHECKER itself:

(a) its --self-test bites: a clean fixture reads OK with zero findings while a fixture with one
    bare expression of every flagged kind reads DEAD_EXPR with exactly that many findings, and
    docstrings / ``...`` / calls are never counted;
(b) it analyses every shipped harness without raising and returns a well-formed verdict.

(b) keeps the real-repo code path exercised on all five CI Pythons without making cleanliness a
required check (which would make the gate required, not advisory).
"""

from __future__ import annotations

import unittest

from tools import dead_expr_checker


class DeadExprCheckerTest(unittest.TestCase):
    def test_checker_self_test_detects_dead_fixture(self) -> None:
        self.assertEqual(dead_expr_checker.main(["--self-test"]), 0)

    def test_runs_over_every_harness_without_error(self) -> None:
        for path in dead_expr_checker._discover():
            with self.subTest(harness=path.name):
                result = dead_expr_checker.check_harness(path)
                self.assertIn(result["status"], {"OK", "DEAD_EXPR"})
                self.assertIsInstance(result["findings"], list)


if __name__ == "__main__":
    unittest.main()
