"""Tests for the combinatorial coverage harness."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from harnesses.core.combinatorial_coverage_test_harness import (
    MODEL,
    PAIRWISE_SUITE,
    TEETH,
    InteractionCase,
    audit_coverage,
    collapsed_value_mutant,
    exhaustive_count,
    list_scenarios,
    main,
    missing_interaction_mutant,
    omitted_parameter_mutant,
    oracle_pairwise_suite,
    prove,
    required_interactions,
)


class TestCombinatorialCoverageModel(unittest.TestCase):
    def test_required_pairwise_interaction_count(self) -> None:
        required = required_interactions(MODEL, strength=2)
        self.assertEqual(len(required), 16)

    def test_invalid_strength_rejected(self) -> None:
        with self.assertRaises(ValueError):
            required_interactions(MODEL, strength=0)
        with self.assertRaises(ValueError):
            required_interactions(MODEL, strength=4)

    def test_pairwise_suite_is_smaller_than_exhaustive(self) -> None:
        self.assertEqual(exhaustive_count(MODEL), 12)
        self.assertEqual(len(PAIRWISE_SUITE), 6)
        self.assertLess(len(PAIRWISE_SUITE), exhaustive_count(MODEL))

    def test_oracle_suite_covers_all_pairs(self) -> None:
        audit = audit_coverage(oracle_pairwise_suite())
        self.assertTrue(audit.passed)
        self.assertFalse(audit.missing)
        self.assertFalse(audit.malformed_cases)

    def test_missing_interaction_mutant_loses_required_pairs(self) -> None:
        audit = audit_coverage(missing_interaction_mutant())
        self.assertFalse(audit.passed)
        self.assertIn((("browser", "chrome"), ("format", "xml")), audit.missing)
        self.assertIn((("role", "admin"), ("format", "xml")), audit.missing)

    def test_collapsed_value_mutant_loses_firefox_pairs(self) -> None:
        audit = audit_coverage(collapsed_value_mutant())
        self.assertFalse(audit.passed)
        self.assertIn((("browser", "firefox"), ("role", "admin")), audit.missing)

    def test_omitted_parameter_mutant_is_malformed(self) -> None:
        audit = audit_coverage(omitted_parameter_mutant())
        self.assertFalse(audit.passed)
        self.assertEqual(len(audit.malformed_cases), len(PAIRWISE_SUITE))

    def test_duplicate_parameter_case_is_malformed(self) -> None:
        duplicate = InteractionCase(
            "duplicate_browser",
            (("browser", "chrome"), ("browser", "firefox"), ("role", "admin"), ("format", "json")),
        )
        audit = audit_coverage((duplicate,))
        self.assertFalse(audit.passed)
        self.assertEqual(audit.malformed_cases, ("duplicate_browser",))

    def test_unknown_value_case_is_malformed(self) -> None:
        bad_value = InteractionCase(
            "safari_admin_json",
            (("browser", "safari"), ("role", "admin"), ("format", "json")),
        )
        audit = audit_coverage((bad_value,))
        self.assertFalse(audit.passed)
        self.assertEqual(audit.malformed_cases, ("safari_admin_json",))


class TestCombinatorialTeeth(unittest.TestCase):
    def test_teeth_contract_clean_oracle_and_catches_mutants(self) -> None:
        self.assertFalse(prove(oracle_pairwise_suite))
        self.assertTrue(prove(missing_interaction_mutant))
        self.assertTrue(prove(collapsed_value_mutant))
        self.assertTrue(prove(omitted_parameter_mutant))
        self.assertEqual(TEETH.corpus_size, 16)

    def test_broken_generator_exception_is_caught(self) -> None:
        def broken_generator():
            raise RuntimeError("generation failed")

        self.assertTrue(prove(broken_generator))


class TestCombinatorialCli(unittest.TestCase):
    def test_list_scenarios(self) -> None:
        self.assertEqual(
            list_scenarios(),
            [
                "chrome_admin_json",
                "chrome_reader_csv",
                "firefox_admin_csv",
                "firefox_reader_json",
                "chrome_admin_xml",
                "firefox_reader_xml",
            ],
        )

    def test_main_list_scenarios(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--list-scenarios"])
        self.assertEqual(code, 0)
        self.assertIn("chrome_admin_json", buffer.getvalue())

    def test_main_self_test(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--self-test"])
        self.assertEqual(code, 0)
        self.assertIn("OK: core/combinatorial_coverage", buffer.getvalue())

    def test_main_json(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["harness"], "core/combinatorial_coverage")


if __name__ == "__main__":
    unittest.main()
