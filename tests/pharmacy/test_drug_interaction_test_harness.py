"""Test suite for drug_interaction_test_harness."""

import sqlite3
import unittest

from harnesses.pharmacy.drug_interaction_test_harness import (
    SCENARIOS,
    FoodInteraction,
    Interaction,
    InteractionEngine,
    Severity,
    _run_self_test,
    _seed_engine,
    list_scenarios,
)


class TestSeverity(unittest.TestCase):
    def test_ordering(self):
        self.assertLess(Severity.MILD, Severity.MODERATE)
        self.assertLess(Severity.MODERATE, Severity.SEVERE)
        self.assertLess(Severity.SEVERE, Severity.CONTRAINDICATED)

    def test_int_values_stable(self):
        self.assertEqual(int(Severity.MILD), 1)
        self.assertEqual(int(Severity.CONTRAINDICATED), 4)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.engine = InteractionEngine(sqlite3.connect(":memory:"))

    def test_load_and_lookup_canonical_order(self):
        self.engine.load_interaction(Interaction("z_drug", "a_drug", Severity.SEVERE))
        ix1 = self.engine.lookup_pair("a_drug", "z_drug")
        ix2 = self.engine.lookup_pair("z_drug", "a_drug")
        self.assertIsNotNone(ix1)
        self.assertEqual(ix1, ix2)

    def test_lookup_missing_returns_none(self):
        self.assertIsNone(self.engine.lookup_pair("x", "y"))

    def test_scan_regimen_pairwise(self):
        self.engine.load_interaction(Interaction("a", "b", Severity.SEVERE))
        self.engine.load_interaction(Interaction("c", "d", Severity.MILD))
        issues = self.engine.scan_regimen(["a", "b", "c", "d"])
        self.assertEqual(len(issues), 2)

    def test_scan_regimen_returns_only_meaningful_severity(self):
        # NONE-level interactions should not be returned.
        self.engine.load_interaction(Interaction("a", "b", Severity.NONE))
        issues = self.engine.scan_regimen(["a", "b"])
        self.assertEqual(issues, [])

    def test_food_lookup(self):
        self.engine.load_food_interaction(
            FoodInteraction("statin", "grapefruit", Severity.SEVERE))
        fxs = self.engine.scan_food(["statin"], ["grapefruit"])
        self.assertEqual(len(fxs), 1)
        self.assertEqual(fxs[0].severity, Severity.SEVERE)

    def test_can_add_blocks_contraindicated(self):
        self.engine.load_interaction(
            Interaction("maoi", "ssri", Severity.CONTRAINDICATED))
        allowed, issues = self.engine.can_add(["maoi"], "ssri")
        self.assertFalse(allowed)

    def test_can_add_warns_but_allows_severe(self):
        self.engine.load_interaction(
            Interaction("warfarin", "aspirin", Severity.SEVERE))
        allowed, issues = self.engine.can_add(["warfarin"], "aspirin")
        self.assertTrue(allowed)
        self.assertEqual(len(issues), 1)

    def test_qt_escalation_two_drugs_severe(self):
        qt = {"d1", "d2", "d3"}
        self.assertEqual(self.engine.escalate_qt_prolongation(["d1", "d2"], qt),
                         Severity.SEVERE)

    def test_qt_escalation_three_drugs_contraindicated(self):
        qt = {"d1", "d2", "d3"}
        self.assertEqual(
            self.engine.escalate_qt_prolongation(["d1", "d2", "d3"], qt),
            Severity.CONTRAINDICATED,
        )

    def test_qt_no_escalation_one_drug(self):
        qt = {"d1", "d2"}
        self.assertEqual(self.engine.escalate_qt_prolongation(["d1"], qt),
                         Severity.NONE)

    def test_override_audit_persists(self):
        oid = self.engine.record_override("a", "b", Severity.SEVERE,
                                          role="md", reason="benefit > risk")
        self.assertGreater(oid, 0)
        overrides = self.engine.list_overrides()
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0]["role"], "md")

    def test_override_audit_canonicalizes_drug_order(self):
        self.engine.record_override("z", "a", Severity.SEVERE, "md", "test")
        overrides = self.engine.list_overrides()
        self.assertEqual(overrides[0]["drug_a"], "a")
        self.assertEqual(overrides[0]["drug_b"], "z")


class TestSeeded(unittest.TestCase):
    def test_seed_engine_has_known_interactions(self):
        eng = _seed_engine()
        ix = eng.lookup_pair("maoi", "ssri")
        self.assertEqual(ix.severity, Severity.CONTRAINDICATED)
        self.assertFalse(ix.override_allowed)


class TestScenarios(unittest.TestCase):
    def test_all_scenarios_pass(self):
        for name, fn in SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertTrue(fn().passed, f"{name} failed")

    def test_list_scenarios_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 8)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
