"""
Test suite for fuzz_test_harness.py
48 tests covering all major components.
Uses seeded random for reproducible results.
"""

import math
import random
import unittest

from harnesses._teeth import verify
from harnesses.core.fuzz_test_harness import (
    BOUNDARY_FLOATS,
    BOUNDARY_INTS,
    DEFAULT_PORT,
    MAX_INT,
    # Constants
    MIN_INT,
    SQL_INJECTION_STRINGS,
    # Teeth
    TEETH,
    XSS_STRINGS,
    BoundaryExplorer,
    CorpusManager,
    # Classes
    CrashClassifier,
    # Dataclasses
    CrashRecord,
    DifferentialFuzzer,
    FuzzHTTPServer,
    FuzzReport,
    FuzzRunner,
    HTTPFuzzClient,
    MockFuzzHandler,
    MutationEngine,
    _make_fingerprint,
    # Utilities
    compute_entropy,
    explore_boundaries,
    fuzz_bool,
    fuzz_bytes,
    fuzz_dict,
    fuzz_float,
    # Generators
    fuzz_int,
    fuzz_list,
    fuzz_none,
    fuzz_string,
    generate_seed_sequence,
    is_valid_utf8,
    oracle_target,
    prove,
    quick_fuzz,
    truncate_repr,
)

# ─── Helper functions ─────────────────────────────────────────────────────────

def _make_rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


def _always_crash(x):
    raise ValueError(f"Always crashes on {x!r}")


def _crash_on_zero(x):
    return 1 / x


def _crash_on_none(x):
    return x.upper()


def _safe_identity(x):
    return x


def _int_only(x):
    if not isinstance(x, int):
        raise TypeError(f"Expected int, got {type(x).__name__}")
    return x * 2


# ─── Tests: Constants ─────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):

    def test_min_int_is_negative(self):
        self.assertLess(MIN_INT, 0)

    def test_max_int_is_positive(self):
        self.assertGreater(MAX_INT, 0)

    def test_min_max_symmetry(self):
        self.assertEqual(MAX_INT, -(MIN_INT + 1))

    def test_sql_injection_strings_nonempty(self):
        self.assertGreater(len(SQL_INJECTION_STRINGS), 5)
        for s in SQL_INJECTION_STRINGS:
            self.assertIsInstance(s, str)

    def test_xss_strings_nonempty(self):
        self.assertGreater(len(XSS_STRINGS), 5)
        for s in XSS_STRINGS:
            self.assertIsInstance(s, str)

    def test_boundary_ints_contains_zero(self):
        self.assertIn(0, BOUNDARY_INTS)

    def test_boundary_ints_contains_extremes(self):
        self.assertIn(MIN_INT, BOUNDARY_INTS)
        self.assertIn(MAX_INT, BOUNDARY_INTS)

    def test_boundary_floats_contains_nan(self):
        nan_present = any(math.isnan(f) for f in BOUNDARY_FLOATS)
        self.assertTrue(nan_present)

    def test_boundary_floats_contains_inf(self):
        inf_present = any(math.isinf(f) for f in BOUNDARY_FLOATS)
        self.assertTrue(inf_present)

    def test_default_port(self):
        self.assertEqual(DEFAULT_PORT, 18960)


# ─── Tests: CrashRecord ───────────────────────────────────────────────────────

class TestCrashRecord(unittest.TestCase):

    def _make_record(self, input_val="test", exc_type="ValueError", msg="error", fp="abc123"):
        return CrashRecord(
            input_value=input_val,
            exception_type=exc_type,
            message=msg,
            traceback_fingerprint=fp,
        )

    def test_crash_record_creation(self):
        r = self._make_record()
        self.assertEqual(r.exception_type, "ValueError")
        self.assertEqual(r.message, "error")
        self.assertEqual(r.traceback_fingerprint, "abc123")

    def test_crash_record_to_dict(self):
        r = self._make_record(input_val=42, exc_type="ZeroDivisionError")
        d = r.to_dict()
        self.assertIn("input_value", d)
        self.assertIn("exception_type", d)
        self.assertIn("message", d)
        self.assertIn("traceback_fingerprint", d)
        self.assertEqual(d["exception_type"], "ZeroDivisionError")

    def test_crash_record_default_iteration(self):
        r = self._make_record()
        self.assertEqual(r.iteration, 0)

    def test_crash_record_full_traceback_default(self):
        r = self._make_record()
        self.assertEqual(r.full_traceback, "")


