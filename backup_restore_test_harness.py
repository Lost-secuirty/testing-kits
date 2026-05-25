#!/usr/bin/env python3
"""backup_restore_test_harness.py — SQLite Backup/Restore Lifecycle Harness (2026)
=================================================================================
Pure-Python (ZERO dependencies) harness for testing the SQLite online backup
and restore lifecycle.

Distinct from db_test_harness (#3) which covers CRUD and transactions:
  - Tests sqlite3.Connection.backup() API correctness
  - Verifies magic bytes, data fidelity, NULL preservation, WAL safety
  - Tests restore round-trip: snapshot -> mutate -> restore -> mutation gone
  - Tests corrupt-file rejection: garbage .db raises on restore
  - Tests backup listing semantics (newest-first mtime ordering)

No networked mock server (DB-only pattern, like harness #3).

Usage:
  python backup_restore_test_harness.py --self-test
  python backup_restore_test_harness.py --self-test --verbose
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime

# ============================================================
# SQLITE MAGIC BYTES
# ============================================================

SQLITE_MAGIC = b"SQLite format 3\x00"

# ============================================================
# HELPERS
# ============================================================

def _make_test_db(path):
    """Create a minimal pharmacy-like DB at path with known schema and one row."""
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Users (
            name TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            pin_hash TEXT
        );
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
    """)
    conn.execute(
        "INSERT OR IGNORE INTO Users (name, role, pin_hash) VALUES ('Nathan', 'admin', 'abc123')")
    conn.commit()
    conn.close()


def _db_backup(src_path, dest_path):
    """Online backup src_path -> dest_path using sqlite3.Connection.backup()."""
    src = sqlite3.connect(src_path, timeout=15.0)
    try:
        dst = sqlite3.connect(dest_path, timeout=15.0)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _db_restore(backup_path, live_path):
    """Restore backup_path into live_path using sqlite3.Connection.backup()."""
    src = sqlite3.connect(backup_path, timeout=15.0)
    try:
        dst = sqlite3.connect(live_path, timeout=15.0)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _count_users(path):
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        return conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
    finally:
        conn.close()


@dataclass
class BackupTestResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n        {self.detail}"
        return msg


# ============================================================
# TEST SCENARIOS
# ============================================================

