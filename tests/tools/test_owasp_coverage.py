"""test_owasp_coverage.py — unittest suite for the OWASP coverage meta-tool."""

import unittest

from tools.owasp_coverage import (
    OWASP_2025_WEB,
    REGISTRY,
    build_matrix,
    discovered_keys,
    list_scenarios,
    missing_categories,
    render_markdown,
    run_all_scenarios,
    stale_entries,
    unmapped_harnesses,
)


class TestCoverage(unittest.TestCase):
    def test_all_categories_covered(self):
        self.assertEqual(missing_categories(), [])

    def test_matrix_has_all_codes(self):
        matrix = build_matrix()
        for code in OWASP_2025_WEB:
            self.assertIn(code, matrix)
            self.assertGreaterEqual(len(matrix[code]), 1, code)

    def test_registry_nonempty(self):
        self.assertGreaterEqual(len(REGISTRY), 10)

    def test_markdown_renders_table(self):
        md = render_markdown()
        self.assertIn("OWASP 2025", md)
        self.assertIn("A01", md)
        self.assertIn("A10", md)

    def test_llm_categories_present(self):
        codes = {c for cov in REGISTRY for c in cov.categories}
        self.assertTrue(any(c.startswith("LLM") for c in codes))

    def test_corrected_2025_llm_codes(self):
        # The 2025 OWASP LLM Top 10 renames: insecure_output_handling is LLM05 (was LLM02),
        # sensitive_disclosure is LLM02 (was LLM07). Guard against regressing to old codes.
        by_module = {cov.module: cov.categories for cov in REGISTRY}
        self.assertIn("LLM05", by_module["ai/insecure_output_handling"])
        self.assertIn("LLM02", by_module["ai/sensitive_disclosure"])


class TestRegistryTreeSync(unittest.TestCase):
    """The matrix must not drift from the actual harness tree."""

    def test_no_stale_entries(self):
        # Every REGISTRY module points at a harness that still exists.
        self.assertEqual(stale_entries(), [])

    def test_every_security_ai_harness_mapped(self):
        self.assertEqual(unmapped_harnesses(), [])

    def test_registry_modules_are_real(self):
        keys = discovered_keys()
        for cov in REGISTRY:
            self.assertIn(cov.module, keys, f"{cov.module} not found in tree")


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 12)


if __name__ == "__main__":
    unittest.main()