# ─── Tests: FuzzReport ────────────────────────────────────────────────────────

class TestFuzzReport(unittest.TestCase):

    def test_survival_rate_zero_iterations(self):
        r = FuzzReport()
        self.assertEqual(r.survival_rate, 1.0)

    def test_survival_rate_all_pass(self):
        r = FuzzReport(total_iterations=100, successful_runs=100)
        self.assertAlmostEqual(r.survival_rate, 1.0)

    def test_survival_rate_half(self):
        r = FuzzReport(total_iterations=100, successful_runs=50)
        self.assertAlmostEqual(r.survival_rate, 0.5)

    def test_crash_rate_complementary(self):
        r = FuzzReport(total_iterations=100, successful_runs=70, crashed_runs=30)
        self.assertAlmostEqual(r.crash_rate, 0.30)

    def test_crash_rate_zero_iterations(self):
        r = FuzzReport()
        self.assertEqual(r.crash_rate, 0.0)

    def test_to_dict_keys(self):
        r = FuzzReport(total_iterations=10, successful_runs=8, crashed_runs=2)
        d = r.to_dict()
        for key in ["total_iterations", "successful_runs", "crashed_runs",
                    "survival_rate", "crash_rate", "elapsed_seconds", "seed"]:
            self.assertIn(key, d)

    def test_str_representation(self):
        r = FuzzReport(total_iterations=50, successful_runs=40, unique_crashes=2)
        s = str(r)
        self.assertIn("FuzzReport", s)
        self.assertIn("50", s)


# ─── Tests: CrashClassifier ───────────────────────────────────────────────────

class TestCrashClassifier(unittest.TestCase):

    def _make_record(self, exc_type="ValueError", fp="aaa"):
        return CrashRecord("input", exc_type, "msg", fp)

    def test_classify_new_crash(self):
        clf = CrashClassifier()
        r = self._make_record()
        is_new, sev = clf.classify(r)
        self.assertTrue(is_new)

    def test_classify_duplicate(self):
        clf = CrashClassifier()
        r1 = self._make_record(fp="dup1")
        r2 = self._make_record(fp="dup1")
        clf.classify(r1)
        is_new, _ = clf.classify(r2)
        self.assertFalse(is_new)

    def test_unique_crashes_count(self):
        clf = CrashClassifier()
        for fp in ["aaa", "bbb", "ccc", "aaa"]:
            clf.classify(self._make_record(fp=fp))
        self.assertEqual(len(clf.get_unique_crashes()), 3)

    def test_by_type_grouping(self):
        clf = CrashClassifier()
        clf.classify(self._make_record("ValueError", "v1"))
        clf.classify(self._make_record("ValueError", "v2"))
        clf.classify(self._make_record("TypeError", "t1"))
        crashes = clf.get_crashes_by_type("ValueError")
        self.assertEqual(len(crashes), 2)

    def test_severity_classification(self):
        clf = CrashClassifier()
        self.assertEqual(clf.get_severity("MemoryError"), "critical")
        self.assertEqual(clf.get_severity("ValueError"), "medium")
        self.assertEqual(clf.get_severity("NotImplementedError"), "low")
        self.assertEqual(clf.get_severity("SomethingWeird"), "unknown")

    def test_summary_keys(self):
        clf = CrashClassifier()
        clf.classify(self._make_record())
        s = clf.summary()
        self.assertIn("total_crashes", s)
        self.assertIn("unique_crashes", s)
        self.assertIn("by_type", s)

    def test_get_type_counts(self):
        clf = CrashClassifier()
        clf.classify(self._make_record("TypeError", "t1"))
        clf.classify(self._make_record("TypeError", "t2"))
        counts = clf.get_type_counts()
        self.assertEqual(counts["TypeError"], 2)


# ─── Tests: Generators ───────────────────────────────────────────────────────

