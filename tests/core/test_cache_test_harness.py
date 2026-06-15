"""
Tests for cache_test_harness.py — 118 tests total.

Covers:
- Cache basic operations
- BuggyCache stale-after-write detection
- FakeClock TTL boundary conditions
- LRU eviction (recency on both get and set)
- CacheStats (hits, misses, evictions, hit_ratio)
- SingleFlightCacheV2 thundering-herd prevention
- NamespacedCache isolation
- CacheReport aggregation
- MockCacheServer HTTP integration
"""

import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from urllib.request import urlopen, Request

from harnesses._teeth import verify
from harnesses.core.cache_test_harness import (
    TEETH,
    Cache,
    BuggyCache,
    CacheEntry,
    CacheStats,
    CacheReport,
    FakeClock,
    RealClock,
    MockCacheServer,
    NamespacedCache,
    NaiveCache,
    SingleFlightCacheV2,
    TestResult,
    run_harness,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_clock(t: float = 1000.0) -> FakeClock:
    return FakeClock(start=t)


def http_get(url: str):
    with urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read())


def http_post(url: str, data: dict):
    body = json.dumps(data).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def http_delete(url: str):
    req = Request(url, method="DELETE")
    with urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


# ---------------------------------------------------------------------------
# 1. FakeClock tests (8 tests)
# ---------------------------------------------------------------------------

class TestFakeClock(unittest.TestCase):

    def test_initial_time(self):
        c = FakeClock(start=500.0)
        self.assertEqual(c.now(), 500.0)

    def test_default_start_zero(self):
        c = FakeClock()
        self.assertEqual(c.now(), 0.0)

    def test_advance_positive(self):
        c = FakeClock(start=100.0)
        c.advance(10.0)
        self.assertEqual(c.now(), 110.0)

    def test_advance_fractional(self):
        c = FakeClock(start=0.0)
        c.advance(0.001)
        self.assertAlmostEqual(c.now(), 0.001)

    def test_advance_multiple_times(self):
        c = FakeClock(start=0.0)
        c.advance(5.0)
        c.advance(3.0)
        self.assertEqual(c.now(), 8.0)

    def test_set_absolute(self):
        c = FakeClock(start=0.0)
        c.set(9999.0)
        self.assertEqual(c.now(), 9999.0)

    def test_set_then_advance(self):
        c = FakeClock(start=0.0)
        c.set(100.0)
        c.advance(5.0)
        self.assertEqual(c.now(), 105.0)

    def test_real_clock_returns_float(self):
        rc = RealClock()
        t = rc.now()
        self.assertIsInstance(t, float)


# ---------------------------------------------------------------------------
# 2. CacheEntry tests (6 tests)
# ---------------------------------------------------------------------------

class TestCacheEntry(unittest.TestCase):

    def test_entry_fields(self):
        e = CacheEntry(value="hello", expires_at=1010.0, created_at=1000.0)
        self.assertEqual(e.value, "hello")
        self.assertEqual(e.expires_at, 1010.0)
        self.assertEqual(e.created_at, 1000.0)

    def test_entry_no_expiry(self):
        e = CacheEntry(value=42, expires_at=None, created_at=0.0)
        self.assertIsNone(e.expires_at)

    def test_entry_stores_any_value_type(self):
        for val in [None, 0, "", [], {}, object()]:
            e = CacheEntry(value=val, expires_at=None, created_at=0.0)
            self.assertIs(e.value, val)

    def test_entry_is_dataclass(self):
        import dataclasses
        self.assertTrue(dataclasses.is_dataclass(CacheEntry))

    def test_entry_mutation(self):
        e = CacheEntry(value="a", expires_at=100.0, created_at=0.0)
        e.value = "b"
        self.assertEqual(e.value, "b")

    def test_entry_created_at_stored(self):
        e = CacheEntry(value=1, expires_at=None, created_at=555.5)
        self.assertEqual(e.created_at, 555.5)


