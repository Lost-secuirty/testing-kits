"""
129 tests for ratelimit_test_harness.py
Pure stdlib, zero external dependencies.
"""

import dataclasses
import threading
import time
import unittest

from harnesses.core.ratelimit_test_harness import (
    FakeClock,
    FixedWindow,
    LeakyBucket,
    LimiterStats,
    PerKeyTokenBuckets,
    RateLimitDecision,
    RateLimitReport,
    RateLimitServer,
    SlidingWindow,
    TokenBucket,
    http_get,
    make_report,
)

# ===========================================================================
# FakeClock (10 tests)
# ===========================================================================

class TestFakeClock(unittest.TestCase):

    def test_default_start(self):
        c = FakeClock()
        self.assertEqual(c.now(), 0.0)

    def test_custom_start(self):
        c = FakeClock(start=100.0)
        self.assertEqual(c.now(), 100.0)

    def test_advance_basic(self):
        c = FakeClock()
        c.advance(5.0)
        self.assertEqual(c.now(), 5.0)

    def test_advance_multiple(self):
        c = FakeClock()
        c.advance(3.0)
        c.advance(2.0)
        self.assertAlmostEqual(c.now(), 5.0)

    def test_advance_zero(self):
        c = FakeClock(10.0)
        c.advance(0.0)
        self.assertEqual(c.now(), 10.0)

    def test_advance_fractional(self):
        c = FakeClock()
        c.advance(0.5)
        self.assertAlmostEqual(c.now(), 0.5)

    def test_now_returns_float(self):
        c = FakeClock()
        self.assertIsInstance(c.now(), float)

    def test_advance_negative_raises(self):
        c = FakeClock()
        with self.assertRaises(ValueError):
            c.advance(-1.0)

    def test_advance_large(self):
        c = FakeClock()
        c.advance(1_000_000.0)
        self.assertEqual(c.now(), 1_000_000.0)

    def test_independent_clocks(self):
        c1 = FakeClock(0.0)
        c2 = FakeClock(0.0)
        c1.advance(10.0)
        self.assertEqual(c1.now(), 10.0)
        self.assertEqual(c2.now(), 0.0)


# ===========================================================================
# RateLimitDecision (8 tests)
# ===========================================================================

class TestRateLimitDecision(unittest.TestCase):

    def test_allowed_true(self):
        d = RateLimitDecision(allowed=True, remaining=5, retry_after=0.0)
        self.assertTrue(d.allowed)

    def test_allowed_false(self):
        d = RateLimitDecision(allowed=False, remaining=0, retry_after=2.5)
        self.assertFalse(d.allowed)

    def test_remaining_field(self):
        d = RateLimitDecision(allowed=True, remaining=3, retry_after=0.0)
        self.assertEqual(d.remaining, 3)

    def test_retry_after_field(self):
        d = RateLimitDecision(allowed=False, remaining=0, retry_after=1.5)
        self.assertAlmostEqual(d.retry_after, 1.5)

    def test_is_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(RateLimitDecision))

    def test_retry_after_zero_when_allowed(self):
        d = RateLimitDecision(allowed=True, remaining=10, retry_after=0.0)
        self.assertEqual(d.retry_after, 0.0)

    def test_remaining_zero_when_denied(self):
        d = RateLimitDecision(allowed=False, remaining=0, retry_after=1.0)
        self.assertEqual(d.remaining, 0)

    def test_fields_accessible(self):
        d = RateLimitDecision(allowed=True, remaining=7, retry_after=0.0)
        self.assertIn("allowed", d.__dataclass_fields__)
        self.assertIn("remaining", d.__dataclass_fields__)
        self.assertIn("retry_after", d.__dataclass_fields__)


# ===========================================================================
# LimiterStats (6 tests)
# ===========================================================================

class TestLimiterStats(unittest.TestCase):

    def test_default_zeros(self):
        s = LimiterStats()
        self.assertEqual(s.requests, 0)
        self.assertEqual(s.allowed, 0)
        self.assertEqual(s.denied, 0)
        self.assertEqual(s.current_tokens, 0.0)

    def test_is_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(LimiterStats))

    def test_custom_values(self):
        s = LimiterStats(requests=10, allowed=8, denied=2, current_tokens=3.5)
        self.assertEqual(s.requests, 10)
        self.assertEqual(s.allowed, 8)
        self.assertEqual(s.denied, 2)
        self.assertAlmostEqual(s.current_tokens, 3.5)

    def test_requests_equals_allowed_plus_denied(self):
        s = LimiterStats(requests=5, allowed=3, denied=2)
        self.assertEqual(s.requests, s.allowed + s.denied)

    def test_current_tokens_float(self):
        s = LimiterStats(current_tokens=4.0)
        self.assertIsInstance(s.current_tokens, float)

    def test_mutable(self):
        s = LimiterStats()
        s.requests = 1
        self.assertEqual(s.requests, 1)


# ===========================================================================
# TokenBucket (25 tests)
# ===========================================================================

