"""Unittest wrapper for tools/prove_purity_checker.py — every TEETH prove() must be pure.

(a) the checker's own --self-test must bite: a pure fixture reads OK while an impure one,
whose prove reaches a clock through a helper, reads IMPURE (so the call-graph walk works);
(b) no non-legacy harness may have a prove() that reaches a clock / RNG / network /
filesystem call — prove must judge a frozen corpus deterministically.
"""

from __future__ import annotations

import unittest

from tools import prove_purity_checker


class ProvePurityTest(unittest.TestCase):
    def test_checker_self_test_detects_impure_fixture(self) -> None:
        self.assertEqual(prove_purity_checker.main(["--self-test"]), 0)

    def test_no_harness_prove_is_impure(self) -> None:
        self.assertEqual(prove_purity_checker.run_gate(), 0)


if __name__ == "__main__":
    unittest.main()