# ---------------------------------------------------------------------------
# 3. CacheStats tests (8 tests)
# ---------------------------------------------------------------------------

class TestCacheStats(unittest.TestCase):

    def test_defaults_zero(self):
        s = CacheStats()
        self.assertEqual(s.hits, 0)
        self.assertEqual(s.misses, 0)
        self.assertEqual(s.evictions, 0)

    def test_hit_ratio_zero_when_no_requests(self):
        s = CacheStats()
        self.assertEqual(s.hit_ratio, 0.0)

    def test_hit_ratio_all_hits(self):
        s = CacheStats(hits=5, misses=0)
        self.assertEqual(s.hit_ratio, 1.0)

    def test_hit_ratio_all_misses(self):
        s = CacheStats(hits=0, misses=10)
        self.assertEqual(s.hit_ratio, 0.0)

    def test_hit_ratio_half(self):
        s = CacheStats(hits=5, misses=5)
        self.assertEqual(s.hit_ratio, 0.5)

    def test_hit_ratio_two_thirds(self):
        s = CacheStats(hits=2, misses=1)
        self.assertAlmostEqual(s.hit_ratio, 2 / 3)

    def test_evictions_tracked(self):
        s = CacheStats(hits=0, misses=0, evictions=3)
        self.assertEqual(s.evictions, 3)

    def test_stats_is_dataclass(self):
        import dataclasses
        self.assertTrue(dataclasses.is_dataclass(CacheStats))


# ---------------------------------------------------------------------------
# 4. Cache basic operations (18 tests)
# ---------------------------------------------------------------------------

