"""Test suite for error_path_leak_test_harness."""

import unittest

from harnesses._teeth import verify
from harnesses.core.error_path_leak_test_harness import (
    LEAK_CORPUS,
    TEETH,
    LeakProbeConfig,
    LeakProbeResult,
    LeakRunner,
    ResourceTracker,
    TargetSpec,
    TransientError,
    _make_double_release,
    _make_good_pool,
    _make_good_with_context,
    _make_leaky_fd,
    _make_leaky_pool,
    _run_self_test,
    leak_raw_exception,
    list_leak_scenarios,
    list_scenarios,
    oracle_handle,
    prove,
)


class TestResourceTracker(unittest.TestCase):
    def test_initial_state(self):
        t = ResourceTracker()
        self.assertEqual(t.live, 0)
        self.assertEqual(t.stats["high_water"], 0)

    def test_acquire_increments_live(self):
        t = ResourceTracker()
        t.on_acquire()
        self.assertEqual(t.live, 1)

    def test_release_decrements_live(self):
        t = ResourceTracker()
        t.on_acquire()
        t.on_release()
        self.assertEqual(t.live, 0)

    def test_high_water_records_peak(self):
        t = ResourceTracker()
        t.on_acquire()
        t.on_acquire()
        t.on_acquire()
        t.on_release()
        self.assertEqual(t.stats["high_water"], 3)
        self.assertEqual(t.live, 2)

    def test_release_without_acquire_goes_negative(self):
        t = ResourceTracker()
        t.on_release()
        self.assertEqual(t.live, -1)


class TestLeakRunner(unittest.TestCase):
    def setUp(self):
        self.config = LeakProbeConfig(iterations=200, error_rate=0.5)

    def test_good_pool_no_leak(self):
        runner = LeakRunner(self.config)
        result = runner.run(_make_good_pool())
        self.assertFalse(result.leaked)
        self.assertEqual(result.final_live, 0)

    def test_leaky_pool_detected(self):
        runner = LeakRunner(self.config)
        result = runner.run(_make_leaky_pool())
        self.assertTrue(result.leaked)
        self.assertGreater(result.final_live, 0)

    def test_leaky_fd_detected(self):
        runner = LeakRunner(self.config)
        result = runner.run(_make_leaky_fd())
        self.assertTrue(result.leaked)

    def test_good_context_manager_no_leak(self):
        runner = LeakRunner(self.config)
        result = runner.run(_make_good_with_context())
        self.assertFalse(result.leaked)
        self.assertEqual(result.final_live, 0)

    def test_double_release_goes_negative(self):
        runner = LeakRunner(self.config)
        result = runner.run(_make_double_release())
        self.assertLess(result.final_live, 0)

    def test_errors_injected_matches_iterations(self):
        runner = LeakRunner(self.config)
        result = runner.run(_make_good_pool())
        self.assertLessEqual(result.errors_injected, self.config.iterations)
        self.assertGreater(result.errors_injected, 0)

    def test_zero_error_rate_no_injection(self):
        config = LeakProbeConfig(iterations=100, error_rate=0.0)
        runner = LeakRunner(config)
        result = runner.run(_make_good_pool())
        self.assertEqual(result.errors_injected, 0)

    def test_reproducible_with_seed(self):
        config1 = LeakProbeConfig(iterations=100, seed=42)
        config2 = LeakProbeConfig(iterations=100, seed=42)
        r1 = LeakRunner(config1).run(_make_good_pool())
        r2 = LeakRunner(config2).run(_make_good_pool())
        self.assertEqual(r1.errors_injected, r2.errors_injected)


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios_count(self):
        self.assertEqual(len(list_scenarios()), 5)

    def test_list_scenarios_includes_good_and_bad(self):
        scenarios = list_scenarios()
        self.assertIn("good_pool", scenarios)
        self.assertIn("leaky_pool_error_path", scenarios)
        self.assertIn("double_release", scenarios)

    def test_self_test_passes(self):
        rc = _run_self_test(LeakProbeConfig(iterations=200, error_rate=0.3))
        self.assertEqual(rc, 0)


class TestTransientError(unittest.TestCase):
    def test_is_runtime_error(self):
        self.assertTrue(issubclass(TransientError, RuntimeError))


class TestLeakProbeResult(unittest.TestCase):
    def test_fields(self):
        r = LeakProbeResult(name="x", iterations=10, errors_injected=3,
                            final_live=2, high_water=5, leaked=True)
        self.assertTrue(r.leaked)
        self.assertEqual(r.high_water, 5)


# ---------------------------------------------------------------------------
# Teeth: the harness must catch a real planted error-path information leak.
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct sanitizing handler must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_handle))

    def test_every_mutant_is_caught(self):
        self.assertGreaterEqual(len(TEETH.mutants), 1)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl),
                            f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(LEAK_CORPUS))

    def test_oracle_leaks_nothing(self):
        # On every frozen scenario the oracle returns the exact public message
        # and contains none of the forbidden tokens.
        for sc in LEAK_CORPUS:
            out = oracle_handle(sc)
            self.assertEqual(out, sc.public_message)
            for token in sc.forbidden:
                self.assertNotIn(token, out, f"{sc.name}: leaked {token!r}")

    def test_raw_exception_mutant_leaks_secret(self):
        # The DSN-password scenario must actually surface the secret via the
        # raw-exception-echo mutant, proving the corpus has real teeth.
        db = next(s for s in LEAK_CORPUS if s.name == "db_connect_dsn_password")
        self.assertIn(db.secret, leak_raw_exception(db))

    def test_list_leak_scenarios(self):
        names = list_leak_scenarios()
        self.assertEqual(len(names), len(LEAK_CORPUS))
        self.assertIn("db_connect_dsn_password", names)


if __name__ == "__main__":
    unittest.main()
