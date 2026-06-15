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
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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
# TEETH: a FROZEN corpus of (operation, input) -> expected final DB state,
# judged against a PURE in-process SQLite (:memory:) database.
#
# A DB-access harness only has teeth if it CATCHES a data-access layer that
# commits a genuine database defect. The correct access logic above (build SQL
# with bound `?` placeholders; commit only after every statement in a write
# path succeeds; roll back the whole unit of work on any error) is reused as the
# ORACLE. Each Mutant below is a faithful in-process model of a real-world bug:
#
#   * string-interpolated SQL (the classic injection hole) — concatenating an
#     attacker-controlled value into the statement text instead of binding it,
#     so a crafted `name` like `x'); DROP TABLE users;--` executes a second
#     statement and destroys the table;
#   * a write path that does NOT roll back when a later statement fails — the
#     debit lands but the credit raises, leaving money destroyed (partial write
#     / leaked open transaction);
#   * a write path that forgets to COMMIT — the row is invisible to a fresh
#     reader/connection and is lost when the connection is recycled.
#
# An impl is a callable
#     run(op: str, payload: dict) -> dict      # the observable final DB state
# that operates on its OWN fresh :memory: connection (zero shared state, no real
# network/disk, no threads, no clock-dependent behaviour, fully deterministic).
# prove() drives the impl across the frozen corpus and compares each observed
# final state to the corpus's LITERAL hand-computed expectation — it NEVER
# compares an impl's output to the oracle object, so the check is non-circular.
# prove(impl) is True iff any observed final state diverges (the bug is caught).
# ---------------------------------------------------------------------------

# A value chosen to be load-bearing: a classic stacked-statement SQL injection
# payload. A parameterized writer binds it verbatim as ordinary text (table intact,
# one row stored). A writer that interpolates it into the statement string breaks out
# of the string literal — `executescript` then either runs the trailing `DROP TABLE`
# or aborts on the now-malformed first statement; either way no clean row is stored,
# so the final state diverges from the oracle and the injection sink is caught.
_INJECTION_NAME = "Robert'); DROP TABLE users;--"


@dataclass(frozen=True)
class DbCase:
    """One frozen data-access case with a literal, hand-computed final state.

    `op` selects the operation under test; `payload` is its input. `expected`
    is the full observed final state the correct access layer MUST produce —
    every key here is asserted against the impl's output.
    """
    name: str
    op: str
    payload: Dict[str, Any]
    expected: Dict[str, Any]
    note: str = ""


# Cases chosen so the correct oracle reproduces every expectation AND at least
# one planted mutant gets each one wrong. All expectations are constants derived
# by hand from the data-access contract, never read back from the oracle.
DB_CORPUS: Tuple[DbCase, ...] = (
    # --- INSERT a benign user: stored verbatim, exactly one row, no surprises.
    DbCase(
        "insert_plain_user",
        op="insert_user",
        payload={"name": "Alice", "email": "alice@example.com"},
        expected={"users_table_exists": True, "user_count": 1,
                  "stored_name": "Alice"},
        note="a plain insert stores the name verbatim and leaves one row",
    ),
    # --- INSERT a SQL-injection payload as data: the parameterized oracle binds
    #     it as a literal string, so the users table SURVIVES with one row holding
    #     the payload verbatim. The interpolating mutant breaks out of the literal
    #     (stacked DROP, or an aborted malformed statement) and stores no clean row,
    #     so its final state diverges. THIS is the injection teeth case.
    DbCase(
        "insert_injection_payload_is_inert",
        op="insert_user",
        payload={"name": _INJECTION_NAME, "email": "evil@example.com"},
        expected={"users_table_exists": True, "user_count": 1,
                  "stored_name": _INJECTION_NAME},
        note="a bound `?` keeps an injection payload inert; the table must survive",
    ),
    # --- TRANSFER that fails midway: the whole unit of work rolls back, so the
    #     debited account is restored to its full balance (atomicity). The
    #     no-rollback mutant leaves the debit applied -> money destroyed.
    DbCase(
        "transfer_failure_rolls_back",
        op="transfer_failing",
        payload={"from_balance": 1000.0, "amount": 200.0},
        expected={"from_balance_after": 1000.0},
        note="a failed transfer must roll back the debit, not destroy money",
    ),
    # --- WRITE then read on a SEPARATE connection: a committed write is durable
    #     and visible to a fresh reader. The forgot-to-commit mutant leaves the
    #     row invisible to the new connection (and lost on recycle).
    DbCase(
        "write_is_committed_and_visible",
        op="write_then_read_fresh",
        payload={"name": "Durable", "email": "durable@example.com"},
        expected={"visible_to_fresh_reader": True, "fresh_count": 1},
        note="a write must be committed so a fresh connection can see it",
    ),
)


# --- ORACLE: correct data-access logic, mirroring the harness's own helpers ---

