"""Tests for the hardened proof audit and shared harness discovery tooling.

The gate was hardened (2026 teeth campaign): non-legacy harnesses are proven only
by a verified TEETH swap-check (`required`) or reported `pending`; the older
keyword/self-test soft check now applies only to `legacy` categories (pharmacy).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harnesses._teeth import Mutant, Teeth, verify
from tools.harness_registry import REPO_ROOT, discover_harnesses, run_self_test
from tools.proof_audit import audit_harnesses


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TeethContractTests(unittest.TestCase):
    """Unit-test the core swap-check directly (no subprocess, no repo layout)."""

    @staticmethod
    def _corpus():
        return ((1, True), (2, False))

    def _prove_factory(self):
        corpus = self._corpus()

        def prove(impl):
            return any(impl(value) != expected for value, expected in corpus)

        return prove

    def test_correct_oracle_and_caught_mutant_verifies(self):
        prove = self._prove_factory()
        teeth = Teeth(
            prove=prove,
            oracle=lambda v: v == 1,            # agrees with the corpus
            mutants=(Mutant("always_true", lambda v: True, "wrong on v=2"),),
            corpus_size=2,
        )
        result = verify(teeth)
        self.assertTrue(result["teeth_verified"])
        self.assertTrue(result["oracle_clean"])
        self.assertEqual(result["mutants_caught"], 1)

    def test_swapped_oracle_and_mutant_fails(self):
        prove = self._prove_factory()
        teeth = Teeth(
            prove=prove,
            oracle=lambda v: True,              # buggy as oracle -> flagged
            mutants=(Mutant("real", lambda v: v == 1, ""),),
            corpus_size=2,
        )
        self.assertFalse(verify(teeth)["teeth_verified"])

    def test_teeth_requires_at_least_one_mutant(self):
        with self.assertRaises(ValueError):
            Teeth(prove=lambda impl: False, oracle=lambda v: v, mutants=(), corpus_size=1)


class ProofAuditToolTests(unittest.TestCase):
    def _fixture_root(self):
        return tempfile.TemporaryDirectory()

    def test_discovery_counts_all_real_harness_modules(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/core/__init__.py", "")
            _write(root / "harnesses/core/sample_test_harness.py", "# placeholder\n")
            _write(root / "harnesses/core/stress_harness.py", "# placeholder\n")

            records = discover_harnesses(root)

        self.assertEqual([record.name for record in records], ["sample", "stress"])

    def test_non_legacy_without_teeth_is_pending_not_failing(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/core/sample_test_harness.py", "# no TEETH yet\n")
            _write(root / "tests/core/test_sample_test_harness.py", "# paired test\n")
            records = discover_harnesses(root)

            # run_teeth=False: no subprocess; a TEETH-less in-scope harness is pending.
            result = audit_harnesses(records, selftest_statuses={"core/sample": "OK"},
                                     run_teeth=False)

        row = result["per_harness"][0]
        self.assertEqual(result["summary"]["fail"], 0)
        self.assertTrue(row["ok"])
        self.assertTrue(row["pending"])
        self.assertEqual(row["scope"], "pending")

    def test_legacy_missing_paired_unittest_fails_audit(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/pharmacy/sample_test_harness.py",
                   "# safe fixture passes; planted bad fixture detected\n")
            records = discover_harnesses(root)

            result = audit_harnesses(records, selftest_statuses={"pharmacy/sample": "OK"})

        self.assertEqual(result["summary"]["fail"], 1)
        self.assertEqual(result["per_harness"][0]["scope"], "legacy")
        self.assertIn("missing paired unittest", result["per_harness"][0]["failures"])

    def test_legacy_missing_bad_control_evidence_fails_audit(self):
        with self._fixture_root() as tmp:
            root = Path(tmp)
            _write(root / "harnesses/pharmacy/sample_test_harness.py",
                   "# safe good fixture passes\n")
            _write(root / "tests/pharmacy/test_sample_test_harness.py",
                   "# valid clean path ok\n")
            records = discover_harnesses(root)

            result = audit_harnesses(records, selftest_statuses={"pharmacy/sample": "OK"})

        self.assertEqual(result["summary"]["fail"], 1)
        self.assertIn("missing planted-bad/negative control evidence",
                      result["per_harness"][0]["failures"])

    def test_real_gold_harness_is_required_and_teeth_verified(self):
        # Integration: the real check_digit_identifier harness must pass the full
        # teeth swap-check (subprocess path) and be scoped `required`.
        records = [r for r in discover_harnesses(REPO_ROOT)
                   if r.name == "check_digit_identifier"]
        self.assertEqual(len(records), 1)

        result = audit_harnesses(records, selftest_statuses={"core/check_digit_identifier": "OK"})

        row = result["per_harness"][0]
        self.assertEqual(row["scope"], "required")
        self.assertTrue(row["teeth_verified"], row.get("teeth_error"))
        self.assertTrue(row["ok"])
        self.assertIn("teeth_swap", row["proof_sources"])

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