class TestGenerators(unittest.TestCase):

    def setUp(self):
        self.rng = _make_rng(42)

    def test_fuzz_int_returns_int(self):
        for _ in range(20):
            val = fuzz_int(self.rng)
            self.assertIsInstance(val, int)

    def test_fuzz_float_returns_float(self):
        for _ in range(20):
            val = fuzz_float(self.rng)
            self.assertIsInstance(val, float)

    def test_fuzz_string_returns_str(self):
        for _ in range(20):
            val = fuzz_string(self.rng)
            self.assertIsInstance(val, str)

    def test_fuzz_bytes_returns_bytes(self):
        for _ in range(20):
            val = fuzz_bytes(self.rng)
            self.assertIsInstance(val, bytes)

    def test_fuzz_list_returns_list(self):
        for _ in range(10):
            val = fuzz_list(self.rng)
            self.assertIsInstance(val, list)

    def test_fuzz_dict_returns_dict(self):
        for _ in range(10):
            val = fuzz_dict(self.rng)
            self.assertIsInstance(val, dict)

    def test_fuzz_none_returns_none(self):
        self.assertIsNone(fuzz_none())

    def test_fuzz_bool_returns_bool(self):
        for _ in range(10):
            val = fuzz_bool(self.rng)
            self.assertIsInstance(val, bool)

    def test_fuzz_int_reproducible(self):
        rng1 = random.Random(99)
        rng2 = random.Random(99)
        vals1 = [fuzz_int(rng1) for _ in range(10)]
        vals2 = [fuzz_int(rng2) for _ in range(10)]
        self.assertEqual(vals1, vals2)

    def test_fuzz_string_includes_sql(self):
        """With enough iterations, should produce SQL injection strings."""
        rng = random.Random(42)
        found = False
        for _ in range(200):
            s = fuzz_string(rng)
            if any(sql in s for sql in SQL_INJECTION_STRINGS):
                found = True
                break
        self.assertTrue(found)

    def test_fuzz_string_can_be_empty(self):
        rng = random.Random(42)
        found_empty = False
        for _ in range(200):
            s = fuzz_string(rng)
            if s == "":
                found_empty = True
                break
        self.assertTrue(found_empty)

    def test_fuzz_float_can_be_nan(self):
        rng = random.Random(42)
        found = False
        for _ in range(100):
            f = fuzz_float(rng)
            if math.isnan(f):
                found = True
                break
        self.assertTrue(found)

    def test_fuzz_float_can_be_inf(self):
        rng = random.Random(42)
        found = False
        for _ in range(100):
            f = fuzz_float(rng)
            if math.isinf(f):
                found = True
                break
        self.assertTrue(found)


# ─── Tests: BoundaryExplorer ──────────────────────────────────────────────────

class TestBoundaryExplorer(unittest.TestCase):

    def setUp(self):
        self.explorer = BoundaryExplorer(seed=42)

    def test_int_boundaries_nonempty(self):
        bounds = self.explorer.int_boundaries()
        self.assertGreater(len(bounds), 5)

    def test_int_boundaries_contains_zero(self):
        self.assertIn(0, self.explorer.int_boundaries())

    def test_int_boundaries_contains_min_max(self):
        bounds = self.explorer.int_boundaries()
        self.assertIn(MIN_INT, bounds)
        self.assertIn(MAX_INT, bounds)

    def test_float_boundaries_contains_special(self):
        bounds = self.explorer.float_boundaries()
        self.assertTrue(any(math.isnan(f) for f in bounds))
        self.assertTrue(any(math.isinf(f) for f in bounds))

    def test_string_boundaries_nonempty(self):
        bounds = self.explorer.string_boundaries()
        self.assertGreater(len(bounds), 10)

    def test_bytes_boundaries_nonempty(self):
        bounds = self.explorer.bytes_boundaries()
        self.assertGreater(len(bounds), 3)
        for b in bounds:
            self.assertIsInstance(b, bytes)

    def test_all_boundaries_nonempty(self):
        bounds = self.explorer.all_boundaries()
        self.assertGreater(len(bounds), 20)

    def test_probe_function_catches_crashes(self):
        crashes = self.explorer.probe_function(_crash_on_zero, "int")
        self.assertGreater(len(crashes), 0)
        for c in crashes:
            self.assertIsInstance(c, CrashRecord)
            self.assertEqual(c.exception_type, "ZeroDivisionError")

    def test_probe_function_safe_no_crashes(self):
        crashes = self.explorer.probe_function(_safe_identity, "int")
        self.assertEqual(len(crashes), 0)

    def test_probe_function_none_boundary(self):
        bounds = self.explorer.none_boundary()
        self.assertEqual(bounds, [None])

    def test_probe_function_bool_boundary(self):
        bounds = self.explorer.bool_boundaries()
        self.assertIn(True, bounds)
        self.assertIn(False, bounds)


