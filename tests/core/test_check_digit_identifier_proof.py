import unittest

from harnesses.core import check_digit_identifier_test_harness as harness


class TestCheckDigitIdentifierProof(unittest.TestCase):
    def test_proof_single_digit_sweep_is_100pct_for_luhn(self):
        # Luhn detects every single-digit substitution on a valid identifier.
        for sample in harness.VALID_SAMPLES["LUHN"]:
            sweep = harness.single_digit_corruption_sweep("LUHN", sample)
            self.assertEqual(sweep.corruptions_detected, sweep.corruptions_tested)
            self.assertEqual(sweep.escapes, ())

    def test_proof_single_digit_sweep_for_dea_matches_known_blind_set(self):
        # The DEA checksum is faithfully ported; it detects every single-digit
        # error EXCEPT the mathematically inherent +/-5 swap on a doubled
        # position. The sweep reproduces exactly that derived blind set, proving
        # the harness measures the real (weak) detection rather than overclaiming.
        for sample in harness.VALID_SAMPLES["DEA"]:
            sweep = harness.single_digit_corruption_sweep("DEA", sample)
            self.assertFalse(sweep.perfect)
            self.assertEqual(
                tuple(sorted(sweep.escapes)),
                harness.dea_expected_escapes(sample),
            )

    def test_proof_naive_validator_accepts_a_corruption_real_one_rejects(self):
        # Planted buggy implementation: validate_naive checks shape only, never
        # the check digit. Find a single-digit-corrupted id it waves through but
        # the real validator catches.
        base = "AB3456781"  # valid DEA id
        self.assertTrue(harness.validate("DEA", base))

        caught_by_real_missed_by_naive = []
        for pos in range(2, len(base)):
            original = base[pos]
            for digit in "0123456789":
                if digit == original:
                    continue
                corrupted = base[:pos] + digit + base[pos + 1 :]
                if not harness.validate("DEA", corrupted) and harness.validate_naive(
                    "DEA", corrupted
                ):
                    caught_by_real_missed_by_naive.append(corrupted)

        self.assertTrue(
            caught_by_real_missed_by_naive,
            "naive validator should accept at least one corruption the real one rejects",
        )
        # Spot-check one concrete escape from the buggy oracle.
        sample = caught_by_real_missed_by_naive[0]
        self.assertTrue(harness.validate_naive("DEA", sample))
        self.assertFalse(harness.validate("DEA", sample))

    def test_proof_safe_fixtures_pass_and_bad_fixtures_fail(self):
        valid = [c for c in harness.CASES if c.expected_valid]
        invalid = [c for c in harness.CASES if not c.expected_valid]
        self.assertTrue(valid)
        self.assertTrue(invalid)
        for case in valid:
            self.assertTrue(
                harness.validate(case.scheme, case.identifier), case.name
            )
        for case in invalid:
            self.assertFalse(
                harness.validate(case.scheme, case.identifier), case.name
            )


if __name__ == "__main__":
    unittest.main()
