"""test_supplychain_depth_test_harness.py — unittest suite (SBOM completeness)."""

import unittest

from harnesses.security.supplychain_depth_test_harness import (
    SBOMValidator,
    list_scenarios,
    run_all_scenarios,
)

_GOOD = {
    "name": "requests",
    "version": "2.32.0",
    "hash": "sha256:abc",
    "supplier": "PSF",
}


class TestSBOMValidator(unittest.TestCase):
    def setUp(self):
        self.c = SBOMValidator()

    def test_complete_signed_clean(self):
        sbom = {"signature": "ed25519:x", "components": [dict(_GOOD)]}
        self.assertEqual(self.c.validate(sbom), [])
        self.assertEqual(self.c.audit_codes(sbom), ())

    def test_unsigned_flagged(self):
        codes = self.c.audit_codes({"components": [dict(_GOOD)]})
        self.assertIn("sbom-unsigned", codes)

    def test_empty_components_flagged(self):
        codes = self.c.audit_codes({"signature": "x", "components": []})
        self.assertIn("sbom-no-components", codes)

    def test_missing_hash_flagged(self):
        comp = {"name": "n", "version": "1", "supplier": "s"}
        codes = self.c.audit_codes({"signature": "x", "components": [comp]})
        self.assertIn("sbom-missing-hash", codes)

    def test_blank_hash_counts_as_missing(self):
        comp = {"name": "n", "version": "1", "hash": "   ", "supplier": "s"}
        codes = self.c.audit_codes({"signature": "x", "components": [comp]})
        self.assertIn("sbom-missing-hash", codes)

    def test_missing_name_version_supplier(self):
        comp = {"hash": "h"}
        codes = self.c.audit_codes({"signature": "x", "components": [comp]})
        self.assertIn("sbom-missing-name", codes)
        self.assertIn("sbom-missing-version", codes)
        self.assertIn("sbom-missing-supplier", codes)

    def test_none_sbom_does_not_raise(self):
        codes = self.c.audit_codes({})
        self.assertEqual(set(codes), {"sbom-no-components", "sbom-unsigned"})


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 10)


if __name__ == "__main__":
    unittest.main()