def _fresh_users_conn() -> sqlite3.Connection:
    """A private in-memory DB with the harness's users schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE users ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  name TEXT NOT NULL,"
        "  email TEXT NOT NULL UNIQUE,"
        "  age INTEGER)"
    )
    conn.commit()
    return conn


def _users_table_exists(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    )
    return cur.fetchone() is not None


def _user_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM users")
    return cur.fetchone()[0]


def _stored_name(conn: sqlite3.Connection) -> Optional[str]:
    cur = conn.execute("SELECT name FROM users ORDER BY id LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def oracle_run(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Correct data-access layer. Returns the observable final DB state.

    This is the behaviour MockDbHandler / TransactionTester implement, distilled
    to the load-bearing decisions: bind values with `?`, commit only after every
    write in the unit succeeds, and roll back the whole unit on any error.
    """
    if op == "insert_user":
        conn = _fresh_users_conn()
        try:
            # Correct: the value is BOUND, never concatenated into the SQL text.
            conn.execute(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                (payload["name"], payload["email"]),
            )
            conn.commit()
            return {
                "users_table_exists": _users_table_exists(conn),
                "user_count": _user_count(conn) if _users_table_exists(conn) else 0,
                "stored_name": _stored_name(conn) if _users_table_exists(conn) else None,
            }
        finally:
            conn.close()

    if op == "transfer_failing":
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE accounts (owner TEXT PRIMARY KEY, balance REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO accounts (owner, balance) VALUES ('src', ?)",
                (payload["from_balance"],),
            )
            conn.commit()
            try:
                # Debit succeeds, then the credit leg fails. Correct: roll the
                # WHOLE unit back so the debit is undone (atomicity).
                conn.execute(
                    "UPDATE accounts SET balance = balance - ? WHERE owner = 'src'",
                    (payload["amount"],),
                )
                raise ValueError("credit leg failed")
            except Exception:
                conn.rollback()
            cur = conn.execute("SELECT balance FROM accounts WHERE owner = 'src'")
            return {"from_balance_after": cur.fetchone()[0]}
        finally:
            conn.close()

    if op == "write_then_read_fresh":
        # A shared on-disk-in-name file:: shared-cache DB so a SECOND connection
        # observes only what the first one COMMITTED. Deterministic + private to
        # this call (unique name), no real disk file, no network, no threads.
        uri = f"file:teeth_{id(payload)}?mode=memory&cache=shared"
        keepalive = sqlite3.connect(uri, uri=True)  # holds the shared DB alive
        try:
            writer = sqlite3.connect(uri, uri=True)
            writer.execute(
                "CREATE TABLE users ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name TEXT NOT NULL, email TEXT NOT NULL)"
            )
            writer.commit()
            writer.execute(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                (payload["name"], payload["email"]),
            )
            writer.commit()  # Correct: commit so the write is durable + visible.
            writer.close()
            reader = sqlite3.connect(uri, uri=True)
            try:
                cur = reader.execute("SELECT COUNT(*) FROM users")
                count = cur.fetchone()[0]
                return {"visible_to_fresh_reader": count > 0, "fresh_count": count}
            finally:
                reader.close()
        finally:
            keepalive.close()

    raise ValueError(f"unknown op: {op!r}")


# --- Planted buggy twins (each models a genuine real-world DB defect) --------

