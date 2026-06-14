import unittest

from harnesses.security import diff_secret_gate_test_harness as harness


class TestDiffSecretGateProof(unittest.TestCase):
    """Proves the direction-awareness oracle has teeth: the real scanner ignores
    secrets on removed lines while the intentionally buggy naive scanner flags
    them; and an added secret IS caught at the correct post-change line number."""

    def _case(self, name: str) -> harness.DiffCase:
        return next(c for c in harness.CASES if c.name == name)

    def test_proof_removed_only_secret_not_flagged_by_real_scanner(self):
        case = self._case("removed_secret_only")
        # the fixture really does contain a secret on a removed line
        self.assertIn("-", case.diff)
        self.assertEqual(harness.scan_diff(case.diff), [])

    def test_proof_naive_scanner_false_positives_on_removed_secret(self):
        case = self._case("removed_secret_only")
        naive = harness.scan_diff_naive(case.diff)
        # the common bug: naive flags the removed secret (>0 findings)
        self.assertTrue(naive)

    def test_proof_added_secret_caught_at_correct_line(self):
        case = self._case("added_secret_line_number")
        self.assertEqual(harness.scan_diff(case.diff), [("app/keys.py", 42, "SLACK_TOKEN")])

    def test_proof_centerpiece_remove_then_add_only_added_flagged(self):
        case = self._case("rotate_secret_remove_and_add")
        real = harness.scan_diff(case.diff)
        naive = harness.scan_diff_naive(case.diff)
        # real: exactly one finding, on the added line at the post-change number
        self.assertEqual(real, [("config.py", 10, "AWS_ACCESS_KEY_ID")])
        # naive: flags both the removed and the added occurrence
        self.assertGreater(len(naive), len(real))

    def test_proof_all_expected_findings_caught(self):
        results = harness.run_all()
        self.assertTrue(results)
        self.assertTrue(all(result.ok for result in results),
                        [r.case.name for r in results if not r.ok])


if __name__ == "__main__":
    unittest.main()
