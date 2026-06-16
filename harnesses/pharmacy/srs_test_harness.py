#!/usr/bin/env python3
"""srs_test_harness.py — SM-2 Spaced-Repetition System Test Harness (2026)
=========================================================================
Pure-Python (ZERO dependencies) harness for validating the SM-2
spaced-repetition algorithm and its database persistence lifecycle.

Distinct from the generic property_test_harness (#11) because:
  - Provides a convergence oracle (20 correct cycles must reach interval>100)
  - Tests the DB persistence loop: sm2_update -> upsert -> retrieve -> re-apply
  - Tests calculate_weight overdue/not-yet-due/legacy paths
  - Uses seeded random.Random so failures are reproducible
  - Pure stdlib, no Hypothesis

Usage:
  python srs_test_harness.py --self-test
  python srs_test_harness.py --self-test --verbose
  python srs_test_harness.py --help
"""

import argparse
import math
import random
import sqlite3
import sys
import threading
from datetime import datetime, timedelta

# ============================================================
# CONSTANTS
# ============================================================

EASE_FLOOR = 1.3
EASE_INIT = 2.5
# Cap the interval so repeated-correct growth (interval *= ease each cycle)
# cannot run away to a float OverflowError after ~700+ reviews. 36500 days =
# 100 years, far beyond any real scheduling horizon and > the >100 convergence oracle.
INTERVAL_CAP = 36500

# ============================================================
# SM-2 INLINE IMPLEMENTATION
# ============================================================

def sm2_update(ease_factor, interval_days, repetitions, correct):
    """SM-2 binary correct/incorrect variant. Tolerates None/junk inputs.

    Algorithm (SuperMemo SM-2, Wozniak 1985, binary simplified):
      Correct:  reps 0->1; reps 1->6; reps>=2->round(prev*ease)
                reps+=1; ease+=0.1
      Incorrect: interval=0; reps=0; ease-=0.2
      Ease floored at 1.3.

    Returns (new_ease, new_interval, new_reps).
    """
    try:
        ease = float(ease_factor) if ease_factor is not None else EASE_INIT
        interval = int(interval_days) if interval_days is not None else 0
        reps = int(repetitions) if repetitions is not None else 0
    except (ValueError, TypeError):
        ease, interval, reps = EASE_INIT, 0, 0
    if not math.isfinite(ease):
        ease = EASE_INIT
    ease = max(EASE_FLOOR, ease)
    interval = max(0, interval)
    reps = max(0, reps)

    if correct:
        if reps == 0:
            new_interval = 1
        elif reps == 1:
            new_interval = 6
        else:
            # Guard against numeric overflow when interval*ease becomes infinite
            try:
                prod = interval * ease
                new_interval = int(10 ** 9) if not math.isfinite(prod) else int(round(prod))
            except OverflowError:
                new_interval = int(10**9)
        new_reps = reps + 1
        new_ease = ease + 0.1
    else:
        new_interval = 0
        new_reps = 0
        new_ease = ease - 0.2

    new_ease = max(EASE_FLOOR, new_ease)
    new_interval = min(new_interval, INTERVAL_CAP)
    return new_ease, new_interval, new_reps


def _weight_from_stats(stats, today_str=None):
    """Re-implement calculate_weight logic without a live DB connection.

    Mirrors the logic in pharmacy_app/logic.py calculate_weight():
      SM-2 path (last_reviewed populated):
        overdue_days = days_since - interval_days
        overdue >= 0: weight = min(50, 10 + overdue*2)
        not yet due:  weight = max(1,  10 + overdue)   [overdue<0]
      Legacy fallback:
        no stats: 10; missed>0: 10+5*missed; no misses: 1
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    if not stats or not stats.get("total") or stats.get("correct") is None:
        return 10
    if stats.get("last_reviewed"):
        try:
            last = datetime.fromisoformat(stats["last_reviewed"])
            today = datetime.strptime(today_str, "%Y-%m-%d")
            days_since = (today - last).days
            interval = int(stats.get("interval_days") or 0)
            overdue = days_since - interval
            if overdue >= 0:
                return min(50, 10 + overdue * 2)
            return max(1, 10 + overdue)
        except (ValueError, TypeError):
            pass
    missed = stats["total"] - stats["correct"]
    if missed > 0:
        return 10 + (missed * 5)
    return 1


# ============================================================
# MOCK MASTERY STORE (in-memory SQLite)
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS MasteryStats (
    tech_name TEXT,
    drug_name TEXT,
    correct INTEGER,
    total INTEGER,
    ease_factor REAL,
    interval_days INTEGER,
    last_reviewed TEXT,
    repetitions INTEGER,
    PRIMARY KEY (tech_name, drug_name)
);
"""


