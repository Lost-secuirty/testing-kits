"""Unittest wrapper for tools/corpus_size_checker.py — a declared corpus_size must count the
collection its prove() actually judges.

(a) the checker's own --self-test must bite: an anchored fixture reads OK, a fixture that counts
an input the verdict ignores reads MISLABELED, and a dynamic-corpus fixture reads UNANALYZABLE;
(b) no non-legacy harness may be MISLABELED — corpus_size must anchor to the iterated/compared
corpus, not an unrelated input (UNANALYZABLE is advisory and does not fail the gate).
"""

from __future__ import annotations

import unittest

from tools import corpus_size_checker


class CorpusSizeCheckerTest(unittest.TestCase):
    def test_checker_self_test_detects_mislabeled_fixture(self) -> None:
        self.assertEqual(corpus_size_checker.main(["--self-test"]), 0)

    def test_no_harness_corpus_size_is_mislabeled(self) -> None:
        self.assertEqual(corpus_size_checker.run_gate(), 0)


if __name__ == "__main__":
    unittest.main()
