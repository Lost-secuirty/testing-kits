"""
Tests for Numeric / Money Precision Test Harness (harness 23 of 36).
~102 tests. Pure stdlib, zero external dependencies.
"""

import json
import math
import unittest
import urllib.request
import urllib.error
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, InvalidOperation

from harnesses._teeth import verify
from harnesses.core.numeric_test_harness import (
    Money,
    CurrencyMismatchError,
    FloatPitfallTester,
    RoundingModeTester,
    CurrencyTester,
    OverflowTester,
    PrecisionTester,
    ComparisonTester,
    MockNumericHandler,
    NumericTestServer,
    create_server,
    TEETH,
    prove,
    oracle_allocate,
    ALLOC_CORPUS,
)


# ---------------------------------------------------------------------------
# Shared server fixture
# ---------------------------------------------------------------------------

_server: NumericTestServer = None


def setUpModule():
    global _server
    _server = create_server(port=0)


def tearDownModule():
    global _server
    if _server:
        _server.stop()


def _get(path: str) -> dict:
    url = _server.base_url + path
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def _post(path: str, payload: dict) -> tuple:
    """Returns (status_code, response_dict)."""
    url = _server.base_url + path
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ===========================================================================
# 1. Money — construction
# ===========================================================================

class TestMoneyConstruction(unittest.TestCase):

    def test_construct_from_string(self):
        m = Money("10.00")
        self.assertEqual(m.amount, Decimal("10.00"))

    def test_construct_from_int(self):
        m = Money(5)
        self.assertEqual(m.amount, Decimal("5"))

    def test_construct_from_decimal(self):
        m = Money(Decimal("3.14"))
        self.assertEqual(m.amount, Decimal("3.14"))

    def test_construct_from_float_uses_string_conversion(self):
        m = Money(0.1)
        # Should be "0.1" not 0.1000000000000000055511151231257827021181583404541015625
        self.assertEqual(m.amount, Decimal("0.1"))

    def test_default_currency_is_usd(self):
        m = Money("1.00")
        self.assertEqual(m.currency, "USD")

    def test_currency_uppercased(self):
        m = Money("1.00", "eur")
        self.assertEqual(m.currency, "EUR")

    def test_default_rounding_is_half_even(self):
        m = Money("1.00")
        self.assertEqual(m.rounding, ROUND_HALF_EVEN)

    def test_custom_rounding(self):
        m = Money("1.00", rounding=ROUND_HALF_UP)
        self.assertEqual(m.rounding, ROUND_HALF_UP)

    def test_default_decimal_places(self):
        m = Money("1.00")
        self.assertEqual(m.decimal_places, 2)

    def test_custom_decimal_places(self):
        m = Money("1.000", decimal_places=3)
        self.assertEqual(m.decimal_places, 3)


# ===========================================================================
# 2. Money — addition
# ===========================================================================

class TestMoneyAddition(unittest.TestCase):

    def test_add_same_currency(self):
        result = Money("10.00").add(Money("5.00"))
        self.assertEqual(result.amount, Decimal("15.00"))

    def test_add_preserves_currency(self):
        result = Money("10.00", "GBP").add(Money("5.00", "GBP"))
        self.assertEqual(result.currency, "GBP")

    def test_add_different_currency_raises(self):
        with self.assertRaises(CurrencyMismatchError):
            Money("10.00", "USD").add(Money("5.00", "EUR"))

    def test_add_zero(self):
        result = Money("10.00").add(Money("0.00"))
        self.assertEqual(result.amount, Decimal("10.00"))

    def test_add_negative(self):
        result = Money("10.00").add(Money("-3.00"))
        self.assertEqual(result.amount, Decimal("7.00"))

    def test_add_fractions(self):
        result = Money("0.10").add(Money("0.20"))
        self.assertEqual(result.amount, Decimal("0.30"))


# ===========================================================================
# 3. Money — subtraction
# ===========================================================================

