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
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


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


def _run_scenarios(verbose: bool = False) -> List[ScenarioResult]:
    """Run the in-process expand-contract scenarios (kept for legacy callers)."""
    return [fn() for fn in SCENARIOS.values()]


# ===========================================================================
# TEETH: a FROZEN corpus of (old_schema, new_schema) -> compatible | breaking.
#
# A schema-evolution harness only has teeth if it CATCHES a migration that
# silently breaks consumers. The scenarios above exercise the *runtime* of an
# expand/contract migration; the teeth below add the *static* compatibility
# verdict that the scenarios assume but never check on their own. The ORACLE is
# a correct backward-compatibility checker; each Mutant is a faithful model of a
# real-world checker bug that MISSES a genuinely breaking migration:
#
#   * a checker that ignores DROPPED columns (the migration that bites every
#     team running an old reader against a contracted table);
#   * a checker that ignores TYPE NARROWING (TEXT -> INTEGER silently truncates /
#     errors on existing data);
#   * a checker that treats a new NON-defaulted REQUIRED column as additive
#     (old writers omit it -> NOT NULL violation on insert).
#
# A schema here is a frozen mapping  field_name -> Field(type, required, default).
# prove(impl) judges `impl` against the corpus's FROZEN literal verdicts, never by
# comparing impl to the oracle object at runtime, so the check is non-circular.
# It is fully deterministic: no RNG, clock, network, filesystem, or thread timing.
# prove(impl) is True iff `impl`'s verdict differs from the frozen expected verdict
# on any case (i.e. the planted bug is CAUGHT).
# ===========================================================================


@dataclass(frozen=True)
class Field:
    """One column in a relational schema, as a compatibility checker sees it."""
    type: str                      # "TEXT" | "INTEGER" | "REAL" | "BLOB"
    required: bool = False         # NOT NULL with no usable default
    default: Optional[Any] = None  # server-side default, if any

    @property
    def has_default(self) -> bool:
        return self.default is not None


# A schema is an ordered mapping of field name -> Field.
SchemaT = Dict[str, Field]

# Type-widening lattice: a value of `key` can be safely read as any type in its
# set. Narrowing the other direction (e.g. TEXT -> INTEGER) is a breaking change.
_WIDENS_TO: Dict[str, set] = {
    "INTEGER": {"INTEGER", "REAL", "TEXT"},  # int promotes to real / text safely
    "REAL": {"REAL", "TEXT"},                # real promotes to text safely
    "TEXT": {"TEXT"},                        # text only stays text
    "BLOB": {"BLOB"},                        # blob only stays blob
}


def _is_widening(old_type: str, new_type: str) -> bool:
    """True iff existing `old_type` data is still valid under `new_type`."""
    if old_type == new_type:
        return True
    return new_type in _WIDENS_TO.get(old_type, {old_type})


@dataclass(frozen=True)
class MigrationCase:
    """One frozen migration with its hand-computed compatibility verdict."""
    name: str
    old: SchemaT
    new: SchemaT
    expected_breaking: bool        # True == this migration breaks a consumer
    note: str = ""


