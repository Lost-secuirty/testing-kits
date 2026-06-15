"""Tests for the staged TestHarnessCard ratchet (cards/harness_card.py).

Fast: exercises the pure ratchet logic + the tool's own self-test. Does NOT call
build_cards (which runs the full teeth swap-check across every harness in subprocesses)."""

import unittest

from cards import harness_card as hc


class TeethRatchetTests(unittest.TestCase):
    def test_self_test_passes(self) -> None:
        self.assertEqual(hc._run_self_test(), 0)

    def test_rank_is_monotonic(self) -> None:
        self.assertLess(hc._RANK["legacy"], hc._RANK["pending"])
        self.assertLess(hc._RANK["pending"], hc._RANK["required"])

    def test_catches_required_to_pending_regression(self) -> None:
        ratchet = {"core/x": "required"}
        cards = [{"key": "core/x", "teeth_status": "pending", "paired_test_exists": True,
                  "mutants_total": 0, "mutants_caught": 0}]
        problems = hc.check_ratchet(cards, ratchet)
        self.assertTrue(any("REGRESSED" in p for p in problems), problems)

    def test_passes_when_status_held(self) -> None:
        ratchet = {"core/x": "required"}
        cards = [{"key": "core/x", "teeth_status": "required", "paired_test_exists": True,
                  "mutants_total": 1, "mutants_caught": 1}]
        self.assertEqual(hc.check_ratchet(cards, ratchet), [])

    def test_allows_upgrade(self) -> None:
        # pinned 'pending', now 'required' — an upgrade is never a regression.
        ratchet = {"core/x": "pending"}
        cards = [{"key": "core/x", "teeth_status": "required", "paired_test_exists": True,
                  "mutants_total": 1, "mutants_caught": 1}]
        self.assertEqual(hc.check_ratchet(cards, ratchet), [])

    def test_required_missing_paired_test_is_flagged(self) -> None:
        ratchet = {"core/x": "required"}
        cards = [{"key": "core/x", "teeth_status": "required", "paired_test_exists": False,
                  "mutants_total": 1, "mutants_caught": 1}]
        problems = hc.check_ratchet(cards, ratchet)
        self.assertTrue(any("paired unittest is missing" in p for p in problems), problems)

    def test_vanished_pinned_harness_is_flagged(self) -> None:
        ratchet = {"core/gone": "required"}
        problems = hc.check_ratchet([], ratchet)
        self.assertTrue(any("no longer exists" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
