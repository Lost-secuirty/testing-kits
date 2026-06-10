"""test_lockout_test_harness.py — unittest suite for lockout_test_harness (harness 39)."""

import threading
import unittest

from harnesses.pharmacy.lockout_test_harness import (
    BuggyLockoutManager,
    BuggyLockoutManager2,
    FakeClock,
    LockoutManager,
    LOCKOUT_THRESHOLD,
    LOCKOUT_SECONDS,
    run_all_scenarios,
)


def _fresh(threshold=3, lockout_seconds=300):
    clock = FakeClock(0.0)
    mgr = LockoutManager(threshold=threshold, lockout_seconds=lockout_seconds, clock=clock)
    return mgr, clock


class TestFakeClock(unittest.TestCase):

    def test_initial_time(self):
        c = FakeClock(start=100.0)
        self.assertEqual(c.now(), 100.0)

    def test_advance(self):
        c = FakeClock(0.0)
        c.advance(50)
        self.assertEqual(c.now(), 50.0)

    def test_multiple_advances_cumulate(self):
        c = FakeClock(0.0)
        c.advance(10)
        c.advance(20)
        self.assertEqual(c.now(), 30.0)


class TestLockoutManager(unittest.TestCase):

    def test_first_attempt_not_locked(self):
        mgr, _ = _fresh()
        self.assertFalse(mgr.is_locked("alice"))

    def test_threshold_minus_1_not_locked(self):
        mgr, _ = _fresh(threshold=3)
        mgr.record_failure("alice")
        mgr.record_failure("alice")
        self.assertFalse(mgr.is_locked("alice"))

    def test_exact_threshold_locks(self):
        mgr, _ = _fresh(threshold=3)
        for _ in range(3):
            mgr.record_failure("alice")
        self.assertTrue(mgr.is_locked("alice"))

    def test_locked_at_t_299(self):
        mgr, clock = _fresh(lockout_seconds=300)
        for _ in range(3):
            mgr.record_failure("alice")
        clock.advance(299)
        self.assertTrue(mgr.is_locked("alice"))

    def test_released_at_t_300(self):
        mgr, clock = _fresh(lockout_seconds=300)
        for _ in range(3):
            mgr.record_failure("alice")
        clock.advance(300)
        self.assertFalse(mgr.is_locked("alice"))

    def test_counter_resets_after_window(self):
        mgr, clock = _fresh(threshold=3, lockout_seconds=300)
        for _ in range(3):
            mgr.record_failure("alice")
        clock.advance(300)
        mgr.record_failure("alice")  # single failure after release
        self.assertFalse(mgr.is_locked("alice"))

    def test_success_resets_counter(self):
        mgr, _ = _fresh(threshold=3)
        mgr.record_failure("alice")
        mgr.record_failure("alice")
        mgr.record_success("alice")
        mgr.record_failure("alice")  # starts from 0
        self.assertFalse(mgr.is_locked("alice"))

    def test_per_user_isolation(self):
        mgr, _ = _fresh(threshold=3)
        for _ in range(3):
            mgr.record_failure("alice")
        self.assertFalse(mgr.is_locked("bob"))

    def test_configurable_threshold_1(self):
        mgr, _ = _fresh(threshold=1)
        mgr.record_failure("alice")
        self.assertTrue(mgr.is_locked("alice"))

    def test_configurable_threshold_5(self):
        mgr, _ = _fresh(threshold=5)
        for _ in range(4):
            mgr.record_failure("alice")
        self.assertFalse(mgr.is_locked("alice"))
        mgr.record_failure("alice")
        self.assertTrue(mgr.is_locked("alice"))

    def test_new_user_auto_initialised(self):
        mgr, _ = _fresh()
        self.assertFalse(mgr.is_locked("newuser"))

    def test_boundary_exact_299_still_locked(self):
        mgr, clock = _fresh(lockout_seconds=300)
        for _ in range(3):
            mgr.record_failure("alice")
        clock.advance(299)
        self.assertTrue(mgr.is_locked("alice"))

    def test_boundary_exact_300_released(self):
        mgr, clock = _fresh(lockout_seconds=300)
        for _ in range(3):
            mgr.record_failure("alice")
        clock.advance(300)
        self.assertFalse(mgr.is_locked("alice"))


class TestBuggyManagers(unittest.TestCase):

    def test_buggy_manager_never_unlocks(self):
        clock = FakeClock(0.0)
        buggy = BuggyLockoutManager(threshold=3, lockout_seconds=300, clock=clock)
        for _ in range(3):
            buggy.record_failure("alice")
        clock.advance(300)
        self.assertTrue(buggy.is_locked("alice"))  # bug: stays locked

    def test_buggy_manager2_never_locks(self):
        clock = FakeClock(0.0)
        buggy = BuggyLockoutManager2(threshold=3, lockout_seconds=300, clock=clock)
        for _ in range(10):
            buggy.record_failure("alice")
        self.assertFalse(buggy.is_locked("alice"))  # bug: never locked


class TestConcurrency(unittest.TestCase):

    def test_concurrent_failures_safe(self):
        clock = FakeClock(0.0)
        mgr = LockoutManager(threshold=3, lockout_seconds=300, clock=clock)
        barrier = threading.Barrier(3)
        errors = []

        def worker():
            barrier.wait()
            try:
                mgr.record_failure("alice")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        # After exactly threshold failures, user must be locked
        self.assertTrue(mgr.is_locked("alice"))

    def test_two_thread_threshold2_exactly_one_triggers_lock(self):
        clock = FakeClock(0.0)
        mgr = LockoutManager(threshold=2, lockout_seconds=300, clock=clock)
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            mgr.record_failure("user")

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Both failures fired: user should be locked
        self.assertTrue(mgr.is_locked("user"))


class TestSelfTest(unittest.TestCase):

    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [],
                         "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count_at_least_12(self):
        results = run_all_scenarios(verbose=False)
        self.assertGreaterEqual(len(results), 12)


if __name__ == "__main__":
    unittest.main()
