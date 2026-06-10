"""Test suite for null_propagation_test_harness."""

import math
import unittest
from dataclasses import dataclass
from typing import Optional

from harnesses.core.null_propagation_test_harness import (
    MUTATORS,
    NullProbeConfig,
    NullProbeRunner,
    Outcome,
    ProbeResult,
    TargetSpec,
    _walk,
    list_scenarios,
    summarize,
)


class TestMutators(unittest.TestCase):
    def test_none_replaces_anything(self):
        self.assertIsNone(MUTATORS["none"]("x"))
        self.assertIsNone(MUTATORS["none"](42))
        self.assertIsNone(MUTATORS["none"]({"a": 1}))

    def test_empty_string_to_empty_string(self):
        self.assertEqual(MUTATORS["empty"]("hello"), "")

    def test_empty_dict_to_empty_dict(self):
        self.assertEqual(MUTATORS["empty"]({"a": 1}), {})

    def test_empty_list_to_empty_list(self):
        self.assertEqual(MUTATORS["empty"]([1, 2, 3]), [])

    def test_empty_passes_through_for_unmatched_type(self):
        self.assertEqual(MUTATORS["empty"](42), 42)

    def test_nan_on_numbers(self):
        result = MUTATORS["nan"](1.0)
        self.assertTrue(math.isnan(result))

    def test_nan_passes_through_non_numbers(self):
        self.assertEqual(MUTATORS["nan"]("hello"), "hello")

    def test_missing_key_drops_first_key(self):
        result = MUTATORS["missing_key"]({"a": 1, "b": 2})
        self.assertEqual(len(result), 1)

    def test_missing_key_no_op_on_empty_dict(self):
        self.assertEqual(MUTATORS["missing_key"]({}), {})

    def test_empty_list_specific(self):
        self.assertEqual(MUTATORS["empty_list"]([1, 2]), [])
        self.assertEqual(MUTATORS["empty_list"]("hello"), "hello")


class TestWalker(unittest.TestCase):
    def test_walk_root_only_for_scalar(self):
        out = list(_walk(42))
        # The walker yields at least the root.
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0][0], "<root>")

    def test_walk_dict_yields_root_plus_children(self):
        out = list(_walk({"a": 1, "b": 2}, "u", max_depth=3))
        paths = [t[0] for t in out]
        self.assertIn("u", paths)
        self.assertIn("u.a", paths)
        self.assertIn("u.b", paths)

    def test_walk_dict_rebuild_replaces_correctly(self):
        original = {"a": 1, "b": 2}
        for path, rebuild, value in _walk(original, "u", max_depth=2):
            if path == "u.a":
                rebuilt = rebuild(99)
                self.assertEqual(rebuilt, {"a": 99, "b": 2})
                return
        self.fail("did not visit u.a")

    def test_walk_nested_dict(self):
        original = {"profile": {"address": {"zip": "94110"}}}
        paths = [t[0] for t in _walk(original, "user", max_depth=4)]
        self.assertIn("user.profile.address.zip", paths)

    def test_walk_depth_limit(self):
        original = {"a": {"b": {"c": {"d": 1}}}}
        paths = [t[0] for t in _walk(original, "u", max_depth=2)]
        # max_depth=2 means root + 2 levels — c.d should NOT be visited.
        self.assertNotIn("u.a.b.c.d", paths)

    def test_walk_list(self):
        original = [10, 20, 30]
        paths = [t[0] for t in _walk(original, "items", max_depth=2)]
        self.assertIn("items[0]", paths)
        self.assertIn("items[2]", paths)

    def test_walk_list_rebuild(self):
        original = [10, 20, 30]
        for path, rebuild, value in _walk(original, "items", max_depth=2):
            if path == "items[1]":
                self.assertEqual(rebuild(99), [10, 99, 30])
                return
        self.fail("did not visit items[1]")


@dataclass
class _Box:
    x: Optional[int] = None
    y: Optional[str] = None


class TestWalkerDataclass(unittest.TestCase):
    def test_walk_dataclass_yields_fields(self):
        box = _Box(x=1, y="hi")
        paths = [t[0] for t in _walk(box, "box", max_depth=2)]
        self.assertIn("box.x", paths)
        self.assertIn("box.y", paths)

    def test_walk_dataclass_rebuild(self):
        box = _Box(x=1, y="hi")
        for path, rebuild, value in _walk(box, "box", max_depth=2):
            if path == "box.x":
                new = rebuild(99)
                self.assertEqual(new.x, 99)
                self.assertEqual(new.y, "hi")
                return
        self.fail("did not visit box.x")


class TestProbeRunner(unittest.TestCase):
    def setUp(self):
        self.config = NullProbeConfig(depth=2)
        self.runner = NullProbeRunner(self.config)

    def test_guarded_target_is_handled(self):
        def good(x):
            if not isinstance(x, dict) or not x.get("k"):
                raise ValueError("x.k required")
            return x["k"]

        target = TargetSpec(good, {"x": {"k": "v"}}, name="good")
        results = self.runner.run([target])
        crashes = [r for r in results if r.outcome == Outcome.CRASH]
        self.assertEqual(crashes, [])

    def test_unguarded_target_crashes(self):
        def bad(x):
            return x["k"]

        target = TargetSpec(bad, {"x": {"k": "v"}}, name="bad")
        results = self.runner.run([target])
        crashes = [r for r in results if r.outcome == Outcome.CRASH]
        self.assertGreater(len(crashes), 0)

    def test_silently_wrong_string_coercion_detected(self):
        def bad(x):
            return f"{x.get('name')}"  # None.get → "None"

        target = TargetSpec(bad, {"x": {"name": "Alice"}}, name="silent")
        results = self.runner.run([target])
        bad_results = [r for r in results if r.outcome == Outcome.SILENTLY_WRONG]
        self.assertGreater(len(bad_results), 0)

    def test_nan_propagation_detected(self):
        def bad(values):
            return float(sum(values))

        target = TargetSpec(bad, {"values": [1.0, 2.0]}, name="nan_silent",
                            expected_typed_errors=(ValueError, TypeError))
        results = self.runner.run([target])
        bad_results = [r for r in results if r.outcome == Outcome.SILENTLY_WRONG]
        self.assertGreater(len(bad_results), 0)

    def test_summarize_counts_match_total(self):
        target = TargetSpec(lambda x: x, {"x": {"k": 1}}, name="identity")
        results = self.runner.run([target])
        summary = summarize(results)
        self.assertEqual(
            summary["handled"] + summary["silently_wrong"] + summary["crash"],
            summary["total"],
        )


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios_returns_six(self):
        scenarios = list_scenarios()
        self.assertEqual(len(scenarios), 6)
        self.assertIn("good_zipcode", scenarios)
        self.assertIn("bad_zipcode", scenarios)
        self.assertIn("silently_wrong_format", scenarios)

    def test_self_test_passes(self):
        from harnesses.core.null_propagation_test_harness import _run_self_test
        rc = _run_self_test(NullProbeConfig(depth=3))
        self.assertEqual(rc, 0)


class TestProbeResult(unittest.TestCase):
    def test_outcome_enum_values(self):
        self.assertEqual(Outcome.HANDLED.value, "handled")
        self.assertEqual(Outcome.SILENTLY_WRONG.value, "silently_wrong")
        self.assertEqual(Outcome.CRASH.value, "crash")

    def test_probe_result_fields(self):
        r = ProbeResult("t", "p", "none", Outcome.HANDLED, "ok")
        self.assertEqual(r.target, "t")
        self.assertEqual(r.mutation, "none")


if __name__ == "__main__":
    unittest.main()
