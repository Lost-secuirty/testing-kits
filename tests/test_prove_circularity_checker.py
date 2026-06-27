"""Unittest wrapper for tools/prove_circularity_checker.py — no prove() may call its oracle.

(a) the checker's --self-test must bite: a clean prove (compares to a frozen literal) reads OK,
while a prove that recomputes the expected value by calling its oracle through a helper reads
CIRCULAR; (b) no non-legacy harness's prove() calls the TEETH oracle at runtime — prove must
judge a frozen literal corpus, which is the non-circularity discipline the swap-check can't see.
"""

from __future__ import annotations

import unittest

from tools import prove_circularity_checker


class ProveCircularityTest(unittest.TestCase):
    def test_checker_self_test_detects_circular_fixture(self) -> None:
        self.assertEqual(prove_circularity_checker.main(["--self-test"]), 0)

    def test_no_harness_prove_is_circular(self) -> None:
        self.assertEqual(prove_circularity_checker.run_gate(), 0)


if __name__ == "__main__":
    unittest.main()
