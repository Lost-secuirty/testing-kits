"""
Tests for regression_snapshot_test_harness.py
~40 tests covering all major features.
"""

import hashlib
import json
import unittest

from harnesses._teeth import verify
from harnesses.core.regression_snapshot_test_harness import (
    COMPARE_CORPUS,
    TEETH,
    CompareMode,
    ComparisonResult,
    MockRegressionServer,
    RegressionRunner,
    RegressionTest,
    Snapshot,
    SnapshotComparator,
    SnapshotStore,
    SuiteReport,
    make_runner,
    make_store,
    make_test,
    no_recurse_match,
    oracle_match,
    order_sensitive_match,
    prove,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _tmp_store() -> SnapshotStore:
    """Return a fresh SnapshotStore backed by a new temp directory."""
    return SnapshotStore()


# ===========================================================================
# Snapshot dataclass tests
# ===========================================================================

class TestSnapshot(unittest.TestCase):

    def test_create_sets_name_and_value(self):
        snap = Snapshot.create("my_snap", {"x": 1})
        self.assertEqual(snap.name, "my_snap")
        self.assertEqual(snap.value, {"x": 1})

    def test_create_computes_checksum(self):
        value = {"x": 1}
        snap = Snapshot.create("s", value)
        expected = hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.assertEqual(snap.checksum, expected)

    def test_verify_checksum_valid(self):
        snap = Snapshot.create("s", [1, 2, 3])
        self.assertTrue(snap.verify_checksum())

    def test_verify_checksum_tampered(self):
        snap = Snapshot.create("s", [1, 2, 3])
        # Tamper with the value without updating the checksum
        snap.value = [1, 2, 99]
        self.assertFalse(snap.verify_checksum())

    def test_to_dict_round_trip(self):
        snap = Snapshot.create("round", {"a": 1, "b": [2, 3]})
        d = snap.to_dict()
        restored = Snapshot.from_dict(d)
        self.assertEqual(restored.name, snap.name)
        self.assertEqual(restored.value, snap.value)
        self.assertEqual(restored.checksum, snap.checksum)
        self.assertEqual(restored.created_at, snap.created_at)

    def test_created_at_is_iso8601_utc(self):
        snap = Snapshot.create("ts", 42)
        # Must end with +00:00 or Z (Python's datetime.isoformat with UTC)
        self.assertIn("+00:00", snap.created_at)

    def test_checksum_is_deterministic(self):
        v = {"z": 9, "a": 1}
        c1 = Snapshot._compute_checksum(v)
        c2 = Snapshot._compute_checksum(v)
        self.assertEqual(c1, c2)

    def test_checksum_different_values(self):
        c1 = Snapshot._compute_checksum({"a": 1})
        c2 = Snapshot._compute_checksum({"a": 2})
        self.assertNotEqual(c1, c2)


# ===========================================================================
# SnapshotStore tests
# ===========================================================================

class TestSnapshotStore(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()

    def tearDown(self):
        self.store.destroy()

    def test_save_returns_snapshot(self):
        snap = self.store.save("t1", {"k": "v"})
        self.assertIsInstance(snap, Snapshot)
        self.assertEqual(snap.name, "t1")

    def test_save_and_load(self):
        self.store.save("t2", [1, 2, 3])
        loaded = self.store.load("t2")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.value, [1, 2, 3])

    def test_load_nonexistent_returns_none(self):
        result = self.store.load("does_not_exist")
        self.assertIsNone(result)

    def test_exists_true_after_save(self):
        self.store.save("ex1", 99)
        self.assertTrue(self.store.exists("ex1"))

    def test_exists_false_before_save(self):
        self.assertFalse(self.store.exists("not_saved"))

    def test_delete_existing(self):
        self.store.save("del1", "bye")
        deleted = self.store.delete("del1")
        self.assertTrue(deleted)
        self.assertFalse(self.store.exists("del1"))

    def test_delete_nonexistent_returns_false(self):
        result = self.store.delete("ghost")
        self.assertFalse(result)

    def test_list_empty(self):
        self.assertEqual(self.store.list(), [])

    def test_list_multiple(self):
        self.store.save("b_snap", 1)
        self.store.save("a_snap", 2)
        self.store.save("c_snap", 3)
        names = self.store.list()
        self.assertEqual(names, ["a_snap", "b_snap", "c_snap"])

    def test_overwrite_updates_value(self):
        self.store.save("ovr", "original")
        self.store.save("ovr", "updated")
        loaded = self.store.load("ovr")
        self.assertEqual(loaded.value, "updated")

    def test_clear_removes_all(self):
        self.store.save("x", 1)
        self.store.save("y", 2)
        self.store.clear()
        self.assertEqual(self.store.list(), [])

    def test_persists_to_filesystem(self):
        directory = self.store.directory
        self.store.save("fs_test", {"persisted": True})
        # Create a new store pointing at the same directory
        store2 = SnapshotStore(directory)
        loaded = store2.load("fs_test")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.value, {"persisted": True})

    def test_loaded_checksum_valid(self):
        self.store.save("chk", {"a": 1})
        loaded = self.store.load("chk")
        self.assertTrue(loaded.verify_checksum())