def injection_run(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """BUG: builds the INSERT by STRING-INTERPOLATING the value into the SQL
    text instead of binding it — the textbook SQL-injection hole.

    With a benign name this looks fine, but a stacked-statement payload like
    ``Robert'); DROP TABLE users;--`` executes the trailing ``DROP TABLE`` and
    the users table is annihilated. (executescript is used because real
    injection sinks — string-built queries fed to a multi-statement executor —
    run every statement the attacker smuggled in.)
    """
    if op == "insert_user":
        conn = _fresh_users_conn()
        try:
            name = payload["name"]
            email = payload["email"]
            # BUG: attacker-controlled `name` concatenated straight into SQL.
            sql = (
                f"INSERT INTO users (name, email) "
                f"VALUES ('{name}', '{email}')"
            )
            try:
                conn.executescript(sql)
                conn.commit()
            except sqlite3.Error:
                # A malformed injected statement may error; the damage (DROP) has
                # often already executed by then. Report whatever state remains.
                pass
            exists = _users_table_exists(conn)
            return {
                "users_table_exists": exists,
                "user_count": _user_count(conn) if exists else 0,
                "stored_name": _stored_name(conn) if exists else None,
            }
        finally:
            conn.close()
    return oracle_run(op, payload)


def no_rollback_run(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """BUG: a write path that does NOT roll back when a later statement fails.

    The debit is applied, the credit leg raises, but the except-clause swallows
    the error WITHOUT rolling back (and even commits the partial state). The
    money debited is destroyed — a partial write / lost-update defect.
    """
    if op == "transfer_failing":
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE accounts (owner TEXT PRIMARY KEY, balance REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO accounts (owner, balance) VALUES ('src', ?)",
                (payload["from_balance"],),
            )
            conn.commit()
            try:
                conn.execute(
                    "UPDATE accounts SET balance = balance - ? WHERE owner = 'src'",
                    (payload["amount"],),
                )
                raise ValueError("credit leg failed")
            except Exception:
                conn.commit()  # BUG: commit the partial write instead of rollback
            cur = conn.execute("SELECT balance FROM accounts WHERE owner = 'src'")
            return {"from_balance_after": cur.fetchone()[0]}
        finally:
            conn.close()
    return oracle_run(op, payload)


def no_commit_run(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """BUG: a write path that forgets to COMMIT before the connection closes.

    The insert lands only in the writer's private uncommitted transaction; a
    fresh reader connection never sees it, and it is lost when the writer is
    recycled. A classic 'works in the same session, vanishes afterward' defect.
    """
    if op == "write_then_read_fresh":
        uri = f"file:teeth_{id(payload)}_nc?mode=memory&cache=shared"
        keepalive = sqlite3.connect(uri, uri=True)
        try:
            writer = sqlite3.connect(uri, uri=True)
            writer.execute(
                "CREATE TABLE users ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name TEXT NOT NULL, email TEXT NOT NULL)"
            )
            writer.commit()
            writer.execute(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                (payload["name"], payload["email"]),
            )
            # BUG: no commit() — the row stays in the writer's open transaction.
            writer.close()
            reader = sqlite3.connect(uri, uri=True)
            try:
                cur = reader.execute("SELECT COUNT(*) FROM users")
                count = cur.fetchone()[0]
                return {"visible_to_fresh_reader": count > 0, "fresh_count": count}
            finally:
                reader.close()
        finally:
            keepalive.close()
    return oracle_run(op, payload)


def prove(impl: Callable[[str, Dict[str, Any]], Dict[str, Any]]) -> bool:
    """True iff data-access ``impl`` MISHANDLES any frozen corpus case (i.e. the
    planted bug is caught): the observed final DB state diverges from the case's
    literal expectation, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    DB_CORPUS, never read from the oracle; each impl runs on its own fresh
    in-memory connection with no RNG, clock, real network, real disk, or
    threading. An impl that raises on a corpus case counts as caught.
    """
    for case in DB_CORPUS:
        try:
            observed = impl(case.op, dict(case.payload))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if not isinstance(observed, dict):
            return True
        for key, want in case.expected.items():
            if observed.get(key) != want:
                return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_run,
    mutants=(
        Mutant("string_interpolated_sql_injection", injection_run,
               "builds SQL by interpolating the value instead of binding it -> a "
               "stacked-statement payload DROPs the users table (SQL injection)"),
        Mutant("no_rollback_on_error", no_rollback_run,
               "write path swallows the error and commits the partial write "
               "instead of rolling back -> debited money is destroyed"),
        Mutant("forgot_to_commit", no_commit_run,
               "write path never commits -> the row is invisible to a fresh "
               "reader and lost when the connection is recycled"),
    ),
    corpus_size=len(DB_CORPUS),
    kind="oracle_swap",
    notes="data access must bind values (not interpolate), roll back the whole "
          "unit of work on error, and commit so writes are durable + visible",
)


def list_scenarios() -> List[str]:
    """Names of the frozen corpus cases (the teeth scenarios)."""
    return [c.name for c in DB_CORPUS]


# ---------------------------------------------------------------------------
# Self-test CLI
# ---------------------------------------------------------------------------

def self_test() -> int:
    """Run the legacy DbTestRunner self-test. Returns 0 on success."""
    runner = DbTestRunner()
    ok = runner.run_all()
    runner.print_report()
    return 0 if ok else 1


def _run_self_test(as_json: bool = False, *, legacy: bool = True) -> int:
    """Report-based self-test: fail loud, report structured findings, assert teeth.

    1. The correct oracle reproduces every frozen corpus expectation.
    2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    3. (optional) The legacy DbTestRunner suite over the real harness classes.
    """
    report = Report("core/db")

    # 1. The correct oracle agrees with every frozen corpus expectation.
    for case in DB_CORPUS:
        observed = oracle_run(case.op, dict(case.payload))
        for key, want in case.expected.items():
            report.add(f"oracle:{case.name}:{key}", want,
                       observed.get(key), detail=case.note)

    # 2. Teeth: the oracle is clean and every planted mutant IS caught.
    report.assert_teeth(TEETH)

    # 3. Legacy end-to-end suite over the real harness classes (opt-out for
    #    pure runs; uses threads/timing so it is excluded from the teeth proof).
    if legacy:
        runner = DbTestRunner()
        report.record("legacy_runner_all_pass", runner.run_all(),
                      detail="DbTestRunner.run_all() over the real harness classes")

    return report.emit(as_json=as_json)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Database Test Harness")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run internal self-tests and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable findings (implies --self-test)",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="list the frozen corpus case names",
    )
    parser.add_argument(
        "--no-legacy",
        action="store_true",
        help="skip the legacy DbTestRunner suite (teeth/oracle checks only)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0

    return _run_self_test(as_json=args.json, legacy=not args.no_legacy)


if __name__ == "__main__":
    sys.exit(main())