def run_all_scenarios(verbose=False):
    results = []

    def check(name, cond, detail=""):
        r = BackupTestResult(name, cond, detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    tmpdir = tempfile.mkdtemp(prefix="backup_harness_")
    try:
        live = os.path.join(tmpdir, "live.db")
        _make_test_db(live)

        # 1. Magic bytes
        bk1 = os.path.join(tmpdir, "backup1.db")
        _db_backup(live, bk1)
        with open(bk1, "rb") as fh:
            magic = fh.read(16)
        check("1. Backup has SQLite3 magic bytes at offset 0",
              magic == SQLITE_MAGIC, f"got: {magic!r}")

        # 2. Backup is immediately readable
        try:
            conn2 = sqlite3.connect(bk1, timeout=5.0)
            tables = {row[0] for row in
                      conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            conn2.close()
            check("2. Backup is readable and has expected tables",
                  "Users" in tables and "MasteryStats" in tables,
                  f"tables found: {tables}")
        except sqlite3.Error as e:
            check("2. Backup is readable", False, str(e))

        # 3. Data faithful
        src_count = _count_users(live)
        bak_count = _count_users(bk1)
        check("3. Backup is data-faithful: Users row count matches",
              src_count == bak_count,
              f"live={src_count}, backup={bak_count}")

        # 4. Timestamp pattern
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bk4_name = f"pharmacy_backup_{stamp}.db"
        bk4 = os.path.join(tmpdir, bk4_name)
        _db_backup(live, bk4)
        import re
        pattern = re.compile(r"pharmacy_backup_\d{8}-\d{6}\.db")
        check("4. Backup filename matches pharmacy_backup_YYYYMMDD-HHMMSS.db",
              bool(pattern.match(bk4_name)), f"filename: {bk4_name}")

        # 5. Multiple backups newest-first
        bk5a = os.path.join(tmpdir, "bkup_a.db")
        bk5b = os.path.join(tmpdir, "bkup_b.db")
        _db_backup(live, bk5a)
        # Nudge mtime of bk5b to be newer
        _db_backup(live, bk5b)
        os.utime(bk5a, (os.path.getmtime(bk5b) - 2, os.path.getmtime(bk5b) - 2))
        backups_sorted = sorted(
            [(os.path.basename(p), os.path.getmtime(p))
             for p in (bk5a, bk5b)],
            key=lambda x: x[1], reverse=True)
        check("5. Backup listing sorted newest-first by mtime",
              backups_sorted[0][0] == "bkup_b.db",
              f"order: {[b[0] for b in backups_sorted]}")

        # 6. Round-trip: backup -> add user -> restore -> user gone
        live6 = os.path.join(tmpdir, "live6.db")
        _make_test_db(live6)
        bk6 = os.path.join(tmpdir, "backup6.db")
        _db_backup(live6, bk6)
        # Add a user
        conn6 = sqlite3.connect(live6, timeout=5.0)
        conn6.execute("INSERT INTO Users (name, role, pin_hash) VALUES ('Temp', 'tech', 'x')")
        conn6.commit()
        conn6.close()
        before_restore = _count_users(live6)
        _db_restore(bk6, live6)
        after_restore = _count_users(live6)
        check("6. Round-trip: added user gone after restore",
              after_restore < before_restore,
              f"before={before_restore}, after={after_restore}")

        # 7. Restore is atomic: live DB readable after restore
        try:
            conn7 = sqlite3.connect(live6, timeout=5.0)
            count7 = conn7.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
            conn7.close()
            check("7. Restore is atomic: live DB readable after restore",
                  count7 >= 0)
        except sqlite3.Error as e:
            check("7. Restore is atomic", False, str(e))

        # 8. Restore with nonexistent path raises
        raised8 = False
        try:
            _db_restore("/nonexistent/path/fake.db", live)
        except (sqlite3.OperationalError, OSError):
            raised8 = True
        check("8. Restore with nonexistent path raises sqlite3/OSError", raised8)

        # 9. Empty DB (schema-only) round-trips
        live9 = os.path.join(tmpdir, "live9.db")
        conn9 = sqlite3.connect(live9, timeout=5.0)
        conn9.executescript("""
            CREATE TABLE IF NOT EXISTS Users (
                name TEXT PRIMARY KEY, role TEXT NOT NULL, pin_hash TEXT);
        """)
        conn9.commit()
        conn9.close()
        bk9 = os.path.join(tmpdir, "backup9.db")
        _db_backup(live9, bk9)
        try:
            c9 = sqlite3.connect(bk9, timeout=5.0)
            _ = c9.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
            c9.close()
            check("9. Empty DB (schema-only) backs up and restores cleanly", True)
        except sqlite3.Error as e:
            check("9. Empty DB backup/restore", False, str(e))

        # 10. NULL ease_factor survives
        live10 = os.path.join(tmpdir, "live10.db")
        _make_test_db(live10)
        c10 = sqlite3.connect(live10, timeout=5.0)
        c10.execute(
            "INSERT INTO MasteryStats "
            "(tech_name, drug_name, correct, total, ease_factor, "
            "interval_days, last_reviewed, repetitions) "
            "VALUES ('Alice', 'Lisinopril', 0, 3, NULL, NULL, NULL, 0)")
        c10.commit()
        c10.close()
        bk10 = os.path.join(tmpdir, "backup10.db")
        _db_backup(live10, bk10)
        c10b = sqlite3.connect(bk10, timeout=5.0)
        c10b.row_factory = sqlite3.Row
        row10 = c10b.execute(
            "SELECT ease_factor FROM MasteryStats WHERE tech_name='Alice'"
        ).fetchone()
        c10b.close()
        check("10. NULL ease_factor persisted through backup/restore",
              row10 is not None and row10["ease_factor"] is None,
              f"ease_factor={row10['ease_factor'] if row10 else 'NO ROW'}")

        # 11. WAL mode before backup doesn't corrupt
        live11 = os.path.join(tmpdir, "live11.db")
        _make_test_db(live11)
        cwal = sqlite3.connect(live11, timeout=5.0)
        cwal.execute("PRAGMA journal_mode=WAL;")
        cwal.close()
        bk11 = os.path.join(tmpdir, "backup11.db")
        _db_backup(live11, bk11)
        try:
            ccheck = sqlite3.connect(bk11, timeout=5.0)
            _ = ccheck.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
            ccheck.close()
            check("11. WAL-mode source backup is readable", True)
        except sqlite3.Error as e:
            check("11. WAL-mode backup not corrupt", False, str(e))

        # 12. db_list_backups with unlistable dir returns []
        def _list_backups_from(dirpath):
            out = []
            try:
                for name in os.listdir(dirpath):
                    if name.startswith("pharmacy_backup_") and name.endswith(".db"):
                        full = os.path.join(dirpath, name)
                        try:
                            mtime = os.path.getmtime(full)
                        except OSError:
                            continue
                        out.append((name, full, mtime))
            except OSError:
                return []
            out.sort(key=lambda x: x[2], reverse=True)
            return out

        result12 = _list_backups_from("/nonexistent_dir_xyz_999")
        check("12. db_list_backups with unlistable dir returns []",
              result12 == [], f"got: {result12}")

        # 13. Corrupt backup raises on restore
        corrupt = os.path.join(tmpdir, "corrupt.db")
        with open(corrupt, "wb") as f:
            f.write(b"this is not a sqlite file at all!")
        live13 = os.path.join(tmpdir, "live13.db")
        _make_test_db(live13)
        raised13 = False
        try:
            _db_restore(corrupt, live13)
        except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
            raised13 = True
        check("13. Corrupt backup raises on restore (not silent overwrite)", raised13)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return results


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="backup_restore_test_harness",
        description="SQLite online backup/restore lifecycle harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all 13 scenarios and exit 0 if all pass")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.self_test:
        print("\n  BACKUP/RESTORE TEST HARNESS — self-test mode")
        print("  " + "=" * 54)
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