# ─── Tests: FuzzRunner ────────────────────────────────────────────────────────

class TestFuzzRunner(unittest.TestCase):

    def test_fuzz_returns_report(self):
        runner = FuzzRunner(seed=42, max_iterations=20)
        report = runner.fuzz(_safe_identity, "int", iterations=20)
        self.assertIsInstance(report, FuzzReport)

    def test_fuzz_no_crashes_on_safe_fn(self):
        runner = FuzzRunner(seed=42)
        report = runner.fuzz(_safe_identity, "int", iterations=50)
        self.assertEqual(report.crashed_runs, 0)
        self.assertAlmostEqual(report.survival_rate, 1.0)

    def test_fuzz_detects_crashes(self):
        runner = FuzzRunner(seed=42)
        report = runner.fuzz(_always_crash, "int", iterations=20)
        self.assertGreater(report.crashed_runs, 0)

    def test_fuzz_deduplicates_crashes(self):
        runner = FuzzRunner(seed=42)
        report = runner.fuzz(_always_crash, "int", iterations=50)
        # All crashes from _always_crash are the same type/location
        # unique_crashes should be 1
        self.assertGreaterEqual(report.unique_crashes, 1)
        self.assertLessEqual(report.unique_crashes, report.crashed_runs)

    def test_fuzz_with_inputs(self):
        runner = FuzzRunner(seed=42)
        inputs = [1, 2, 0, -1, "hello"]
        report = runner.fuzz_with_inputs(_crash_on_zero, inputs)
        self.assertEqual(report.total_iterations, 5)
        self.assertGreater(report.crashed_runs, 0)  # 0 and "hello" crash

    def test_fuzz_total_iterations_matches(self):
        runner = FuzzRunner(seed=42)
        report = runner.fuzz(_safe_identity, "string", iterations=30)
        self.assertEqual(report.total_iterations, 30)

    def test_fuzz_report_has_seed(self):
        runner = FuzzRunner(seed=777)
        report = runner.fuzz(_safe_identity, iterations=10)
        self.assertEqual(report.seed, 777)

    def test_fuzz_crash_records_stored(self):
        runner = FuzzRunner(seed=42)
        report = runner.fuzz(_always_crash, iterations=10)
        self.assertGreater(len(report.crash_records), 0)

    def test_quick_fuzz_convenience(self):
        report = quick_fuzz(_safe_identity, "int", iterations=20, seed=42)
        self.assertIsInstance(report, FuzzReport)
        self.assertEqual(report.survival_rate, 1.0)

    def test_explore_boundaries_convenience(self):
        crashes = explore_boundaries(_crash_on_zero, "int")
        self.assertGreater(len(crashes), 0)


# ─── Tests: MutationEngine ───────────────────────────────────────────────────

class TestMutationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MutationEngine(seed=42)

    def test_mutate_int(self):
        result = self.engine.mutate(42)
        self.assertIsInstance(result, int)

    def test_mutate_float(self):
        result = self.engine.mutate(3.14)
        self.assertIsInstance(result, float)

    def test_mutate_str(self):
        result = self.engine.mutate("hello")
        self.assertIsInstance(result, str)

    def test_mutate_bytes(self):
        result = self.engine.mutate(b"hello")
        self.assertIsInstance(result, bytes)

    def test_mutate_list(self):
        result = self.engine.mutate([1, 2, 3])
        self.assertIsInstance(result, list)

    def test_mutate_dict(self):
        result = self.engine.mutate({"a": 1})
        self.assertIsInstance(result, dict)

    def test_mutate_int_zero_boundary(self):
        """Mutating zero should produce a valid int."""
        for _ in range(10):
            result = self.engine.mutate(0)
            self.assertIsInstance(result, int)

    def test_mutate_empty_string(self):
        result = self.engine.mutate("")
        self.assertIsInstance(result, str)

    def test_mutate_empty_list(self):
        result = self.engine.mutate([])
        self.assertIsInstance(result, list)


