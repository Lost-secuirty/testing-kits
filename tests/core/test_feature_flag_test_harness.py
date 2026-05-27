"""Test suite for feature_flag_test_harness."""

import unittest

from harnesses.core.feature_flag_test_harness import (
    ComboResult,
    Flag,
    FlagMatrixConfig,
    FlagMatrixRunner,
    FlagSet,
    _make_flagset,
    _run_self_test,
    buggy_pricer_combo,
    buggy_pricer_crash,
    buggy_pricer_type_drift,
    flag_expects,
    good_pricer,
    list_scenarios,
)


class TestFlag(unittest.TestCase):
    def test_flag_defaults(self):
        f = Flag("x")
        self.assertEqual(f.name, "x")
        self.assertFalse(f.default)
        self.assertFalse(f.deprecated)

    def test_flag_is_hashable(self):
        f = Flag("x")
        self.assertEqual(hash(f), hash(Flag("x")))


class TestFlagSet(unittest.TestCase):
    def test_register_and_all(self):
        fs = FlagSet()
        fs.register(Flag("a"))
        fs.register(Flag("b", default=True))
        names = [f.name for f in fs.all()]
        self.assertEqual(set(names), {"a", "b"})

    def test_default_mismatch_detection(self):
        fs = FlagSet()
        fs.register(Flag("x", default=True))
        fs.register(Flag("x", default=False))
        self.assertEqual(fs.default_mismatches(), ["x"])

    def test_no_mismatch_when_defaults_align(self):
        fs = FlagSet()
        fs.register(Flag("x", default=True))
        fs.register(Flag("x", default=True))
        self.assertEqual(fs.default_mismatches(), [])


class TestFlagMatrixRunner(unittest.TestCase):
    def setUp(self):
        self.config = FlagMatrixConfig()
        self.runner = FlagMatrixRunner(self.config)
        self.fs = _make_flagset()

    def test_combos_cover_all_pairs(self):
        combos = self.runner._combos(self.fs.all())
        # 4 flags, C(4,2)=6 pairs, each with 4 boolean assignments → up to 24,
        # but many collapse via dedup.
        self.assertGreater(len(combos), 0)
        # Ensure each flag is toggled both ways in at least one combo.
        for flag in self.fs.all():
            vals = {c[flag.name] for c in combos}
            self.assertEqual(vals, {True, False})

    def test_triple_wise_yields_more(self):
        triple = FlagMatrixRunner(FlagMatrixConfig(enable_triple_wise=True))
        c2 = self.runner._combos(self.fs.all())
        c3 = triple._combos(self.fs.all())
        self.assertGreaterEqual(len(c3), len(c2))

    def test_good_pricer_has_no_crashes(self):
        results = self.runner.run(good_pricer, self.fs)
        crashes = [r for r in results if r.outcome == "crash"]
        self.assertEqual(crashes, [])

    def test_buggy_pricer_crash_detected(self):
        results = self.runner.run(buggy_pricer_crash, self.fs)
        crashes = [r for r in results if r.outcome == "crash"]
        self.assertGreater(len(crashes), 0)

    def test_buggy_pricer_combo_violation_detected(self):
        results = self.runner.run(buggy_pricer_combo, self.fs)
        vios = [r for r in results if r.outcome == "expectation_violation"]
        self.assertGreater(len(vios), 0)

    def test_type_drift_detected(self):
        results = self.runner.run(buggy_pricer_type_drift, self.fs)
        drifts = [r for r in results if r.outcome == "type_mismatch"]
        self.assertGreater(len(drifts), 0)

    def test_flip_mid_call_runs_one_flip_per_flag(self):
        results = self.runner.flip_mid_call(good_pricer, self.fs)
        self.assertEqual(len(results), len(self.fs.all()))


class TestExpectsDecorator(unittest.TestCase):
    def test_decorator_attaches_metadata(self):
        @flag_expects({"a": True}, returns=42)
        def fn(flags):
            return 42

        self.assertEqual(fn._flag_expects, [({"a": True}, 42)])

    def test_decorator_stacks(self):
        @flag_expects({"a": True}, returns=1)
        @flag_expects({"a": False}, returns=2)
        def fn(flags):
            return 0

        self.assertEqual(len(fn._flag_expects), 2)


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        scenarios = list_scenarios()
        self.assertIn("good_pricer", scenarios)
        self.assertIn("buggy_pricer_combo", scenarios)
        self.assertGreaterEqual(len(scenarios), 5)

    def test_self_test_passes(self):
        rc = _run_self_test(FlagMatrixConfig())
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