class MockMasteryStore:
    """Thread-safe in-memory SQLite store for MasteryStats."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._lock = threading.Lock()

    def upsert(self, tech, drug, correct, total, ease, interval, reps, last_reviewed):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO MasteryStats "
                "(tech_name, drug_name, correct, total, ease_factor, "
                "interval_days, last_reviewed, repetitions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tech, drug, correct, total, ease, interval, last_reviewed, reps),
            )
            self.conn.commit()

    def get(self, tech, drug):
        with self._lock:
            row = self.conn.execute(
                "SELECT total, correct, ease_factor, interval_days, "
                "last_reviewed, repetitions FROM MasteryStats "
                "WHERE tech_name=? AND drug_name=?",
                (tech, drug),
            ).fetchone()
        return dict(row) if row else None

    def close(self):
        self.conn.close()


# ============================================================
# SRS SIMULATOR (convergence testing)
# ============================================================

class SRSSimulator:
    """Run N review rounds and return interval history."""

    def __init__(self, seed=42):
        self.rng = random.Random(seed)

    def simulate(self, rounds, always_correct=True):
        ease, interval, reps = None, None, None
        history = []
        for _ in range(rounds):
            correct = True if always_correct else self.rng.random() > 0.3
            ease, interval, reps = sm2_update(ease, interval, reps, correct)
            history.append(interval)
        return history


# ============================================================
# TEST SCENARIOS
# ============================================================

class SRSTestResult:
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


def run_all_scenarios(verbose=False):
    results = []

    def check(name, cond, detail=""):
        r = SRSTestResult(name, cond, detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    # 1. First review from NULL
    ease, interval, reps = sm2_update(None, None, None, True)
    check(
        "1. First review from NULL gives ease=2.6, interval=1, reps=1",
        abs(ease - 2.6) < 1e-9 and interval == 1 and reps == 1,
        f"got ease={ease}, interval={interval}, reps={reps}",
    )

    # 2. Interval ladder
    e, i, r = sm2_update(None, None, None, True)
    check("2a. reps=0 correct -> interval=1", i == 1, f"got {i}")
    e, i, r = sm2_update(e, i, r, True)
    check("2b. reps=1 correct -> interval=6", i == 6, f"got {i}")
    e_prev, i_prev = e, i
    e, i, r = sm2_update(e, i, r, True)
    expected = int(round(i_prev * e_prev))
    check(
        "2c. reps=2 correct -> round(prev_interval*ease)",
        i == expected,
        f"got {i}, expected {expected}",
    )

    # 3. Incorrect resets
    e3, i3, r3 = sm2_update(2.5, 20, 5, False)
    check("3. Incorrect resets interval=0, reps=0",
          i3 == 0 and r3 == 0, f"got interval={i3}, reps={r3}")

    # 4. Ease floor at 1.3
    e4 = 2.5
    for _ in range(100):
        e4, _, _ = sm2_update(e4, 0, 0, False)
    check("4. Ease floor pinned at 1.3 after 100 incorrect",
          abs(e4 - 1.3) < 1e-9, f"got ease={e4}")

    # 5. Ease doesn't decrease on correct
    e5, i5, r5 = 2.5, 6, 2
    e5n, _, _ = sm2_update(e5, i5, r5, True)
    check("5. Ease doesn't decrease on correct", e5n >= e5,
          f"ease went from {e5} to {e5n}")

    # 6. Interval monotone for 5 rounds
    e6, i6, r6 = 2.5, 6, 2
    for step in range(5):
        e6n, i6n, r6n = sm2_update(e6, i6, r6, True)
        check(f"6. Interval non-decreasing (step={step}, {i6}->{i6n})",
              i6n >= i6, f"interval went from {i6} to {i6n}")
        e6, i6, r6 = e6n, i6n, r6n

    # 7. Convergence
    sim = SRSSimulator(seed=7)
    history = sim.simulate(20, always_correct=True)
    check("7. Convergence: interval > 100 within 20 correct cycles",
          any(h > 100 for h in history), f"max interval: {max(history)}")

    # 8. Ease upper bound
    e8, i8, r8 = 2.5, 6, 2
    for _ in range(1000):
        e8, i8, r8 = sm2_update(e8, i8, r8, True)
    check("8. Ease is finite and <= 105.0 after 1000 correct",
          math.isfinite(e8) and e8 <= 105.0, f"ease={e8}")

    # 9. Junk input
    try:
        ej, ij, rj = sm2_update("abc", "xyz", "!!!", True)
        check("9. Junk inputs return first-review defaults (no raise)",
              ij == 1 and rj == 1, f"got interval={ij}, reps={rj}")
    except Exception as ex:
        check("9. Junk inputs don't raise", False, str(ex))

    # 10. DB round-trip
    store = MockMasteryStore()
    tech, drug = "Alice", "Lisinopril"
    e10, i10, r10 = sm2_update(None, None, None, True)
    today = datetime.now().strftime("%Y-%m-%d")
    store.upsert(tech, drug, 1, 1, e10, i10, r10, today)
    row = store.get(tech, drug)
    check("10a. DB round-trip: ease persisted",
          row is not None and abs(row["ease_factor"] - e10) < 1e-9,
          f"stored={e10}, retrieved={row.get('ease_factor') if row else None}")
    check("10b. DB round-trip: interval persisted",
          row is not None and row["interval_days"] == i10,
          f"stored={i10}, retrieved={row.get('interval_days') if row else None}")
    check("10c. DB round-trip: repetitions persisted",
          row is not None and row["repetitions"] == r10,
          f"stored={r10}, retrieved={row.get('repetitions') if row else None}")
    store.close()

    # 11. Overdue path: reviewed 40 days ago, interval=1 -> overdue=39 -> min(50, 10+78)=50
    reviewed_40 = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    stats11 = {"correct": 3, "total": 5, "ease_factor": 2.5,
               "interval_days": 1, "last_reviewed": reviewed_40, "repetitions": 3}
    w11 = _weight_from_stats(stats11)
    check("11. calculate_weight overdue (40d ago, interval=1) -> 50",
          w11 == 50, f"got weight={w11}")

    # 12. Not-yet-due: reviewed today, interval=3 -> overdue=-3 -> max(1,10-3)=7
    stats12 = {"correct": 5, "total": 5, "ease_factor": 2.5,
               "interval_days": 3, "last_reviewed": today, "repetitions": 3}
    w12 = _weight_from_stats(stats12)
    check("12. calculate_weight not-yet-due (today, interval=3) -> 7",
          w12 == 7, f"got weight={w12}")

    # 13. Monotone with increasing days_since
    weights13 = []
    for days_ago in range(0, 51, 5):
        rev = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        s = {"correct": 3, "total": 5, "ease_factor": 2.5,
             "interval_days": 1, "last_reviewed": rev, "repetitions": 3}
        weights13.append(_weight_from_stats(s))
    monotone = all(weights13[i] <= weights13[i + 1] for i in range(len(weights13) - 1))
    check("13. calculate_weight non-decreasing as days_since increases",
          monotone, f"weights: {weights13}")

    # 14. Legacy fallback: NULL last_reviewed, correct=0, total=5 -> 10+5*5=35
    stats14 = {"correct": 0, "total": 5, "ease_factor": None,
               "interval_days": None, "last_reviewed": None, "repetitions": None}
    w14 = _weight_from_stats(stats14)
    check("14. Legacy fallback (NULL last_reviewed, missed=5) -> 35",
          w14 == 35, f"got weight={w14}")

    return results


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="srs_test_harness",
        description="SM-2 Spaced-Repetition System test harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all 14 SM-2 scenarios and exit 0 if all pass")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print per-scenario result as it runs")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.self_test:
        print("\n  SRS TEST HARNESS — self-test mode")
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