# ─── Tests: CorpusManager ────────────────────────────────────────────────────

class TestCorpusManager(unittest.TestCase):

    def test_add_and_size(self):
        corpus = CorpusManager(seed=42)
        corpus.add(1)
        corpus.add(2)
        corpus.add("hello")
        self.assertEqual(corpus.size(), 3)

    def test_seed_with_boundaries(self):
        corpus = CorpusManager(seed=42)
        corpus.seed_with_boundaries("int")
        self.assertGreater(corpus.size(), 0)

    def test_next_input_returns_value(self):
        corpus = CorpusManager(seed=42)
        corpus.add(42)
        corpus.add("test")
        val = corpus.next_input()
        self.assertIsNotNone(val)

    def test_next_input_empty_corpus(self):
        corpus = CorpusManager(seed=42)
        val = corpus.next_input()
        self.assertIsNone(val)

    def test_max_size_enforced(self):
        corpus = CorpusManager(seed=42, max_size=5)
        for i in range(20):
            corpus.add(i)
        self.assertLessEqual(corpus.size(), 5)

    def test_get_all(self):
        corpus = CorpusManager(seed=42)
        corpus.add(1)
        corpus.add(2)
        all_vals = corpus.get_all()
        self.assertEqual(sorted(all_vals), [1, 2])


# ─── Tests: DifferentialFuzzer ───────────────────────────────────────────────

class TestDifferentialFuzzer(unittest.TestCase):

    def test_no_divergence_identical(self):
        df = DifferentialFuzzer(seed=42)
        def impl_a(x):
            return x * 2
        def impl_b(x):
            return x * 2
        divs = df.compare(impl_a, impl_b, [1, 2, 3, 4])
        self.assertEqual(len(divs), 0)

    def test_divergence_detected(self):
        df = DifferentialFuzzer(seed=42)
        def impl_a(x):
            return x + 1
        def impl_b(x):
            return x + 2
        divs = df.compare(impl_a, impl_b, [0, 1, 2])
        self.assertGreater(len(divs), 0)

    def test_exception_mismatch_detected(self):
        df = DifferentialFuzzer(seed=42)
        def impl_a(x):
            return 1 / x
        def impl_b(x):
            return x
        divs = df.compare(impl_a, impl_b, [0])
        self.assertEqual(len(divs), 1)
        self.assertEqual(divs[0]["divergence_type"], "exception_mismatch")


# ─── Tests: Utilities ────────────────────────────────────────────────────────

class TestUtilities(unittest.TestCase):

    def test_compute_entropy_empty(self):
        self.assertEqual(compute_entropy(b""), 0.0)

    def test_compute_entropy_uniform(self):
        data = bytes(range(256))
        entropy = compute_entropy(data)
        self.assertAlmostEqual(entropy, 8.0, places=1)

    def test_compute_entropy_single_byte(self):
        data = b"\xaa" * 100
        entropy = compute_entropy(data)
        self.assertAlmostEqual(entropy, 0.0, places=5)

    def test_is_valid_utf8_ascii(self):
        self.assertTrue(is_valid_utf8(b"hello world"))

    def test_is_valid_utf8_invalid(self):
        self.assertFalse(is_valid_utf8(b"\xff\xfe"))

    def test_truncate_repr_short(self):
        result = truncate_repr("hello", max_len=100)
        self.assertIn("hello", result)

    def test_truncate_repr_long(self):
        result = truncate_repr("x" * 200, max_len=50)
        self.assertLessEqual(len(result), 55)  # 50 + "..."
        self.assertTrue(result.endswith("..."))

    def test_generate_seed_sequence_length(self):
        seeds = generate_seed_sequence(42, 10)
        self.assertEqual(len(seeds), 10)

    def test_generate_seed_sequence_reproducible(self):
        s1 = generate_seed_sequence(99, 5)
        s2 = generate_seed_sequence(99, 5)
        self.assertEqual(s1, s2)

    def test_generate_seed_sequence_different_seeds(self):
        s1 = generate_seed_sequence(1, 5)
        s2 = generate_seed_sequence(2, 5)
        self.assertNotEqual(s1, s2)

    def test_make_fingerprint_same_exc(self):
        try:
            raise ValueError("test error")
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            fp1 = _make_fingerprint(exc, tb)
            fp2 = _make_fingerprint(exc, tb)
            self.assertEqual(fp1, fp2)

    def test_make_fingerprint_different_exc_types(self):
        try:
            raise ValueError("test")
        except Exception as e1:
            import traceback
            tb1 = traceback.format_exc()
            fp1 = _make_fingerprint(e1, tb1)

        try:
            raise TypeError("test")
        except Exception as e2:
            import traceback
            tb2 = traceback.format_exc()
            fp2 = _make_fingerprint(e2, tb2)

        self.assertNotEqual(fp1, fp2)