class TestCacheBasic(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.cache = Cache(clock=self.clock)

    def test_get_miss_returns_none(self):
        self.assertIsNone(self.cache.get("missing"))

    def test_set_and_get(self):
        self.cache.set("k", "v")
        self.assertEqual(self.cache.get("k"), "v")

    def test_overwrite_returns_new_value(self):
        self.cache.set("k", "first")
        self.cache.set("k", "second")
        self.assertEqual(self.cache.get("k"), "second")

    def test_delete_existing_key(self):
        self.cache.set("k", "v")
        result = self.cache.delete("k")
        self.assertTrue(result)
        self.assertIsNone(self.cache.get("k"))

    def test_delete_missing_key_returns_false(self):
        result = self.cache.delete("nope")
        self.assertFalse(result)

    def test_clear_empties_all_keys(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.clear()
        self.assertIsNone(self.cache.get("a"))
        self.assertIsNone(self.cache.get("b"))

    def test_len_empty(self):
        self.assertEqual(len(self.cache), 0)

    def test_len_after_set(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.assertEqual(len(self.cache), 2)

    def test_len_after_delete(self):
        self.cache.set("a", 1)
        self.cache.delete("a")
        self.assertEqual(len(self.cache), 0)

    def test_contains_existing(self):
        self.cache.set("k", "v")
        self.assertIn("k", self.cache)

    def test_contains_missing(self):
        self.assertNotIn("nope", self.cache)

    def test_value_none_stored_and_returned(self):
        self.cache.set("null_key", None)
        # None value: get returns None, but key exists
        # Note: our get returns None for both miss and None-value
        # The cache does store None; this is expected behavior.
        # Verify via __contains__
        self.assertIn("null_key", self.cache)

    def test_integer_value(self):
        self.cache.set("num", 42)
        self.assertEqual(self.cache.get("num"), 42)

    def test_dict_value(self):
        d = {"x": 1, "y": 2}
        self.cache.set("dict", d)
        self.assertEqual(self.cache.get("dict"), d)

    def test_list_value(self):
        lst = [1, 2, 3]
        self.cache.set("list", lst)
        self.assertEqual(self.cache.get("list"), lst)

    def test_multiple_keys_independent(self):
        self.cache.set("a", "alpha")
        self.cache.set("b", "beta")
        self.assertEqual(self.cache.get("a"), "alpha")
        self.assertEqual(self.cache.get("b"), "beta")

    def test_clear_then_set_works(self):
        self.cache.set("k", "v")
        self.cache.clear()
        self.cache.set("k", "v2")
        self.assertEqual(self.cache.get("k"), "v2")

    def test_delete_then_set_works(self):
        self.cache.set("k", "v")
        self.cache.delete("k")
        self.cache.set("k", "new")
        self.assertEqual(self.cache.get("k"), "new")


# ---------------------------------------------------------------------------
# 5. TTL tests (12 tests)
# ---------------------------------------------------------------------------

class TestCacheTTL(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock(start=1000.0)
        self.cache = Cache(clock=self.clock)

    def test_no_ttl_never_expires(self):
        self.cache.set("k", "v")
        self.clock.advance(1_000_000)
        self.assertEqual(self.cache.get("k"), "v")

    def test_ttl_just_before_expiry(self):
        self.cache.set("k", "v", ttl=10.0)
        self.clock.advance(9.999)
        self.assertEqual(self.cache.get("k"), "v")

    def test_ttl_exactly_at_expiry_is_expired(self):
        self.cache.set("k", "v", ttl=10.0)
        self.clock.advance(10.0)
        self.assertIsNone(self.cache.get("k"))

    def test_ttl_just_after_expiry(self):
        self.cache.set("k", "v", ttl=10.0)
        self.clock.advance(10.001)
        self.assertIsNone(self.cache.get("k"))

    def test_ttl_zero_expires_immediately(self):
        self.cache.set("k", "v", ttl=0.0)
        self.assertIsNone(self.cache.get("k"))

    def test_default_ttl(self):
        c = Cache(default_ttl=5.0, clock=self.clock)
        c.set("k", "v")
        self.clock.advance(4.999)
        self.assertEqual(c.get("k"), "v")
        self.clock.advance(0.002)
        self.assertIsNone(c.get("k"))

    def test_per_entry_ttl_overrides_default(self):
        c = Cache(default_ttl=5.0, clock=self.clock)
        c.set("k", "v", ttl=20.0)
        self.clock.advance(10.0)
        self.assertEqual(c.get("k"), "v")

    def test_expired_key_not_in_contains(self):
        self.cache.set("k", "v", ttl=5.0)
        self.clock.advance(6.0)
        self.assertNotIn("k", self.cache)

    def test_expired_key_removed_from_len(self):
        self.cache.set("k", "v", ttl=5.0)
        self.clock.advance(6.0)
        self.cache.get("k")  # triggers removal
        self.assertEqual(len(self.cache), 0)

    def test_write_resets_ttl(self):
        self.cache.set("k", "v1", ttl=10.0)
        self.clock.advance(8.0)
        self.cache.set("k", "v2", ttl=10.0)  # reset TTL
        self.clock.advance(4.0)  # total 12s from original, but 4s from reset
        self.assertEqual(self.cache.get("k"), "v2")

    def test_multiple_ttls_independent(self):
        self.cache.set("a", "av", ttl=5.0)
        self.cache.set("b", "bv", ttl=15.0)
        self.clock.advance(10.0)
        self.assertIsNone(self.cache.get("a"))
        self.assertEqual(self.cache.get("b"), "bv")

    def test_ttl_fractional_second(self):
        self.cache.set("k", "v", ttl=0.5)
        self.clock.advance(0.499)
        self.assertEqual(self.cache.get("k"), "v")
        self.clock.advance(0.002)
        self.assertIsNone(self.cache.get("k"))


# ---------------------------------------------------------------------------
# 6. LRU eviction tests (12 tests)
# ---------------------------------------------------------------------------

class TestLRUEviction(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.cache = Cache(max_size=3, clock=self.clock)

    def test_evict_oldest_when_full(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.set("d", 4)  # evicts "a"
        self.assertIsNone(self.cache.get("a"))

    def test_evict_not_recently_accessed(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.get("a")  # promote "a"
        self.cache.set("d", 4)  # should evict "b" (LRU)
        self.assertIsNone(self.cache.get("b"))
        self.assertEqual(self.cache.get("a"), 1)

    def test_get_promotes_to_mru(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.get("a")  # a is now MRU
        self.cache.get("b")  # b is now MRU
        self.cache.set("d", 4)  # evicts "c"
        self.assertIsNone(self.cache.get("c"))
        self.assertEqual(self.cache.get("a"), 1)
        self.assertEqual(self.cache.get("b"), 2)

    def test_max_size_one(self):
        c = Cache(max_size=1, clock=self.clock)
        c.set("a", 1)
        c.set("b", 2)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), 2)

    def test_len_never_exceeds_max_size(self):
        for i in range(20):
            self.cache.set(str(i), i)
        self.assertLessEqual(len(self.cache), 3)

    def test_eviction_increments_stat(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.set("d", 4)
        self.assertGreater(self.cache.stats.evictions, 0)

    def test_no_eviction_under_max_size(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.assertEqual(self.cache.stats.evictions, 0)

    def test_update_existing_does_not_evict(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.set("a", 99)  # update — should NOT evict
        self.assertEqual(self.cache.stats.evictions, 0)
        self.assertEqual(len(self.cache), 3)

    def test_update_existing_promotes_to_mru(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.set("a", 10)  # a is now MRU
        self.cache.set("d", 4)   # evicts b (LRU)
        self.assertIsNone(self.cache.get("b"))
        self.assertEqual(self.cache.get("a"), 10)

    def test_zero_max_size_means_unlimited(self):
        c = Cache(max_size=0, clock=self.clock)
        for i in range(1000):
            c.set(str(i), i)
        self.assertEqual(len(c), 1000)

    def test_lru_order_set_set_set_get_set(self):
        # a, b, c → get(a) → set(d) → b evicted
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.set("c", 3)
        self.cache.get("a")
        self.cache.set("d", 4)
        self.assertIsNone(self.cache.get("b"))
        self.assertIsNotNone(self.cache.get("c"))
        self.assertIsNotNone(self.cache.get("a"))
        self.assertIsNotNone(self.cache.get("d"))

    def test_set_same_key_repeated(self):
        for i in range(10):
            self.cache.set("same", i)
        self.assertEqual(self.cache.get("same"), 9)
        self.assertEqual(self.cache.stats.evictions, 0)


# ---------------------------------------------------------------------------
# 7. CacheStats integration (8 tests)
# ---------------------------------------------------------------------------

class TestCacheStatsIntegration(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.cache = Cache(clock=self.clock)

    def test_hit_increments(self):
        self.cache.set("k", "v")
        self.cache.get("k")
        self.assertEqual(self.cache.stats.hits, 1)

    def test_miss_increments(self):
        self.cache.get("nope")
        self.assertEqual(self.cache.stats.misses, 1)

    def test_expired_counts_as_miss(self):
        self.cache.set("k", "v", ttl=5.0)
        self.clock.advance(10.0)
        self.cache.get("k")
        self.assertEqual(self.cache.stats.misses, 1)
        self.assertEqual(self.cache.stats.hits, 0)

    def test_multiple_hits_and_misses(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        for _ in range(3):
            self.cache.get("a")
        for _ in range(2):
            self.cache.get("missing")
        self.assertEqual(self.cache.stats.hits, 3)
        self.assertEqual(self.cache.stats.misses, 2)

    def test_hit_ratio_accurate(self):
        self.cache.set("k", "v")
        self.cache.get("k")      # hit
        self.cache.get("nope")   # miss
        self.assertEqual(self.cache.stats.hit_ratio, 0.5)

    def test_stats_fresh_on_new_cache(self):
        c = Cache(clock=self.clock)
        self.assertEqual(c.stats.hits, 0)
        self.assertEqual(c.stats.misses, 0)
        self.assertEqual(c.stats.evictions, 0)

    def test_eviction_stat_counted(self):
        c = Cache(max_size=1, clock=self.clock)
        c.set("a", 1)
        c.set("b", 2)
        self.assertEqual(c.stats.evictions, 1)

    def test_clear_does_not_reset_stats(self):
        self.cache.set("k", "v")
        self.cache.get("k")
        self.cache.clear()
        self.assertEqual(self.cache.stats.hits, 1)


# ---------------------------------------------------------------------------
# 8. BuggyCache tests (8 tests)
# ---------------------------------------------------------------------------

class TestBuggyCache(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.buggy = BuggyCache(clock=self.clock)

    def test_first_write_stored(self):
        self.buggy.set("k", "first")
        self.assertEqual(self.buggy.get("k"), "first")

    def test_stale_after_write(self):
        self.buggy.set("k", "original")
        self.buggy.set("k", "updated")
        # BUG: still returns original
        self.assertEqual(self.buggy.get("k"), "original")

    def test_correct_cache_invalidates(self):
        c = Cache(clock=self.clock)
        c.set("k", "original")
        c.set("k", "updated")
        self.assertEqual(c.get("k"), "updated")

    def test_buggy_vs_correct_differ(self):
        self.buggy.set("k", "v1")
        self.buggy.set("k", "v2")
        c = Cache(clock=self.clock)
        c.set("k", "v1")
        c.set("k", "v2")
        buggy_val = self.buggy.get("k")
        correct_val = c.get("k")
        self.assertNotEqual(buggy_val, correct_val)

    def test_buggy_delete_works(self):
        self.buggy.set("k", "v")
        self.buggy.delete("k")
        self.assertIsNone(self.buggy.get("k"))

    def test_buggy_clear_works(self):
        self.buggy.set("a", 1)
        self.buggy.set("b", 2)
        self.buggy.clear()
        self.assertIsNone(self.buggy.get("a"))

    def test_buggy_miss_returns_none(self):
        self.assertIsNone(self.buggy.get("absent"))

    def test_buggy_stats_tracked(self):
        self.buggy.set("k", "v")
        self.buggy.get("k")       # hit
        self.buggy.get("nope")    # miss
        self.assertEqual(self.buggy.stats.hits, 1)
        self.assertEqual(self.buggy.stats.misses, 1)


# ---------------------------------------------------------------------------
# 9. NamespacedCache tests (8 tests)
# ---------------------------------------------------------------------------

class TestNamespacedCache(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.base = Cache(clock=self.clock)
        self.ns1 = NamespacedCache(self.base, "ns1")
        self.ns2 = NamespacedCache(self.base, "ns2")

    def test_same_key_different_namespaces(self):
        self.ns1.set("key", "val1")
        self.ns2.set("key", "val2")
        self.assertEqual(self.ns1.get("key"), "val1")
        self.assertEqual(self.ns2.get("key"), "val2")

    def test_namespace_miss(self):
        self.ns1.set("key", "v")
        self.assertIsNone(self.ns2.get("key"))

    def test_namespaced_delete_scoped(self):
        self.ns1.set("k", "v1")
        self.ns2.set("k", "v2")
        self.ns1.delete("k")
        self.assertIsNone(self.ns1.get("k"))
        self.assertEqual(self.ns2.get("k"), "v2")

    def test_namespaced_clear_scoped(self):
        self.ns1.set("a", 1)
        self.ns1.set("b", 2)
        self.ns2.set("a", 99)
        self.ns1.clear()
        self.assertIsNone(self.ns1.get("a"))
        self.assertIsNone(self.ns1.get("b"))
        self.assertEqual(self.ns2.get("a"), 99)

    def test_base_cache_sees_prefixed_key(self):
        self.ns1.set("hello", "world")
        self.assertEqual(self.base.get("ns1:hello"), "world")

    def test_overwrite_in_namespace(self):
        self.ns1.set("k", "v1")
        self.ns1.set("k", "v2")
        self.assertEqual(self.ns1.get("k"), "v2")

    def test_ttl_in_namespace(self):
        self.ns1.set("k", "v", ttl=5.0)
        self.clock.advance(4.0)
        self.assertEqual(self.ns1.get("k"), "v")
        self.clock.advance(2.0)
        self.assertIsNone(self.ns1.get("k"))

    def test_three_namespaces_isolated(self):
        ns3 = NamespacedCache(self.base, "ns3")
        self.ns1.set("x", "a")
        self.ns2.set("x", "b")
        ns3.set("x", "c")
        self.assertEqual(self.ns1.get("x"), "a")
        self.assertEqual(self.ns2.get("x"), "b")
        self.assertEqual(ns3.get("x"), "c")


# ---------------------------------------------------------------------------
# 10. SingleFlightCacheV2 tests (10 tests)
# ---------------------------------------------------------------------------

class TestSingleFlightCache(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.sfc = SingleFlightCacheV2(clock=self.clock)

    def test_basic_load(self):
        result = self.sfc.get_or_load("k", lambda: "computed")
        self.assertEqual(result, "computed")

    def test_cached_on_second_call(self):
        self.sfc.get_or_load("k", lambda: "first")
        result = self.sfc.get_or_load("k", lambda: "second")
        self.assertEqual(result, "first")

    def test_loader_called_once(self):
        count = [0]
        def loader():
            count[0] += 1
            return "value"
        self.sfc.get_or_load("k", loader)
        self.sfc.get_or_load("k", loader)
        self.assertEqual(count[0], 1)

    def test_different_keys_independent(self):
        r1 = self.sfc.get_or_load("k1", lambda: "v1")
        r2 = self.sfc.get_or_load("k2", lambda: "v2")
        self.assertEqual(r1, "v1")
        self.assertEqual(r2, "v2")

    def test_invalidate_clears_key(self):
        self.sfc.get_or_load("k", lambda: "first")
        self.sfc.invalidate("k")
        result = self.sfc.get_or_load("k", lambda: "second")
        self.assertEqual(result, "second")

    def test_loader_call_count_tracked(self):
        self.sfc.get_or_load("a", lambda: 1)
        self.sfc.get_or_load("b", lambda: 2)
        self.sfc.get_or_load("a", lambda: 99)  # cached
        self.assertEqual(self.sfc.loader_call_count, 2)

    def test_concurrent_access_single_load(self):
        barrier = threading.Barrier(5)
        results = []
        loader_calls = [0]

        def slow_loader():
            loader_calls[0] += 1
            time.sleep(0.05)
            return "data"

        def worker():
            barrier.wait()
            v = self.sfc.get_or_load("shared_key", slow_loader)
            results.append(v)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(results), 5)
        self.assertTrue(all(r == "data" for r in results))
        self.assertEqual(loader_calls[0], 1)

    def test_concurrent_different_keys(self):
        results = {}

        def worker(key):
            v = self.sfc.get_or_load(key, lambda: key + "_val")
            results[key] = v

        threads = [threading.Thread(target=worker, args=(f"k{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        for i in range(10):
            self.assertEqual(results[f"k{i}"], f"k{i}_val")

    def test_naive_vs_single_flight_loader_count(self):
        naive = NaiveCache()
        sfc = SingleFlightCacheV2()
        barrier = threading.Barrier(4)
        naive_counts = [0]
        sfc_counts = [0]

        def naive_loader():
            naive_counts[0] += 1
            time.sleep(0.02)
            return "v"

        def sfc_loader():
            sfc_counts[0] += 1
            time.sleep(0.02)
            return "v"

        def naive_worker():
            barrier.wait()
            naive.get_or_load("k", naive_loader)

        def sfc_worker():
            barrier.wait()
            sfc.get_or_load("k", sfc_loader)

        nt = [threading.Thread(target=naive_worker) for _ in range(4)]
        st = [threading.Thread(target=sfc_worker) for _ in range(4)]
        for t in nt + st:
            t.start()
        for t in nt + st:
            t.join(timeout=5)

        # Single-flight calls loader exactly once
        self.assertEqual(sfc_counts[0], 1)

    def test_invalidate_missing_key_no_error(self):
        self.sfc.invalidate("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# 11. CacheReport tests (6 tests)
# ---------------------------------------------------------------------------

class TestCacheReport(unittest.TestCase):

    def test_empty_report(self):
        r = CacheReport()
        self.assertEqual(r.total, 0)
        self.assertEqual(r.passed, 0)
        self.assertEqual(r.failed, 0)
        self.assertTrue(r.all_passed)

    def test_add_pass(self):
        r = CacheReport()
        r.add("test1", True)
        self.assertEqual(r.total, 1)
        self.assertEqual(r.passed, 1)
        self.assertEqual(r.failed, 0)

    def test_add_fail(self):
        r = CacheReport()
        r.add("test1", False, "something broke")
        self.assertEqual(r.total, 1)
        self.assertEqual(r.passed, 0)
        self.assertEqual(r.failed, 1)
        self.assertFalse(r.all_passed)

    def test_mixed_results(self):
        r = CacheReport()
        r.add("a", True)
        r.add("b", False)
        r.add("c", True)
        self.assertEqual(r.total, 3)
        self.assertEqual(r.passed, 2)
        self.assertEqual(r.failed, 1)

    def test_summary_contains_names(self):
        r = CacheReport()
        r.add("my_test", True)
        summary = r.summary()
        self.assertIn("my_test", summary)
        self.assertIn("PASS", summary)

    def test_summary_shows_fail(self):
        r = CacheReport()
        r.add("bad_test", False, "mismatch")
        summary = r.summary()
        self.assertIn("FAIL", summary)
        self.assertIn("bad_test", summary)


# ---------------------------------------------------------------------------
# 12. MockCacheServer HTTP integration (14 tests)
# ---------------------------------------------------------------------------

class TestMockCacheServer(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.cache = Cache(clock=self.clock)
        self.server = MockCacheServer(cache=self.cache)
        self.server.start()
        self.base = self.server.base_url

    def tearDown(self):
        self.server.stop()

    def test_server_starts(self):
        self.assertIsNotNone(self.server.port)
        self.assertGreater(self.server.port, 0)

    def test_set_and_get_key(self):
        http_post(f"{self.base}/cache/hello", {"value": "world"})
        status, data = http_get(f"{self.base}/cache/hello")
        self.assertEqual(status, 200)
        self.assertEqual(data["value"], "world")

    def test_get_missing_key_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            http_get(f"{self.base}/cache/missing")
        self.assertEqual(ctx.exception.code, 404)

    def test_delete_key(self):
        http_post(f"{self.base}/cache/to_delete", {"value": "bye"})
        http_delete(f"{self.base}/cache/to_delete")
        with self.assertRaises(urllib.error.HTTPError):
            http_get(f"{self.base}/cache/to_delete")

    def test_clear_endpoint(self):
        http_post(f"{self.base}/cache/a", {"value": 1})
        http_post(f"{self.base}/cache/b", {"value": 2})
        http_post(f"{self.base}/clear", {})
        with self.assertRaises(urllib.error.HTTPError):
            http_get(f"{self.base}/cache/a")

    def test_stats_endpoint(self):
        http_post(f"{self.base}/cache/k", {"value": "v"})
        http_get(f"{self.base}/cache/k")    # hit
        status, data = http_get(f"{self.base}/stats")
        self.assertEqual(status, 200)
        self.assertIn("hits", data)
        self.assertIn("misses", data)

    def test_stats_hit_ratio_in_response(self):
        http_post(f"{self.base}/cache/k", {"value": "v"})
        http_get(f"{self.base}/cache/k")
        _, data = http_get(f"{self.base}/stats")
        self.assertIn("hit_ratio", data)

    def test_set_integer_value(self):
        http_post(f"{self.base}/cache/num", {"value": 42})
        _, data = http_get(f"{self.base}/cache/num")
        self.assertEqual(data["value"], 42)

    def test_set_dict_value(self):
        http_post(f"{self.base}/cache/obj", {"value": {"x": 1}})
        _, data = http_get(f"{self.base}/cache/obj")
        self.assertEqual(data["value"], {"x": 1})

    def test_overwrite_value(self):
        http_post(f"{self.base}/cache/k", {"value": "old"})
        http_post(f"{self.base}/cache/k", {"value": "new"})
        _, data = http_get(f"{self.base}/cache/k")
        self.assertEqual(data["value"], "new")

    def test_ttl_via_http(self):
        http_post(f"{self.base}/cache/ttl_key", {"value": "v", "ttl": 5.0})
        _, data = http_get(f"{self.base}/cache/ttl_key")
        self.assertEqual(data["value"], "v")

    def test_context_manager(self):
        clock = make_clock()
        cache = Cache(clock=clock)
        with MockCacheServer(cache=cache) as srv:
            url = srv.base_url
            http_post(f"{url}/cache/cm_key", {"value": "cm_val"})
            _, data = http_get(f"{url}/cache/cm_key")
            self.assertEqual(data["value"], "cm_val")

    def test_delete_response_ok(self):
        http_post(f"{self.base}/cache/k", {"value": "v"})
        status, data = http_delete(f"{self.base}/cache/k")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_unknown_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            http_get(f"{self.base}/unknown/route")
        self.assertEqual(ctx.exception.code, 404)


# ---------------------------------------------------------------------------
# 13. Harness runner tests (4 tests)
# ---------------------------------------------------------------------------

class TestRunHarness(unittest.TestCase):

    def test_harness_runs(self):
        report = run_harness()
        self.assertIsInstance(report, CacheReport)

    def test_harness_all_pass(self):
        report = run_harness()
        self.assertTrue(report.all_passed, report.summary())

    def test_harness_has_results(self):
        report = run_harness()
        self.assertGreater(report.total, 0)

    def test_buggy_cache_caught_by_harness(self):
        # Validate that harness explicitly tests BuggyCache stale-after-write
        report = run_harness()
        names = {r.name for r in report.results}
        self.assertIn("buggy_cache_stale_after_write", names)


# ---------------------------------------------------------------------------
# 14. Thread safety tests (6 tests)
# ---------------------------------------------------------------------------

class TestCacheThreadSafety(unittest.TestCase):

    def setUp(self):
        self.clock = make_clock()
        self.cache = Cache(max_size=100, clock=self.clock)

    def test_concurrent_sets(self):
        errors = []

        def setter(i):
            try:
                self.cache.set(f"k{i}", i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=setter, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_concurrent_gets(self):
        self.cache.set("shared", "value")
        errors = []

        def getter():
            try:
                v = self.cache.get("shared")
                assert v == "value", f"Expected 'value', got {v!r}"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=getter) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_concurrent_set_and_get(self):
        results = []
        errors = []

        def worker(i):
            try:
                self.cache.set(f"k{i}", i)
                v = self.cache.get(f"k{i}")
                results.append(v)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_concurrent_deletes(self):
        for i in range(20):
            self.cache.set(f"k{i}", i)
        errors = []

        def deleter(i):
            try:
                self.cache.delete(f"k{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=deleter, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_concurrent_clear_and_set(self):
        errors = []

        def setter():
            try:
                for i in range(20):
                    self.cache.set(f"k{i}", i)
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(10):
                    self.cache.clear()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=setter) for _ in range(3)]
        threads += [threading.Thread(target=clearer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_stats_under_concurrent_load(self):
        errors = []

        def worker():
            try:
                self.cache.set("shared", "v")
                self.cache.get("shared")
                self.cache.get("missing")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # Stats should be non-negative
        self.assertGreaterEqual(self.cache.stats.hits, 0)
        self.assertGreaterEqual(self.cache.stats.misses, 0)


class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        self.assertFalse(TEETH.prove(TEETH.oracle))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
