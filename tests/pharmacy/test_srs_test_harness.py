"""test_srs_test_harness.py — unittest suite for srs_test_harness (harness 37)."""

import math
import threading
import unittest
from datetime import datetime, timedelta

from harnesses.pharmacy.srs_test_harness import (
    EASE_FLOOR,
    EASE_INIT,
    MockMasteryStore,
    SRSSimulator,
    _weight_from_stats,
    run_all_scenarios,
    sm2_update,
)


class TestSM2Update(unittest.TestCase):

    def test_none_inputs_give_first_review_values(self):
        e, i, r = sm2_update(None, None, None, True)
        self.assertAlmostEqual(e, 2.6)
        self.assertEqual(i, 1)
        self.assertEqual(r, 1)

    def test_reps0_correct_gives_interval_1(self):
        _, i, _ = sm2_update(2.5, 0, 0, True)
        self.assertEqual(i, 1)

    def test_reps1_correct_gives_interval_6(self):
        _, i, _ = sm2_update(2.6, 1, 1, True)
        self.assertEqual(i, 6)

    def test_reps2_correct_scaled_by_ease(self):
        ease_in = 2.5
        _, i, _ = sm2_update(ease_in, 6, 2, True)
        expected = int(round(6 * ease_in))
        self.assertEqual(i, expected)

    def test_incorrect_resets_interval_to_0(self):
        _, i, _ = sm2_update(2.5, 20, 5, False)
        self.assertEqual(i, 0)

    def test_incorrect_resets_reps_to_0(self):
        _, _, r = sm2_update(2.5, 20, 5, False)
        self.assertEqual(r, 0)

    def test_ease_floor_at_1_3_after_many_incorrect(self):
        e = 2.5
        for _ in range(200):
            e, _, _ = sm2_update(e, 0, 0, False)
        self.assertAlmostEqual(e, EASE_FLOOR)

    def test_ease_never_below_floor(self):
        e, i, r = 1.3, 0, 0
        for _ in range(20):
            e, i, r = sm2_update(e, i, r, False)
            self.assertGreaterEqual(e, EASE_FLOOR)

    def test_correct_increases_ease(self):
        e_in = 2.5
        e_out, _, _ = sm2_update(e_in, 6, 2, True)
        self.assertGreater(e_out, e_in)

    def test_junk_string_inputs_do_not_raise(self):
        try:
            sm2_update("abc", "xyz", "!!!", True)
        except Exception as ex:
            self.fail(f"sm2_update raised on junk string inputs: {ex}")

    def test_junk_inputs_return_first_review_interval_and_reps(self):
        _, i, r = sm2_update("abc", None, "!!!", True)
        self.assertEqual(i, 1)
        self.assertEqual(r, 1)

    def test_nan_ease_replaced_with_default(self):
        e, i, r = sm2_update(float("nan"), 6, 2, True)
        self.assertTrue(math.isfinite(e))
        self.assertGreaterEqual(e, EASE_FLOOR)

    def test_ease_finite_after_1000_correct(self):
        e, i, r = 2.5, 6, 2
        for _ in range(1000):
            e, i, r = sm2_update(e, i, r, True)
        self.assertTrue(math.isfinite(e))
        self.assertLessEqual(e, 110.0)

    def test_convergence_interval_exceeds_100_in_20_rounds(self):
        sim = SRSSimulator(seed=42)
        history = sim.simulate(20, always_correct=True)
        self.assertTrue(any(h > 100 for h in history),
                        f"max after 20 rounds: {max(history)}")

    def test_interval_non_decreasing_over_consecutive_correct(self):
        e, i, r = None, None, None
        prev_i = -1
        for step in range(10):
            e, i, r = sm2_update(e, i, r, True)
            if step >= 2:
                self.assertGreaterEqual(i, prev_i,
                                        f"interval decreased at step {step}: {prev_i} -> {i}")
            prev_i = i

    def test_reps_increments_on_correct(self):
        _, _, r = sm2_update(2.5, 1, 3, True)
        self.assertEqual(r, 4)

    def test_reps_does_not_increment_on_incorrect(self):
        _, _, r = sm2_update(2.5, 6, 5, False)
        self.assertEqual(r, 0)


