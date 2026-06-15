"""
test_db_test_harness.py — unittest suite for db_test_harness.py
Harness 3 of 36 — Database Test Harness

37 tests covering:
  - CRUD correctness
  - Transaction rollback on exception
  - NOT NULL / UNIQUE / FOREIGN KEY constraint enforcement
  - Migration ordering and idempotency
  - Connection pool checkout/return and exhaustion
  - Query performance measurement
  - Concurrent write integrity
  - Edge cases (empty tables, double-return, large batches, etc.)
"""

import sqlite3
import threading
import time
import unittest

from harnesses._teeth import verify
from harnesses.core.db_test_harness import (
    MIGRATIONS,
    TEETH,
    ConnectionPool,
    ConnectionPoolExhausted,
    ConnectionPoolMonitor,
    ConcurrentWriteTester,
    DbTestRunner,
    MigrationChecker,
    MockDbHandler,
    QueryPerformanceTracker,
    TransactionTester,
    oracle_run,
    prove,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def fresh_db() -> MockDbHandler:
    db = MockDbHandler()
    db.apply_schema()
    return db


# ---------------------------------------------------------------------------
# 1. CRUD tests (8 tests)
# ---------------------------------------------------------------------------

class TestCRUD(unittest.TestCase):

    def setUp(self):
        self.db = fresh_db()

    def tearDown(self):
        self.db.close()

    def test_insert_returns_positive_id(self):
        uid = self.db.insert_user("Alice", "alice@test.com", 25)
        self.assertGreater(uid, 0)

    def test_select_returns_correct_row(self):
        uid = self.db.insert_user("Bob", "bob@test.com", 40)
        row = self.db.get_user(uid)
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Bob")
        self.assertEqual(row["email"], "bob@test.com")
        self.assertEqual(row["age"], 40)

    def test_select_nonexistent_row_returns_none(self):
        row = self.db.get_user(999999)
        self.assertIsNone(row)

    def test_update_changes_fields(self):
        uid = self.db.insert_user("Carol", "carol@test.com", 30)
        rowcount = self.db.update_user(uid, name="Caroline", age=31)
        self.assertEqual(rowcount, 1)
        row = self.db.get_user(uid)
        self.assertEqual(row["name"], "Caroline")
        self.assertEqual(row["age"], 31)

    def test_update_nonexistent_row_returns_zero(self):
        rowcount = self.db.update_user(999999, name="Ghost")
        self.assertEqual(rowcount, 0)

    def test_delete_removes_row(self):
        uid = self.db.insert_user("Dave", "dave@test.com")
        rowcount = self.db.delete_user(uid)
        self.assertEqual(rowcount, 1)
        self.assertIsNone(self.db.get_user(uid))

    def test_delete_nonexistent_row_returns_zero(self):
        rowcount = self.db.delete_user(999999)
        self.assertEqual(rowcount, 0)

    def test_multiple_users_stored_independently(self):
        id1 = self.db.insert_user("Eve", "eve@test.com", 22)
        id2 = self.db.insert_user("Frank", "frank@test.com", 33)
        self.assertNotEqual(id1, id2)
        row1 = self.db.get_user(id1)
        row2 = self.db.get_user(id2)
        self.assertEqual(row1["name"], "Eve")
        self.assertEqual(row2["name"], "Frank")


# ---------------------------------------------------------------------------
# 2. Orders / foreign key CRUD (3 tests)
# ---------------------------------------------------------------------------

class TestOrderCRUD(unittest.TestCase):

    def setUp(self):
        self.db = fresh_db()

    def tearDown(self):
        self.db.close()

    def test_insert_order_returns_positive_id(self):
        uid = self.db.insert_user("Grace", "grace@test.com")
        oid = self.db.insert_order(uid, "Widget", 3)
        self.assertGreater(oid, 0)

    def test_get_orders_for_user(self):
        uid = self.db.insert_user("Hank", "hank@test.com")
        self.db.insert_order(uid, "Gadget", 1)
        self.db.insert_order(uid, "Doohickey", 2)
        orders = self.db.get_orders_for_user(uid)
        self.assertEqual(len(orders), 2)

    def test_get_orders_for_user_with_no_orders(self):
        uid = self.db.insert_user("Iris", "iris@test.com")
        orders = self.db.get_orders_for_user(uid)
        self.assertEqual(orders, [])


# ---------------------------------------------------------------------------
# 3. Transaction / rollback tests (5 tests)
# ---------------------------------------------------------------------------

class TestTransactions(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.tester = TransactionTester(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_rollback_on_error_restores_balance(self):
        self.tester.seed("alice_rb", 1000.0)
        result = self.tester.transfer("alice_rb", "bad_account", 200.0)
        self.assertFalse(result)
        self.assertEqual(self.tester.get_balance("alice_rb"), 1000.0)

    def test_successful_transfer_debits_and_credits(self):
        self.tester.seed("sender", 500.0)
        self.tester.seed("receiver", 100.0)
        result = self.tester.transfer("sender", "receiver", 150.0)
        self.assertTrue(result)
        self.assertEqual(self.tester.get_balance("sender"), 350.0)
        self.assertEqual(self.tester.get_balance("receiver"), 250.0)

    def test_transaction_context_manager_commits_on_success(self):
        db = fresh_db()
        try:
            with db.transaction():
                db._conn.execute(
                    "INSERT INTO users (name, email) VALUES ('Txn', 'txn@test.com')"
                )
            count = db.row_count("users")
            self.assertEqual(count, 1)
        finally:
            db.close()

    def test_transaction_context_manager_rolls_back_on_exception(self):
        db = fresh_db()
        try:
            with self.assertRaises(ValueError):
                with db.transaction():
                    db._conn.execute(
                        "INSERT INTO users (name, email) VALUES ('RbTxn', 'rbtxn@test.com')"
                    )
                    raise ValueError("forced error")
            count = db.row_count("users")
            self.assertEqual(count, 0)
        finally:
            db.close()

    def test_transfer_zero_amount_succeeds(self):
        self.tester.seed("zero_src", 100.0)
        self.tester.seed("zero_dst", 50.0)
        result = self.tester.transfer("zero_src", "zero_dst", 0.0)
        self.assertTrue(result)
        self.assertEqual(self.tester.get_balance("zero_src"), 100.0)
        self.assertEqual(self.tester.get_balance("zero_dst"), 50.0)


# ---------------------------------------------------------------------------
# 4. Constraint enforcement (4 tests)
# ---------------------------------------------------------------------------

class TestConstraints(unittest.TestCase):

    def setUp(self):
        self.db = fresh_db()

    def tearDown(self):
        self.db.close()

    def test_not_null_raises_integrity_error(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db._conn.execute(
                "INSERT INTO users (name, email) VALUES (NULL, 'nn@test.com')"
            )

    def test_unique_constraint_raises_integrity_error(self):
        self.db.insert_user("Uniq", "uniq@test.com")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db._conn.execute(
                "INSERT INTO users (name, email) VALUES ('Uniq2', 'uniq@test.com')"
            )

    def test_foreign_key_raises_integrity_error(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db._conn.execute(
                "INSERT INTO orders (user_id, product) VALUES (99999, 'Ghost')"
            )

    def test_cascade_delete_removes_child_rows(self):
        uid = self.db.insert_user("Cascade", "cascade@test.com")
        self.db.insert_order(uid, "Item", 1)
        self.assertEqual(len(self.db.get_orders_for_user(uid)), 1)
        self.db.delete_user(uid)
        self.assertEqual(len(self.db.get_orders_for_user(uid)), 0)


# ---------------------------------------------------------------------------
# 5. Migration tests (5 tests)
# ---------------------------------------------------------------------------

class TestMigrations(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.checker = MigrationChecker(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_first_run_applies_all_migrations(self):
        applied = self.checker.apply_all()
        self.assertEqual(len(applied), len(MIGRATIONS))

    def test_migrations_applied_in_ascending_version_order(self):
        applied = self.checker.apply_all()
        self.assertEqual(applied, sorted(applied))

    def test_second_run_is_idempotent(self):
        self.checker.apply_all()
        second = self.checker.apply_all()
        self.assertEqual(second, [])

    def test_current_version_matches_max_migration(self):
        self.checker.apply_all()
        expected = max(m["version"] for m in MIGRATIONS)
        self.assertEqual(self.checker.current_version(), expected)

    def test_applied_versions_all_recorded(self):
        self.checker.apply_all()
        applied = self.checker.applied_versions()
        expected = {m["version"] for m in MIGRATIONS}
        self.assertEqual(set(applied), expected)


# ---------------------------------------------------------------------------
# 6. Connection pool tests (6 tests)
# ---------------------------------------------------------------------------

class TestConnectionPool(unittest.TestCase):

    def test_checkout_and_checkin_cycle(self):
        pool = ConnectionPool(max_connections=2, timeout=1.0)
        try:
            conn = pool.checkout()
            self.assertIsNotNone(conn)
            self.assertEqual(pool.available, 1)
            pool.checkin(conn)
            self.assertEqual(pool.available, 2)
        finally:
            pool.close_all()

    def test_max_connections_respected(self):
        pool = ConnectionPool(max_connections=2, timeout=0.3)
        try:
            c1 = pool.checkout()
            c2 = pool.checkout()
            with self.assertRaises(ConnectionPoolExhausted):
                pool.checkout()
            pool.checkin(c1)
            pool.checkin(c2)
        finally:
            pool.close_all()

    def test_pool_context_manager(self):
        pool = ConnectionPool(max_connections=2, timeout=1.0)
        try:
            with pool.connection() as conn:
                self.assertIsNotNone(conn)
                self.assertEqual(pool.available, 1)
            self.assertEqual(pool.available, 2)
        finally:
            pool.close_all()

    def test_stats_accuracy(self):
        pool = ConnectionPool(max_connections=3, timeout=1.0)
        try:
            c1 = pool.checkout()
            c2 = pool.checkout()
            stats = pool.stats()
            self.assertEqual(stats["checked_out"], 2)
            self.assertEqual(stats["available"], 1)
            pool.checkin(c1)
            pool.checkin(c2)
            stats2 = pool.stats()
            self.assertEqual(stats2["total_checkouts"], 2)
            self.assertEqual(stats2["total_returns"], 2)
        finally:
            pool.close_all()

    def test_monitor_peak_tracking(self):
        pool = ConnectionPool(max_connections=3, timeout=1.0)
        monitor = ConnectionPoolMonitor(pool)
        try:
            conns = [monitor.checkout_with_monitoring() for _ in range(3)]
            self.assertEqual(monitor.peak_checked_out, 3)
            for c in conns:
                monitor.checkin_with_monitoring(c)
        finally:
            pool.close_all()

    def test_monitor_exhaustion_count(self):
        pool = ConnectionPool(max_connections=1, timeout=0.2)
        monitor = ConnectionPoolMonitor(pool)
        try:
            c = monitor.checkout_with_monitoring()
            try:
                monitor.checkout_with_monitoring()
            except ConnectionPoolExhausted:
                pass
            try:
                monitor.checkout_with_monitoring()
            except ConnectionPoolExhausted:
                pass
            self.assertEqual(monitor.exhaustion_count, 2)
            monitor.checkin_with_monitoring(c)
        finally:
            pool.close_all()


# ---------------------------------------------------------------------------
# 7. Query performance tests (3 tests)
# ---------------------------------------------------------------------------

class TestQueryPerformance(unittest.TestCase):

    def setUp(self):
        self.db = fresh_db()
        self.tracker = QueryPerformanceTracker(self.db)

    def tearDown(self):
        self.db.close()

    def test_timing_recorded_for_each_query(self):
        for i in range(5):
            self.tracker.execute(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                (f"U{i}", f"u{i}@perf.com"),
            )
        self.db.commit()
        stats = self.tracker.stats()
        self.assertEqual(stats["count"], 5)

    def test_elapsed_time_is_positive(self):
        self.tracker.execute("SELECT 1")
        stats = self.tracker.stats()
        self.assertGreater(stats["total"], 0)

    def test_slowest_returns_sorted_descending(self):
        for i in range(10):
            self.tracker.execute(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                (f"P{i}", f"p{i}@slow.com"),
            )
        self.db.commit()
        slowest = self.tracker.slowest(3)
        self.assertLessEqual(len(slowest), 3)
        if len(slowest) > 1:
            self.assertGreaterEqual(slowest[0]["elapsed"], slowest[-1]["elapsed"])


# ---------------------------------------------------------------------------
# 8. Concurrent writes (2 tests)
# ---------------------------------------------------------------------------

class TestConcurrentWrites(unittest.TestCase):

    def test_concurrent_writes_no_data_corruption(self):
        tester = ConcurrentWriteTester(n_threads=6, rows_per_thread=20)
        result = tester.run()
        tester.close()
        self.assertTrue(result["data_integrity_ok"])
        self.assertEqual(result["actual_rows"], 120)

    def test_concurrent_writes_no_errors(self):
        tester = ConcurrentWriteTester(n_threads=4, rows_per_thread=10)
        result = tester.run()
        tester.close()
        self.assertEqual(result["errors"], [])


# ---------------------------------------------------------------------------
# 9. MockDbHandler edge cases (3 tests)
# ---------------------------------------------------------------------------

class TestMockDbHandlerEdgeCases(unittest.TestCase):

    def test_table_names_after_schema_applied(self):
        db = MockDbHandler()
        db.apply_schema()
        names = db.table_names()
        self.assertIn("users", names)
        self.assertIn("orders", names)
        db.close()

    def test_row_count_empty_table_returns_zero(self):
        db = MockDbHandler()
        db.apply_schema()
        self.assertEqual(db.row_count("users"), 0)
        db.close()

    def test_drop_schema_removes_tables(self):
        db = MockDbHandler()
        db.apply_schema()
        db.drop_schema()
        names = db.table_names()
        self.assertNotIn("users", names)
        self.assertNotIn("orders", names)
        db.close()


# ---------------------------------------------------------------------------
# 10. DbTestRunner integration (1 test)
# ---------------------------------------------------------------------------

class TestDbTestRunnerIntegration(unittest.TestCase):

    def test_run_all_passes(self):
        runner = DbTestRunner()
        ok = runner.run_all()
        self.assertTrue(ok, "DbTestRunner.run_all() should return True when all tests pass")


# ---------------------------------------------------------------------------
# 11. Teeth: the harness must catch a real planted database bug.
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct data-access layer must NOT be flagged by prove.
        self.assertFalse(prove(oracle_run))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
