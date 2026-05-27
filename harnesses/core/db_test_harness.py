"""
db_test_harness.py — Database Test Harness (Harness 3 of 36)

Tests database operations using an in-memory SQLite backend.
No networked mock server, no external dependencies — pure stdlib.

Components:
    ConnectionPool         — simple pool of sqlite3 connections with semaphore
    MockDbHandler          — SQLite-in-memory database wrapper
    MigrationChecker       — schema migration runner and idempotency checker
    TransactionTester      — transaction isolation and rollback testing
    ConnectionPoolMonitor  — monitors pool checkout/return behavior
    DbTestRunner           — orchestrates all test scenarios
"""

import sqlite3
import threading
import time
import queue
import logging
import argparse
import sys
import os
import traceback
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("db_test_harness")


# ---------------------------------------------------------------------------
# ConnectionPool
# ---------------------------------------------------------------------------

class ConnectionPoolExhausted(Exception):
    """Raised when no connection is available within the timeout."""


class ConnectionPool:
    """
    A simple pool of sqlite3 in-memory connections backed by a Semaphore.

    Because each :memory: database is isolated, the pool demonstrates
    checkout / return semantics without requiring a shared on-disk file.
    For tests that need a shared database, callers can pass a shared
    sqlite3.Connection as the *shared_conn* argument; the pool will then
    return the same connection to every caller (useful for integrity tests).
    """

    def __init__(
        self,
        max_connections: int = 5,
        timeout: float = 2.0,
        shared_conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        self.max_connections = max_connections
        self.timeout = timeout
        self._semaphore = threading.Semaphore(max_connections)
        self._lock = threading.Lock()
        self._pool: queue.Queue = queue.Queue()
        self._shared_conn = shared_conn
        self._all_connections: List[sqlite3.Connection] = []
        self._checked_out = 0
        self._total_checkouts = 0
        self._total_returns = 0

        # Pre-populate the pool
        for _ in range(max_connections):
            if shared_conn is not None:
                conn = shared_conn
            else:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
            self._pool.put(conn)
            if conn not in self._all_connections:
                self._all_connections.append(conn)

    def checkout(self) -> sqlite3.Connection:
        """Acquire a connection from the pool (blocking up to *timeout* seconds)."""
        acquired = self._semaphore.acquire(timeout=self.timeout)
        if not acquired:
            raise ConnectionPoolExhausted(
                f"All {self.max_connections} connections are checked out"
            )
        conn = self._pool.get_nowait()
        with self._lock:
            self._checked_out += 1
            self._total_checkouts += 1
        return conn

    def checkin(self, conn: sqlite3.Connection) -> None:
        """Return a connection to the pool."""
        self._pool.put(conn)
        with self._lock:
            self._checked_out -= 1
            self._total_returns += 1
        self._semaphore.release()

    @contextmanager
    def connection(self):
        """Context manager that checks out and returns a connection."""
        conn = self.checkout()
        try:
            yield conn
        finally:
            self.checkin(conn)

    @property
    def available(self) -> int:
        return self._pool.qsize()

    @property
    def checked_out_count(self) -> int:
        with self._lock:
            return self._checked_out

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "max_connections": self.max_connections,
                "checked_out": self._checked_out,
                "available": self._pool.qsize(),
                "total_checkouts": self._total_checkouts,
                "total_returns": self._total_returns,
            }

    def close_all(self) -> None:
        """Close all connections in the pool (drains the queue first)."""
        if self._shared_conn is not None:
            return  # don't close the shared connection
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break


# ---------------------------------------------------------------------------
# MockDbHandler  (SQLite-in-memory, no HTTP server)
# ---------------------------------------------------------------------------