class TestCalculateWeight(unittest.TestCase):

    def _stats(self, correct, total, interval_days, days_ago, reps=3):
        reviewed = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        return {
            "correct": correct, "total": total,
            "ease_factor": 2.5, "interval_days": interval_days,
            "last_reviewed": reviewed, "repetitions": reps,
        }

    def test_overdue_weight_capped_at_50(self):
        s = self._stats(3, 5, 1, 40)
        self.assertEqual(_weight_from_stats(s), 50)

    def test_not_yet_due_weight_is_7(self):
        today = datetime.now().strftime("%Y-%m-%d")
        s = {"correct": 5, "total": 5, "ease_factor": 2.5,
             "interval_days": 3, "last_reviewed": today, "repetitions": 3}
        self.assertEqual(_weight_from_stats(s), 7)

    def test_monotone_with_increasing_days_ago(self):
        weights = [_weight_from_stats(self._stats(3, 5, 1, d)) for d in range(0, 50, 5)]
        for idx in range(len(weights) - 1):
            self.assertLessEqual(weights[idx], weights[idx + 1])

    def test_legacy_fallback_missed_5(self):
        s = {"correct": 0, "total": 5, "ease_factor": None,
             "interval_days": None, "last_reviewed": None, "repetitions": None}
        self.assertEqual(_weight_from_stats(s), 35)

    def test_no_stats_returns_base_10(self):
        self.assertEqual(_weight_from_stats(None), 10)
        self.assertEqual(_weight_from_stats({}), 10)

    def test_weight_min_is_1(self):
        today = datetime.now().strftime("%Y-%m-%d")
        s = {"correct": 100, "total": 100, "ease_factor": 2.5,
             "interval_days": 1000, "last_reviewed": today, "repetitions": 50}
        self.assertGreaterEqual(_weight_from_stats(s), 1)

    def test_all_correct_mastered_weight_is_low(self):
        today = datetime.now().strftime("%Y-%m-%d")
        s = {"correct": 10, "total": 10, "ease_factor": 2.5,
             "interval_days": 50, "last_reviewed": today, "repetitions": 5}
        self.assertLessEqual(_weight_from_stats(s), 10)


class TestMockMasteryStore(unittest.TestCase):

    def setUp(self):
        self.store = MockMasteryStore()

    def tearDown(self):
        self.store.close()

    def test_upsert_and_get_round_trip(self):
        self.store.upsert("Alice", "Lisinopril", 5, 7, 2.6, 6, 1, "2026-05-25")
        row = self.store.get("Alice", "Lisinopril")
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["ease_factor"], 2.6)
        self.assertEqual(row["interval_days"], 6)
        self.assertEqual(row["repetitions"], 1)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("Bob", "Metformin"))

    def test_upsert_replaces_existing(self):
        self.store.upsert("Alice", "Lisinopril", 1, 2, 2.5, 1, 1, "2026-05-20")
        self.store.upsert("Alice", "Lisinopril", 5, 6, 2.7, 10, 3, "2026-05-25")
        row = self.store.get("Alice", "Lisinopril")
        self.assertAlmostEqual(row["ease_factor"], 2.7)
        self.assertEqual(row["interval_days"], 10)

    def test_null_last_reviewed_persisted(self):
        self.store.upsert("Alice", "Metformin", 0, 3, None, None, 0, None)
        row = self.store.get("Alice", "Metformin")
        self.assertIsNone(row["last_reviewed"])

    def test_concurrent_upserts_are_safe(self):
        barrier = threading.Barrier(5)
        errors = []

        def worker(drug):
            barrier.wait()
            try:
                self.store.upsert("Alice", drug, 1, 1, 2.5, 1, 1, "2026-05-25")
            except Exception as e:
                errors.append(str(e))

        drugs = [f"Drug{n}" for n in range(5)]
        threads = [threading.Thread(target=worker, args=(d,)) for d in drugs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        for drug in drugs:
            self.assertIsNotNone(self.store.get("Alice", drug))


class TestSRSSimulator(unittest.TestCase):

    def test_simulate_returns_intervals(self):
        sim = SRSSimulator(seed=42)
        history = sim.simulate(5, always_correct=True)
        self.assertEqual(len(history), 5)

    def test_simulate_correct_starts_at_1(self):
        sim = SRSSimulator(seed=42)
        history = sim.simulate(1, always_correct=True)
        self.assertEqual(history[0], 1)

    def test_simulate_always_correct_grows(self):
        sim = SRSSimulator(seed=42)
        history = sim.simulate(20, always_correct=True)
        self.assertGreater(history[-1], history[0])


class TestSelfTest(unittest.TestCase):

    def test_all_self_test_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [],
                         "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count_at_least_14(self):
        results = run_all_scenarios(verbose=False)
        self.assertGreaterEqual(len(results), 14)


if __name__ == "__main__":
    unittest.main()