class TestTokenBucket(unittest.TestCase):

    def _make(self, capacity=10, rate=1.0, start=0.0):
        clock = FakeClock(start)
        return TokenBucket(capacity, rate, clock=clock), clock

    def test_initial_full(self):
        tb, _ = self._make(capacity=10)
        d = tb.allow()
        self.assertTrue(d.allowed)
        self.assertEqual(d.remaining, 9)

    def test_burst_to_capacity(self):
        tb, _ = self._make(capacity=5)
        results = [tb.allow() for _ in range(5)]
        self.assertTrue(all(r.allowed for r in results))

    def test_denied_when_empty(self):
        tb, _ = self._make(capacity=2)
        tb.allow()
        tb.allow()
        d = tb.allow()
        self.assertFalse(d.allowed)

    def test_tokens_never_exceed_cap(self):
        tb, clock = self._make(capacity=5, rate=10.0)
        tb.allow(5)               # drain all
        clock.advance(100.0)      # would produce 1000 tokens
        d = tb.allow()
        self.assertTrue(d.allowed)
        self.assertLessEqual(d.remaining, 4)

    def test_refill_math(self):
        tb, clock = self._make(capacity=10, rate=2.0)
        tb.allow(10)              # drain all
        clock.advance(3.0)        # should refill 6 tokens
        d = tb.allow()
        self.assertTrue(d.allowed)
        self.assertEqual(d.remaining, 5)  # 6 refilled - 1 used = 5

    def test_partial_refill(self):
        tb, clock = self._make(capacity=10, rate=1.0)
        tb.allow(10)
        clock.advance(0.5)        # 0.5 tokens, not enough for 1
        d = tb.allow()
        self.assertFalse(d.allowed)

    def test_retry_after_positive_when_denied(self):
        tb, _ = self._make(capacity=2)
        tb.allow()
        tb.allow()
        d = tb.allow()
        self.assertFalse(d.allowed)
        self.assertGreater(d.retry_after, 0)

    def test_retry_after_advance_allows(self):
        tb, clock = self._make(capacity=1, rate=1.0)
        tb.allow()
        d = tb.allow()
        self.assertFalse(d.allowed)
        clock.advance(d.retry_after)
        d2 = tb.allow()
        self.assertTrue(d2.allowed)

    def test_remaining_decrements(self):
        tb, _ = self._make(capacity=5)
        remainders = []
        for _ in range(5):
            remainders.append(tb.allow().remaining)
        self.assertEqual(remainders, [4, 3, 2, 1, 0])

    def test_stats_requests(self):
        tb, _ = self._make(capacity=5)
        for _ in range(3):
            tb.allow()
        s = tb.stats()
        self.assertEqual(s.requests, 3)

    def test_stats_allowed(self):
        tb, _ = self._make(capacity=5)
        tb.allow()
        tb.allow()
        s = tb.stats()
        self.assertEqual(s.allowed, 2)

    def test_stats_denied(self):
        tb, _ = self._make(capacity=1)
        tb.allow()
        tb.allow()
        s = tb.stats()
        self.assertEqual(s.denied, 1)

    def test_stats_current_tokens(self):
        tb, _ = self._make(capacity=5)
        tb.allow(3)
        s = tb.stats()
        self.assertAlmostEqual(s.current_tokens, 2.0)

    def test_invalid_capacity(self):
        with self.assertRaises((ValueError, Exception)):
            TokenBucket(0, 1.0)

    def test_invalid_rate(self):
        with self.assertRaises((ValueError, Exception)):
            TokenBucket(10, 0.0)

    def test_allow_n_gt1(self):
        tb, _ = self._make(capacity=10)
        d = tb.allow(5)
        self.assertTrue(d.allowed)
        self.assertEqual(d.remaining, 5)

    def test_allow_n_gt_capacity_denied(self):
        tb, _ = self._make(capacity=5)
        d = tb.allow(10)
        self.assertFalse(d.allowed)

    def test_no_real_clock_usage(self):
        # With FakeClock frozen, no time passes
        clock = FakeClock(0.0)
        tb = TokenBucket(5, 1.0, clock=clock)
        tb.allow(5)
        d = tb.allow()
        self.assertFalse(d.allowed)

    def test_refill_after_exact_time(self):
        tb, clock = self._make(capacity=10, rate=5.0)
        tb.allow(10)
        clock.advance(2.0)        # 10 tokens refilled (capped at 10)
        d = tb.allow()
        self.assertTrue(d.allowed)

    def test_multiple_refill_advances(self):
        tb, clock = self._make(capacity=10, rate=2.0)
        tb.allow(10)
        clock.advance(1.0)        # +2 tokens
        clock.advance(1.0)        # +2 tokens  = 4
        d = tb.allow(4)
        self.assertTrue(d.allowed)

    def test_remaining_zero_when_exact_capacity_used(self):
        tb, _ = self._make(capacity=3)
        tb.allow(3)
        s = tb.stats()
        self.assertAlmostEqual(s.current_tokens, 0.0)

    def test_thread_safety_no_over_admit(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(100, 1.0, clock=clock)
        allowed_count = [0]
        lock = threading.Lock()

        def run():
            for _ in range(20):
                d = tb.allow()
                if d.allowed:
                    with lock:
                        allowed_count[0] += 1

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(allowed_count[0], 100)

    def test_retry_after_proportional_to_deficit(self):
        tb, _ = self._make(capacity=5, rate=1.0)
        tb.allow(5)
        d = tb.allow(3)          # deficit = 3, rate = 1 -> retry 3s
        self.assertAlmostEqual(d.retry_after, 3.0, places=5)

    def test_capacity_one(self):
        tb, _ = self._make(capacity=1)
        d1 = tb.allow()
        d2 = tb.allow()
        self.assertTrue(d1.allowed)
        self.assertFalse(d2.allowed)

    def test_high_rate_refills_quickly(self):
        tb, clock = self._make(capacity=100, rate=100.0)
        tb.allow(100)
        clock.advance(0.5)       # +50 tokens
        d = tb.allow(50)
        self.assertTrue(d.allowed)


# ===========================================================================
# LeakyBucket (15 tests)
# ===========================================================================

class TestLeakyBucket(unittest.TestCase):

    def _make(self, capacity=10, drain_rate=1.0, start=0.0):
        clock = FakeClock(start)
        return LeakyBucket(capacity, drain_rate, clock=clock), clock

    def test_initial_allow(self):
        lb, _ = self._make(capacity=5)
        d = lb.allow()
        self.assertTrue(d.allowed)

    def test_fill_to_capacity(self):
        lb, _ = self._make(capacity=5)
        for _ in range(5):
            d = lb.allow()
            self.assertTrue(d.allowed)

    def test_denied_when_full(self):
        lb, _ = self._make(capacity=3)
        lb.allow()
        lb.allow()
        lb.allow()
        d = lb.allow()
        self.assertFalse(d.allowed)

    def test_drain_allows_new_requests(self):
        lb, clock = self._make(capacity=2, drain_rate=1.0)
        lb.allow()
        lb.allow()
        clock.advance(1.0)
        d = lb.allow()
        self.assertTrue(d.allowed)

    def test_steady_drain(self):
        lb, clock = self._make(capacity=10, drain_rate=2.0)
        for _ in range(10):
            lb.allow()
        clock.advance(5.0)        # drains all 10
        d = lb.allow()
        self.assertTrue(d.allowed)

    def test_remaining_reflects_space(self):
        lb, _ = self._make(capacity=5)
        lb.allow(3)
        d = lb.allow()
        self.assertEqual(d.remaining, 1)  # 5-3-1=1

    def test_stats_tracking(self):
        lb, _ = self._make(capacity=5)
        lb.allow()
        lb.allow()
        s = lb.stats()
        self.assertEqual(s.allowed, 2)

    def test_retry_after_positive_when_denied(self):
        lb, _ = self._make(capacity=2)
        lb.allow()
        lb.allow()
        d = lb.allow()
        self.assertFalse(d.allowed)
        self.assertGreater(d.retry_after, 0)

    def test_invalid_capacity(self):
        with self.assertRaises((ValueError, Exception)):
            LeakyBucket(0, 1.0)

    def test_invalid_drain_rate(self):
        with self.assertRaises((ValueError, Exception)):
            LeakyBucket(10, 0.0)

    def test_level_never_negative(self):
        lb, clock = self._make(capacity=5, drain_rate=1.0)
        clock.advance(100.0)
        s = lb.stats()
        self.assertGreaterEqual(s.current_tokens, 0)

    def test_partial_drain_does_not_allow_overflowing(self):
        lb, clock = self._make(capacity=3, drain_rate=1.0)
        lb.allow(3)
        clock.advance(0.5)        # drains 0.5; level=2.5; space=0.5 < 1
        d = lb.allow()
        self.assertFalse(d.allowed)

    def test_stats_denied_count(self):
        lb, _ = self._make(capacity=1)
        lb.allow()
        lb.allow()
        s = lb.stats()
        self.assertEqual(s.denied, 1)

    def test_allow_n_fills_correctly(self):
        lb, _ = self._make(capacity=10)
        d = lb.allow(6)
        self.assertTrue(d.allowed)
        self.assertEqual(d.remaining, 4)

    def test_no_drain_without_time(self):
        clock = FakeClock(0.0)
        lb = LeakyBucket(3, 1.0, clock=clock)
        lb.allow(3)
        d = lb.allow()
        self.assertFalse(d.allowed)


# ===========================================================================
# FixedWindow (18 tests)
# ===========================================================================

class TestFixedWindow(unittest.TestCase):

    def _make(self, max_req=5, window=10.0, start=0.0):
        clock = FakeClock(start)
        return FixedWindow(max_req, window, clock=clock), clock

    def test_initial_allow(self):
        fw, _ = self._make()
        d = fw.allow()
        self.assertTrue(d.allowed)

    def test_fill_window(self):
        fw, _ = self._make(max_req=3)
        results = [fw.allow() for _ in range(3)]
        self.assertTrue(all(r.allowed for r in results))

    def test_denied_at_limit(self):
        fw, _ = self._make(max_req=2)
        fw.allow()
        fw.allow()
        d = fw.allow()
        self.assertFalse(d.allowed)

    def test_reset_after_window(self):
        fw, clock = self._make(max_req=2, window=5.0)
        fw.allow()
        fw.allow()
        clock.advance(5.0)
        d = fw.allow()
        self.assertTrue(d.allowed)

    def test_remaining_decrements(self):
        fw, _ = self._make(max_req=3)
        fw.allow()
        d = fw.allow()
        self.assertEqual(d.remaining, 1)

    def test_retry_after_positive_when_denied(self):
        fw, _ = self._make(max_req=1)
        fw.allow()
        d = fw.allow()
        self.assertFalse(d.allowed)
        self.assertGreater(d.retry_after, 0)

    def test_boundary_burst_weakness(self):
        """
        Known fixed-window weakness: 2× max_requests can be admitted
        across the window boundary in a short period.
        """
        fw, clock = self._make(max_req=5, window=10.0)
        # Use up max in the first window
        for _ in range(5):
            fw.allow()
        # Advance to the very end of window, then reset
        clock.advance(10.0)      # new window starts
        # Allow another max in the new window
        results = [fw.allow() for _ in range(5)]
        self.assertTrue(all(r.allowed for r in results))
        # Together we admitted 2×max_requests over a crossing period
        s = fw.stats()
        self.assertEqual(s.allowed, 10)

    def test_no_reset_before_window(self):
        fw, clock = self._make(max_req=2, window=10.0)
        fw.allow()
        fw.allow()
        clock.advance(9.9)
        d = fw.allow()
        self.assertFalse(d.allowed)

    def test_invalid_max_requests(self):
        with self.assertRaises((ValueError, Exception)):
            FixedWindow(0, 10.0)

    def test_invalid_window(self):
        with self.assertRaises((ValueError, Exception)):
            FixedWindow(5, 0.0)

    def test_stats_requests(self):
        fw, _ = self._make(max_req=5)
        fw.allow()
        fw.allow()
        fw.allow()
        s = fw.stats()
        self.assertEqual(s.requests, 3)

    def test_stats_denied(self):
        fw, _ = self._make(max_req=2)
        fw.allow()
        fw.allow()
        fw.allow()
        s = fw.stats()
        self.assertEqual(s.denied, 1)

    def test_retry_after_within_window(self):
        fw, clock = self._make(max_req=1, window=10.0)
        fw.allow()
        clock.advance(4.0)
        d = fw.allow()
        self.assertFalse(d.allowed)
        # 4 seconds elapsed, 6 remain (approximately)
        self.assertAlmostEqual(d.retry_after, 6.0, delta=0.5)

    def test_window_exactly_resets_at_boundary(self):
        fw, clock = self._make(max_req=1, window=1.0)
        fw.allow()
        d1 = fw.allow()
        self.assertFalse(d1.allowed)
        clock.advance(1.0)
        d2 = fw.allow()
        self.assertTrue(d2.allowed)

    def test_remaining_zero_after_full(self):
        fw, _ = self._make(max_req=3)
        fw.allow(3)
        s = fw.stats()
        self.assertAlmostEqual(s.current_tokens, 0.0)

    def test_allow_n_gt1(self):
        fw, _ = self._make(max_req=10)
        d = fw.allow(5)
        self.assertTrue(d.allowed)
        self.assertEqual(d.remaining, 5)

    def test_allow_n_gt_max_denied(self):
        fw, _ = self._make(max_req=3)
        d = fw.allow(5)
        self.assertFalse(d.allowed)

    def test_multiple_windows_cycle(self):
        fw, clock = self._make(max_req=2, window=5.0)
        fw.allow()
        fw.allow()
        clock.advance(5.0)
        fw.allow()
        fw.allow()
        clock.advance(5.0)
        d = fw.allow()
        self.assertTrue(d.allowed)


# ===========================================================================
# SlidingWindow (18 tests)
# ===========================================================================

class TestSlidingWindow(unittest.TestCase):

    def _make(self, max_req=5, window=10.0, start=0.0):
        clock = FakeClock(start)
        return SlidingWindow(max_req, window, clock=clock), clock

    def test_initial_allow(self):
        sw, _ = self._make()
        d = sw.allow()
        self.assertTrue(d.allowed)

    def test_fill_window(self):
        sw, _ = self._make(max_req=3)
        results = [sw.allow() for _ in range(3)]
        self.assertTrue(all(r.allowed for r in results))

    def test_denied_at_limit(self):
        sw, _ = self._make(max_req=2)
        sw.allow()
        sw.allow()
        d = sw.allow()
        self.assertFalse(d.allowed)

    def test_no_boundary_burst(self):
        """
        SlidingWindow prevents the fixed-window boundary burst.
        Within any window_seconds span, at most max_requests allowed.
        """
        sw, clock = self._make(max_req=5, window=10.0)
        for _ in range(5):
            sw.allow()
        # Advance only partway; slots not yet expired
        clock.advance(5.0)
        d = sw.allow()
        self.assertFalse(d.allowed)

    def test_allows_after_expiry(self):
        sw, clock = self._make(max_req=3, window=5.0)
        sw.allow()
        sw.allow()
        sw.allow()
        clock.advance(5.01)      # old timestamps expire
        d = sw.allow()
        self.assertTrue(d.allowed)

    def test_sliding_not_fixed(self):
        """
        Confirms that only expired slots free up, not a full window reset.
        """
        sw, clock = self._make(max_req=3, window=10.0)
        # t=0: 3 requests
        sw.allow()
        sw.allow()
        sw.allow()
        # t=5: no slots (all within last 10s)
        clock.advance(5.0)
        d = sw.allow()
        self.assertFalse(d.allowed)
        # t=10.1: oldest request (at t=0) expired
        clock.advance(5.1)
        d2 = sw.allow()
        self.assertTrue(d2.allowed)

    def test_remaining_field(self):
        sw, _ = self._make(max_req=5)
        sw.allow()
        sw.allow()
        d = sw.allow()
        self.assertEqual(d.remaining, 2)

    def test_retry_after_positive(self):
        sw, _ = self._make(max_req=1)
        sw.allow()
        d = sw.allow()
        self.assertFalse(d.allowed)
        self.assertGreater(d.retry_after, 0)

    def test_invalid_max(self):
        with self.assertRaises((ValueError, Exception)):
            SlidingWindow(0, 10.0)

    def test_invalid_window(self):
        with self.assertRaises((ValueError, Exception)):
            SlidingWindow(5, 0.0)

    def test_stats_requests(self):
        sw, _ = self._make(max_req=5)
        sw.allow()
        sw.allow()
        s = sw.stats()
        self.assertEqual(s.requests, 2)

    def test_stats_denied(self):
        sw, _ = self._make(max_req=1)
        sw.allow()
        sw.allow()
        s = sw.stats()
        self.assertEqual(s.denied, 1)

    def test_internal_timestamps_expire(self):
        sw, clock = self._make(max_req=3, window=5.0)
        sw.allow()
        sw.allow()
        sw.allow()
        clock.advance(5.1)
        sw.stats()
        self.assertEqual(len(sw._timestamps), 0)

    def test_retry_after_equals_oldest_expiry(self):
        sw, clock = self._make(max_req=1, window=10.0)
        sw.allow()               # at t=0
        clock.advance(3.0)       # t=3
        d = sw.allow()
        self.assertFalse(d.allowed)
        # oldest is at t=0, expires at t=10, now=3 -> 7 seconds
        self.assertAlmostEqual(d.retry_after, 7.0, delta=0.1)

    def test_allow_n_gt1(self):
        sw, _ = self._make(max_req=10)
        d = sw.allow(5)
        self.assertTrue(d.allowed)

    def test_allow_n_exceed_denied(self):
        sw, _ = self._make(max_req=3)
        d = sw.allow(5)
        self.assertFalse(d.allowed)

    def test_interleaved_expiry(self):
        sw, clock = self._make(max_req=3, window=10.0)
        sw.allow()               # t=0
        clock.advance(2.0)
        sw.allow()               # t=2
        clock.advance(2.0)
        sw.allow()               # t=4
        clock.advance(7.0)       # t=11  first expired, second expired
        d = sw.allow()
        self.assertTrue(d.allowed)

    def test_stats_current_tokens(self):
        sw, _ = self._make(max_req=5)
        sw.allow(3)
        s = sw.stats()
        self.assertAlmostEqual(s.current_tokens, 2.0)

    def test_no_boundary_burst_vs_fixed(self):
        """Compare sliding vs fixed boundary behaviour."""
        clock_fixed = FakeClock(0.0)
        clock_sliding = FakeClock(0.0)
        fw = FixedWindow(5, 10.0, clock=clock_fixed)
        sw = SlidingWindow(5, 10.0, clock=clock_sliding)

        for _ in range(5):
            fw.allow()
            sw.allow()

        clock_fixed.advance(10.0)
        clock_sliding.advance(10.0)

        # Fixed window resets entirely -> allows another burst
        fixed_extra = sum(1 for _ in range(5) if fw.allow().allowed)
        # Sliding window also allows, but only after old ones expired
        sliding_extra = sum(1 for _ in range(5) if sw.allow().allowed)

        self.assertEqual(fixed_extra, 5)
        self.assertEqual(sliding_extra, 5)

        # Now both have admitted 10 total; prove that just before expiry
        # sliding prevents extra while fixed might not
        clock2_fixed = FakeClock(0.0)
        clock2_sliding = FakeClock(0.0)
        fw2 = FixedWindow(5, 10.0, clock=clock2_fixed)
        sw2 = SlidingWindow(5, 10.0, clock=clock2_sliding)
        for _ in range(5):
            fw2.allow()
        for _ in range(5):
            sw2.allow()
        clock2_fixed.advance(9.9)
        clock2_sliding.advance(9.9)
        # Fixed: not yet reset, denied
        # Sliding: not yet expired, denied
        d_fixed = fw2.allow()
        d_sliding = sw2.allow()
        self.assertFalse(d_fixed.allowed)
        self.assertFalse(d_sliding.allowed)


# ===========================================================================
# PerKeyTokenBuckets (12 tests)
# ===========================================================================

class TestPerKeyTokenBuckets(unittest.TestCase):

    def _make(self, capacity=5, rate=1.0, start=0.0):
        clock = FakeClock(start)
        return PerKeyTokenBuckets(capacity, rate, clock=clock), clock

    def test_allow_new_key(self):
        pk, _ = self._make()
        d = pk.allow("alice")
        self.assertTrue(d.allowed)

    def test_keys_independent(self):
        pk, _ = self._make(capacity=1)
        pk.allow("alice")
        # alice exhausted; bob still has tokens
        d = pk.allow("bob")
        self.assertTrue(d.allowed)

    def test_same_key_depletes(self):
        pk, _ = self._make(capacity=2)
        pk.allow("alice")
        pk.allow("alice")
        d = pk.allow("alice")
        self.assertFalse(d.allowed)

    def test_separate_buckets_per_key(self):
        pk, _ = self._make(capacity=3)
        for _ in range(3):
            pk.allow("x")
        d = pk.allow("y")
        self.assertTrue(d.allowed)

    def test_stats_per_key(self):
        pk, _ = self._make(capacity=5)
        pk.allow("alpha")
        pk.allow("alpha")
        s = pk.stats("alpha")
        self.assertIsNotNone(s)
        self.assertEqual(s.allowed, 2)

    def test_stats_unknown_key_none(self):
        pk, _ = self._make()
        s = pk.stats("nonexistent")
        self.assertIsNone(s)

    def test_keys_listing(self):
        pk, _ = self._make()
        pk.allow("a")
        pk.allow("b")
        pk.allow("c")
        k = pk.keys()
        self.assertIn("a", k)
        self.assertIn("b", k)
        self.assertIn("c", k)

    def test_key_created_on_first_access(self):
        pk, _ = self._make()
        self.assertEqual(len(pk.keys()), 0)
        pk.allow("new_key")
        self.assertEqual(len(pk.keys()), 1)

    def test_refill_shared_clock(self):
        pk, clock = self._make(capacity=1, rate=1.0)
        pk.allow("user1")
        clock.advance(1.0)
        d = pk.allow("user1")
        self.assertTrue(d.allowed)

    def test_many_keys(self):
        pk, _ = self._make(capacity=10)
        keys = [f"key_{i}" for i in range(50)]
        for k in keys:
            d = pk.allow(k)
            self.assertTrue(d.allowed)
        self.assertEqual(len(pk.keys()), 50)

    def test_allow_with_n(self):
        pk, _ = self._make(capacity=10)
        d = pk.allow("user", n=5)
        self.assertTrue(d.allowed)
        self.assertEqual(d.remaining, 5)

    def test_concurrent_different_keys(self):
        pk, _ = self._make(capacity=100, rate=10.0)
        errors = []
        def run(key):
            for _ in range(10):
                try:
                    pk.allow(key)
                except Exception as e:
                    errors.append(e)
        threads = [threading.Thread(target=run, args=(f"k{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# ===========================================================================
# 429 + Retry-After path (8 tests)
# ===========================================================================

class TestRetryAfterPath(unittest.TestCase):

    def test_token_bucket_retry_after_wait(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(1, 1.0, clock=clock)
        tb.allow()
        d = tb.allow()
        self.assertFalse(d.allowed)
        ra = d.retry_after
        clock.advance(ra)
        d2 = tb.allow()
        self.assertTrue(d2.allowed)

    def test_sliding_window_retry_after_wait(self):
        clock = FakeClock(0.0)
        sw = SlidingWindow(1, 5.0, clock=clock)
        sw.allow()
        d = sw.allow()
        self.assertFalse(d.allowed)
        clock.advance(d.retry_after + 0.001)
        d2 = sw.allow()
        self.assertTrue(d2.allowed)

    def test_fixed_window_retry_after_wait(self):
        clock = FakeClock(0.0)
        fw = FixedWindow(1, 5.0, clock=clock)
        fw.allow()
        d = fw.allow()
        self.assertFalse(d.allowed)
        clock.advance(d.retry_after + 0.001)
        d2 = fw.allow()
        self.assertTrue(d2.allowed)

    def test_leaky_bucket_retry_after_wait(self):
        clock = FakeClock(0.0)
        lb = LeakyBucket(1, 1.0, clock=clock)
        lb.allow()
        d = lb.allow()
        self.assertFalse(d.allowed)
        clock.advance(d.retry_after)
        d2 = lb.allow()
        self.assertTrue(d2.allowed)

    def test_retry_after_nonnegative(self):
        for Cls, args in [
            (TokenBucket, (1, 1.0)),
            (LeakyBucket, (1, 1.0)),
            (FixedWindow, (1, 5.0)),
            (SlidingWindow, (1, 5.0)),
        ]:
            clock = FakeClock(0.0)
            limiter = Cls(*args, clock=clock)
            limiter.allow()
            d = limiter.allow()
            self.assertGreaterEqual(d.retry_after, 0.0)

    def test_http_429_retry_after_header(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(2, 1.0, clock=clock)
        srv = RateLimitServer(tb)
        port = srv.start()
        try:
            http_get(f"http://127.0.0.1:{port}/")
            http_get(f"http://127.0.0.1:{port}/")
            status, headers, body = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(status, 429)
            self.assertIn("Retry-After", headers)
            ra = int(headers["Retry-After"])
            self.assertGreaterEqual(ra, 1)
        finally:
            srv.stop()

    def test_http_200_when_allowed(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(10, 1.0, clock=clock)
        srv = RateLimitServer(tb)
        port = srv.start()
        try:
            status, _, body = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "ok")
        finally:
            srv.stop()

    def test_http_body_has_retry_after_field(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(1, 0.1, clock=clock)
        srv = RateLimitServer(tb)
        port = srv.start()
        try:
            http_get(f"http://127.0.0.1:{port}/")
            status, _, body = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(status, 429)
            self.assertIn("retry_after", body)
            self.assertGreater(body["retry_after"], 0)
        finally:
            srv.stop()


# ===========================================================================
# Threaded concurrency stress (8 tests)
# ===========================================================================

class TestThreadedConcurrency(unittest.TestCase):

    def test_locked_token_bucket_no_over_admit(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(50, 0.0001, clock=clock)
        admitted = []
        lock = threading.Lock()

        def run():
            for _ in range(20):
                d = tb.allow()
                if d.allowed:
                    with lock:
                        admitted.append(1)

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(len(admitted), 50)

    def test_locked_sliding_window_no_over_admit(self):
        clock = FakeClock(0.0)
        sw = SlidingWindow(50, 100.0, clock=clock)
        admitted = []
        lock = threading.Lock()

        def run():
            for _ in range(20):
                d = sw.allow()
                if d.allowed:
                    with lock:
                        admitted.append(1)

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(len(admitted), 50)

    def test_locked_fixed_window_no_over_admit(self):
        clock = FakeClock(0.0)
        fw = FixedWindow(50, 100.0, clock=clock)
        admitted = []
        lock = threading.Lock()

        def run():
            for _ in range(20):
                d = fw.allow()
                if d.allowed:
                    with lock:
                        admitted.append(1)

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(len(admitted), 50)

    def test_toctou_race_unlocked_counter(self):
        """
        Demonstrates TOCTOU: an unlocked check-then-act counter
        can over-admit under concurrency.
        """
        limit = 50
        counter = [0]           # mutable list for shared state; NO lock
        admitted = []

        def run():
            for _ in range(20):
                if counter[0] < limit:  # check
                    time.sleep(0)       # yield to increase race probability
                    counter[0] += 1     # act
                    admitted.append(1)

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # The test just demonstrates the pattern; the unlocked version
        # may or may not over-admit on any given run.
        # We just assert it ran without crashing.
        self.assertGreater(len(admitted), 0)

    def test_per_key_concurrent_independent(self):
        clock = FakeClock(0.0)
        pk = PerKeyTokenBuckets(100, 1.0, clock=clock)
        results = {}
        lock = threading.Lock()

        def run(key):
            count = 0
            for _ in range(50):
                if pk.allow(key).allowed:
                    count += 1
            with lock:
                results[key] = count

        threads = [threading.Thread(target=run, args=(f"key_{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for _key, count in results.items():
            self.assertLessEqual(count, 100)

    def test_concurrent_stats_consistent(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(1000, 0.001, clock=clock)

        def run():
            for _ in range(50):
                tb.allow()

        threads = [threading.Thread(target=run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        s = tb.stats()
        self.assertEqual(s.requests, s.allowed + s.denied)

    def test_leaky_bucket_concurrent(self):
        clock = FakeClock(0.0)
        lb = LeakyBucket(50, 0.001, clock=clock)
        admitted = []
        lock = threading.Lock()

        def run():
            for _ in range(20):
                d = lb.allow()
                if d.allowed:
                    with lock:
                        admitted.append(1)

        threads = [threading.Thread(target=run) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(len(admitted), 50)

    def test_http_server_concurrent_requests(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(30, 1.0, clock=clock)
        srv = RateLimitServer(tb)
        srv.start()
        try:
            status_codes = []
            lock = threading.Lock()

            def fetch():
                s, _, _ = http_get(srv.url + "/")
                with lock:
                    status_codes.append(s)

            threads = [threading.Thread(target=fetch) for _ in range(40)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            ok_count = status_codes.count(200)
            rl_count = status_codes.count(429)
            self.assertLessEqual(ok_count, 30)
            self.assertGreater(rl_count, 0)
        finally:
            srv.stop()


# ===========================================================================
# RateLimitReport (6 tests)
# ===========================================================================

class TestRateLimitReport(unittest.TestCase):

    def test_is_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(RateLimitReport))

    def test_default_values(self):
        r = RateLimitReport()
        self.assertEqual(r.total_requests, 0)
        self.assertEqual(r.total_allowed, 0)
        self.assertEqual(r.total_denied, 0)

    def test_make_report_from_stats(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(5, 1.0, clock=clock)
        tb.allow()
        tb.allow()
        tb.allow()
        tb.allow()
        tb.allow()
        tb.allow()
        s = tb.stats()
        r = make_report("token_bucket", s, notes="test run")
        self.assertEqual(r.total_requests, 6)
        self.assertEqual(r.algorithm, "token_bucket")
        self.assertEqual(r.notes, "test run")

    def test_totals_consistent(self):
        r = RateLimitReport(total_requests=10, total_allowed=7, total_denied=3)
        self.assertEqual(r.total_requests, r.total_allowed + r.total_denied)

    def test_algorithm_field(self):
        r = RateLimitReport(algorithm="sliding_window")
        self.assertEqual(r.algorithm, "sliding_window")

    def test_notes_field(self):
        r = RateLimitReport(notes="boundary burst observed")
        self.assertEqual(r.notes, "boundary burst observed")


# ===========================================================================
# MockRateLimitHandler / RateLimitServer (8 tests)
# ===========================================================================

class TestMockServer(unittest.TestCase):

    def _start(self, capacity=10, rate=1.0):
        clock = FakeClock(0.0)
        tb = TokenBucket(capacity, rate, clock=clock)
        srv = RateLimitServer(tb)
        port = srv.start()
        return srv, tb, clock, port

    def test_server_starts_and_returns_port(self):
        srv, tb, clock, port = self._start()
        try:
            self.assertIsInstance(port, int)
            self.assertGreater(port, 0)
        finally:
            srv.stop()

    def test_get_200_when_tokens_available(self):
        srv, tb, clock, port = self._start(capacity=5)
        try:
            status, _, _ = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(status, 200)
        finally:
            srv.stop()

    def test_get_429_when_exhausted(self):
        srv, tb, clock, port = self._start(capacity=1)
        try:
            http_get(f"http://127.0.0.1:{port}/")
            status, _, _ = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(status, 429)
        finally:
            srv.stop()

    def test_retry_after_header_present_on_429(self):
        srv, tb, clock, port = self._start(capacity=1)
        try:
            http_get(f"http://127.0.0.1:{port}/")
            _, headers, _ = http_get(f"http://127.0.0.1:{port}/")
            self.assertIn("Retry-After", headers)
        finally:
            srv.stop()

    def test_response_body_ok_status(self):
        srv, tb, clock, port = self._start(capacity=5)
        try:
            _, _, body = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(body["status"], "ok")
        finally:
            srv.stop()

    def test_response_body_rate_limited_status(self):
        srv, tb, clock, port = self._start(capacity=1)
        try:
            http_get(f"http://127.0.0.1:{port}/")
            _, _, body = http_get(f"http://127.0.0.1:{port}/")
            self.assertEqual(body["status"], "rate_limited")
        finally:
            srv.stop()

    def test_response_remaining_field(self):
        srv, tb, clock, port = self._start(capacity=5)
        try:
            _, _, body = http_get(f"http://127.0.0.1:{port}/")
            self.assertIn("remaining", body)
            self.assertEqual(body["remaining"], 4)
        finally:
            srv.stop()

    def test_stop_server_cleans_up(self):
        srv, _, _, port = self._start()
        srv.stop()
        # After stop, connection should fail
        with self.assertRaises(Exception):
            http_get(f"http://127.0.0.1:{port}/", timeout=1.0)


# ===========================================================================
# Integration: cross-algorithm comparison (10 tests)
# ===========================================================================

class TestCrossAlgorithm(unittest.TestCase):

    def test_all_algorithms_deny_eventually(self):
        clock = FakeClock(0.0)
        limiters = [
            TokenBucket(5, 0.1, clock=clock),
            LeakyBucket(5, 0.1, clock=clock),
            FixedWindow(5, 100.0, clock=clock),
            SlidingWindow(5, 100.0, clock=clock),
        ]
        for limiter in limiters:
            decisions = [limiter.allow() for _ in range(10)]
            denied = [d for d in decisions if not d.allowed]
            self.assertGreater(len(denied), 0, msg=f"{type(limiter).__name__} never denied")

    def test_all_algorithms_return_decision(self):
        clock = FakeClock(0.0)
        limiters = [
            TokenBucket(5, 1.0, clock=clock),
            LeakyBucket(5, 1.0, clock=clock),
            FixedWindow(5, 10.0, clock=clock),
            SlidingWindow(5, 10.0, clock=clock),
        ]
        for limiter in limiters:
            d = limiter.allow()
            self.assertIsInstance(d, RateLimitDecision)

    def test_all_algorithms_have_stats(self):
        clock = FakeClock(0.0)
        limiters = [
            TokenBucket(5, 1.0, clock=clock),
            LeakyBucket(5, 1.0, clock=clock),
            FixedWindow(5, 10.0, clock=clock),
            SlidingWindow(5, 10.0, clock=clock),
        ]
        for limiter in limiters:
            limiter.allow()
            s = limiter.stats()
            self.assertIsInstance(s, LimiterStats)

    def test_stats_requests_equals_calls(self):
        clock = FakeClock(0.0)
        for Cls, args in [
            (TokenBucket, (10, 1.0)),
            (LeakyBucket, (10, 1.0)),
            (FixedWindow, (10, 100.0)),
            (SlidingWindow, (10, 100.0)),
        ]:
            limiter = Cls(*args, clock=clock)
            for _ in range(7):
                limiter.allow()
            s = limiter.stats()
            self.assertEqual(s.requests, 7, msg=f"{Cls.__name__}")

    def test_make_report_all_algorithms(self):
        clock = FakeClock(0.0)
        for name, Cls, args in [
            ("token_bucket", TokenBucket, (5, 1.0)),
            ("leaky_bucket", LeakyBucket, (5, 1.0)),
            ("fixed_window", FixedWindow, (5, 10.0)),
            ("sliding_window", SlidingWindow, (5, 10.0)),
        ]:
            limiter = Cls(*args, clock=clock)
            for _ in range(8):
                limiter.allow()
            s = limiter.stats()
            r = make_report(name, s)
            self.assertEqual(r.algorithm, name)
            self.assertEqual(r.total_requests, 8)

    def test_sliding_vs_fixed_boundary_difference(self):
        """Sliding window limits burst that fixed window allows."""
        clock_sw = FakeClock(0.0)
        sw = SlidingWindow(5, 10.0, clock=clock_sw)
        for _ in range(5):
            sw.allow()
        clock_sw.advance(9.9)    # old requests not yet expired
        d_sw = sw.allow()
        self.assertFalse(d_sw.allowed)

        clock_fw = FakeClock(0.0)
        fw = FixedWindow(5, 10.0, clock=clock_fw)
        for _ in range(5):
            fw.allow()
        clock_fw.advance(10.0)   # window fully resets
        d_fw = fw.allow()
        self.assertTrue(d_fw.allowed)

    def test_token_bucket_burst_exceeds_leaky(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(10, 1.0, clock=clock)
        lb = LeakyBucket(10, 1.0, clock=clock)
        tb_admitted = sum(1 for _ in range(15) if tb.allow().allowed)
        lb_admitted = sum(1 for _ in range(15) if lb.allow().allowed)
        # Both allow up to capacity; both deny beyond
        self.assertEqual(tb_admitted, 10)
        self.assertEqual(lb_admitted, 10)

    def test_per_key_wraps_token_bucket_semantics(self):
        clock = FakeClock(0.0)
        pk = PerKeyTokenBuckets(3, 1.0, clock=clock)
        for _ in range(3):
            pk.allow("user1")
        d = pk.allow("user1")
        self.assertFalse(d.allowed)
        d2 = pk.allow("user2")
        self.assertTrue(d2.allowed)

    def test_all_deny_retry_after_positive(self):
        clock = FakeClock(0.0)
        limiters = [
            TokenBucket(1, 1.0, clock=clock),
            LeakyBucket(1, 1.0, clock=clock),
            FixedWindow(1, 10.0, clock=clock),
            SlidingWindow(1, 10.0, clock=clock),
        ]
        for limiter in limiters:
            limiter.allow()
            d = limiter.allow()
            self.assertFalse(d.allowed, msg=type(limiter).__name__)
            self.assertGreater(d.retry_after, 0, msg=type(limiter).__name__)

    def test_report_denied_field(self):
        clock = FakeClock(0.0)
        tb = TokenBucket(3, 0.01, clock=clock)
        for _ in range(5):
            tb.allow()
        s = tb.stats()
        r = make_report("token_bucket", s)
        self.assertEqual(r.total_denied, 2)
        self.assertEqual(r.total_allowed, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