class MockDbHandler:
    """
    SQLite in-memory database wrapper used as the backend for all harness tests.

    Provides:
        - schema creation / teardown
        - CRUD helpers
        - transaction context manager
        - query execution with timing
    """

    SCHEMA_V1 = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT    NOT NULL,
            email    TEXT    NOT NULL UNIQUE,
            age      INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product    TEXT    NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """,
    ]

    def __init__(self, conn: Optional[sqlite3.Connection] = None) -> None:
        if conn is not None:
            self._conn = conn
            self._owns_conn = False
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._owns_conn = True
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    def apply_schema(self) -> None:
        for stmt in self.SCHEMA_V1:
            self._conn.execute(stmt)
        self._conn.commit()

    def drop_schema(self) -> None:
        self._conn.execute("DROP TABLE IF EXISTS orders")
        self._conn.execute("DROP TABLE IF EXISTS users")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Timed query execution
    # ------------------------------------------------------------------

    def execute(
        self, sql: str, params: Tuple = ()
    ) -> Tuple[sqlite3.Cursor, float]:
        """Execute *sql* and return ``(cursor, elapsed_seconds)``."""
        start = time.perf_counter()
        cur = self._conn.execute(sql, params)
        elapsed = time.perf_counter() - start
        return cur, elapsed

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def insert_user(self, name: str, email: str, age: Optional[int] = None) -> int:
        cur, _ = self.execute(
            "INSERT INTO users (name, email, age) VALUES (?, ?, ?)",
            (name, email, age),
        )
        self.commit()
        return cur.lastrowid

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        cur, _ = self.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()

    def update_user(self, user_id: int, **fields) -> int:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        cur, _ = self.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", tuple(values)
        )
        self.commit()
        return cur.rowcount

    def delete_user(self, user_id: int) -> int:
        cur, _ = self.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.commit()
        return cur.rowcount

    def insert_order(
        self, user_id: int, product: str, quantity: int = 1
    ) -> int:
        cur, _ = self.execute(
            "INSERT INTO orders (user_id, product, quantity) VALUES (?, ?, ?)",
            (user_id, product, quantity),
        )
        self.commit()
        return cur.lastrowid

    def get_orders_for_user(self, user_id: int) -> List[sqlite3.Row]:
        cur, _ = self.execute(
            "SELECT * FROM orders WHERE user_id = ?", (user_id,)
        )
        return cur.fetchall()

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self):
        """
        Yields the raw connection inside a transaction.
        Commits on clean exit; rolls back on exception.
        """
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def table_names(self) -> List[str]:
        cur, _ = self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row[0] for row in cur.fetchall()]

    def row_count(self, table: str) -> int:
        cur, _ = self.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]

    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()


# ---------------------------------------------------------------------------
# MigrationChecker
# ---------------------------------------------------------------------------

MIGRATIONS: List[Dict[str, Any]] = [
    {
        "version": 1,
        "description": "Create users table",
        "sql": """
            CREATE TABLE IF NOT EXISTS users (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT    NOT NULL,
                email TEXT    NOT NULL UNIQUE,
                age   INTEGER
            )
        """,
    },
    {
        "version": 2,
        "description": "Create orders table",
        "sql": """
            CREATE TABLE IF NOT EXISTS orders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                product    TEXT    NOT NULL,
                quantity   INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """,
    },
    {
        "version": 3,
        "description": "Add status column to orders",
        "sql": "ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
    },
    {
        "version": 4,
        "description": "Create schema_migrations tracking table",
        "sql": """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                description TEXT,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """,
    },
]


class MigrationChecker:
    """
    Applies schema migrations in order and verifies idempotency.

    Uses a *schema_migrations* table (created as part of migration #4) to
    track which migrations have been applied.  When run a second time the
    already-applied migrations are skipped.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------
    # Bootstrap: ensure the tracking table exists before applying anything
    # ------------------------------------------------------------------

    def _ensure_tracking_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                description TEXT,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.commit()

    def applied_versions(self) -> List[int]:
        try:
            cur = self._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
            return [row[0] for row in cur.fetchall()]
        except sqlite3.OperationalError:
            return []

    def apply_all(self, migrations: List[Dict[str, Any]] = None) -> List[int]:
        """
        Apply all pending migrations in version order.
        Returns list of newly applied version numbers.
        """
        if migrations is None:
            migrations = MIGRATIONS
        self._ensure_tracking_table()
        applied = set(self.applied_versions())
        newly_applied = []
        for mig in sorted(migrations, key=lambda m: m["version"]):
            v = mig["version"]
            if v in applied:
                logger.debug("Migration %d already applied — skipping", v)
                continue
            logger.info("Applying migration %d: %s", v, mig["description"])
            self._conn.execute(mig["sql"])
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
                (v, mig["description"]),
            )
            self._conn.commit()
            newly_applied.append(v)
        return newly_applied

    def verify_idempotent(self, migrations: List[Dict[str, Any]] = None) -> bool:
        """Run apply_all twice; second run must return no newly applied migrations."""
        if migrations is None:
            migrations = MIGRATIONS
        first_run = self.apply_all(migrations)
        second_run = self.apply_all(migrations)
        return len(second_run) == 0

    def current_version(self) -> int:
        versions = self.applied_versions()
        return max(versions) if versions else 0


