"""Unittest wrapper for tools/fragility_checker.py — the mutant-fragility (corpus-
cardinality floor) gate.

This gate is ADVISORY (DP3): it never red-locks main, so — like the dead_expr test and
unlike the purity/circularity tests — there is intentionally NO assertion that the live
repo is fragility-clean. The tests here verify the CHECKER itself:

(a) its --self-test bites: a >=2-case fixture reads OK, a single-case fixture reads FRAGILE,
    the same fixture under an exempt entry reads EXEMPT, and a dynamic corpus_size reads
    UNANALYZABLE;
(b) it analyses every shipped harness without raising and returns a well-formed verdict;
(c) every shipped FRAGILITY_EXEMPT entry is live (names a discovered harness that still
    reads below the floor) — a stale waiver is itself a defect.

(b) keeps the real-repo code path exercised on all CI Pythons without making cleanliness a
required check (which would make the gate required, not advisory).
"""

from __future__ import annotations

import unittest

from tools import fragility_checker


class FragilityCheckerTest(unittest.TestCase):
    def test_checker_self_test_bites(self) -> None:
        self.assertEqual(fragility_checker.main(["--self-test"]), 0)

    def test_runs_over_every_harness_without_error(self) -> None:
        for path in fragility_checker._discover():
            with self.subTest(harness=path.name):
                result = fragility_checker.check_harness(path)
                self.assertIn(result["status"],
                              {"OK", "FRAGILE", "EXEMPT", "UNANALYZABLE", "NO_TEETH"})
                self.assertIsInstance(result["findings"], list)

    def test_no_stale_exempt_waivers(self) -> None:
        discovered = {p.relative_to(fragility_checker.ROOT).as_posix()
                      for p in fragility_checker._discover()}
        self.assertEqual(fragility_checker._stale_waivers(discovered), [])


if __name__ == "__main__":
    unittest.main()
