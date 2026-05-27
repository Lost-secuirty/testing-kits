"""test_clinical_calc_test_harness.py — unittest suite for clinical_calc_test_harness (38)."""

import math
import unittest

from harnesses.pharmacy.clinical_calc_test_harness import (
    _ref_bsa,
    _ref_crcl,
    _ref_peds_mg,
    _ref_peds_ml,
    calc_bsa_mosteller,
    calc_crcl,
    calc_days_supply,
    calc_insulin_days,
    calc_peds_dose,
    run_all_scenarios,
)


class TestBSA(unittest.TestCase):

    def test_formula_identity_h170_w70(self):
        got = calc_bsa_mosteller(170, 70)
        ref = round(_ref_bsa(170, 70), 2)
        self.assertAlmostEqual(got, ref, places=2)

    def test_plausibility_bounds_grid(self):
        for h in (100, 150, 170, 200):
            for w in (20, 60, 100, 200):
                b = calc_bsa_mosteller(h, w)
                self.assertGreaterEqual(b, 0.10, f"BSA too low for h={h} w={w}: {b}")
                self.assertLessEqual(b, 4.00, f"BSA too high for h={h} w={w}: {b}")

    def test_monotone_in_height(self):
        values = [calc_bsa_mosteller(h, 70) for h in (100, 130, 160, 180, 200)]
        for i in range(len(values) - 1):
            self.assertLess(values[i], values[i + 1],
                            f"BSA not increasing at h index {i}")

    def test_monotone_in_weight(self):
        values = [calc_bsa_mosteller(170, w) for w in (20, 50, 80, 120, 200)]
        for i in range(len(values) - 1):
            self.assertLess(values[i], values[i + 1],
                            f"BSA not increasing at w index {i}")

    def test_zero_height_raises(self):
        with self.assertRaises(ValueError):
            calc_bsa_mosteller(0, 70)

    def test_height_301_raises(self):
        with self.assertRaises(ValueError):
            calc_bsa_mosteller(301, 70)

    def test_nan_height_raises(self):
        with self.assertRaises(ValueError):
            calc_bsa_mosteller(float("nan"), 70)

    def test_none_height_raises(self):
        with self.assertRaises((ValueError, TypeError)):
            calc_bsa_mosteller(None, 70)

    def test_negative_weight_raises(self):
        with self.assertRaises(ValueError):
            calc_bsa_mosteller(170, -10)


class TestCrCl(unittest.TestCase):

    def test_identity_male(self):
        got = calc_crcl(40, 80, 1.0, is_female=False)
        ref = round(_ref_crcl(40, 80, 1.0, False), 1)
        self.assertAlmostEqual(got, ref, delta=0.2)

    def test_female_factor_0_85(self):
        male = _ref_crcl(50, 70, 1.2, False)
        female = _ref_crcl(50, 70, 1.2, True)
        self.assertAlmostEqual(female / male, 0.85, places=9)

    def test_decreasing_with_age(self):
        values = [_ref_crcl(a, 70, 1.0) for a in (20, 40, 60, 80)]
        for i in range(len(values) - 1):
            self.assertGreater(values[i], values[i + 1])

    def test_decreasing_with_scr(self):
        values = [_ref_crcl(60, 70, s) for s in (0.5, 1.0, 2.0, 5.0)]
        for i in range(len(values) - 1):
            self.assertGreater(values[i], values[i + 1])

    def test_age_131_raises(self):
        with self.assertRaises(ValueError):
            calc_crcl(131, 70, 1.0)

    def test_age_130_ok(self):
        result = calc_crcl(130, 70, 1.0)
        self.assertIsInstance(result, float)

    def test_scr_zero_raises(self):
        with self.assertRaises(ValueError):
            calc_crcl(50, 70, 0)

    def test_scr_30_ok(self):
        result = calc_crcl(50, 70, 30)
        self.assertIsInstance(result, float)

    def test_negative_age_raises(self):
        with self.assertRaises(ValueError):
            calc_crcl(-1, 70, 1.0)


class TestPedsDose(unittest.TestCase):

    def test_amoxicillin_mg_dose(self):
        mg, _ = calc_peds_dose(18, 90, 2, 50)
        ref = round(_ref_peds_mg(18, 90, 2), 2)
        self.assertAlmostEqual(mg, ref, places=2)

    def test_amoxicillin_ml_dose(self):
        _, ml = calc_peds_dose(18, 90, 2, 50)
        ref = round(_ref_peds_ml(18, 90, 2, 50), 2)
        self.assertAlmostEqual(ml, ref, places=2)

    def test_zero_weight_raises(self):
        with self.assertRaises(ValueError):
            calc_peds_dose(0, 90, 2, 50)

    def test_zero_conc_raises(self):
        with self.assertRaises(ValueError):
            calc_peds_dose(18, 90, 2, 0)

    def test_fractional_doses_per_day_raises(self):
        with self.assertRaises(ValueError):
            calc_peds_dose(18, 90, 2.5, 50)

    def test_weight_201_raises(self):
        with self.assertRaises(ValueError):
            calc_peds_dose(201, 90, 2, 50)

    def test_result_is_tuple_of_floats(self):
        result = calc_peds_dose(18, 90, 2, 50)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


class TestDaysSupply(unittest.TestCase):

    def test_floor_31_div_3(self):
        self.assertEqual(calc_days_supply(31, 3), 10)

    def test_floor_30_div_3(self):
        self.assertEqual(calc_days_supply(30, 3), 10)

    def test_exact_division(self):
        self.assertEqual(calc_days_supply(90, 3), 30)

    def test_negative_qty_raises(self):
        with self.assertRaises(ValueError):
            calc_days_supply(-1, 3)

    def test_huge_qty_raises(self):
        with self.assertRaises(ValueError):
            calc_days_supply(1e7, 1)

    def test_zero_daily_raises(self):
        with self.assertRaises(ValueError):
            calc_days_supply(30, 0)


class TestInsulinDays(unittest.TestCase):

    def test_priming_subtracted(self):
        self.assertEqual(calc_insulin_days(10, 10, 100, priming=10), 50)

    def test_without_priming(self):
        self.assertEqual(calc_insulin_days(10, 10, 100, priming=0), 100)

    def test_huge_total_ml_raises(self):
        with self.assertRaises(ValueError):
            calc_insulin_days(10, 1e7, 100)

    def test_negative_priming_raises(self):
        with self.assertRaises(ValueError):
            calc_insulin_days(10, 10, 100, priming=-1)

    def test_zero_conc_raises(self):
        with self.assertRaises(ValueError):
            calc_insulin_days(10, 10, 0)

    def test_integer_floor_result(self):
        result = calc_insulin_days(10, 10, 100)
        self.assertIsInstance(result, int)


class TestSelfTest(unittest.TestCase):

    def test_all_self_test_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [],
                         "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count_at_least_16(self):
        results = run_all_scenarios(verbose=False)
        self.assertGreaterEqual(len(results), 16)


if __name__ == "__main__":
    unittest.main()
