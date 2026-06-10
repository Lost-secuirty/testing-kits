"""Test suite for cardinality_test_harness."""

import unittest

from harnesses.core.cardinality_test_harness import (
    SCENARIOS,
    CardinalityConfig,
    CardinalityProbe,
    CardinalityReport,
    _run_self_test,
    assert_bounded_cardinality,
    list_scenarios,
)


class TestProbe(unittest.TestCase):
    def test_records_distinct(self):
        probe = CardinalityProbe(["tier"])
        for v in ["free", "free", "pro", "pro", "enterprise"]:
            probe.emit("tier", v)
        report = probe.report()
        self.assertEqual(report[0].samples, 5)
        self.assertEqual(report[0].distinct, 3)

    def test_unknown_dimension_auto_registers(self):
        probe = CardinalityProbe([])
        probe.emit("x", "a")
        probe.emit("x", "b")
        report = probe.report()
        self.assertEqual(report[0].dimension, "x")
        self.assertEqual(report[0].distinct, 2)

    def test_bounded_at_low_ratio(self):
        probe = CardinalityProbe(["d"])
        for i in range(100):
            probe.emit("d", i % 5)
        report = probe.report(threshold=0.5)
        self.assertTrue(report[0].bounded)

    def test_unbounded_at_high_ratio(self):
        probe = CardinalityProbe(["d"])
        for i in range(100):
            probe.emit("d", str(i))
        report = probe.report(threshold=0.5)
        self.assertFalse(report[0].bounded)


class TestAssertHelper(unittest.TestCase):
    def test_passes_under_bound(self):
        probe = CardinalityProbe(["d"])
        for i in range(10):
            probe.emit("d", i % 3)
        assert_bounded_cardinality(probe, "d", max_distinct=5)

    def test_raises_over_bound(self):
        probe = CardinalityProbe(["d"])
        for i in range(50):
            probe.emit("d", i)
        with self.assertRaises(AssertionError):
            assert_bounded_cardinality(probe, "d", max_distinct=5)


class TestScenarios(unittest.TestCase):
    def test_list_count(self):
        self.assertEqual(len(list_scenarios()), 6)

    def test_all_scenarios_match_expectation(self):
        config = CardinalityConfig(samples=500)
        for name, (emit_fn, dim, expected) in SCENARIOS.items():
            with self.subTest(scenario=name):
                probe = CardinalityProbe([dim])
                emit_fn(probe, config.samples)
                report = probe.report(threshold=config.growth_threshold)
                self.assertEqual(report[0].bounded, expected, f"{name}: {report[0]}")

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(CardinalityConfig(samples=500)), 0)


class TestReport(unittest.TestCase):
    def test_report_fields(self):
        r = CardinalityReport(dimension="x", samples=10, distinct=3,
                              growth_ratio=0.3, bounded=True)
        self.assertEqual(r.dimension, "x")
        self.assertTrue(r.bounded)


if __name__ == "__main__":
    unittest.main()
