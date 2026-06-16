"""Test suite for tracing_test_harness."""

import unittest

from harnesses.core.tracing_test_harness import (
    BUGGY_TRACES,
    SCENARIOS,
    BuggyPropagator,
    Propagator,
    Span,
    TraceConfig,
    TraceParent,
    TraceReport,
    _run_self_test,
    list_scenarios,
    valid_trace,
    validate_trace,
)


class TestTraceParent(unittest.TestCase):
    def test_roundtrip(self):
        tp = TraceParent("00", "4bf92f3577b34da6a3ce929d0e0e4736",
                         "b7ad6b7169203331", "01")
        self.assertEqual(TraceParent.parse(tp.format()), tp)

    def test_sampled_flag(self):
        self.assertTrue(TraceParent("00", "a" * 32, "b" * 16, "01").sampled)
        self.assertFalse(TraceParent("00", "a" * 32, "b" * 16, "00").sampled)

    def test_rejects_wrong_field_count(self):
        with self.assertRaises(ValueError):
            TraceParent.parse("00-aaaa-bbbb")

    def test_rejects_nonhex_trace_id(self):
        with self.assertRaises(ValueError):
            TraceParent.parse(f"00-{'g' * 32}-{'b' * 16}-01")

    def test_rejects_all_zero_trace_id(self):
        with self.assertRaises(ValueError):
            TraceParent.parse(f"00-{'0' * 32}-{'b' * 16}-01")

    def test_rejects_all_zero_span_id(self):
        with self.assertRaises(ValueError):
            TraceParent.parse(f"00-{'a' * 32}-{'0' * 16}-01")

    def test_rejects_version_ff(self):
        with self.assertRaises(ValueError):
            TraceParent.parse(f"ff-{'a' * 32}-{'b' * 16}-01")

    def test_rejects_short_span_id(self):
        with self.assertRaises(ValueError):
            TraceParent.parse(f"00-{'a' * 32}-{'b' * 8}-01")


class TestPropagator(unittest.TestCase):
    def test_roundtrip_preserves_ids(self):
        span = valid_trace()[0]
        tp = Propagator.extract(Propagator.inject(span))
        self.assertIsNotNone(tp)
        self.assertEqual(tp.trace_id, span.trace_id)
        self.assertEqual(tp.span_id, span.span_id)

    def test_buggy_propagator_drops_context(self):
        span = valid_trace()[0]
        self.assertIsNone(BuggyPropagator.extract(BuggyPropagator.inject(span)))


class TestValidatorOracle(unittest.TestCase):
    def test_valid_trace_is_valid(self):
        r = validate_trace(valid_trace())
        self.assertTrue(r.is_valid)
        self.assertEqual(r.root_count, 1)
        self.assertEqual(r.orphans, 0)
        self.assertEqual(r.span_count, 4)

    def test_deep_chain_no_false_orphan(self):
        self.assertEqual(validate_trace(valid_trace()).orphans, 0)

    def test_skew_within_tolerance_ok(self):
        spans = [
            Span("a" * 32, "1" * 16, None, "root", 1000, 9000),
            Span("a" * 32, "2" * 16, "1" * 16, "child", 600, 4000),
        ]
        r = validate_trace(spans, TraceConfig(required_attrs=(), max_clock_skew_ns=1000))
        self.assertEqual(r.skew_violations, 0)

    def test_skew_exceeds_tolerance_flagged(self):
        spans = [
            Span("a" * 32, "1" * 16, None, "root", 10000, 90000),
            Span("a" * 32, "2" * 16, "1" * 16, "child", 5000, 40000),
        ]
        r = validate_trace(spans, TraceConfig(required_attrs=(), max_clock_skew_ns=1000))
        self.assertGreaterEqual(r.skew_violations, 1)


class TestBuggyTracesCaught(unittest.TestCase):
    def test_every_buggy_trace_flips_its_field(self):
        for name, (builder, field_name) in BUGGY_TRACES.items():
            r = validate_trace(builder())
            count = getattr(r, field_name)
            if field_name == "root_count":
                self.assertNotEqual(count, 1, f"{name}: root_count={count}")
            else:
                self.assertGreaterEqual(count, 1, f"{name}: {field_name}={count}")
            self.assertFalse(r.is_valid, f"{name} should be invalid")


class TestTraceReport(unittest.TestCase):
    def test_is_valid_requires_all_clean(self):
        clean = TraceReport(3, 1, 0, 0, 0, 0, 0, 0, 0)
        self.assertTrue(clean.is_valid)
        self.assertFalse(TraceReport(3, 2, 0, 0, 0, 0, 0, 0, 0).is_valid)
        self.assertFalse(TraceReport(3, 1, 1, 0, 0, 0, 0, 0, 0).is_valid)


class TestSelfTest(unittest.TestCase):
    def test_has_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)

    def test_scenarios_unique(self):
        self.assertEqual(len(list_scenarios()), len(set(list_scenarios())))
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