# ─── Tests: HTTP Server ───────────────────────────────────────────────────────

class TestFuzzHTTPServer(unittest.TestCase):

    def test_server_starts_and_stops(self):
        server = FuzzHTTPServer(port=0)
        server.start()
        self.assertGreater(server.port, 0)
        server.stop()

    def test_server_base_url(self):
        server = FuzzHTTPServer(port=0)
        server.start()
        url = server.base_url
        self.assertTrue(url.startswith("http://"))
        self.assertIn("127.0.0.1", url)
        server.stop()

    def test_server_context_manager(self):
        with FuzzHTTPServer(port=0) as server:
            self.assertGreater(server.port, 0)

    def test_server_receives_requests(self):
        import http.client
        with FuzzHTTPServer(port=0) as server:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/test-path")
            resp = conn.getresponse()
            resp.read()
            conn.close()

            requests = server.get_requests()
            self.assertGreater(len(requests), 0)
            self.assertEqual(requests[0]["method"], "GET")
            self.assertEqual(requests[0]["path"], "/test-path")

    def test_server_set_response(self):
        import http.client
        with FuzzHTTPServer(port=0) as server:
            server.set_response(status=404, body=b"not found")
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            self.assertEqual(resp.status, 404)
            self.assertEqual(body, b"not found")

    def test_mock_handler_reset(self):
        MockFuzzHandler.reset()
        self.assertEqual(len(MockFuzzHandler.received_requests), 0)
        self.assertEqual(MockFuzzHandler.response_status, 200)
        self.assertIsNone(MockFuzzHandler.crash_on_path)


# ─── Tests: HTTPFuzzClient ───────────────────────────────────────────────────

class TestHTTPFuzzClient(unittest.TestCase):

    def test_fuzz_paths_returns_results(self):
        with FuzzHTTPServer(port=0) as server:
            client = HTTPFuzzClient(server.base_url, seed=42, timeout=3.0)
            results = client.fuzz_paths(num=5)
            self.assertEqual(len(results), 5)

    def test_fuzz_paths_result_structure(self):
        with FuzzHTTPServer(port=0) as server:
            client = HTTPFuzzClient(server.base_url, seed=42, timeout=3.0)
            results = client.fuzz_paths(num=3)
            for r in results:
                self.assertIn("path", r)
                self.assertIn("status", r)
                self.assertIn("error", r)

    def test_fuzz_bodies_returns_results(self):
        with FuzzHTTPServer(port=0) as server:
            client = HTTPFuzzClient(server.base_url, seed=42, timeout=3.0)
            results = client.fuzz_bodies(num=5)
            self.assertEqual(len(results), 5)


# ─── Tests: Teeth ─────────────────────────────────────────────────────────────

class TestTeeth(unittest.TestCase):

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"],
                        f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The robust oracle target must NOT be flagged by prove.
        self.assertFalse(prove(oracle_target))
        self.assertFalse(prove(TEETH.oracle))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl),
                            f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)

    def test_prove_is_deterministic(self):
        # prove() replays a fixed input list with no RNG -> identical verdicts.
        for impl in (TEETH.oracle, *(m.impl for m in TEETH.mutants)):
            self.assertEqual(prove(impl), prove(impl))


if __name__ == "__main__":
    unittest.main(verbosity=2)