# Cases chosen so the correct oracle agrees with every verdict AND at least one
# planted mutant gets each breaking case WRONG (calls it compatible). All verdicts
# are computed by hand from the compatibility contract, never read from the oracle.
MIGRATION_CORPUS: Tuple[MigrationCase, ...] = (
    # --- ADDITIVE: a new OPTIONAL column is backward-compatible ---------------
    MigrationCase(
        "add_optional_column",
        {"id": Field("INTEGER", required=True), "name": Field("TEXT")},
        {"id": Field("INTEGER", required=True), "name": Field("TEXT"),
         "nickname": Field("TEXT")},
        expected_breaking=False,
        note="adding a nullable column is the canonical safe additive change",
    ),
    # --- ADDITIVE: a new REQUIRED column WITH a default is compatible ----------
    MigrationCase(
        "add_required_with_default",
        {"id": Field("INTEGER", required=True)},
        {"id": Field("INTEGER", required=True),
         "status": Field("TEXT", required=True, default="active")},
        expected_breaking=False,
        note="a NOT NULL column with a server default does not break old writers",
    ),
    # --- WIDENING: INTEGER -> TEXT keeps existing data readable ---------------
    MigrationCase(
        "widen_int_to_text",
        {"id": Field("INTEGER", required=True), "zip": Field("INTEGER")},
        {"id": Field("INTEGER", required=True), "zip": Field("TEXT")},
        expected_breaking=False,
        note="widening INTEGER->TEXT is non-destructive (every int renders as text)",
    ),
    # --- BREAKING: dropping a column breaks every reader of that column -------
    # Teeth case for the drop-blind mutant.
    MigrationCase(
        "drop_column",
        {"id": Field("INTEGER", required=True), "email": Field("TEXT"),
         "phone": Field("TEXT")},
        {"id": Field("INTEGER", required=True), "email": Field("TEXT")},
        expected_breaking=True,
        note="dropping `phone` breaks any consumer that still selects it",
    ),
    # --- BREAKING: narrowing TEXT -> INTEGER corrupts non-numeric rows --------
    # Teeth case for the narrow-blind mutant.
    MigrationCase(
        "narrow_text_to_int",
        {"id": Field("INTEGER", required=True), "code": Field("TEXT")},
        {"id": Field("INTEGER", required=True), "code": Field("INTEGER")},
        expected_breaking=True,
        note="narrowing TEXT->INTEGER errors/truncates existing non-numeric data",
    ),
    # --- BREAKING: a new REQUIRED column with NO default rejects old writers --
    # Teeth case for the required-as-additive mutant.
    MigrationCase(
        "add_required_no_default",
        {"id": Field("INTEGER", required=True)},
        {"id": Field("INTEGER", required=True),
         "tenant_id": Field("INTEGER", required=True)},
        expected_breaking=True,
        note="a NOT NULL column with no default fails inserts from old writers",
    ),
    # --- BREAKING: a rename surfaces as drop(old)+add(new) -> drop is breaking -
    MigrationCase(
        "rename_column",
        {"id": Field("INTEGER", required=True), "email": Field("TEXT")},
        {"id": Field("INTEGER", required=True), "email_canonical": Field("TEXT")},
        expected_breaking=True,
        note="a rename drops `email`; readers of the old name break",
    ),
)


# --- ORACLE: a correct backward-compatibility checker -----------------------

def is_breaking(old: SchemaT, new: SchemaT) -> bool:
    """Return True iff migrating from `old` to `new` breaks a consumer.

    Correct contract (the reference verdict the corpus is hand-computed against):
      * dropping any field present in `old` but absent in `new` is BREAKING;
      * narrowing a retained field's type (not a widening) is BREAKING;
      * adding a required field with no usable default is BREAKING;
      * everything else (new optional fields, new required-with-default fields,
        widening type changes) is compatible.
    """
    # 1. Dropped (or renamed-away) columns break readers of the old name.
    for name in old:
        if name not in new:
            return True
    # 2. Retained columns: a non-widening type change corrupts existing data.
    for name, old_field in old.items():
        new_field = new.get(name)
        if new_field is None:
            continue
        if not _is_widening(old_field.type, new_field.type):
            return True
    # 3. New columns: a required column with no default rejects old writers.
    for name, new_field in new.items():
        if name in old:
            continue
        if new_field.required and not new_field.has_default:
            return True
    return False


# --- Planted buggy twins (each MISSES a genuinely breaking migration) -------

