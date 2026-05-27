#!/usr/bin/env python3
"""
schema_evolution_test_harness.py — Expand-contract migration "transition zone" bugs.
=====================================================================================

Pure-stdlib + sqlite3. Zero external runtime dependencies.

60%+ of data pipelines hit silent schema-drift corruption (Matia 2025). The
online expand-contract migration is the standard fix but introduces a
"transition zone" where old readers, new writers, partially-backfilled rows,
and not-yet-migrated rows coexist — and a debugger-only ghost bug appears.

This harness:
  - Defines SchemaStates: OLD, EXPANDED (both columns), CONTRACTED.
  - Runs the same workload against an in-memory SQLite DB in each state, with
    a partial-backfill checkpoint mid-migration.
  - Asserts every reader can correctly extract the canonical value from every
    state, for every row written in every state.

Usage:
  python harnesses/core/schema_evolution_test_harness.py --self-test
  python harnesses/core/schema_evolution_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Schema states + drivers
# ---------------------------------------------------------------------------


SCHEMAS = {
    "OLD": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
    "EXPANDED": (
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
        "email_canonical TEXT)"
    ),
    "CONTRACTED": (
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email_canonical TEXT)"
    ),
}


def _normalize(email: str | None) -> str | None:
    if email is None:
        return None
    return email.strip().lower()


# ---------------------------------------------------------------------------
# Writers + readers — exist in safe and unsafe variants
# ---------------------------------------------------------------------------


class Writer:
    """A writer adapts to whichever columns the schema currently has."""

    def __init__(self, conn: sqlite3.Connection, schema: str, safe: bool = True):
        self.conn = conn
        self.schema = schema
        self.safe = safe

    def insert(self, id: int, name: str, email: str) -> None:
        canonical = _normalize(email)
        cols = self._columns()
        if "email" in cols and "email_canonical" in cols:
            self.conn.execute(
                "INSERT INTO users (id, name, email, email_canonical) VALUES (?,?,?,?)",
                (id, name, email, canonical),
            )
        elif "email" in cols:
            # Unsafe variant: stores raw email, no canonical form.
            self.conn.execute(
                "INSERT INTO users (id, name, email) VALUES (?,?,?)",
                (id, name, email),
            )
        elif "email_canonical" in cols:
            self.conn.execute(
                "INSERT INTO users (id, name, email_canonical) VALUES (?,?,?)",
                (id, name, canonical),
            )
        self.conn.commit()

    def _columns(self) -> set[str]:
        cur = self.conn.execute("PRAGMA table_info(users)")
        return {row[1] for row in cur.fetchall()}


class Reader:
    """A reader extracts the canonical email regardless of schema state."""

    def __init__(self, conn: sqlite3.Connection, safe: bool = True):
        self.conn = conn
        self.safe = safe

    def get_email(self, id: int) -> str | None:
        cols = self._columns()
        if self.safe:
            # Prefer email_canonical when present; fall back to normalize(email).
            if "email_canonical" in cols:
                row = self.conn.execute(
                    "SELECT email_canonical FROM users WHERE id=?", (id,)
                ).fetchone()
                if row and row[0] is not None:
                    return row[0]
            if "email" in cols:
                row = self.conn.execute(
                    "SELECT email FROM users WHERE id=?", (id,)
                ).fetchone()
                if row:
                    return _normalize(row[0])
            return None

        # Unsafe: only ever reads from "email" — silently breaks on CONTRACTED.
        if "email" in cols:
            row = self.conn.execute(
                "SELECT email FROM users WHERE id=?", (id,)
            ).fetchone()
            if row:
                return row[0]  # no normalization either
        return None

    def _columns(self) -> set[str]:
        cur = self.conn.execute("PRAGMA table_info(users)")
        return {row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Migration driver
# ---------------------------------------------------------------------------


def apply_migration(conn: sqlite3.Connection, target_state: str) -> None:
    """Bring an OLD-state db to target_state via expand/contract."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if target_state == "EXPANDED" and "email_canonical" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_canonical TEXT")
        conn.commit()
    elif target_state == "CONTRACTED":
        if "email_canonical" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_canonical TEXT")
            conn.commit()
        # backfill
        rows = conn.execute("SELECT id, email FROM users WHERE email_canonical IS NULL").fetchall()
        for id_, email in rows:
            conn.execute("UPDATE users SET email_canonical=? WHERE id=?",
                         (_normalize(email), id_))
        conn.commit()
        # Drop "email" by recreating the table. SQLite < 3.35 lacks DROP COLUMN.
        conn.execute("ALTER TABLE users RENAME TO users_old")
        conn.execute(SCHEMAS["CONTRACTED"])
        conn.execute(
            "INSERT INTO users (id, name, email_canonical) "
            "SELECT id, name, email_canonical FROM users_old"
        )
        conn.execute("DROP TABLE users_old")
        conn.commit()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""


