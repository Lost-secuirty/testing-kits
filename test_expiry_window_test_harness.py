"""test_expiry_window_test_harness.py — unittest suite for expiry_window_test_harness (42)."""

import sqlite3
import unittest
from datetime import datetime, timedelta

from expiry_window_test_harness import (
    DateWindowOracle,
    ExpiryStore,
    _like_escape,
    run_all_scenarios,
)

REF_TODAY = "2026-05-25"


def _offset(today_str, days):
    base = datetime.strptime(today_str, "%Y-%m-%d")
    return (base + timedelta(days=days)).strftime("%Y-%m-%d")


def _fresh_store(drugs=None):
    conn = sqlite3.connect(":memory:")
    s = ExpiryStore(conn)
    if drugs:
        s.seed(drugs)
    return s


class TestExpiryStore(unittest.TestCase):

    def test_inclusive_cutoff(self):
        s = _fresh_store([("DrugA", _offset(REF_TODAY, 30))])
        rows = s.expiring(within_days=30, today=REF_TODAY)
        self.assertTrue(any(r[0] == "DrugA" for r in rows))

    def test_exclusive_upper(self):
        s = _fresh_store([("DrugB", _offset(REF_TODAY, 31))])
        rows = s.expiring(within_days=30, today=REF_TODAY)
        self.assertFalse(any(r[0] == "DrugB" for r in rows))

    def test_old_expired_in_expiring_window(self):
        s = _fresh_store([("OldDrug", _offset(REF_TODAY, -365))])
        rows = s.expiring(within_days=30, today=REF_TODAY)
        self.assertTrue(any(r[0] == "OldDrug" for r in rows))

    def test_today_not_in_expired(self):
        s = _fresh_store([("TodayDrug", REF_TODAY)])
        rows = s.expired(today=REF_TODAY)
        self.assertFalse(any(r[0] == "TodayDrug" for r in rows))

    def test_yesterday_in_expired(self):
        s = _fresh_store([("YestDrug", _offset(REF_TODAY, -1))])
        rows = s.expired(today=REF_TODAY)
        self.assertTrue(any(r[0] == "YestDrug" for r in rows))

    def test_sorting_asc_exp_date_then_name(self):
        drugs = [("Zoloft", "2026-06-01"),
                 ("Aspirin", "2026-05-30"),
                 ("Benadryl", "2026-05-30")]
        s = _fresh_store(drugs)
        rows = s.expiring(within_days=60, today=REF_TODAY)
        names = [r[0] for r in rows]
        self.assertEqual(names, ["Aspirin", "Benadryl", "Zoloft"])

    def test_empty_result_far_future(self):
        s = _fresh_store([("FarDrug", _offset(REF_TODAY, 365))])
        rows = s.expiring(within_days=30, today=REF_TODAY)
        self.assertEqual(rows, [])

    def test_zero_day_window_includes_today(self):
        s = _fresh_store([("TodayDrug", REF_TODAY)])
        rows = s.expiring(within_days=0, today=REF_TODAY)
        self.assertTrue(any(r[0] == "TodayDrug" for r in rows))

    def test_zero_day_window_excludes_tomorrow(self):
        s = _fresh_store([("TomDrug", _offset(REF_TODAY, 1))])
        rows = s.expiring(within_days=0, today=REF_TODAY)
        self.assertFalse(any(r[0] == "TomDrug" for r in rows))

    def test_multiple_drugs_count(self):
        drugs = [(f"Drug{i}", _offset(REF_TODAY, i - 5)) for i in range(10)]
        s = _fresh_store(drugs)
        oracle = DateWindowOracle.expiring_names(drugs, 30, REF_TODAY)
        rows = s.expiring(within_days=30, today=REF_TODAY)
        self.assertEqual({r[0] for r in rows}, oracle)


class TestLeapAndRollover(unittest.TestCase):

    def test_leap_day_appears_as_expired(self):
        s = _fresh_store([("LeapDrug", "2024-02-29")])
        rows = s.expired(today="2024-03-01")
        self.assertTrue(any(r[0] == "LeapDrug" for r in rows))

    def test_month_end_rollover(self):
        s = _fresh_store([("FebDrug", "2026-02-01")])
        rows = s.expiring(within_days=1, today="2026-01-31")
        self.assertTrue(any(r[0] == "FebDrug" for r in rows))

    def test_year_end_rollover(self):
        s = _fresh_store([("NYDrug", "2027-01-01")])
        rows = s.expiring(within_days=1, today="2026-12-31")
        self.assertTrue(any(r[0] == "NYDrug" for r in rows))

    def test_feb_non_leap_year(self):
        # 2026 is not a leap year; Feb 28 + 1 = Mar 1
        s = _fresh_store([("MarDrug", "2026-03-01")])
        rows = s.expiring(within_days=1, today="2026-02-28")
        self.assertTrue(any(r[0] == "MarDrug" for r in rows))


class TestLikeEscape(unittest.TestCase):

    def test_percent_in_name_retrieved_exactly(self):
        s = _fresh_store([("Test%Drug", REF_TODAY), ("TestXDrug", REF_TODAY)])
        rows = s.inventory_list(name_filter="Test%Drug")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Test%Drug")

    def test_underscore_in_name_retrieved_exactly(self):
        s = _fresh_store([("Test_Drug", REF_TODAY), ("TestXDrug", REF_TODAY)])
        rows = s.inventory_list(name_filter="Test_Drug")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Test_Drug")

    def test_like_escape_helper_escapes_percent(self):
        self.assertEqual(_like_escape("50%off"), "50\\%off")

    def test_like_escape_helper_escapes_underscore(self):
        self.assertEqual(_like_escape("drug_name"), "drug\\_name")

    def test_no_filter_returns_all(self):
        drugs = [("DrugA", REF_TODAY), ("DrugB", REF_TODAY)]
        s = _fresh_store(drugs)
        rows = s.inventory_list()
        self.assertEqual(len(rows), 2)


class TestDateWindowOracle(unittest.TestCase):

    def test_oracle_expiring_names(self):
        drugs = [("Near", _offset(REF_TODAY, 10)), ("Far", _offset(REF_TODAY, 60))]
        names = DateWindowOracle.expiring_names(drugs, 30, REF_TODAY)
        self.assertIn("Near", names)
        self.assertNotIn("Far", names)

    def test_oracle_expired_names(self):
        drugs = [("Old", _offset(REF_TODAY, -5)), ("New", _offset(REF_TODAY, 5))]
        names = DateWindowOracle.expired_names(drugs, REF_TODAY)
        self.assertIn("Old", names)
        self.assertNotIn("New", names)


class TestSelfTest(unittest.TestCase):

    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [],
                         "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count_at_least_13(self):
        results = run_all_scenarios(verbose=False)
        self.assertGreaterEqual(len(results), 13)


if __name__ == "__main__":
    unittest.main()