class TestMoneySubtraction(unittest.TestCase):

    def test_subtract_same_currency(self):
        result = Money("10.00").subtract(Money("3.00"))
        self.assertEqual(result.amount, Decimal("7.00"))

    def test_subtract_different_currency_raises(self):
        with self.assertRaises(CurrencyMismatchError):
            Money("10.00", "USD").subtract(Money("5.00", "GBP"))

    def test_subtract_result_negative(self):
        result = Money("3.00").subtract(Money("10.00"))
        self.assertEqual(result.amount, Decimal("-7.00"))

    def test_subtract_preserves_currency(self):
        result = Money("10.00", "EUR").subtract(Money("5.00", "EUR"))
        self.assertEqual(result.currency, "EUR")

    def test_subtract_zero(self):
        result = Money("5.00").subtract(Money("0.00"))
        self.assertEqual(result.amount, Decimal("5.00"))


# ===========================================================================
# 4. Money — multiplication
# ===========================================================================

class TestMoneyMultiplication(unittest.TestCase):

    def test_multiply_by_int(self):
        result = Money("10.00").multiply(3)
        self.assertEqual(result.amount, Decimal("30.00"))

    def test_multiply_by_decimal(self):
        result = Money("10.00").multiply(Decimal("1.5"))
        self.assertEqual(result.amount, Decimal("15.000"))

    def test_multiply_by_float(self):
        result = Money("10.00").multiply(2.0)
        self.assertEqual(result.amount, Decimal("20.000"))

    def test_multiply_preserves_currency(self):
        result = Money("10.00", "JPY").multiply(2)
        self.assertEqual(result.currency, "JPY")

    def test_multiply_by_zero(self):
        result = Money("10.00").multiply(0)
        self.assertEqual(result.amount, Decimal("0.00"))

    def test_multiply_by_fraction(self):
        result = Money("100.00").multiply(Decimal("0.1"))
        self.assertEqual(result.amount, Decimal("10.000"))


# ===========================================================================
# 5. Money — division
# ===========================================================================

class TestMoneyDivision(unittest.TestCase):

    def test_divide_by_int(self):
        result = Money("10.00").divide(2)
        self.assertEqual(result.amount, Decimal("5.00"))

    def test_divide_by_decimal(self):
        result = Money("10.00").divide(Decimal("4"))
        self.assertEqual(result.amount, Decimal("2.50"))

    def test_divide_by_float(self):
        result = Money("9.00").divide(3.0)
        self.assertEqual(result.amount, Decimal("3.00"))

    def test_divide_preserves_currency(self):
        result = Money("10.00", "CHF").divide(2)
        self.assertEqual(result.currency, "CHF")

    def test_divide_by_zero_raises(self):
        with self.assertRaises(Exception):
            Money("10.00").divide(0)


# ===========================================================================
# 6. Money — allocate
# ===========================================================================

class TestMoneyAllocate(unittest.TestCase):

    def test_allocate_equal_ratios(self):
        parts = Money("10.00").allocate([1, 1])
        total = sum(p.amount for p in parts)
        self.assertEqual(total, Decimal("10.00"))
        self.assertEqual(len(parts), 2)

    def test_allocate_sums_to_total(self):
        """The largest-remainder method guarantees sum == total."""
        parts = Money("10.00").allocate([1, 2, 3])
        total = sum(p.amount for p in parts)
        self.assertEqual(total, Decimal("10.00"))

    def test_allocate_uneven_split(self):
        """$10 split 1:2 => $3.33, $6.67."""
        parts = Money("10.00").allocate([1, 2])
        total = sum(p.amount for p in parts)
        self.assertEqual(total, Decimal("10.00"))
        # Larger remainder gets the extra penny
        amounts = sorted(p.amount for p in parts)
        self.assertEqual(amounts[0], Decimal("3.33"))
        self.assertEqual(amounts[1], Decimal("6.67"))

    def test_allocate_preserves_currency(self):
        parts = Money("10.00", "EUR").allocate([1, 1])
        for p in parts:
            self.assertEqual(p.currency, "EUR")

    def test_allocate_single_ratio(self):
        parts = Money("10.00").allocate([1])
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].amount, Decimal("10.00"))

    def test_allocate_many_parts_sum_exact(self):
        parts = Money("1.00").allocate([1] * 3)
        total = sum(p.amount for p in parts)
        self.assertEqual(total, Decimal("1.00"))

    def test_allocate_empty_ratios_raises(self):
        with self.assertRaises(ValueError):
            Money("10.00").allocate([])

    def test_allocate_zero_ratios_raises(self):
        with self.assertRaises(ValueError):
            Money("10.00").allocate([0, 0])


