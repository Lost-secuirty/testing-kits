"""Test suite for clock_skew_test_harness."""

import contextlib
import io
import json
import unittest

from harnesses.core.clock_skew_test_harness import (
    SCENARIOS,
    FakeClock,
    TTLCache,
    WriteOp,
    _run_self_test,
    build_parser,
    last_write_wins,
    list_scenarios,
)


class TestFakeClock(unittest.TestCase):
    def test_initial_state(self):
        c = FakeClock(start=1000.0)
        self.assertEqual(c.time(), 1000.0)
        self.assertEqual(c.monotonic(), 1000.0)

    def test_advance_moves_both(self):
        c = FakeClock(start=1000.0)
        c.advance(5.0)
        self.assertEqual(c.time(), 1005.0)
        self.assertEqual(c.monotonic(), 1005.0)

    def test_jump_forward_only_moves_wall(self):
        c = FakeClock(start=1000.0)
        c.jump_forward(50.0)
        self.assertEqual(c.time(), 1050.0)
        self.assertEqual(c.monotonic(), 1000.0)  # monotonic stays put

    def test_jump_back_only_moves_wall(self):
        c = FakeClock(start=1000.0)
        c.jump_back(20.0)
        self.assertEqual(c.time(), 980.0)
        self.assertEqual(c.monotonic(), 1000.0)

    def test_regress_monotonic(self):
        c = FakeClock(start=1000.0)
        c.regress_monotonic(10.0)
        self.assertLess(c.monotonic(), 1000.0)

    def test_node_offset(self):
        c = FakeClock(start=1000.0)
        c.set_node_offset("A", 5.0)
        c.set_node_offset("B", -10.0)
        self.assertEqual(c.time("A"), 1005.0)
        self.assertEqual(c.time("B"), 990.0)
        self.assertEqual(c.time("local"), 1000.0)

    def test_freeze_blocks_advance(self):
        c = FakeClock(start=1000.0)
        c.freeze()
        c.advance(5.0)
        self.assertEqual(c.time(), 1000.0)
        c.unfreeze()
        c.advance(5.0)
        self.assertEqual(c.time(), 1005.0)


class TestTTLCache(unittest.TestCase):
    def test_set_get_basic(self):
        c = FakeClock()
        cache = TTLCache(c.time, c.monotonic, ttl=60.0)
        cache.set("k", "v")
        self.assertEqual(cache.get("k"), "v")

    def test_get_missing_returns_none(self):
        c = FakeClock()
        cache = TTLCache(c.time, c.monotonic, ttl=60.0)
        self.assertIsNone(cache.get("missing"))

    def test_expires_after_ttl_via_monotonic_advance(self):
        c = FakeClock()
        cache = TTLCache(c.time, c.monotonic, ttl=60.0)
        cache.set("k", "v")
        c.advance(61.0)
        self.assertIsNone(cache.get("k"))

    def test_safe_survives_wall_jump_forward(self):
        c = FakeClock()
        cache = TTLCache(c.time, c.monotonic, ttl=60.0, safe=True)
        cache.set("k", "v")
        c.jump_forward(300.0)
        self.assertEqual(cache.get("k"), "v")

    def test_unsafe_expires_on_wall_jump_forward(self):
        c = FakeClock()
        cache = TTLCache(c.time, c.monotonic, ttl=60.0, safe=False)
        cache.set("k", "v")
        c.jump_forward(300.0)
        self.assertIsNone(cache.get("k"))


class TestLastWriteWins(unittest.TestCase):
    def test_empty_ops(self):
        self.assertEqual(last_write_wins([]), {})

    def test_single_op(self):
        op = WriteOp("A", 100.0, "k", "v")
        self.assertEqual(last_write_wins([op]), {"k": "v"})

    def test_latest_wins(self):
        ops = [
            WriteOp("A", 100.0, "k", "first"),
            WriteOp("B", 200.0, "k", "later"),
        ]
        self.assertEqual(last_write_wins(ops)["k"], "later")

    def test_safe_drops_implausible(self):
        ops = [
            WriteOp("A", 100.0, "k", "A"),
            WriteOp("B", 105.0, "k", "B"),
            WriteOp("C", 5000.0, "k", "C"),  # outlier
        ]
        result = last_write_wins(ops, safe=True)
        self.assertEqual(result["k"], "B")

    def test_unsafe_picks_outlier(self):
        ops = [
            WriteOp("A", 100.0, "k", "A"),
            WriteOp("B", 105.0, "k", "B"),
            WriteOp("C", 5000.0, "k", "C"),
        ]
        result = last_write_wins(ops, safe=False)
        self.assertEqual(result["k"], "C")


class TestScenarios(unittest.TestCase):
    def test_all_scenarios_pass(self):
        for name, fn in SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertTrue(fn().passed, f"{name} failed")

    def test_list_scenarios_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 6)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)

    def test_json_flag_parses(self):
        args = build_parser().parse_args(["--json"])
        self.assertTrue(args.json)

    def test_json_self_test_emits_report(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _run_self_test(as_json=True)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["harness"], "core/clock_skew")
        self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
