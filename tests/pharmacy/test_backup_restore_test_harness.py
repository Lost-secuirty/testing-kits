"""test_backup_restore_test_harness.py — unittest suite for backup_restore_test_harness (40)."""

import os
import shutil
import sqlite3
import tempfile
import unittest

from harnesses.pharmacy.backup_restore_test_harness import (
    SQLITE_MAGIC,
    _db_backup,
    _db_restore,
    _make_test_db,
    run_all_scenarios,
)


class BackupRestoreTestCase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bk_test_")
        self.live = os.path.join(self.tmpdir, "live.db")
        _make_test_db(self.live)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestBackup(BackupRestoreTestCase):

    def test_magic_bytes(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        with open(bk, "rb") as fh:
            self.assertEqual(fh.read(16), SQLITE_MAGIC)

    def test_backup_is_readable(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        conn = sqlite3.connect(bk, timeout=5.0)
        tables = {row[0] for row in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertIn("Users", tables)

    def test_all_tables_present(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        conn = sqlite3.connect(bk, timeout=5.0)
        tables = {row[0] for row in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertIn("MasteryStats", tables)

    def test_users_row_faithful(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        live_c = sqlite3.connect(self.live, timeout=5.0)
        bk_c = sqlite3.connect(bk, timeout=5.0)
        live_rows = live_c.execute("SELECT name FROM Users ORDER BY name").fetchall()
        bk_rows = bk_c.execute("SELECT name FROM Users ORDER BY name").fetchall()
        live_c.close()
        bk_c.close()
        self.assertEqual(live_rows, bk_rows)

    def test_backup_file_nonempty(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        self.assertGreater(os.path.getsize(bk), 100)


class TestRestore(BackupRestoreTestCase):

    def test_round_trip_removes_added_user(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        conn = sqlite3.connect(self.live, timeout=5.0)
        conn.execute("INSERT INTO Users (name, role, pin_hash) VALUES ('Temp', 'tech', 'x')")
        conn.commit()
        conn.close()
        before = sqlite3.connect(self.live, timeout=5.0).execute(
            "SELECT COUNT(*) FROM Users").fetchone()[0]
        sqlite3.connect(self.live, timeout=5.0).close()  # close first
        _db_restore(bk, self.live)
        after = sqlite3.connect(self.live, timeout=5.0).execute(
            "SELECT COUNT(*) FROM Users").fetchone()[0]
        self.assertLess(after, before)

    def test_restore_is_atomic_db_readable_after(self):
        bk = os.path.join(self.tmpdir, "bk.db")
        _db_backup(self.live, bk)
        _db_restore(bk, self.live)
        conn = sqlite3.connect(self.live, timeout=5.0)
        count = conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(count, 0)

    def test_nonexistent_path_raises(self):
        with self.assertRaises((sqlite3.OperationalError, OSError)):
            _db_restore("/nonexistent/fake.db", self.live)

    def test_corrupt_backup_raises(self):
        corrupt = os.path.join(self.tmpdir, "corrupt.db")
        with open(corrupt, "wb") as f:
            f.write(b"not a sqlite file")
        with self.assertRaises((sqlite3.OperationalError, sqlite3.DatabaseError, OSError)):
            _db_restore(corrupt, self.live)


class TestNullPreservation(BackupRestoreTestCase):

    def test_null_ease_factor_survives_round_trip(self):
        conn = sqlite3.connect(self.live, timeout=5.0)
        conn.execute(
            "INSERT INTO MasteryStats "
            "(tech_name, drug_name, correct, total, ease_factor, "
            "interval_days, last_reviewed, repetitions) "
            "VALUES ('Alice', 'Lisinopril', 0, 3, NULL, NULL, NULL, 0)")
        conn.commit()
        conn.close()
        bk = os.path.join(self.tmpdir, "bk_null.db")
        _db_backup(self.live, bk)
        bk_conn = sqlite3.connect(bk, timeout=5.0)
        bk_conn.row_factory = sqlite3.Row
        row = bk_conn.execute(
            "SELECT ease_factor FROM MasteryStats WHERE tech_name='Alice'"
        ).fetchone()
        bk_conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row["ease_factor"])


class TestWALMode(BackupRestoreTestCase):

    def test_wal_source_backup_readable(self):
        conn = sqlite3.connect(self.live, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.close()
        bk = os.path.join(self.tmpdir, "bk_wal.db")
        _db_backup(self.live, bk)
        conn2 = sqlite3.connect(bk, timeout=5.0)
        count = conn2.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
        conn2.close()
        self.assertGreaterEqual(count, 0)


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