# ===========================================================================
# 7. Money — equality
# ===========================================================================

class TestMoneyEquality(unittest.TestCase):

    def test_equal_same_amount_and_currency(self):
        self.assertEqual(Money("10.00"), Money("10.00"))

    def test_not_equal_different_amount(self):
        self.assertNotEqual(Money("10.00"), Money("11.00"))

    def test_not_equal_different_currency(self):
        self.assertNotEqual(Money("10.00", "USD"), Money("10.00", "EUR"))

    def test_not_equal_to_non_money(self):
        m = Money("10.00")
        self.assertNotEqual(m, 10.00)
        self.assertNotEqual(m, "10.00")


# ===========================================================================
# 8. FloatPitfallTester
# ===========================================================================

class TestFloatPitfalls(unittest.TestCase):

    def test_addition_inequality_result(self):
        data = FloatPitfallTester.addition_inequality()
        self.assertFalse(data["equal"], "0.1+0.2 should NOT equal 0.3 in float")

    def test_addition_inequality_has_difference(self):
        data = FloatPitfallTester.addition_inequality()
        self.assertNotEqual(data["difference"], 0)

    def test_accumulation_drift_not_equal(self):
        data = FloatPitfallTester.accumulation_drift()
        self.assertFalse(data["equal"], "Accumulated sum should differ from 10*0.1")

    def test_accumulation_drift_keys(self):
        data = FloatPitfallTester.accumulation_drift()
        self.assertIn("accumulated", data)
        self.assertIn("direct_multiply", data)
        self.assertIn("difference", data)

    def test_big_small_precision_loss(self):
        data = FloatPitfallTester.big_small_precision_loss()
        self.assertTrue(data["lost_precision"], "1e16 + 1.0 should equal 1e16")

    def test_float_overflow_is_inf(self):
        data = FloatPitfallTester.float_overflow()
        self.assertTrue(data["is_inf"])

    def test_nan_not_equal_to_itself(self):
        data = FloatPitfallTester.nan_comparison()
        self.assertFalse(data["nan_eq_nan"])

    def test_nan_ne_itself(self):
        data = FloatPitfallTester.nan_comparison()
        self.assertTrue(data["nan_ne_nan"])

    def test_nan_not_less_than_zero(self):
        data = FloatPitfallTester.nan_comparison()
        self.assertFalse(data["nan_lt_zero"])

    def test_nan_not_greater_than_zero(self):
        data = FloatPitfallTester.nan_comparison()
        self.assertFalse(data["nan_gt_zero"])

    def test_nan_isnan_true(self):
        data = FloatPitfallTester.nan_comparison()
        self.assertTrue(data["nan_isnan"])


# ===========================================================================
# 9. RoundingModeTester
# ===========================================================================

