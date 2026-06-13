import subprocess
import sys
import unittest

from harnesses.core import check_digit_identifier_test_harness as harness


class TestCheckDigitIdentifierHarness(unittest.TestCase):
    def test_case_catalog_has_valid_and_invalid_fixtures(self):
        self.assertGreaterEqual(len(harness.CASES), 15)
        self.assertTrue(any(case.expected_valid for case in harness.CASES))
        self.assertTrue(any(not case.expected_valid for case in harness.CASES))
        # All three named schemes are represented.
        schemes = {case.scheme for case in harness.CASES}
        self.assertEqual(schemes, {"DEA", "LUHN", "ISBN10"})

    def test_each_fixture_matches_validator(self):
        results = harness.run_all()
        bad = [case.name for case, ok in results if not ok]
        self.assertEqual(bad, [], bad)

    def test_sample_identifiers_validate(self):
        for scheme, samples in harness.VALID_SAMPLES.items():
            for sample in samples:
                self.assertTrue(
                    harness.validate(scheme, sample),
                    f"{scheme} sample {sample!r} should validate",
                )

    def test_validate_unknown_scheme_raises(self):
        with self.assertRaises(KeyError):
            harness.validate("NOPE", "123")

    def test_dea_faithful_port(self):
        # Exact reproduction of verify_dea_logic on a known-good id.
        self.assertTrue(harness.validate("DEA", "AB3456781"))
        # Wrong check digit.
        self.assertFalse(harness.validate("DEA", "AB3456782"))

    def test_ascii_only_guard_rejects_unicode_digit(self):
        # Arabic-Indic zero must NOT be treated as ASCII '0' (bug-fix F-05).
        self.assertFalse(harness.validate("DEA", "AB345678٠"))
        self.assertFalse(harness.validate("LUHN", "453914880343646٧"))
        # And the all-ASCII helper agrees.
        self.assertFalse(harness._all_ascii_digits("12٠3"))
        self.assertTrue(harness._all_ascii_digits("1230"))

    def test_dea_prefix_class_signal(self):
        self.assertIs(harness.dea_prescriber_class("AB3456781"), True)
        self.assertIs(harness.dea_prescriber_class("FB3456781"), False)
        self.assertIsNone(harness.dea_prescriber_class("ZB3456781"))
        self.assertIsNone(harness.dea_prescriber_class(""))

    def test_luhn_and_isbn10_detect_all_single_digit_errors(self):
        for scheme in ("LUHN", "ISBN10"):
            for sample in harness.VALID_SAMPLES[scheme]:
                sweep = harness.single_digit_corruption_sweep(scheme, sample)
                self.assertTrue(
                    sweep.perfect,
                    f"{scheme} {sample!r} should detect 100%, escapes={sweep.escapes}",
                )
                self.assertEqual(sweep.escapes, ())
                self.assertEqual(sweep.detection_rate, 1.0)

    def test_dea_blind_set_matches_derivation(self):
        for sample in harness.VALID_SAMPLES["DEA"]:
            sweep = harness.single_digit_corruption_sweep("DEA", sample)
            self.assertEqual(
                tuple(sorted(sweep.escapes)),
                harness.dea_expected_escapes(sample),
            )
            # The DEA blind set is exactly the three +/-5 doubled-position swaps.
            self.assertEqual(len(sweep.escapes), 3)

    def test_sweep_requires_valid_base(self):
        with self.assertRaises(ValueError):
            harness.single_digit_corruption_sweep("DEA", "AB3456782")

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_cli_list_scenarios(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--list-scenarios"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dea_valid_1", proc.stdout)

    def test_cli_json(self):
        import json

        proc = subprocess.run(
            [sys.executable, harness.__file__, "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("cases", payload)
        self.assertIn("sweeps", payload)


if __name__ == "__main__":
    unittest.main()
