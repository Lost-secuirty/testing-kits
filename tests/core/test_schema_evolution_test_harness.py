"""Test suite for schema_evolution_test_harness."""

import sqlite3
import unittest

from harnesses._teeth import verify
from harnesses.core.schema_evolution_test_harness import (
    MIGRATION_CORPUS,
    SCENARIOS,
    SCHEMAS,
    TEETH,
    Field,
    Reader,
    Writer,
    _fresh_db,
    _normalize,
    _run_self_test,
    apply_migration,
    is_breaking,
    list_scenarios,
)


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_strip(self):
        self.assertEqual(_normalize("  Alice@Example.COM "), "alice@example.com")

    def test_none_passthrough(self):
        self.assertIsNone(_normalize(None))

    def test_empty_string(self):
        self.assertEqual(_normalize(""), "")


class TestApplyMigration(unittest.TestCase):
    def test_old_to_expanded(self):
        conn = _fresh_db("OLD")
        apply_migration(conn, "EXPANDED")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        self.assertIn("email", cols)
        self.assertIn("email_canonical", cols)

    def test_old_to_contracted_backfills(self):
        conn = _fresh_db("OLD")
        Writer(conn, "OLD").insert(1, "Alice", "ALICE@X.com")
        apply_migration(conn, "CONTRACTED")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        self.assertNotIn("email", cols)
        self.assertIn("email_canonical", cols)
        row = conn.execute("SELECT email_canonical FROM users WHERE id=1").fetchone()
        self.assertEqual(row[0], "alice@x.com")


class TestWriter(unittest.TestCase):
    def test_writer_on_old_schema(self):
        conn = _fresh_db("OLD")
        Writer(conn, "OLD").insert(1, "Alice", "alice@x.com")
        row = conn.execute("SELECT email FROM users WHERE id=1").fetchone()
        self.assertEqual(row[0], "alice@x.com")

    def test_writer_on_expanded_fills_both(self):
        conn = _fresh_db("EXPANDED")
        Writer(conn, "EXPANDED").insert(1, "Alice", "ALICE@X.com")
        row = conn.execute(
            "SELECT email, email_canonical FROM users WHERE id=1"
        ).fetchone()
        self.assertEqual(row[0], "ALICE@X.com")
        self.assertEqual(row[1], "alice@x.com")

    def test_writer_on_contracted_fills_canonical_only(self):
        conn = _fresh_db("CONTRACTED")
        Writer(conn, "CONTRACTED").insert(1, "Alice", "ALICE@X.com")
        row = conn.execute("SELECT email_canonical FROM users WHERE id=1").fetchone()
        self.assertEqual(row[0], "alice@x.com")


class TestReader(unittest.TestCase):
    def test_safe_reader_canonical_from_old(self):
        conn = _fresh_db("OLD")
        Writer(conn, "OLD").insert(1, "Alice", "ALICE@X.com")
        self.assertEqual(Reader(conn, safe=True).get_email(1), "alice@x.com")

    def test_safe_reader_canonical_from_expanded(self):
        conn = _fresh_db("EXPANDED")
        Writer(conn, "EXPANDED").insert(1, "Alice", "ALICE@X.com")
        self.assertEqual(Reader(conn, safe=True).get_email(1), "alice@x.com")

    def test_safe_reader_canonical_from_contracted(self):
        conn = _fresh_db("CONTRACTED")
        Writer(conn, "CONTRACTED").insert(1, "Alice", "ALICE@X.com")
        self.assertEqual(Reader(conn, safe=True).get_email(1), "alice@x.com")

    def test_unsafe_reader_breaks_on_contracted(self):
        conn = _fresh_db("CONTRACTED")
        Writer(conn, "CONTRACTED").insert(1, "Alice", "ALICE@X.com")
        self.assertIsNone(Reader(conn, safe=False).get_email(1))

    def test_unsafe_reader_returns_raw_on_old(self):
        """The unsafe reader skips normalization and returns the raw value."""
        conn = _fresh_db("OLD")
        Writer(conn, "OLD").insert(1, "Alice", "ALICE@X.com")
        self.assertEqual(Reader(conn, safe=False).get_email(1), "ALICE@X.com")


class TestScenarios(unittest.TestCase):
    def test_all_scenarios_pass(self):
        for name, fn in SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertTrue(fn().passed, f"{name} failed")

    def test_list_scenarios_count(self):
        self.assertEqual(len(list_scenarios()), 5)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


class TestCompatibilityOracle(unittest.TestCase):
    """The backward-compatibility checker that backs the teeth corpus."""

    def test_oracle_matches_every_corpus_verdict(self):
        for case in MIGRATION_CORPUS:
            with self.subTest(case=case.name):
                self.assertEqual(
                    is_breaking(case.old, case.new),
                    case.expected_breaking,
                    f"{case.name}: {case.note}",
                )

    def test_add_optional_is_compatible(self):
        old = {"id": Field("INTEGER", required=True)}
        new = {"id": Field("INTEGER", required=True), "tag": Field("TEXT")}
        self.assertFalse(is_breaking(old, new))

    def test_drop_is_breaking(self):
        old = {"id": Field("INTEGER", required=True), "x": Field("TEXT")}
        new = {"id": Field("INTEGER", required=True)}
        self.assertTrue(is_breaking(old, new))

    def test_narrow_is_breaking(self):
        old = {"x": Field("TEXT")}
        new = {"x": Field("INTEGER")}
        self.assertTrue(is_breaking(old, new))

    def test_widen_is_compatible(self):
        old = {"x": Field("INTEGER")}
        new = {"x": Field("TEXT")}
        self.assertFalse(is_breaking(old, new))

    def test_new_required_no_default_is_breaking(self):
        old = {"id": Field("INTEGER", required=True)}
        new = {"id": Field("INTEGER", required=True),
               "t": Field("INTEGER", required=True)}
        self.assertTrue(is_breaking(old, new))

    def test_new_required_with_default_is_compatible(self):
        old = {"id": Field("INTEGER", required=True)}
        new = {"id": Field("INTEGER", required=True),
               "t": Field("TEXT", required=True, default="x")}
        self.assertFalse(is_breaking(old, new))


class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted schema-compatibility bug."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        self.assertFalse(TEETH.prove(TEETH.oracle))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == "__main__":
    unittest.main()