# ===========================================================================
# SnapshotComparator tests
# ===========================================================================

class TestSnapshotComparator(unittest.TestCase):

    def setUp(self):
        self.cmp = SnapshotComparator()

    def _make_snap(self, value):
        return Snapshot.create("test", value)

    # --- exact ---

    def test_exact_match(self):
        snap = self._make_snap({"a": 1, "b": 2})
        result = self.cmp.compare_exact({"a": 1, "b": 2}, snap)
        self.assertTrue(result.match)
        self.assertEqual(result.mode, "exact")

    def test_exact_mismatch_value(self):
        snap = self._make_snap({"a": 1})
        result = self.cmp.compare_exact({"a": 2}, snap)
        self.assertFalse(result.match)
        self.assertIsNotNone(result.diff)

    def test_exact_key_order_irrelevant(self):
        # sort_keys=True makes key order irrelevant for exact compare
        snap = self._make_snap({"a": 1, "b": 2})
        result = self.cmp.compare_exact({"b": 2, "a": 1}, snap)
        self.assertTrue(result.match)

    # --- json_normalized ---

    def test_json_normalized_match(self):
        snap = self._make_snap({"z": 3, "a": 1})
        result = self.cmp.compare_json_normalized({"a": 1, "z": 3}, snap)
        self.assertTrue(result.match)
        self.assertEqual(result.mode, "json_normalized")

    def test_json_normalized_nested_dict(self):
        snap = self._make_snap({"outer": {"b": 2, "a": 1}})
        result = self.cmp.compare_json_normalized({"outer": {"a": 1, "b": 2}}, snap)
        self.assertTrue(result.match)

    def test_json_normalized_mismatch(self):
        snap = self._make_snap({"a": 1})
        result = self.cmp.compare_json_normalized({"a": 99}, snap)
        self.assertFalse(result.match)
        self.assertIsNotNone(result.diff)

    # --- lines ---

    def test_lines_match(self):
        text = "line one\nline two\nline three\n"
        snap = self._make_snap(text)
        result = self.cmp.compare_lines(text, snap)
        self.assertTrue(result.match)
        self.assertEqual(result.mode, "lines")

    def test_lines_mismatch(self):
        snap = self._make_snap("hello\nworld\n")
        result = self.cmp.compare_lines("hello\nearth\n", snap)
        self.assertFalse(result.match)
        self.assertIsNotNone(result.diff)
        self.assertIn("earth", result.diff)

    def test_lines_ignore_whitespace_match(self):
        snap = self._make_snap("  hello  \n  world  \n")
        result = self.cmp.compare_lines("hello\nworld\n", snap, ignore_whitespace=True)
        self.assertTrue(result.match)

    def test_lines_ignore_whitespace_mismatch(self):
        snap = self._make_snap("hello\n")
        result = self.cmp.compare_lines("goodbye\n", snap, ignore_whitespace=True)
        self.assertFalse(result.match)

    def test_lines_non_string_raises(self):
        snap = self._make_snap("text")
        with self.assertRaises(TypeError):
            self.cmp.compare_lines(12345, snap)

    def test_comparison_result_bool(self):
        r_pass = ComparisonResult(match=True, mode="exact")
        r_fail = ComparisonResult(match=False, mode="exact")
        self.assertTrue(bool(r_pass))
        self.assertFalse(bool(r_fail))


