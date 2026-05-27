"""test_auditlog_cap_test_harness.py — unittest suite for auditlog_cap_test_harness (41)."""

import os
import shutil
import sqlite3
import tempfile
import threading
import unittest

from harnesses.pharmacy.auditlog_cap_test_harness import (
    AuditLogStore,
    BuggyAuditLogStore,
    run_all_scenarios,
)


def _fresh(cap=10000):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return AuditLogStore(conn, cap=cap)


class TestAuditLogStore(unittest.TestCase):

    def test_single_write_count_is_1(self):
        s = _fresh()
        s.write("alice", "login")
        self.assertEqual(s.count(), 1)

    def test_read_returns_newest_first(self):
        s = _fresh()
        s.write("alice", "first")
        s.write("alice", "second")
        rows = s.read(limit=10)
        self.assertEqual(rows[0][2], "second")

    def test_cap_not_exceeded_at_n_minus_1(self):
        s = _fresh(cap=5)
        for i in range(4):
            s.write("u", f"a{i}")
        self.assertEqual(s.count(), 4)

    def test_cap_triggers_at_n_plus_1(self):
        s = _fresh(cap=5)
        for i in range(6):
            s.write("u", f"a{i}")
        self.assertEqual(s.count(), 5)

    def test_newest_retained_oldest_gone(self):
        s = _fresh(cap=5)
        for i in range(6):
            s.write("u", f"action_{i}")
        actions = {r[2] for r in s.read(limit=10)}
        self.assertNotIn("action_0", actions)
        self.assertIn("action_5", actions)

    def test_idempotent_under_3x_cap_inserts(self):
        s = _fresh(cap=5)
        for i in range(15):
            s.write("u", f"a{i}")
        self.assertEqual(s.count(), 5)

    def test_low_cap_3_prunes_at_4(self):
        s = _fresh(cap=3)
        for i in range(4):
            s.write("u", f"r{i}")
        self.assertEqual(s.count(), 3)

    def test_id_not_reset_after_prune(self):
        s = _fresh(cap=3)
        for i in range(3):
            s.write("u", f"r{i}")
        max_before = s.max_id()
        s.write("u", "trigger")
        max_after = s.max_id()
        self.assertGreater(max_after, max_before)

    def test_filter_works_after_prune(self):
        s = _fresh(cap=5)
        for i in range(6):
            s.write("u", f"generic_{i}")
        s.write("admin", "special_action")
        found = s.read(text_filter="special_action", limit=10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][2], "special_action")

    def test_buggy_store_exceeds_cap(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        bs = BuggyAuditLogStore(conn, cap=5)
        for i in range(6):
            bs.write("u", f"b{i}")
        self.assertGreater(bs.count(), 5)

    def test_read_limit_respected(self):
        s = _fresh(cap=100)
        for i in range(20):
            s.write("u", f"a{i}")
        rows = s.read(limit=5)
        self.assertLessEqual(len(rows), 5)


class TestExport(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="auditlog_ex_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_export_creates_file(self):
        s = _fresh(cap=100)
        s.write("alice", "login")
        path = os.path.join(self.tmpdir, "log.txt")
        s.export(path)
        self.assertTrue(os.path.exists(path))

    def test_export_header_starts_correct(self):
        s = _fresh(cap=100)
        s.write("alice", "login")
        path = os.path.join(self.tmpdir, "log.txt")
        s.export(path)
        with open(path) as fh:
            content = fh.read()
        self.assertTrue(content.startswith("Pharmacy Audit Log Export"))

    def test_export_contains_generated_line(self):
        s = _fresh(cap=100)
        s.write("alice", "login")
        path = os.path.join(self.tmpdir, "log.txt")
        s.export(path)
        with open(path) as fh:
            content = fh.read()
        self.assertIn("Generated:", content)

    def test_export_contains_separator(self):
        s = _fresh(cap=100)
        s.write("alice", "login")
        path = os.path.join(self.tmpdir, "log.txt")
        s.export(path)
        with open(path) as fh:
            content = fh.read()
        self.assertIn("---", content)

    def test_export_data_rows_match_count(self):
        s = _fresh(cap=10)
        for i in range(15):
            s.write("u", f"act_{i}")
        path = os.path.join(self.tmpdir, "full.txt")
        s.export(path)
        with open(path) as fh:
            lines = [l for l in fh.readlines() if l.startswith("202")]
        # 15 inserts with cap=10 -> 10 rows in DB -> export has 10 data rows
        self.assertEqual(len(lines), 10)


class TestConcurrency(unittest.TestCase):

    def test_concurrent_writes_no_exceptions(self):
        s = _fresh(cap=5)
        barrier = threading.Barrier(4)
        errors = []

        def writer(n):
            barrier.wait()
            try:
                for i in range(3):
                    s.write(f"t{n}", f"action_{i}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_concurrent_writes_count_does_not_exceed_cap(self):
        s = _fresh(cap=5)
        barrier = threading.Barrier(4)

        def writer(n):
            barrier.wait()
            for i in range(3):
                s.write(f"t{n}", f"a_{i}")

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(s.count(), 5)


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