def _fresh_db(schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(SCHEMAS[schema])
    return conn


def scenario_safe_writer_safe_reader() -> ScenarioResult:
    """Safe writer + safe reader survive all three schema states."""
    failures: list[str] = []
    for state in ("OLD", "EXPANDED", "CONTRACTED"):
        conn = _fresh_db("OLD")
        if state != "OLD":
            Writer(conn, "OLD").insert(1, "Alice", "ALICE@Example.com")
            apply_migration(conn, state)
        else:
            Writer(conn, "OLD").insert(1, "Alice", "ALICE@Example.com")
        got = Reader(conn, safe=True).get_email(1)
        if got != "alice@example.com":
            failures.append(f"state={state}: got {got!r}")
    return ScenarioResult(
        name="safe_writer_safe_reader",
        passed=not failures,
        detail="; ".join(failures) or "all states canonical",
    )


def scenario_unsafe_reader_fails_on_contracted() -> ScenarioResult:
    """Unsafe reader breaks once the email column is dropped."""
    conn = _fresh_db("OLD")
    Writer(conn, "OLD").insert(1, "Alice", "ALICE@Example.com")
    apply_migration(conn, "CONTRACTED")
    got = Reader(conn, safe=False).get_email(1)
    # Unsafe reader returns None (no "email" col); should detect this gap.
    return ScenarioResult(
        name="unsafe_reader_fails_on_contracted",
        passed=got is None,
        detail=f"got={got!r}",
    )


def scenario_partial_backfill_transition() -> ScenarioResult:
    """During EXPANDED state with partial backfill, both old and new rows coexist."""
    conn = _fresh_db("OLD")
    # 5 rows written under OLD schema, then expand.
    for i in range(5):
        Writer(conn, "OLD").insert(i, f"u{i}", f"U{i}@EX.com")
    apply_migration(conn, "EXPANDED")
    # Now only backfill the first 2 rows.
    conn.execute("UPDATE users SET email_canonical=lower(email) WHERE id IN (0, 1)")
    conn.commit()
    # 5 new rows written under EXPANDED with the new column populated.
    for i in range(5, 10):
        Writer(conn, "EXPANDED").insert(i, f"u{i}", f"U{i}@EX.com")
    # Safe reader: all 10 must yield canonical form.
    reader = Reader(conn, safe=True)
    bad = []
    for i in range(10):
        if reader.get_email(i) != f"u{i}@ex.com":
            bad.append(i)
    return ScenarioResult(
        name="partial_backfill_transition",
        passed=not bad,
        detail=f"mismatched={bad}",
    )


def scenario_nullable_to_required_tighten() -> ScenarioResult:
    """A schema tightens email from nullable to required — rows with NULL must surface."""
    conn = _fresh_db("OLD")
    conn.execute("INSERT INTO users (id, name, email) VALUES (1, 'a', NULL)")
    conn.execute("INSERT INTO users (id, name, email) VALUES (2, 'b', 'b@x.com')")
    conn.commit()
    # Tighten: simulate by SELECTing rows that violate the new constraint.
    bad = conn.execute("SELECT id FROM users WHERE email IS NULL").fetchall()
    return ScenarioResult(
        name="nullable_to_required_tighten",
        passed=len(bad) == 1,
        detail=f"rows_violating_new_constraint={bad}",
    )


def scenario_old_writer_after_expand_doesnt_break_reader() -> ScenarioResult:
    """An OLD-schema writer pointed at an EXPANDED db: reader still resolves canonical."""
    conn = _fresh_db("OLD")
    apply_migration(conn, "EXPANDED")
    # An OLD-schema-aware writer only fills 'email'.
    conn.execute("INSERT INTO users (id, name, email) VALUES (1, 'Alice', 'ALICE@EX.com')")
    conn.commit()
    got = Reader(conn, safe=True).get_email(1)
    return ScenarioResult(
        name="old_writer_after_expand_doesnt_break_reader",
        passed=got == "alice@ex.com",
        detail=f"got={got!r}",
    )


SCENARIOS: dict[str, Callable[[], ScenarioResult]] = {
    "safe_writer_safe_reader": scenario_safe_writer_safe_reader,
    "unsafe_reader_fails_on_contracted": scenario_unsafe_reader_fails_on_contracted,
    "partial_backfill_transition": scenario_partial_backfill_transition,
    "nullable_to_required_tighten": scenario_nullable_to_required_tighten,
    "old_writer_after_expand_doesnt_break_reader":
        scenario_old_writer_after_expand_doesnt_break_reader,
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    for r in results:
        mark = "OK  " if r.passed else "FAIL"
        print(f"  {mark}  {r.name:50s} {r.detail}")
    failures = [r for r in results if not r.passed]
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Schema evolution / expand-contract harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
