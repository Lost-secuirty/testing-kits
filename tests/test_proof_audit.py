"""Tests for proof audit and shared harness discovery tooling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.harness_registry import discover_harnesses, run_self_test
from tools.proof_audit import audit_harnesses


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ProofAuditToolTests(unittest.TestCase):
    def _fixture_root(self):
        return tempfile.TemporaryDirectory()

    def test_discovery_counts_all_real_harness_modules(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/core/__init__.py", "")
            _write(root / "harnesses/core/sample_test_harness.py", "# safe bad planted pass\n")
            _write(root / "harnesses/core/stress_harness.py", "# safe bad planted pass\n")

            records = discover_harnesses(root)

        self.assertEqual([record.name for record in records], ["sample", "stress"])

    def test_valid_harness_passes_audit(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/core/sample_test_harness.py",
                   "# safe good fixture passes; planted bad fixture detected\n")
            _write(root / "tests/core/test_sample_test_harness.py",
                   "# valid path ok; buggy negative path fails\n")
            records = discover_harnesses(root)

            result = audit_harnesses(records, selftest_statuses={"core/sample": "OK"})

        self.assertEqual(result["summary"]["fail"], 0)
        self.assertTrue(result["per_harness"][0]["ok"])

    def test_missing_paired_unittest_fails_audit(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/core/sample_test_harness.py",
                   "# safe fixture passes; planted bad fixture detected\n")
            records = discover_harnesses(root)

            result = audit_harnesses(records, selftest_statuses={"core/sample": "OK"})

        self.assertEqual(result["summary"]["fail"], 1)
        self.assertIn("missing paired unittest", result["per_harness"][0]["failures"])

    def test_missing_bad_control_evidence_fails_audit(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/core/sample_test_harness.py",
                   "# safe good fixture passes\n")
            _write(root / "tests/core/test_sample_test_harness.py",
                   "# valid clean path ok\n")
            records = discover_harnesses(root)

            result = audit_harnesses(records, selftest_statuses={"core/sample": "OK"})

        self.assertEqual(result["summary"]["fail"], 1)
        self.assertIn("missing planted-bad/negative control evidence",
                      result["per_harness"][0]["failures"])

    def test_unicode_selftest_output_does_not_crash_runner(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            script = root / "harnesses/core/unicode_test_harness.py"
            _write(script, "print('OK: unicode \\u2265 \\u2713')\n")

            status, _duration, tail = run_self_test(script, root, timeout_s=5)

        self.assertEqual(status, "OK")
        self.assertIn("unicode", tail)


if __name__ == "__main__":
    unittest.main()
