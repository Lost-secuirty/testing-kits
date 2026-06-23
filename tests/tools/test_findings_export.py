"""test_findings_export.py — unittest suite for SARIF/JSON export."""

import json
import unittest

from tools.findings_export import (
    SAMPLE_FINDINGS,
    is_valid_sarif,
    list_scenarios,
    normalize,
    run_all_scenarios,
    to_json,
    to_sarif,
)


class TestNormalize(unittest.TestCase):
    def test_check_name_to_rule_id(self):
        n = normalize({"check_name": "X", "severity": "high", "description": "d"})
        self.assertEqual(n["rule_id"], "X")
        self.assertEqual(n["severity"], "HIGH")
        self.assertEqual(n["message"], "d")

    def test_object_finding(self):
        class F:
            rule_id = "R"
            cwe = "CWE-1"
            severity = "LOW"
            message = "m"
            line = 3
        n = normalize(F())
        self.assertEqual(n["rule_id"], "R")
        self.assertEqual(n["line"], 3)


class TestSarif(unittest.TestCase):
    def test_valid_structure(self):
        doc = to_sarif(SAMPLE_FINDINGS)
        self.assertTrue(is_valid_sarif(doc))
        self.assertEqual(doc["version"], "2.1.0")

    def test_result_count(self):
        doc = to_sarif(SAMPLE_FINDINGS)
        self.assertEqual(len(doc["runs"][0]["results"]), len(SAMPLE_FINDINGS))

    def test_level_mapping(self):
        doc = to_sarif([{"rule_id": "a", "severity": "CRITICAL", "message": "x"},
                        {"rule_id": "b", "severity": "LOW", "message": "y"}])
        levels = [r["level"] for r in doc["runs"][0]["results"]]
        self.assertEqual(levels, ["error", "note"])

    def test_location_when_file(self):
        doc = to_sarif([{"rule_id": "a", "severity": "HIGH", "message": "x",
                         "file": "f.py", "line": 7}])
        loc = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        self.assertEqual(loc["artifactLocation"]["uri"], "f.py")
        self.assertEqual(loc["region"]["startLine"], 7)

    def test_invalid_rejected(self):
        self.assertFalse(is_valid_sarif({"version": "1.0"}))
        self.assertFalse(is_valid_sarif({"version": "2.1.0", "runs": []}))


class TestJson(unittest.TestCase):
    def test_round_trip(self):
        parsed = json.loads(to_json(SAMPLE_FINDINGS))
        self.assertEqual(len(parsed), len(SAMPLE_FINDINGS))
        self.assertEqual(parsed[0]["rule_id"], "CryptoChecker")


class TestRobustness(unittest.TestCase):
    def test_non_numeric_line_coerced(self):
        self.assertEqual(normalize({"rule_id": "x", "line": "N/A"})["line"], 0)
        self.assertEqual(normalize({"rule_id": "x", "line": -4})["line"], 0)
        self.assertEqual(normalize({"rule_id": "x", "line": "7"})["line"], 7)

    def test_region_omitted_when_line_unknown(self):
        doc = to_sarif([{"rule_id": "x", "severity": "HIGH", "message": "m", "file": "f.py"}])
        phys = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        self.assertNotIn("region", phys)
        self.assertEqual(phys["artifactLocation"]["uri"], "f.py")

    def test_cwe_aggregated_across_rule(self):
        doc = to_sarif([{"rule_id": "R", "cwe": "CWE-1", "message": "a"},
                        {"rule_id": "R", "cwe": "CWE-2", "message": "b"}])
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        self.assertIn("CWE-1", rule["properties"]["tags"])
        self.assertIn("CWE-2", rule["properties"]["tags"])

    def test_cwe_captured_when_first_finding_has_none(self):
        doc = to_sarif([{"rule_id": "R", "message": "a"},
                        {"rule_id": "R", "cwe": "CWE-9", "message": "b"}])
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        self.assertIn("CWE-9", rule["properties"]["tags"])


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 10)


if __name__ == "__main__":
    unittest.main()