def drop_blind_checker(old: SchemaT, new: SchemaT) -> bool:
    """BUG: only checks new columns + type changes; never notices a DROPPED field.

    Models the extremely common compatibility check that diffs the *new* schema
    against the old ("is everything I now require already present?") but forgets
    the reverse direction, so removing or renaming a column sails through as
    compatible while every reader of that column silently breaks.
    """
    for name, old_field in old.items():
        new_field = new.get(name)
        if new_field is None:
            continue  # BUG: a dropped column is simply skipped, not flagged.
        if not _is_widening(old_field.type, new_field.type):
            return True
    for name, new_field in new.items():
        if name in old:
            continue
        if new_field.required and not new_field.has_default:
            return True
    return False


def narrow_blind_checker(old: SchemaT, new: SchemaT) -> bool:
    """BUG: treats ANY type change as compatible (presence-only check).

    Models a checker that compares only the *set of column names* and required
    flags, never the types — so narrowing TEXT->INTEGER (which truncates or errors
    on existing non-numeric rows) is reported compatible. Data is silently
    corrupted on the next read/migrate.
    """
    for name in old:
        if name not in new:
            return True
    # BUG: the type-narrowing loop is missing entirely.
    for name, new_field in new.items():
        if name in old:
            continue
        if new_field.required and not new_field.has_default:
            return True
    return False


def required_additive_checker(old: SchemaT, new: SchemaT) -> bool:
    """BUG: treats every NEW column as additive, ignoring required/default.

    Models the classic "new columns are always safe" assumption. Adding a NOT NULL
    column with no default actually rejects every insert from an old writer
    (NOT NULL constraint violation), but this checker never inspects the new
    column's `required`/`default`, so it green-lights the breaking migration.
    """
    for name in old:
        if name not in new:
            return True
    for name, old_field in old.items():
        new_field = new.get(name)
        if new_field is None:
            continue
        if not _is_widening(old_field.type, new_field.type):
            return True
    # BUG: new columns are assumed safe regardless of required/default.
    return False


def prove(impl: Callable[[SchemaT, SchemaT], bool]) -> bool:
    """True iff ``impl``'s verdict differs from the frozen expected verdict on any
    corpus case (i.e. the planted compatibility bug is CAUGHT).

    Non-circular + deterministic: every verdict is a literal baked into
    MIGRATION_CORPUS, never read from the oracle object; there is no RNG, clock,
    network, filesystem, or thread timing. An impl that raises on a corpus case
    counts as caught.
    """
    for case in MIGRATION_CORPUS:
        try:
            verdict = bool(impl(case.old, case.new))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.expected_breaking:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=is_breaking,
    mutants=(
        Mutant("drop_blind", drop_blind_checker,
               "checker diffs new-vs-old only; misses a DROPPED/renamed column -> "
               "readers of the removed column silently break"),
        Mutant("narrow_blind", narrow_blind_checker,
               "presence-only checker ignores type changes; misses TEXT->INTEGER "
               "narrowing that truncates/errors on existing data"),
        Mutant("required_additive", required_additive_checker,
               "assumes every new column is additive; misses a NOT NULL column with "
               "no default that rejects inserts from old writers"),
    ),
    corpus_size=len(MIGRATION_CORPUS),
    kind="oracle_swap",
    notes="a backward-compat checker must flag a dropped/renamed column, a "
          "narrowing type change, and a new non-defaulted required column as "
          "BREAKING — not silently allow them",
)


def teeth_scenarios() -> List[str]:
    """Names of the frozen migration corpus cases (the teeth scenarios)."""
    return [c.name for c in MIGRATION_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------


def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/schema_evolution")

    # 1. Legacy expand-contract scenarios still pass (the runtime behaviour).
    for r in _run_scenarios():
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # 2. The correct oracle agrees with every frozen migration verdict.
    for case in MIGRATION_CORPUS:
        report.add(f"oracle_verdict:{case.name}", case.expected_breaking,
                   is_breaking(case.old, case.new), detail=case.note)

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Schema evolution / expand-contract harness")
    p.add_argument("--self-test", action="store_true", help="run built-in checks")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        for s in list_scenarios() + teeth_scenarios():
            print(s)
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