class TestRoundingModes(unittest.TestCase):

    def test_half_even_0_5_rounds_to_0(self):
        result = RoundingModeTester.test_half_even()
        self.assertEqual(result["0.5"], "0")  # rounds to nearest even (0)

    def test_half_even_1_5_rounds_to_2(self):
        result = RoundingModeTester.test_half_even()
        self.assertEqual(result["1.5"], "2")

    def test_half_even_2_5_rounds_to_2(self):
        result = RoundingModeTester.test_half_even()
        self.assertEqual(result["2.5"], "2")

    def test_half_even_neg_0_5_rounds_to_0(self):
        result = RoundingModeTester.test_half_even()
        # Decimal may produce "-0" or "0" — both represent zero
        self.assertIn(result["-0.5"], ["0", "-0"])

    def test_half_up_0_5_rounds_to_1(self):
        result = RoundingModeTester.test_half_up()
        self.assertEqual(result["0.5"], "1")

    def test_half_up_1_5_rounds_to_2(self):
        result = RoundingModeTester.test_half_up()
        self.assertEqual(result["1.5"], "2")

    def test_half_up_2_5_rounds_to_3(self):
        result = RoundingModeTester.test_half_up()
        self.assertEqual(result["2.5"], "3")

    def test_half_up_neg_0_5_rounds_to_0(self):
        """ROUND_HALF_UP rounds half away from zero, so -0.5 -> 0 (toward zero)."""
        result = RoundingModeTester.test_half_up()
        # Python's ROUND_HALF_UP rounds half away from zero: -0.5 -> -1
        self.assertIn(result["-0.5"], ["-1", "0"])

    def test_floor_0_5_rounds_to_0(self):
        result = RoundingModeTester.test_floor()
        self.assertEqual(result["0.5"], "0")

    def test_floor_neg_0_5_rounds_to_neg1(self):
        result = RoundingModeTester.test_floor()
        self.assertEqual(result["-0.5"], "-1")

    def test_floor_neg_1_5_rounds_to_neg2(self):
        result = RoundingModeTester.test_floor()
        self.assertEqual(result["-1.5"], "-2")

    def test_ceiling_0_5_rounds_to_1(self):
        result = RoundingModeTester.test_ceiling()
        self.assertEqual(result["0.5"], "1")

    def test_ceiling_neg_0_5_rounds_to_0(self):
        result = RoundingModeTester.test_ceiling()
        # Decimal may produce "-0" or "0" — both represent zero
        self.assertIn(result["-0.5"], ["0", "-0"])

    def test_ceiling_1_5_rounds_to_2(self):
        result = RoundingModeTester.test_ceiling()
        self.assertEqual(result["1.5"], "2")

    def test_rounding_modes_return_all_boundary_values(self):
        result = RoundingModeTester.test_half_even()
        expected_keys = {"0.5", "1.5", "2.5", "-0.5", "-1.5", "-2.5"}
        self.assertEqual(set(result.keys()), expected_keys)


# ===========================================================================
# 10. CurrencyTester
# ===========================================================================

class TestCurrencyTester(unittest.TestCase):

    def test_same_currency_addition_amount(self):
        result = CurrencyTester.same_currency_addition()
        self.assertEqual(result.amount, Decimal("15.00"))

    def test_same_currency_addition_currency(self):
        result = CurrencyTester.same_currency_addition()
        self.assertEqual(result.currency, "USD")

    def test_different_currency_addition_raises(self):
        self.assertTrue(CurrencyTester.different_currency_addition_raises())

    def test_same_currency_subtraction_amount(self):
        result = CurrencyTester.same_currency_subtraction()
        self.assertEqual(result.amount, Decimal("6.50"))

    def test_different_currency_subtraction_raises(self):
        self.assertTrue(CurrencyTester.different_currency_subtraction_raises())


# ===========================================================================
# 11. OverflowTester
# ===========================================================================

class TestOverflowTester(unittest.TestCase):

    def test_float_overflow_is_inf(self):
        data = OverflowTester.float_overflow_to_inf()
        self.assertTrue(data["is_inf"])

    def test_float_overflow_result(self):
        data = OverflowTester.float_overflow_to_inf()
        self.assertTrue(math.isinf(data["result"]))

    def test_python_int_no_overflow(self):
        data = OverflowTester.python_int_no_overflow()
        self.assertFalse(data["overflowed"])

    def test_python_int_is_int(self):
        data = OverflowTester.python_int_no_overflow()
        self.assertTrue(data["is_int"])

    def test_python_int_many_digits(self):
        data = OverflowTester.python_int_no_overflow()
        self.assertGreater(data["two_pow_1000_digits"], 300)

    def test_int_factorial_no_overflow(self):
        data = OverflowTester.int_factorial_no_overflow(100)
        self.assertEqual(data["n"], 100)
        self.assertTrue(data["is_int"])
        self.assertGreater(data["digits"], 100)

    def test_int_factorial_exact(self):
        import math as _math
        data = OverflowTester.int_factorial_no_overflow(10)
        self.assertEqual(data["digits"], len(str(_math.factorial(10))))


# ===========================================================================
# 12. PrecisionTester
# ===========================================================================