# ---------------------------------------------------------------------------
# TransactionTester
# ---------------------------------------------------------------------------

class TransactionTester:
    """
    Tests transaction isolation: rollback on error, visibility of uncommitted
    data, and atomicity of multi-statement transactions.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._setup()

    def _setup(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id      INTEGER PRIMARY KEY,
                owner   TEXT    NOT NULL UNIQUE,
                balance REAL    NOT NULL DEFAULT 0.0
            )
            """
        )
        self._conn.commit()

    def seed(self, owner: str, balance: float) -> int:
        cur = self._conn.execute(
            "INSERT INTO accounts (owner, balance) VALUES (?, ?)", (owner, balance)
        )
        self._conn.commit()
        return cur.lastrowid

    def get_balance(self, owner: str) -> Optional[float]:
        cur = self._conn.execute(
            "SELECT balance FROM accounts WHERE owner = ?", (owner,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def transfer(self, from_owner: str, to_owner: str, amount: float) -> bool:
        """
        Transfer *amount* between two accounts atomically.
        Returns True on success, False if rolled back.
        """
        try:
            self._conn.execute(
                "UPDATE accounts SET balance = balance - ? WHERE owner = ?",
                (amount, from_owner),
            )
            # Simulate an error when transferring to "bad_account"
            if to_owner == "bad_account":
                raise ValueError("Simulated transfer failure")
            self._conn.execute(
                "UPDATE accounts SET balance = balance + ? WHERE owner = ?",
                (amount, to_owner),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def test_rollback_on_error(self) -> Dict[str, Any]:
        """Return a dict describing rollback test results."""
        alice_id = self.seed("alice_txn", 1000.0)
        bob_id = self.seed("bob_txn", 500.0)

        success = self.transfer("alice_txn", "bad_account", 200.0)
        alice_balance = self.get_balance("alice_txn")
        result = {
            "transfer_succeeded": success,
            "alice_balance_after": alice_balance,
            "rollback_worked": alice_balance == 1000.0,
        }
        return result

    def test_successful_transfer(self) -> Dict[str, Any]:
        self.seed("carol_txn", 800.0)
        self.seed("dave_txn", 300.0)
        success = self.transfer("carol_txn", "dave_txn", 150.0)
        return {
            "transfer_succeeded": success,
            "carol_balance": self.get_balance("carol_txn"),
            "dave_balance": self.get_balance("dave_txn"),
        }


# ---------------------------------------------------------------------------
# ConnectionPoolMonitor
# ---------------------------------------------------------------------------

class ConnectionPoolMonitor:
    """
    Observes ConnectionPool behavior: records checkout times, peak usage,
    exhaustion events, and per-thread latency.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._peak_checked_out = 0
        self._exhaustion_count = 0

    def _record(self, event_type: str, **kwargs) -> None:
        with self._lock:
            entry = {"type": event_type, "ts": time.perf_counter(), **kwargs}
            self._events.append(entry)

    def checkout_with_monitoring(self) -> sqlite3.Connection:
        t0 = time.perf_counter()
        try:
            conn = self._pool.checkout()
            latency = time.perf_counter() - t0
            checked_out = self._pool.checked_out_count
            with self._lock:
                if checked_out > self._peak_checked_out:
                    self._peak_checked_out = checked_out
            self._record("checkout", latency=latency, thread=threading.current_thread().name)
            return conn
        except ConnectionPoolExhausted:
            with self._lock:
                self._exhaustion_count += 1
            self._record("exhausted", thread=threading.current_thread().name)
            raise

    def checkin_with_monitoring(self, conn: sqlite3.Connection) -> None:
        self._pool.checkin(conn)
        self._record("checkin", thread=threading.current_thread().name)

    @property
    def peak_checked_out(self) -> int:
        with self._lock:
            return self._peak_checked_out

    @property
    def exhaustion_count(self) -> int:
        with self._lock:
            return self._exhaustion_count

    def summary(self) -> Dict[str, Any]:
        stats = self._pool.stats()
        with self._lock:
            return {
                **stats,
                "peak_checked_out": self._peak_checked_out,
                "exhaustion_events": self._exhaustion_count,
                "total_events": len(self._events),
            }


# ---------------------------------------------------------------------------
# ConcurrentWriteTester
# ---------------------------------------------------------------------------

class ConcurrentWriteTester:
    """
    Spawns N threads, each inserting M rows into a shared SQLite database.
    Verifies final row count = N * M (no lost writes, no corruption).
    """

    def __init__(self, n_threads: int = 8, rows_per_thread: int = 25) -> None:
        self.n_threads = n_threads
        self.rows_per_thread = rows_per_thread
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE concurrent_writes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                seq       INTEGER NOT NULL,
                value     TEXT    NOT NULL
            )
            """
        )
        self._conn.commit()
        self._write_lock = threading.Lock()
        self._errors: List[str] = []

    def _worker(self, thread_id: int) -> None:
        for seq in range(self.rows_per_thread):
            try:
                with self._write_lock:
                    self._conn.execute(
                        "INSERT INTO concurrent_writes (thread_id, seq, value) VALUES (?, ?, ?)",
                        (thread_id, seq, f"t{thread_id}_s{seq}"),
                    )
                    self._conn.commit()
            except Exception as exc:
                self._errors.append(str(exc))

    def run(self) -> Dict[str, Any]:
        threads = [
            threading.Thread(target=self._worker, args=(i,), daemon=True)
            for i in range(self.n_threads)
        ]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.perf_counter() - t0

        cur = self._conn.execute("SELECT COUNT(*) FROM concurrent_writes")
        final_count = cur.fetchone()[0]
        expected = self.n_threads * self.rows_per_thread

        return {
            "expected_rows": expected,
            "actual_rows": final_count,
            "errors": self._errors,
            "elapsed_seconds": elapsed,
            "data_integrity_ok": final_count == expected and not self._errors,
        }

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# QueryPerformanceTracker
# ---------------------------------------------------------------------------

class QueryPerformanceTracker:
    """
    Wraps a MockDbHandler and records execution time for every query.
    """

    def __init__(self, handler: MockDbHandler) -> None:
        self._handler = handler
        self._timings: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def execute(self, sql: str, params: Tuple = ()) -> sqlite3.Cursor:
        cur, elapsed = self._handler.execute(sql, params)
        with self._lock:
            self._timings.append({"sql": sql, "elapsed": elapsed, "params": params})
        return cur

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            if not self._timings:
                return {"count": 0, "total": 0.0, "mean": 0.0, "max": 0.0, "min": 0.0}
            times = [t["elapsed"] for t in self._timings]
            return {
                "count": len(times),
                "total": sum(times),
                "mean": sum(times) / len(times),
                "max": max(times),
                "min": min(times),
            }

    def slowest(self, n: int = 3) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted(self._timings, key=lambda t: t["elapsed"], reverse=True)[:n]


# ---------------------------------------------------------------------------
# DbTestRunner  — orchestrates all sub-components
# ---------------------------------------------------------------------------

class DbTestResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.passed: List[str] = []
        self.failed: List[str] = []
        self.errors: List[str] = []

    def record(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed.append(label)
        else:
            msg = f"{label}" + (f": {detail}" if detail else "")
            self.failed.append(msg)

    def add_error(self, label: str, exc: Exception) -> None:
        self.errors.append(f"{label}: {exc}")

    @property
    def ok(self) -> bool:
        return not self.failed and not self.errors

    def summary(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return (
            f"[{status}] {self.name} — "
            f"{len(self.passed)} passed, {len(self.failed)} failed, "
            f"{len(self.errors)} errors"
        )


class DbTestRunner:
    """
    Orchestrates all database harness test scenarios.
    """

    def __init__(self) -> None:
        self._results: List[DbTestResult] = []

    # ------------------------------------------------------------------
    # Individual test groups
    # ------------------------------------------------------------------

    def test_crud(self) -> DbTestResult:
        res = DbTestResult("CRUD")
        db = MockDbHandler()
        try:
            db.apply_schema()

            # INSERT
            uid = db.insert_user("Alice", "alice@example.com", 30)
            res.record("insert returns id > 0", uid > 0)

            # SELECT
            row = db.get_user(uid)
            res.record("select returns row", row is not None)
            res.record("name matches", row["name"] == "Alice")
            res.record("email matches", row["email"] == "alice@example.com")
            res.record("age matches", row["age"] == 30)

            # UPDATE
            n = db.update_user(uid, name="Alicia", age=31)
            res.record("update rowcount == 1", n == 1)
            row = db.get_user(uid)
            res.record("updated name", row["name"] == "Alicia")
            res.record("updated age", row["age"] == 31)

            # DELETE
            n = db.delete_user(uid)
            res.record("delete rowcount == 1", n == 1)
            row = db.get_user(uid)
            res.record("row gone after delete", row is None)

        except Exception as exc:
            res.add_error("crud_exception", exc)
        finally:
            db.close()
        return res

    def test_transaction_isolation(self) -> DbTestResult:
        res = DbTestResult("TransactionIsolation")
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        tester = TransactionTester(conn)
        try:
            rollback = tester.test_rollback_on_error()
            res.record("rollback on error works", rollback["rollback_worked"])
            res.record("failed transfer returns False", not rollback["transfer_succeeded"])

            success = tester.test_successful_transfer()
            res.record("successful transfer returns True", success["transfer_succeeded"])
            res.record("carol debited", success["carol_balance"] == 650.0)
            res.record("dave credited", success["dave_balance"] == 450.0)

        except Exception as exc:
            res.add_error("transaction_exception", exc)
        finally:
            conn.close()
        return res

    def test_constraints(self) -> DbTestResult:
        res = DbTestResult("Constraints")
        db = MockDbHandler()
        try:
            db.apply_schema()

            # NOT NULL constraint
            not_null_raised = False
            try:
                db._conn.execute("INSERT INTO users (name, email) VALUES (NULL, 'x@x.com')")
                db._conn.commit()
            except sqlite3.IntegrityError:
                not_null_raised = True
                db._conn.rollback()
            res.record("NOT NULL raises IntegrityError", not_null_raised)

            # UNIQUE constraint
            uid = db.insert_user("Bob", "bob@example.com")
            unique_raised = False
            try:
                db._conn.execute(
                    "INSERT INTO users (name, email) VALUES ('Bob2', 'bob@example.com')"
                )
                db._conn.commit()
            except sqlite3.IntegrityError:
                unique_raised = True
                db._conn.rollback()
            res.record("UNIQUE raises IntegrityError", unique_raised)

            # FOREIGN KEY constraint
            fk_raised = False
            try:
                db._conn.execute(
                    "INSERT INTO orders (user_id, product) VALUES (99999, 'Widget')"
                )
                db._conn.commit()
            except sqlite3.IntegrityError:
                fk_raised = True
                db._conn.rollback()
            res.record("FOREIGN KEY raises IntegrityError", fk_raised)

            # CASCADE DELETE
            uid2 = db.insert_user("Charlie", "charlie@example.com")
            db.insert_order(uid2, "Gadget", 2)
            orders_before = len(db.get_orders_for_user(uid2))
            res.record("order inserted before cascade test", orders_before == 1)
            db.delete_user(uid2)
            orders_after = len(db.get_orders_for_user(uid2))
            res.record("cascade delete removes child rows", orders_after == 0)

        except Exception as exc:
            res.add_error("constraint_exception", exc)
        finally:
            db.close()
        return res

    def test_migrations(self) -> DbTestResult:
        res = DbTestResult("Migrations")
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            checker = MigrationChecker(conn)

            first_run = checker.apply_all()
            res.record("first run applies migrations", len(first_run) > 0)
            res.record("versions applied in order", first_run == sorted(first_run))

            current_v = checker.current_version()
            res.record("current version is max migration", current_v == max(m["version"] for m in MIGRATIONS))

            second_run = checker.apply_all()
            res.record("second run is idempotent (no new migrations)", len(second_run) == 0)

            applied = checker.applied_versions()
            res.record("all versions recorded", set(applied) == {m["version"] for m in MIGRATIONS})

        except Exception as exc:
            res.add_error("migration_exception", exc)
        finally:
            conn.close()
        return res

    def test_connection_pool(self) -> DbTestResult:
        res = DbTestResult("ConnectionPool")
        pool = ConnectionPool(max_connections=3, timeout=0.5)
        monitor = ConnectionPoolMonitor(pool)
        try:
            # Normal checkout/return cycle
            conn = monitor.checkout_with_monitoring()
            res.record("checkout succeeds", conn is not None)
            res.record("available decreases after checkout", pool.available == 2)
            monitor.checkin_with_monitoring(conn)
            res.record("available restored after checkin", pool.available == 3)

            # Exhaust the pool
            conns = [monitor.checkout_with_monitoring() for _ in range(3)]
            res.record("can checkout max_connections", len(conns) == 3)
            res.record("available == 0 when exhausted", pool.available == 0)

            exhausted = False
            try:
                monitor.checkout_with_monitoring()
            except ConnectionPoolExhausted:
                exhausted = True
            res.record("exhaustion raises ConnectionPoolExhausted", exhausted)
            res.record("exhaustion_count incremented", monitor.exhaustion_count == 1)

            for c in conns:
                monitor.checkin_with_monitoring(c)
            res.record("all connections returned", pool.available == 3)

            summary = monitor.summary()
            res.record("peak_checked_out == max", summary["peak_checked_out"] == 3)

        except Exception as exc:
            res.add_error("pool_exception", exc)
        finally:
            pool.close_all()
        return res

    def test_query_performance(self) -> DbTestResult:
        res = DbTestResult("QueryPerformance")
        db = MockDbHandler()
        tracker = QueryPerformanceTracker(db)
        try:
            db.apply_schema()
            # Insert rows via tracker
            for i in range(20):
                tracker.execute(
                    "INSERT INTO users (name, email, age) VALUES (?, ?, ?)",
                    (f"User{i}", f"user{i}@example.com", 20 + i),
                )
            db.commit()

            tracker.execute("SELECT * FROM users")

            stats = tracker.stats()
            res.record("count == 21 (20 inserts + 1 select)", stats["count"] == 21)
            res.record("total time > 0", stats["total"] > 0)
            res.record("mean time > 0", stats["mean"] > 0)
            res.record("max >= mean", stats["max"] >= stats["mean"])
            res.record("min <= mean", stats["min"] <= stats["mean"])

            slowest = tracker.slowest(3)
            res.record("slowest returns up to 3 entries", len(slowest) <= 3)
            res.record("slowest sorted descending", slowest[0]["elapsed"] >= slowest[-1]["elapsed"])

        except Exception as exc:
            res.add_error("perf_exception", exc)
        finally:
            db.close()
        return res

    def test_concurrent_writes(self) -> DbTestResult:
        res = DbTestResult("ConcurrentWrites")
        tester = ConcurrentWriteTester(n_threads=8, rows_per_thread=25)
        try:
            result = tester.run()
            res.record("no write errors", not result["errors"])
            res.record(
                "all rows written (data integrity)",
                result["data_integrity_ok"],
                f"expected={result['expected_rows']} actual={result['actual_rows']}",
            )
            res.record("elapsed < 30s", result["elapsed_seconds"] < 30)
        except Exception as exc:
            res.add_error("concurrent_write_exception", exc)
        finally:
            tester.close()
        return res

    # ------------------------------------------------------------------
    # Run all
    # ------------------------------------------------------------------

    def run_all(self) -> bool:
        test_methods = [
            self.test_crud,
            self.test_transaction_isolation,
            self.test_constraints,
            self.test_migrations,
            self.test_connection_pool,
            self.test_query_performance,
            self.test_concurrent_writes,
        ]
        all_ok = True
        for method in test_methods:
            result = method()
            self._results.append(result)
            logger.info(result.summary())
            if not result.ok:
                all_ok = False
                for f in result.failed:
                    logger.warning("  FAIL: %s", f)
                for e in result.errors:
                    logger.error("  ERROR: %s", e)
        return all_ok

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("DATABASE TEST HARNESS — REPORT")
        print("=" * 60)
        total_passed = sum(len(r.passed) for r in self._results)
        total_failed = sum(len(r.failed) for r in self._results)
        total_errors = sum(len(r.errors) for r in self._results)
        for r in self._results:
            status = "PASS" if r.ok else "FAIL"
            print(f"  [{status}] {r.name}: {len(r.passed)}P / {len(r.failed)}F / {len(r.errors)}E")
        print("-" * 60)
        print(f"  TOTAL: {total_passed} passed, {total_failed} failed, {total_errors} errors")
        print("=" * 60)


# ---------------------------------------------------------------------------
# Self-test CLI
# ---------------------------------------------------------------------------

def self_test() -> int:
    """Run the harness in self-test mode. Returns 0 on success."""
    runner = DbTestRunner()
    ok = runner.run_all()
    runner.print_report()
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Database Test Harness")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run internal self-tests and exit",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.self_test:
        return self_test()

    # Default: run self-test
    return self_test()


if __name__ == "__main__":
    sys.exit(main())
