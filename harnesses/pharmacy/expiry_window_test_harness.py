#!/usr/bin/env python3
"""expiry_window_test_harness.py — Inventory Expiry Window Alerting Harness (2026)
================================================================================
Pure-Python (ZERO dependencies) harness for testing date-window inventory
expiration alerting.

Distinct from datetime_test_harness (#20) which tests pure date arithmetic:
  - Combines calendar date arithmetic with SQLite queries
  - Tests inclusive vs exclusive cutoff semantics (the classic off-by-one hotspot)
  - Tests leap day, month-end rollover, year-end rollover under injected today
  - Tests LIKE wildcard escape for drug names containing % or _
  - DateWindowOracle provides an independent ground-truth implementation

Models db_inventory_expiring() and db_expired_inventory() from pharmacy_app/data.py.

Port: 19280

Usage:
  python expiry_window_test_harness.py --self-test
  python expiry_window_test_harness.py --mock-server --port 19280
  python expiry_window_test_harness.py --self-test --verbose
"""

import argparse
import json
import re
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ============================================================
# EXPIRY STORE
# ============================================================

def _like_escape(s):
    """Escape SQL LIKE wildcards so user text is treated as literal."""
    if not s:
        return s
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ExpiryStore:
    """In-memory SQLite inventory with date-window expiry queries."""

    def __init__(self, conn):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS Inventory (
                drug_name TEXT PRIMARY KEY,
                exp_date TEXT NOT NULL
            );
        """)
        self.conn.commit()

    def seed(self, drugs):
        """drugs: list of (drug_name, exp_date) tuples."""
        for name, exp in drugs:
            self.conn.execute(
                "INSERT OR REPLACE INTO Inventory (drug_name, exp_date) VALUES (?, ?)",
                (name, exp))
        self.conn.commit()

    def clear(self):
        self.conn.execute("DELETE FROM Inventory")
        self.conn.commit()

    def expiring(self, within_days=30, today=None):
        """Return (drug_name, exp_date) where exp_date <= today+within_days."""
        if today is None:
            base = datetime.now()
        else:
            base = datetime.strptime(today, "%Y-%m-%d")
        cutoff = (base + timedelta(days=within_days)).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT drug_name, exp_date FROM Inventory "
            "WHERE exp_date <= ? ORDER BY exp_date ASC, drug_name ASC",
            (cutoff,)
        ).fetchall()
        return [(r["drug_name"], r["exp_date"]) for r in rows]

    def expired(self, today=None):
        """Return (drug_name, exp_date) where exp_date < today (strictly past)."""
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")
        rows = self.conn.execute(
            "SELECT drug_name, exp_date FROM Inventory "
            "WHERE exp_date < ? ORDER BY exp_date ASC, drug_name ASC",
            (today,)
        ).fetchall()
        return [(r["drug_name"], r["exp_date"]) for r in rows]

    def inventory_list(self, name_filter=""):
        """All inventory rows; optional LIKE filter with wildcard escape."""
        if name_filter:
            pat = "%" + _like_escape(name_filter) + "%"
            rows = self.conn.execute(
                "SELECT drug_name, exp_date FROM Inventory "
                "WHERE drug_name LIKE ? ESCAPE '\\' "
                "ORDER BY exp_date, drug_name",
                (pat,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT drug_name, exp_date FROM Inventory "
                "ORDER BY exp_date, drug_name"
            ).fetchall()
        return [(r["drug_name"], r["exp_date"]) for r in rows]


# ============================================================
# DATE WINDOW ORACLE
# ============================================================

class DateWindowOracle:
    """Independent ground-truth implementation using only datetime arithmetic."""

    @staticmethod
    def expiring_names(drugs, within_days, today_str):
        """Return set of drug names that should appear in expiring(within_days, today_str)."""
        base = datetime.strptime(today_str, "%Y-%m-%d")
        cutoff_str = (base + timedelta(days=within_days)).strftime("%Y-%m-%d")
        return {name for name, exp in drugs if exp <= cutoff_str}

    @staticmethod
    def expired_names(drugs, today_str):
        """Return set of drug names that should appear in expired(today_str)."""
        return {name for name, exp in drugs if exp < today_str}


# ============================================================
# MOCK HTTP SERVER
# ============================================================

class ExpiryHandler(BaseHTTPRequestHandler):
    store = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        today = params.get("today", [None])[0]

        if parsed.path == "/expiring":
            within = int(params.get("within_days", ["30"])[0])
            rows = ExpiryHandler.store.expiring(within_days=within, today=today)
        elif parsed.path == "/expired":
            rows = ExpiryHandler.store.expired(today=today)
        else:
            self.send_response(404)
            self.end_headers()
            return

        resp = json.dumps(rows).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19280):
    store = ExpiryStore(sqlite3.connect(":memory:", check_same_thread=False))
    ExpiryHandler.store = store
    server = ThreadingHTTPServer(("127.0.0.1", port), ExpiryHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

REF_TODAY = "2026-05-25"


class ExpiryTestResult:
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n        {self.detail}"
        return msg


def _store_with(drugs):
    conn = sqlite3.connect(":memory:")
    s = ExpiryStore(conn)
    s.seed(drugs)
    return s


def _offset_date(today_str, days):
    base = datetime.strptime(today_str, "%Y-%m-%d")
    return (base + timedelta(days=days)).strftime("%Y-%m-%d")


def run_all_scenarios(verbose=False):
    results = []

    def check(name, cond, detail=""):
        r = ExpiryTestResult(name, cond, detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    today = REF_TODAY

    # 1. Drug expiring today+30 IS in expiring(30) (inclusive cutoff)
    drug1 = [("DrugA", _offset_date(today, 30))]
    rows1 = _store_with(drug1).expiring(within_days=30, today=today)
    check("1. Drug expiring today+30 IS in expiring(30) (inclusive cutoff)",
          any(r[0] == "DrugA" for r in rows1), f"rows: {rows1}")

    # 2. Drug expiring today+31 is NOT in expiring(30)
    drug2 = [("DrugB", _offset_date(today, 31))]
    rows2 = _store_with(drug2).expiring(within_days=30, today=today)
    check("2. Drug expiring today+31 NOT in expiring(30)",
          not any(r[0] == "DrugB" for r in rows2), f"rows: {rows2}")

    # 3. Already-expired drug IS in expiring(30) (exp_date <= cutoff)
    drug3 = [("DrugC", _offset_date(today, -365))]
    rows3 = _store_with(drug3).expiring(within_days=30, today=today)
    check("3. Drug expired 1 year ago IS in expiring(30) (exp <= cutoff)",
          any(r[0] == "DrugC" for r in rows3), f"rows: {rows3}")

    # 4. Drug expiring TODAY is NOT in expired() (strict < today)
    drug4 = [("DrugD", today)]
    rows4 = _store_with(drug4).expired(today=today)
    check("4. Drug expiring TODAY is NOT in expired() (< strict)",
          not any(r[0] == "DrugD" for r in rows4), f"rows: {rows4}")

    # 5. Drug expiring yesterday IS in expired()
    drug5 = [("DrugE", _offset_date(today, -1))]
    rows5 = _store_with(drug5).expired(today=today)
    check("5. Drug expiring yesterday IS in expired()",
          any(r[0] == "DrugE" for r in rows5), f"rows: {rows5}")

    # 6. Leap day: exp_date=2024-02-29, today=2024-03-01 -> expired
    drug6 = [("LeapDrug", "2024-02-29")]
    rows6 = _store_with(drug6).expired(today="2024-03-01")
    check("6. Leap day 2024-02-29 appears as expired on 2024-03-01",
          any(r[0] == "LeapDrug" for r in rows6), f"rows: {rows6}")

    # 7. Month-end rollover: today=2026-01-31, within_days=1 -> cutoff=2026-02-01
    drug7 = [("FebDrug", "2026-02-01")]
    rows7 = _store_with(drug7).expiring(within_days=1, today="2026-01-31")
    check("7. Month-end rollover: today=2026-01-31+1=2026-02-01 included",
          any(r[0] == "FebDrug" for r in rows7), f"rows: {rows7}")

    # 8. Year-end rollover: today=2026-12-31, within_days=1 -> cutoff=2027-01-01
    drug8 = [("NewYearDrug", "2027-01-01")]
    rows8 = _store_with(drug8).expiring(within_days=1, today="2026-12-31")
    check("8. Year-end rollover: today=2026-12-31+1=2027-01-01 included",
          any(r[0] == "NewYearDrug" for r in rows8), f"rows: {rows8}")

    # 9. Sorting: ASC exp_date then drug_name
    drugs9 = [("Zoloft", "2026-06-01"), ("Aspirin", "2026-05-30"), ("Benadryl", "2026-05-30")]
    s9 = _store_with(drugs9)
    rows9 = s9.expiring(within_days=60, today=today)
    drug_names9 = [r[0] for r in rows9]
    check("9. Results sorted ASC by exp_date then drug_name",
          drug_names9 == ["Aspirin", "Benadryl", "Zoloft"],
          f"order: {drug_names9}")

    # 10. Empty result when all drugs far in future
    drugs10 = [("FarDrug", _offset_date(today, 365))]
    rows10 = _store_with(drugs10).expiring(within_days=30, today=today)
    check("10. Empty result when all drugs far in future",
          rows10 == [], f"rows: {rows10}")

    # 11. within_days=0: only drugs where exp_date <= today
    drugs11 = [("TodayDrug", today), ("YestDrug", _offset_date(today, -1)),
               ("TomorrowDrug", _offset_date(today, 1))]
    rows11 = _store_with(drugs11).expiring(within_days=0, today=today)
    names11 = {r[0] for r in rows11}
    check("11. within_days=0: only today and past drugs",
          "TodayDrug" in names11 and "YestDrug" in names11 and "TomorrowDrug" not in names11,
          f"names: {names11}")

    # 12. Large scan: 365-day includes 11-month drug, excludes 13-month drug
    drugs12 = [("In11Months", _offset_date(today, 330)),
               ("In13Months", _offset_date(today, 396))]
    rows12 = _store_with(drugs12).expiring(within_days=365, today=today)
    names12 = {r[0] for r in rows12}
    check("12. 365-day scan includes 11-month, excludes 13-month",
          "In11Months" in names12 and "In13Months" not in names12,
          f"names: {names12}")

    # 13. LIKE wildcard escape: drug with % in name retrieved correctly
    drugs13 = [("Test%Drug", today), ("Normal_Drug", today)]
    s13 = _store_with(drugs13)
    rows13 = s13.inventory_list(name_filter="Test%Drug")
    check("13. LIKE wildcard escape: 'Test%Drug' retrieved without wildcard expansion",
          len(rows13) == 1 and rows13[0][0] == "Test%Drug",
          f"rows: {rows13}")

    return results


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="expiry_window_test_harness",
        description="Inventory expiry window alerting harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all 13 scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only")
    p.add_argument("--port", type=int, default=19280,
                   help="Mock server port (default: 19280)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    import time as _time
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        server = start_mock_server(args.port)
        print(f"  Expiry window mock server on http://127.0.0.1:{args.port} — Ctrl+C to stop")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
            server.server_close()
        return

    if args.self_test:
        print("\n  EXPIRY WINDOW TEST HARNESS — self-test mode")
        print("  " + "=" * 52)
        results = run_all_scenarios(verbose=args.verbose)
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        if not args.verbose:
            for r in results:
                print(r)
        print()
        print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
        print()
        sys.exit(0 if failed == 0 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