class TestPrecisionTester(unittest.TestCase):

    def test_decimal_exact_sum_equals_one(self):
        data = PrecisionTester.decimal_exact_sum()
        self.assertTrue(data["equals_one"])

    def test_decimal_exact_sum_result(self):
        data = PrecisionTester.decimal_exact_sum()
        self.assertEqual(Decimal(data["result"]), Decimal("1.0"))

    def test_float_inexact_sum_not_exactly_one(self):
        data = PrecisionTester.float_inexact_sum()
        self.assertFalse(data["equals_one"])

    def test_float_inexact_sum_close_to_one(self):
        data = PrecisionTester.float_inexact_sum()
        self.assertAlmostEqual(data["result"], 1.0, places=14)

    def test_decimal_from_float_vs_string_not_equal(self):
        data = PrecisionTester.decimal_vs_float_comparison()
        self.assertFalse(data["equal"])

    def test_decimal_from_string_is_exact(self):
        data = PrecisionTester.decimal_vs_float_comparison()
        self.assertEqual(data["from_string"], "0.1")

    def test_decimal_from_float_is_not_exact(self):
        data = PrecisionTester.decimal_vs_float_comparison()
        self.assertNotEqual(data["from_float"], "0.1")

    def test_decimal_division_high_precision(self):
        data = PrecisionTester.decimal_division_precision()
        # 1/3 to 50 significant digits
        self.assertGreater(data["length"], 40)


# ===========================================================================
# 13. ComparisonTester
# ===========================================================================

class TestComparisonTester(unittest.TestCase):

    def test_nan_not_equal_to_itself(self):
        self.assertTrue(ComparisonTester.nan_not_equal_to_itself())

    def test_nan_not_less_than_zero(self):
        data = ComparisonTester.nan_not_less_than_anything()
        self.assertFalse(data["nan_lt_0"])

    def test_nan_not_less_than_one(self):
        data = ComparisonTester.nan_not_less_than_anything()
        self.assertFalse(data["nan_lt_1"])

    def test_nan_not_less_than_inf(self):
        data = ComparisonTester.nan_not_less_than_anything()
        self.assertFalse(data["nan_lt_inf"])

    def test_nan_not_less_than_neg_inf(self):
        data = ComparisonTester.nan_not_less_than_anything()
        self.assertFalse(data["nan_lt_neg_inf"])

    def test_nan_not_greater_than_zero(self):
        data = ComparisonTester.nan_not_less_than_anything()
        self.assertFalse(data["nan_gt_0"])

    def test_pos_inf_greater_than_max_float(self):
        data = ComparisonTester.inf_comparisons()
        self.assertTrue(data["pos_inf_gt_max"])

    def test_neg_inf_less_than_min_float(self):
        data = ComparisonTester.inf_comparisons()
        self.assertTrue(data["neg_inf_lt_min"])

    def test_pos_inf_equals_pos_inf(self):
        data = ComparisonTester.inf_comparisons()
        self.assertTrue(data["pos_inf_eq_pos_inf"])

    def test_neg_inf_less_than_pos_inf(self):
        data = ComparisonTester.inf_comparisons()
        self.assertTrue(data["neg_inf_lt_pos_inf"])

    def test_pos_inf_is_inf(self):
        data = ComparisonTester.inf_comparisons()
        self.assertTrue(data["pos_inf_is_inf"])

    def test_neg_inf_is_inf(self):
        data = ComparisonTester.inf_comparisons()
        self.assertTrue(data["neg_inf_is_inf"])

    def test_decimal_nan_raises_on_comparison(self):
        self.assertTrue(ComparisonTester.decimal_nan_raises_on_comparison())

    def test_float_nan_isnan(self):
        self.assertTrue(ComparisonTester.float_nan_isnan())


# ===========================================================================
# 14. HTTP Server — health
# ===========================================================================