# ===========================================================================
# RegressionRunner tests
# ===========================================================================

class TestRegressionRunner(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.runner = RegressionRunner(self.store)

    def tearDown(self):
        self.store.destroy()

    def _simple_test(self, name="t", value=42, mode=CompareMode.EXACT):
        return RegressionTest(name=name, func=lambda: value, compare_mode=mode)

    # --- first-run mode ---

    def test_first_run_creates_snapshot(self):
        t = self._simple_test("first", {"key": "val"})
        result = self.runner.run(t)
        self.assertTrue(result.first_run)
        self.assertTrue(result.passed)
        self.assertTrue(self.store.exists("first"))

    def test_first_run_then_pass(self):
        t = self._simple_test("seq", 100)
        r1 = self.runner.run(t)
        r2 = self.runner.run(t)
        self.assertTrue(r1.first_run)
        self.assertFalse(r2.first_run)
        self.assertTrue(r2.passed)

    def test_first_run_then_fail(self):
        # Save snapshot with value 100
        r1 = self.runner.run(RegressionTest(name="change", func=lambda: 100))
        self.assertTrue(r1.first_run)
        # Now run with a different value — regression!
        r2 = self.runner.run(RegressionTest(name="change", func=lambda: 200))
        self.assertFalse(r2.passed)
        self.assertIsNotNone(r2.comparison)

    # --- error handling ---

    def test_function_raises_returns_failed_result(self):
        def boom():
            raise ValueError("boom!")

        t = RegressionTest(name="err", func=boom)
        result = self.runner.run(t)
        self.assertFalse(result.passed)
        self.assertIsNotNone(result.error)
        self.assertIn("ValueError", result.error)

    # --- reset ---

    def test_reset_allows_new_first_run(self):
        t = self._simple_test("rst", 7)
        self.runner.run(t)
        self.runner.reset("rst")
        r = self.runner.run(t)
        self.assertTrue(r.first_run)

    # --- run_all / SuiteReport ---

    def test_run_all_returns_suite_report(self):
        tests = [
            self._simple_test("a", 1),
            self._simple_test("b", 2),
            self._simple_test("c", 3),
        ]
        report = self.runner.run_all(tests)
        self.assertIsInstance(report, SuiteReport)
        self.assertEqual(report.total, 3)

    def test_suite_report_all_first_runs(self):
        tests = [self._simple_test(f"s{i}", i) for i in range(4)]
        report = self.runner.run_all(tests)
        self.assertEqual(report.first_runs, 4)
        self.assertTrue(report.all_passed)

    def test_suite_report_failure_counts(self):
        # First run to create baselines
        tests = [self._simple_test(f"r{i}", i) for i in range(3)]
        self.runner.run_all(tests)
        # Second run with one changed
        tests2 = [
            self._simple_test("r0", 0),
            self._simple_test("r1", 999),  # regression
            self._simple_test("r2", 2),
        ]
        report = self.runner.run_all(tests2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.passed, 2)

    def test_suite_report_summary_string(self):
        t = self._simple_test("sum", "hello")
        report = self.runner.run_all([t])
        summary = report.summary()
        self.assertIn("Suite Report", summary)
        self.assertIn("sum", summary)

    # --- duration ---

    def test_result_has_duration(self):
        t = self._simple_test("dur", "x")
        result = self.runner.run(t)
        self.assertGreaterEqual(result.duration_seconds, 0)

    # --- comparison modes via runner ---

    def test_runner_json_normalized_mode(self):
        # Save with key order z, a
        self.store.save("norm", {"z": 3, "a": 1})
        t = RegressionTest(
            name="norm",
            func=lambda: {"a": 1, "z": 3},
            compare_mode=CompareMode.JSON_NORMALIZED,
        )
        result = self.runner.run(t)
        self.assertTrue(result.passed)

    def test_runner_lines_mode(self):
        self.store.save("ln", "hello\nworld\n")
        t = RegressionTest(
            name="ln",
            func=lambda: "hello\nworld\n",
            compare_mode=CompareMode.LINES,
        )
        result = self.runner.run(t)
        self.assertTrue(result.passed)


# ===========================================================================
# MockRegressionServer tests
# ===========================================================================

class TestMockRegressionServer(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.server = MockRegressionServer(store=self.store, port=0)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.store.destroy()

    def test_health_endpoint(self):
        status, body = self.server.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_list_snapshots_empty(self):
        status, body = self.server.get("/snapshots")
        self.assertEqual(status, 200)
        self.assertEqual(body, [])

    def test_post_creates_snapshot(self):
        status, body = self.server.post("/snapshots/http_snap", {"x": 99})
        self.assertEqual(status, 201)
        self.assertEqual(body["name"], "http_snap")
        self.assertEqual(body["value"], {"x": 99})

    def test_get_snapshot_by_name(self):
        self.store.save("get_me", "hello")
        status, body = self.server.get("/snapshots/get_me")
        self.assertEqual(status, 200)
        self.assertEqual(body["value"], "hello")

    def test_get_missing_snapshot_404(self):
        status, body = self.server.get("/snapshots/nope")
        self.assertEqual(status, 404)

    def test_delete_snapshot(self):
        self.store.save("to_del", 1)
        status, body = self.server.delete("/snapshots/to_del")
        self.assertEqual(status, 200)
        self.assertFalse(self.store.exists("to_del"))

    def test_delete_missing_snapshot_404(self):
        status, body = self.server.delete("/snapshots/not_there")
        self.assertEqual(status, 404)

    def test_list_after_post(self):
        self.server.post("/snapshots/item1", 1)
        self.server.post("/snapshots/item2", 2)
        status, body = self.server.get("/snapshots")
        self.assertEqual(status, 200)
        self.assertIn("item1", body)
        self.assertIn("item2", body)

    def test_unknown_path_404(self):
        status, body = self.server.get("/unknown/path")
        self.assertEqual(status, 404)

    def test_context_manager(self):
        store = _tmp_store()
        with MockRegressionServer(store=store, port=0) as srv:
            status, body = srv.get("/health")
            self.assertEqual(status, 200)
        store.destroy()


# ===========================================================================
# Factory helper tests
# ===========================================================================

class TestFactoryHelpers(unittest.TestCase):

    def test_make_store_returns_store(self):
        s = make_store()
        self.assertIsInstance(s, SnapshotStore)
        s.destroy()

    def test_make_runner_returns_runner(self):
        r = make_runner()
        self.assertIsInstance(r, RegressionRunner)
        r.store.destroy()

    def test_make_test_returns_regression_test(self):
        t = make_test("hi", lambda: 1)
        self.assertIsInstance(t, RegressionTest)
        self.assertEqual(t.name, "hi")
        self.assertEqual(t.compare_mode, CompareMode.EXACT)

    def test_make_test_with_mode(self):
        t = make_test("hi", lambda: "x", mode=CompareMode.LINES)
        self.assertEqual(t.compare_mode, CompareMode.LINES)


# ===========================================================================
# Edge-case / integration tests
# ===========================================================================

class TestEdgeCases(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.runner = RegressionRunner(self.store)

    def tearDown(self):
        self.store.destroy()

    def test_snapshot_with_none_value(self):
        self.store.save("null_snap", None)
        loaded = self.store.load("null_snap")
        self.assertIsNone(loaded.value)
        self.assertTrue(loaded.verify_checksum())

    def test_snapshot_with_list_value(self):
        self.store.save("lst", [1, "two", 3.0, True, None])
        loaded = self.store.load("lst")
        self.assertEqual(loaded.value, [1, "two", 3.0, True, None])

    def test_snapshot_with_nested_structure(self):
        nested = {"a": {"b": {"c": [1, 2, {"d": "deep"}]}}}
        self.store.save("deep", nested)
        loaded = self.store.load("deep")
        self.assertEqual(loaded.value, nested)

    def test_regression_on_integer_type_change(self):
        # Store integer, then return string — should fail exact compare
        self.store.save("type_chg", 42)
        t = RegressionTest(name="type_chg", func=lambda: "42")
        result = self.runner.run(t)
        self.assertFalse(result.passed)

    def test_suite_report_finished_at_set(self):
        report = self.runner.run_all([])
        self.assertIsNotNone(report.finished_at)

    def test_invalid_compare_mode_raises(self):
        self.store.save("bad_mode", 1)
        t = RegressionTest(name="bad_mode", func=lambda: 1, compare_mode="nonexistent")
        with self.assertRaises(ValueError):
            self.runner.run(t)


# ===========================================================================
# Teeth tests — the universal swap-check on the normalized-comparator oracle.
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The teeth contract: the correct oracle is clean, every planted comparator
    mutant is caught, the corpus is non-empty, and prove() is judged ONLY against
    frozen literal expectations (non-circular)."""

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(len(COMPARE_CORPUS), 1)
        self.assertEqual(TEETH.corpus_size, len(COMPARE_CORPUS))

    def test_oracle_is_clean(self):
        # The correct impl must NOT be flagged.
        self.assertIs(prove(oracle_match), False)

    def test_oracle_matches_every_frozen_literal(self):
        # Directly: the oracle reproduces every hand-decided verdict.
        for case in COMPARE_CORPUS:
            self.assertEqual(
                oracle_match(case.actual, case.stored),
                case.expected_match,
                msg=f"oracle disagreed with frozen literal on {case.name}",
            )

    def test_mutant_no_recurse_is_caught(self):
        self.assertIs(prove(no_recurse_match), True)

    def test_mutant_order_sensitive_is_caught(self):
        self.assertIs(prove(order_sensitive_match), True)

    def test_every_declared_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            self.assertIs(prove(mutant.impl), True,
                          msg=f"mutant {mutant.name} was NOT caught")

    def test_teeth_verified_via_shared_gate(self):
        # The same universal swap-check the external gate runs.
        result = verify(TEETH)
        self.assertIsNone(result["error"])
        self.assertTrue(result["oracle_clean"])
        self.assertEqual(result["mutants_uncaught"], [])
        self.assertTrue(result["teeth_verified"])

    def test_noncircular_flipping_a_literal_passes_the_oracle(self):
        # Non-circularity evidence: the SHIPPED prove() compares against FROZEN
        # literals, not a runtime oracle call. Flip one expected_match in the
        # module corpus and the (still correct) oracle must now be "caught" —
        # proving the literal, not the oracle, is the source of truth.
        import dataclasses

        flipped = list(COMPARE_CORPUS)
        flipped[0] = dataclasses.replace(
            flipped[0], expected_match=not flipped[0].expected_match
        )
        # Patch the module global prove() closes over (no second harness import).
        module_ns = prove.__globals__
        original = module_ns["COMPARE_CORPUS"]
        try:
            module_ns["COMPARE_CORPUS"] = tuple(flipped)
            self.assertTrue(prove(oracle_match))
        finally:
            module_ns["COMPARE_CORPUS"] = original
        # Sanity: restored corpus makes the oracle clean again.
        self.assertFalse(prove(oracle_match))

    def test_no_recurse_caught_by_at_least_two_cases(self):
        # Robustness: the no_recurse mutant must be caught by >=2 corpus cases.
        diverging = [
            c.name for c in COMPARE_CORPUS
            if bool(no_recurse_match(c.actual, c.stored)) != c.expected_match
        ]
        self.assertGreaterEqual(len(diverging), 2, msg=f"only {diverging} caught it")

    def test_order_sensitive_caught_by_at_least_two_cases(self):
        diverging = [
            c.name for c in COMPARE_CORPUS
            if bool(order_sensitive_match(c.actual, c.stored)) != c.expected_match
        ]
        self.assertGreaterEqual(len(diverging), 2, msg=f"only {diverging} caught it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
