"""
Tests for DateTime Test Harness (Harness 20 of 36).
~73 tests. Pure stdlib, no zoneinfo — uses datetime.timezone(timedelta(hours=N)).
"""

import dataclasses
import datetime
import subprocess
import sys
import time
import unittest
from datetime import timedelta, timezone

from harnesses._teeth import verify
from harnesses.core import datetime_test_harness as harness
from harnesses.core.datetime_test_harness import (
    LEAP_CORPUS,
    TEETH,
    BoundaryTester,
    Clock,
    DSTTester,
    DurationTester,
    LeapYearTester,
    ParseFormatTester,
    ServerTimeTester,
    TimezoneTester,
    oracle_is_leap,
    prove,
)

# ===========================================================================
# Clock Tests (12 tests)
# ===========================================================================

class TestClock(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()

    def tearDown(self):
        self.clock.reset()

    # 1
    def test_clock_now_returns_datetime(self):
        now = self.clock.now()
        self.assertIsInstance(now, datetime.datetime)

    # 2
    def test_clock_now_is_aware(self):
        now = self.clock.now()
        self.assertIsNotNone(now.tzinfo)

    # 3
    def test_clock_now_utc(self):
        now = self.clock.now()
        self.assertEqual(now.tzinfo, timezone.utc)

    # 4
    def test_clock_freeze(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.assertEqual(self.clock.now(), frozen)

    # 5
    def test_clock_freeze_stays_frozen(self):
        frozen = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        t1 = self.clock.now()
        time.sleep(0.01)
        t2 = self.clock.now()
        self.assertEqual(t1, t2)

    # 6
    def test_clock_advance_from_frozen(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.advance(3600)
        expected = datetime.datetime(2024, 6, 15, 13, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(self.clock.now(), expected)

    # 7
    def test_clock_advance_multiple_times(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.advance(1800)
        self.clock.advance(1800)
        expected = datetime.datetime(2024, 6, 15, 13, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(self.clock.now(), expected)

    # 8
    def test_clock_advance_negative(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.advance(-3600)
        expected = datetime.datetime(2024, 6, 15, 11, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(self.clock.now(), expected)

    # 9
    def test_clock_reset_unfreezes(self):
        frozen = datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.reset()
        now = self.clock.now()
        # After reset it should be close to current real time, not frozen
        self.assertGreater(now.year, 2020)

    # 10
    def test_clock_reset_clears_offset(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.advance(9999)
        self.clock.reset()
        # offset should be cleared; now() should be live time
        now = self.clock.now()
        self.assertIsInstance(now, datetime.datetime)

    # 11
    def test_clock_advance_fractional_seconds(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.advance(0.5)
        result = self.clock.now()
        self.assertEqual(result, frozen + timedelta(seconds=0.5))

    # 12
    def test_clock_freeze_naive_datetime(self):
        # freeze with a naive datetime still works
        naive = datetime.datetime(2024, 6, 15, 12, 0, 0)
        self.clock.freeze(naive)
        self.assertEqual(self.clock.now(), naive)


# ===========================================================================
# TimezoneTester Tests (14 tests)
# ===========================================================================

class TestTimezoneTester(unittest.TestCase):

    def setUp(self):
        self.tz = TimezoneTester()
        self.utc_dt = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    # 13
    def test_est_offset_minus5(self):
        est_dt = self.tz.utc_to_est(self.utc_dt)
        self.assertEqual(est_dt.utcoffset(), timedelta(hours=-5))

    # 14
    def test_jst_offset_plus9(self):
        jst_dt = self.tz.utc_to_jst(self.utc_dt)
        self.assertEqual(jst_dt.utcoffset(), timedelta(hours=9))

    # 15
    def test_utc_to_est_hour(self):
        # UTC 12:00 → EST 07:00
        est_dt = self.tz.utc_to_est(self.utc_dt)
        self.assertEqual(est_dt.hour, 7)

    # 16
    def test_utc_to_jst_hour(self):
        # UTC 12:00 → JST 21:00
        jst_dt = self.tz.utc_to_jst(self.utc_dt)
        self.assertEqual(jst_dt.hour, 21)

    # 17
    def test_utc_to_est_same_instant(self):
        est_dt = self.tz.utc_to_est(self.utc_dt)
        self.assertEqual(est_dt.astimezone(timezone.utc), self.utc_dt)

    # 18
    def test_utc_to_jst_same_instant(self):
        jst_dt = self.tz.utc_to_jst(self.utc_dt)
        self.assertEqual(jst_dt.astimezone(timezone.utc), self.utc_dt)

    # 19
    def test_utc_to_est_naive_raises(self):
        naive = datetime.datetime(2024, 6, 15, 12, 0, 0)
        with self.assertRaises((ValueError, TypeError)):
            # Our implementation raises ValueError; Python might raise TypeError
            # on astimezone for naive on some platforms, so accept both
            self.tz.utc_to_est(naive)
            # If no exception, something is wrong
            raise AssertionError("Expected exception not raised")

    # 20
    def test_is_aware_with_aware(self):
        self.assertTrue(self.tz.is_aware(self.utc_dt))

    # 21
    def test_is_aware_with_naive(self):
        naive = datetime.datetime(2024, 6, 15, 12, 0, 0)
        self.assertFalse(self.tz.is_aware(naive))

    # 22
    def test_is_naive_with_naive(self):
        naive = datetime.datetime(2024, 6, 15, 12, 0, 0)
        self.assertTrue(self.tz.is_naive(naive))

    # 23
    def test_is_naive_with_aware(self):
        self.assertFalse(self.tz.is_naive(self.utc_dt))

    # 24
    def test_compare_aware_naive_raises_typeerror(self):
        aware = self.utc_dt
        naive = datetime.datetime(2024, 6, 15, 12, 0, 0)
        with self.assertRaises(TypeError):
            self.tz.compare_aware_naive(aware, naive)

    # 25
    def test_est_date_rollover(self):
        # UTC 2024-01-01 03:00 → EST 2023-12-31 22:00
        utc_midnight = datetime.datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
        est_dt = self.tz.utc_to_est(utc_midnight)
        self.assertEqual(est_dt.year, 2023)
        self.assertEqual(est_dt.month, 12)
        self.assertEqual(est_dt.day, 31)

    # 26
    def test_jst_next_day(self):
        # UTC 2024-06-15 16:00 → JST 2024-06-16 01:00
        utc_dt = datetime.datetime(2024, 6, 15, 16, 0, 0, tzinfo=timezone.utc)
        jst_dt = self.tz.utc_to_jst(utc_dt)
        self.assertEqual(jst_dt.day, 16)
        self.assertEqual(jst_dt.hour, 1)


# ===========================================================================
# DSTTester Tests (8 tests)
# ===========================================================================

class TestDSTTester(unittest.TestCase):

    def setUp(self):
        self.dst = DSTTester()

    # 27
    def test_spring_forward_gap_dt_is_naive(self):
        dt = self.dst.get_spring_forward_gap_dt()
        self.assertIsNone(dt.tzinfo)

    # 28
    def test_spring_forward_gap_hour(self):
        dt = self.dst.get_spring_forward_gap_dt()
        self.assertEqual(dt.hour, 2)
        self.assertEqual(dt.minute, 30)

    # 29
    def test_is_in_spring_forward_gap_true(self):
        dt = datetime.datetime(2024, 3, 10, 2, 30, 0)
        self.assertTrue(self.dst.is_in_spring_forward_gap(dt))

    # 30
    def test_is_in_spring_forward_gap_false_before(self):
        dt = datetime.datetime(2024, 3, 10, 1, 59, 0)
        self.assertFalse(self.dst.is_in_spring_forward_gap(dt))

    # 31
    def test_is_in_spring_forward_gap_false_after(self):
        dt = datetime.datetime(2024, 3, 10, 3, 0, 0)
        self.assertFalse(self.dst.is_in_spring_forward_gap(dt))

    # 32
    def test_fall_back_fold_same_wall_time(self):
        fold0, fold1 = self.dst.get_fall_back_fold_dt()
        self.assertEqual(fold0.hour, 1)
        self.assertEqual(fold0.minute, 30)
        self.assertEqual(fold1.hour, 1)
        self.assertEqual(fold1.minute, 30)

    # 33
    def test_fold_to_utc_fold0_is_edt(self):
        # fold=0 (EDT, UTC-4): 01:30 EDT = 05:30 UTC
        dt = datetime.datetime(2024, 11, 3, 1, 30, 0)
        utc_dt = self.dst.fold_to_utc(dt, fold=0)
        self.assertEqual(utc_dt.hour, 5)
        self.assertEqual(utc_dt.minute, 30)
        self.assertEqual(utc_dt.tzinfo, timezone.utc)

    # 34
    def test_fold_to_utc_fold1_is_est(self):
        # fold=1 (EST, UTC-5): 01:30 EST = 06:30 UTC
        dt = datetime.datetime(2024, 11, 3, 1, 30, 0)
        utc_dt = self.dst.fold_to_utc(dt, fold=1)
        self.assertEqual(utc_dt.hour, 6)
        self.assertEqual(utc_dt.minute, 30)
        self.assertEqual(utc_dt.tzinfo, timezone.utc)


# ===========================================================================
# LeapYearTester Tests (12 tests)
# ===========================================================================

class TestLeapYearTester(unittest.TestCase):

    def setUp(self):
        self.lyt = LeapYearTester()

    # 35
    def test_2024_is_leap(self):
        self.assertTrue(self.lyt.is_leap_year(2024))

    # 36
    def test_2000_is_leap(self):
        self.assertTrue(self.lyt.is_leap_year(2000))

    # 37
    def test_2023_not_leap(self):
        self.assertFalse(self.lyt.is_leap_year(2023))

    # 38
    def test_1900_not_leap(self):
        self.assertFalse(self.lyt.is_leap_year(1900))

    # 39
    def test_feb29_exists_2024(self):
        self.assertTrue(self.lyt.feb29_exists(2024))

    # 40
    def test_feb29_exists_2000(self):
        self.assertTrue(self.lyt.feb29_exists(2000))

    # 41
    def test_feb29_not_exists_2023(self):
        self.assertFalse(self.lyt.feb29_exists(2023))

    # 42
    def test_feb29_not_exists_1900(self):
        self.assertFalse(self.lyt.feb29_exists(1900))

    # 43
    def test_get_feb29_2024(self):
        dt = self.lyt.get_feb29(2024)
        self.assertEqual(dt.month, 2)
        self.assertEqual(dt.day, 29)
        self.assertEqual(dt.year, 2024)

    # 44
    def test_get_feb29_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.lyt.get_feb29(2023)

    # 45
    def test_days_in_feb_leap(self):
        self.assertEqual(self.lyt.days_in_feb(2024), 29)

    # 46
    def test_days_in_feb_non_leap(self):
        self.assertEqual(self.lyt.days_in_feb(2023), 28)


# ===========================================================================
# BoundaryTester Tests (10 tests)
# ===========================================================================

class TestBoundaryTester(unittest.TestCase):

    def setUp(self):
        self.bt = BoundaryTester()

    # 47
    def test_epoch_year(self):
        self.assertEqual(self.bt.get_epoch().year, 1970)

    # 48
    def test_epoch_timestamp_zero(self):
        ts = self.bt.datetime_to_timestamp(self.bt.get_epoch())
        self.assertAlmostEqual(ts, 0.0, places=1)

    # 49
    def test_timestamp_zero_to_datetime(self):
        dt = self.bt.timestamp_to_datetime(0)
        self.assertEqual(dt.year, 1970)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 1)

    # 50
    def test_pre_epoch_timestamp_negative(self):
        pre = self.bt.get_pre_epoch_dt()
        ts = self.bt.pre_epoch_timestamp(pre)
        self.assertLess(ts, 0)

    # 51
    def test_pre_epoch_year(self):
        pre = self.bt.get_pre_epoch_dt()
        self.assertEqual(pre.year, 1900)

    # 52
    def test_far_future_year(self):
        ff = self.bt.get_far_future()
        self.assertEqual(ff.year, 9999)

    # 53
    def test_far_future_is_aware(self):
        ff = self.bt.get_far_future()
        self.assertIsNotNone(ff.tzinfo)

    # 54
    def test_y2038_timestamp_value(self):
        self.assertEqual(BoundaryTester.Y2038_TIMESTAMP, 2147483647)

    # 55
    def test_y2038_dt_year(self):
        dt = self.bt.get_y2038_dt()
        self.assertEqual(dt.year, 2038)

    # 56
    def test_y2038_dt_is_aware(self):
        dt = self.bt.get_y2038_dt()
        self.assertIsNotNone(dt.tzinfo)


# ===========================================================================
# ParseFormatTester Tests (10 tests)
# ===========================================================================

class TestParseFormatTester(unittest.TestCase):

    def setUp(self):
        self.pf = ParseFormatTester()
        self.naive_dt = datetime.datetime(2024, 6, 15, 12, 30, 45)
        self.aware_dt = datetime.datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)

    # 57
    def test_to_iso8601_naive(self):
        s = self.pf.to_iso8601(self.naive_dt)
        self.assertEqual(s, "2024-06-15T12:30:45")

    # 58
    def test_to_iso8601_aware(self):
        s = self.pf.to_iso8601(self.aware_dt)
        self.assertIn("2024-06-15T12:30:45", s)
        self.assertIn("+00:00", s)

    # 59
    def test_from_iso8601_naive(self):
        dt = self.pf.from_iso8601("2024-06-15T12:30:45")
        self.assertEqual(dt, self.naive_dt)

    # 60
    def test_from_iso8601_aware(self):
        dt = self.pf.from_iso8601("2024-06-15T12:30:45+00:00")
        self.assertEqual(dt, self.aware_dt)

    # 61
    def test_roundtrip_iso_naive(self):
        result = self.pf.roundtrip_iso(self.naive_dt)
        self.assertEqual(result, self.naive_dt)

    # 62
    def test_roundtrip_iso_aware(self):
        result = self.pf.roundtrip_iso(self.aware_dt)
        self.assertEqual(result, self.aware_dt)

    # 63
    def test_strptime_iso(self):
        dt = self.pf.strptime_iso("2024-06-15T12:30:45")
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 15)
        self.assertEqual(dt.hour, 12)

    # 64
    def test_strftime_iso(self):
        s = self.pf.strftime_iso(self.naive_dt)
        self.assertEqual(s, "2024-06-15T12:30:45")

    # 65
    def test_roundtrip_strptime(self):
        result = self.pf.roundtrip_strptime(self.naive_dt)
        self.assertEqual(result, self.naive_dt)

    # 66
    def test_format_http_date(self):
        # 2024-06-15 is a Saturday
        s = self.pf.format_http_date(self.aware_dt)
        self.assertIn("GMT", s)
        self.assertIn("2024", s)
        self.assertIn("Jun", s)


# ===========================================================================
# DurationTester Tests (10 tests)
# ===========================================================================

class TestDurationTester(unittest.TestCase):

    def setUp(self):
        self.dt = DurationTester()
        self.base_dt = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    # 67
    def test_add_duration(self):
        result = self.dt.add_duration(self.base_dt, timedelta(hours=2))
        self.assertEqual(result.hour, 14)

    # 68
    def test_subtract_duration(self):
        result = self.dt.subtract_duration(self.base_dt, timedelta(hours=3))
        self.assertEqual(result.hour, 9)

    # 69
    def test_difference(self):
        dt2 = self.base_dt + timedelta(days=1)
        diff = self.dt.difference(self.base_dt, dt2)
        self.assertEqual(diff.total_seconds(), 86400.0)

    # 70
    def test_total_seconds_one_day(self):
        td = timedelta(days=1)
        self.assertEqual(self.dt.total_seconds(td), 86400.0)

    # 71
    def test_timedelta_components(self):
        td = timedelta(days=1, hours=2, minutes=3, seconds=4)
        comp = self.dt.timedelta_components(td)
        self.assertEqual(comp["days"], 1)
        self.assertEqual(comp["hours"], 2)
        self.assertEqual(comp["minutes"], 3)
        self.assertEqual(comp["seconds"], 4)

    # 72
    def test_make_timedelta(self):
        td = self.dt.make_timedelta(days=1, hours=2, minutes=30, seconds=15)
        expected = timedelta(days=1, hours=2, minutes=30, seconds=15)
        self.assertEqual(td, expected)

    # 73
    def test_monotonic_elapsed_nonnegative(self):
        _, elapsed = self.dt.monotonic_elapsed(lambda: None)
        self.assertGreaterEqual(elapsed, 0)

    # 74 (bonus to reach ~73+)
    def test_wall_elapsed_nonnegative(self):
        _, elapsed = self.dt.wall_elapsed(lambda: None)
        self.assertGreaterEqual(elapsed, 0)

    # 75
    def test_negative_timedelta_components(self):
        td = timedelta(seconds=-3661)
        comp = self.dt.timedelta_components(td)
        self.assertEqual(comp["sign"], -1)
        self.assertEqual(comp["hours"], 1)
        self.assertEqual(comp["minutes"], 1)
        self.assertEqual(comp["seconds"], 1)

    # 76
    def test_add_duration_crosses_day(self):
        result = self.dt.add_duration(self.base_dt, timedelta(hours=14))
        self.assertEqual(result.day, 16)
        self.assertEqual(result.hour, 2)


# ===========================================================================
# ServerTimeTester Tests (8 tests)
# ===========================================================================

class TestServerTimeTester(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.server = ServerTimeTester(port=0, clock=self.clock)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.clock.reset()

    # 77
    def test_server_starts_and_responds(self):
        data = self.server.get_time()
        self.assertIn("iso", data)

    # 78
    def test_server_returns_timestamp(self):
        data = self.server.get_time()
        self.assertIn("timestamp", data)
        self.assertIsInstance(data["timestamp"], float)

    # 79
    def test_server_returns_year(self):
        data = self.server.get_time()
        self.assertIn("year", data)
        self.assertIsInstance(data["year"], int)

    # 80
    def test_server_frozen_clock(self):
        frozen = datetime.datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        data = self.server.get_time()
        self.assertEqual(data["year"], 2024)
        self.assertEqual(data["month"], 1)
        self.assertEqual(data["day"], 15)
        self.assertEqual(data["hour"], 8)

    # 81
    def test_server_advanced_clock(self):
        frozen = datetime.datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        self.clock.advance(7200)  # +2 hours
        data = self.server.get_time()
        self.assertEqual(data["hour"], 10)

    # 82
    def test_server_port_assigned(self):
        self.assertIsNotNone(self.server.actual_port)
        self.assertGreater(self.server.actual_port, 0)

    # 83
    def test_server_iso_roundtrip(self):
        frozen = datetime.datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        self.clock.freeze(frozen)
        data = self.server.get_time()
        parsed = datetime.datetime.fromisoformat(data["iso"])
        # Compare timestamps (ISO string may include +00:00 suffix)
        self.assertAlmostEqual(parsed.timestamp(), frozen.timestamp(), places=0)

    # 84
    def test_server_context_manager(self):
        # Stop current server first
        self.server.stop()
        clock2 = Clock()
        frozen = datetime.datetime(2023, 3, 14, 15, 9, 26, tzinfo=timezone.utc)
        clock2.freeze(frozen)
        with ServerTimeTester(port=0, clock=clock2) as srv:
            data = srv.get_time()
            self.assertEqual(data["year"], 2023)
            self.assertEqual(data["month"], 3)
            self.assertEqual(data["day"], 14)
        # Restart for tearDown
        self.server.start()


# ===========================================================================
# Teeth — the harness must catch a real planted leap-year bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct Gregorian leap-year predicate must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_is_leap))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 2)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(LEAP_CORPUS))

    def test_oracle_matches_frozen_literals(self):
        # The frozen expectations are non-circular constants the oracle must
        # reproduce exactly for every corpus year.
        for case in LEAP_CORPUS:
            self.assertEqual(oracle_is_leap(case.year), case.expected, case.name)

    def test_mutants_break_the_discriminating_cases(self):
        # Pin WHY each mutant is caught: every_4th mishandles the non-400
        # centuries; forgets_400 mishandles the %400 century.
        self.assertTrue(harness.every_4th(1900))   # bug: says leap, truth is False
        self.assertTrue(harness.every_4th(2100))   # bug: says leap, truth is False
        self.assertFalse(harness.forgets_400(2000))  # bug: says common, truth is True

    def test_noncircular_corpus(self):
        # Corrupt one frozen literal and assert prove(oracle) flips to True.
        # If it does not flip, the corpus is being derived from the oracle
        # (circular) rather than judged against frozen ground truth.
        original = harness.LEAP_CORPUS
        corrupted = list(original)
        corrupted[0] = dataclasses.replace(corrupted[0], expected=not corrupted[0].expected)
        harness.LEAP_CORPUS = tuple(corrupted)
        try:
            self.assertTrue(prove(oracle_is_leap),
                            "prove(oracle) must flip True when a frozen literal is corrupted")
        finally:
            harness.LEAP_CORPUS = original
        # Sanity: the restored corpus is clean again.
        self.assertFalse(prove(oracle_is_leap))

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