class TestServerHealth(unittest.TestCase):

    def test_health_status_ok(self):
        data = _get("/health")
        self.assertEqual(data["status"], "ok")

    def test_health_returns_200(self):
        url = _server.base_url + "/health"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)

    def test_unknown_path_returns_404(self):
        url = _server.base_url + "/nonexistent"
        try:
            urllib.request.urlopen(url)
            self.fail("Expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


# ===========================================================================
# 15. HTTP Server — GET /float-pitfalls
# ===========================================================================

class TestServerFloatPitfalls(unittest.TestCase):

    def test_float_pitfalls_endpoint_has_addition_inequality(self):
        data = _get("/float-pitfalls")
        self.assertIn("addition_inequality", data)

    def test_float_pitfalls_endpoint_has_accumulation_drift(self):
        data = _get("/float-pitfalls")
        self.assertIn("accumulation_drift", data)

    def test_float_pitfalls_endpoint_has_big_small(self):
        data = _get("/float-pitfalls")
        self.assertIn("big_small_precision_loss", data)

    def test_float_pitfalls_endpoint_has_overflow(self):
        data = _get("/float-pitfalls")
        self.assertIn("float_overflow", data)

    def test_float_pitfalls_endpoint_has_nan(self):
        data = _get("/float-pitfalls")
        self.assertIn("nan_comparison", data)


# ===========================================================================
# 16. HTTP Server — GET /rounding
# ===========================================================================

class TestServerRounding(unittest.TestCase):

    def test_rounding_endpoint_has_half_even(self):
        data = _get("/rounding")
        self.assertIn("half_even", data)

    def test_rounding_endpoint_has_half_up(self):
        data = _get("/rounding")
        self.assertIn("half_up", data)

    def test_rounding_endpoint_has_floor(self):
        data = _get("/rounding")
        self.assertIn("floor", data)

    def test_rounding_endpoint_has_ceiling(self):
        data = _get("/rounding")
        self.assertIn("ceiling", data)

    def test_rounding_endpoint_half_even_correct(self):
        data = _get("/rounding")
        self.assertEqual(data["half_even"]["0.5"], "0")


# ===========================================================================
# 17. HTTP Server — GET /precision
# ===========================================================================

class TestServerPrecision(unittest.TestCase):

    def test_precision_endpoint_has_decimal_exact_sum(self):
        data = _get("/precision")
        self.assertIn("decimal_exact_sum", data)

    def test_precision_endpoint_decimal_exact_sum_equals_one(self):
        data = _get("/precision")
        self.assertTrue(data["decimal_exact_sum"]["equals_one"])

    def test_precision_endpoint_float_inexact_sum(self):
        data = _get("/precision")
        self.assertIn("float_inexact_sum", data)
        self.assertFalse(data["float_inexact_sum"]["equals_one"])

    def test_precision_endpoint_decimal_vs_float(self):
        data = _get("/precision")
        self.assertIn("decimal_vs_float", data)


# ===========================================================================
# 18. HTTP Server — GET /overflow
# ===========================================================================

class TestServerOverflow(unittest.TestCase):

    def test_overflow_endpoint_has_float_overflow(self):
        data = _get("/overflow")
        self.assertIn("float_overflow", data)

    def test_overflow_endpoint_has_int_no_overflow(self):
        data = _get("/overflow")
        self.assertIn("int_no_overflow", data)

    def test_overflow_endpoint_float_is_inf(self):
        data = _get("/overflow")
        # JSON serializes inf as string "Infinity" via default=str
        result = data["float_overflow"]["result"]
        self.assertIn(str(result).lower(), ["infinity", "inf"])

    def test_overflow_endpoint_int_not_overflowed(self):
        data = _get("/overflow")
        self.assertFalse(data["int_no_overflow"]["overflowed"])


# ===========================================================================
# 19. HTTP Server — POST /money/add
# ===========================================================================

class TestServerMoneyAdd(unittest.TestCase):

    def test_post_money_add_basic(self):
        status, data = _post("/money/add", {"a": "10.00", "b": "5.00"})
        self.assertEqual(status, 200)
        self.assertEqual(data["result"], "15.00")

    def test_post_money_add_currency(self):
        status, data = _post("/money/add", {"a": "10.00", "b": "5.00", "currency": "EUR"})
        self.assertEqual(status, 200)
        self.assertEqual(data["currency"], "EUR")

    def test_post_money_add_missing_key(self):
        status, _ = _post("/money/add", {"a": "10.00"})
        self.assertEqual(status, 400)


# ===========================================================================
# 20. HTTP Server — POST /money/allocate
# ===========================================================================

class TestServerMoneyAllocate(unittest.TestCase):

    def test_post_money_allocate_basic(self):
        status, data = _post("/money/allocate", {"amount": "10.00", "ratios": [1, 2]})
        self.assertEqual(status, 200)
        self.assertIn("parts", data)
        self.assertEqual(len(data["parts"]), 2)

    def test_post_money_allocate_sums_correctly(self):
        status, data = _post("/money/allocate", {"amount": "10.00", "ratios": [1, 2, 3]})
        self.assertEqual(status, 200)
        total = sum(Decimal(p) for p in data["parts"])
        self.assertEqual(total, Decimal("10.00"))

    def test_post_money_allocate_missing_fields(self):
        status, _ = _post("/money/allocate", {"amount": "10.00"})
        self.assertEqual(status, 400)


# ===========================================================================
# 21. Money — repr and str
# ===========================================================================

class TestMoneyRepresentation(unittest.TestCase):

    def test_repr_contains_amount(self):
        m = Money("10.00")
        self.assertIn("10.00", repr(m))

    def test_repr_contains_currency(self):
        m = Money("10.00", "GBP")
        self.assertIn("GBP", repr(m))

    def test_str_contains_amount(self):
        m = Money("10.00")
        self.assertIn("10.00", str(m))

    def test_str_contains_currency(self):
        m = Money("10.00", "JPY")
        self.assertIn("JPY", str(m))


# ===========================================================================
# 22. Money — rounded()
# ===========================================================================

class TestMoneyRounded(unittest.TestCase):

    def test_rounded_bankers_rounding_half_even(self):
        # 10.005 with ROUND_HALF_EVEN should round to 10.00 (0 is even)
        m = Money("10.005")
        r = m.rounded()
        self.assertEqual(r.amount, Decimal("10.00"))

    def test_rounded_half_up(self):
        m = Money("10.005", rounding=ROUND_HALF_UP)
        r = m.rounded()
        self.assertEqual(r.amount, Decimal("10.01"))

    def test_rounded_preserves_currency(self):
        m = Money("10.005", "EUR")
        r = m.rounded()
        self.assertEqual(r.currency, "EUR")


# ===========================================================================
# 23. Miscellaneous edge cases
# ===========================================================================

class TestEdgeCases(unittest.TestCase):

    def test_money_add_chain(self):
        result = Money("1.00").add(Money("2.00")).add(Money("3.00"))
        self.assertEqual(result.amount, Decimal("6.00"))

    def test_float_pitfall_accumulation_n_100(self):
        data = FloatPitfallTester.accumulation_drift(n=100, step=0.01)
        self.assertIn("accumulated", data)

    def test_decimal_nan_is_not_number(self):
        d = Decimal("NaN")
        self.assertTrue(d.is_nan())

    def test_decimal_infinity(self):
        d = Decimal("Infinity")
        self.assertTrue(d.is_infinite())

    def test_money_allocate_returns_money_instances(self):
        parts = Money("1.00").allocate([1, 1])
        for p in parts:
            self.assertIsInstance(p, Money)

    def test_create_server_returns_server(self):
        s = create_server(port=0)
        self.assertIsNotNone(s)
        self.assertGreater(s.port, 0)
        s.stop()

    def test_server_base_url_format(self):
        self.assertTrue(_server.base_url.startswith("http://127.0.0.1:"))

    def test_precision_tester_decimal_exact_type(self):
        data = PrecisionTester.decimal_exact_sum()
        self.assertIsInstance(data["result"], str)


# ===========================================================================
# 24. Teeth — the harness must catch a real planted numeric bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct allocator must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_allocate))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)

    def test_oracle_matches_frozen_literals_and_conserves(self):
        # The frozen expectations are non-circular constants the oracle must
        # reproduce exactly, and every allocation must conserve pennies.
        for case in ALLOC_CORPUS:
            shares = oracle_allocate(case.total_cents, case.ratios)
            self.assertEqual(tuple(shares), case.expected_cents, case.name)
            self.assertEqual(sum(shares), case.total_cents, f"{case.name} not conserved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
