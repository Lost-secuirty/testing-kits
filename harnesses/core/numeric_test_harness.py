"""
Numeric / Money Precision Test Harness (harness 23 of 36)
Demonstrates and guards against silent numeric bugs.
Pure stdlib, zero external dependencies.
"""

import decimal
import math
import threading
import json
import http.server
import socket
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, InvalidOperation
from typing import List, Optional


# ---------------------------------------------------------------------------
# Money class
# ---------------------------------------------------------------------------

class CurrencyMismatchError(Exception):
    """Raised when operations are attempted on Money with different currencies."""
    pass


class Money:
    """
    Wraps decimal.Decimal with a currency code and configurable rounding.
    Default rounding: ROUND_HALF_EVEN (banker's rounding).
    """

    def __init__(
        self,
        amount,
        currency: str = "USD",
        rounding=ROUND_HALF_EVEN,
        decimal_places: int = 2,
    ):
        self.currency = currency.upper()
        self.rounding = rounding
        self.decimal_places = decimal_places
        if isinstance(amount, float):
            # Convert via string to avoid float imprecision
            self._amount = Decimal(str(amount))
        elif isinstance(amount, Decimal):
            self._amount = amount
        else:
            self._amount = Decimal(amount)

    @property
    def amount(self) -> Decimal:
        return self._amount

    def _quantize(self, value: Decimal) -> Decimal:
        quantizer = Decimal(10) ** -self.decimal_places
        return value.quantize(quantizer, rounding=self.rounding)

    def rounded(self) -> "Money":
        return Money(
            self._quantize(self._amount),
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def _check_currency(self, other: "Money"):
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot operate on {self.currency} and {other.currency}"
            )

    def add(self, other: "Money") -> "Money":
        self._check_currency(other)
        return Money(
            self._amount + other._amount,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def subtract(self, other: "Money") -> "Money":
        self._check_currency(other)
        return Money(
            self._amount - other._amount,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def multiply(self, scalar) -> "Money":
        """Multiply by a scalar (int, float, or Decimal)."""
        if isinstance(scalar, float):
            scalar = Decimal(str(scalar))
        else:
            scalar = Decimal(scalar)
        return Money(
            self._amount * scalar,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def divide(self, scalar) -> "Money":
        """Divide by a scalar (int, float, or Decimal)."""
        if isinstance(scalar, float):
            scalar = Decimal(str(scalar))
        else:
            scalar = Decimal(scalar)
        return Money(
            self._amount / scalar,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def allocate(self, ratios: List) -> List["Money"]:
        """
        Distribute self among ratios using the largest-remainder method.
        Allocations sum to the exact total.
        """
        if not ratios:
            raise ValueError("ratios must be non-empty")
        ratios = [Decimal(str(r)) if isinstance(r, float) else Decimal(r) for r in ratios]
        total_ratio = sum(ratios)
        if total_ratio == 0:
            raise ValueError("ratios must sum to a non-zero value")

        quantizer = Decimal(10) ** -self.decimal_places
        total = self._quantize(self._amount)

        # Compute exact shares
        exact_shares = [total * r / total_ratio for r in ratios]

        # Floor each share to decimal_places
        floored = [v.quantize(quantizer, rounding=ROUND_FLOOR) for v in exact_shares]

        # Remainders (how much was lost by flooring)
        remainders = [exact_shares[i] - floored[i] for i in range(len(ratios))]

        # How much we still need to distribute
        distributed = sum(floored)
        leftover = total - distributed
        # leftover should be a multiple of quantizer
        leftover_units = int((leftover / quantizer).to_integral_value())

        # Sort indices by remainder descending, give one unit to largest remainders
        indices_by_remainder = sorted(range(len(ratios)), key=lambda i: remainders[i], reverse=True)
        result = list(floored)
        for i in range(leftover_units):
            result[indices_by_remainder[i]] += quantizer

        return [
            Money(result[i], self.currency, self.rounding, self.decimal_places)
            for i in range(len(ratios))
        ]

    def __eq__(self, other):
        if isinstance(other, Money):
            return self.currency == other.currency and self._amount == other._amount
        return NotImplemented

    def __repr__(self):
        return f"Money({self._amount}, {self.currency!r})"

    def __str__(self):
        return f"{self.currency} {self._amount}"


# ---------------------------------------------------------------------------
# FloatPitfallTester
# ---------------------------------------------------------------------------

class FloatPitfallTester:
    """Demonstrates common float pitfalls."""

    @staticmethod
    def addition_inequality() -> dict:
        """0.1 + 0.2 != 0.3 in float arithmetic."""
        result = 0.1 + 0.2
        expected = 0.3
        return {
            "result": result,
            "expected": expected,
            "equal": result == expected,
            "difference": result - expected,
        }

    @staticmethod
    def accumulation_drift(n: int = 10, step: float = 0.1) -> dict:
        """Sum 0.1 ten times; result differs from 1.0."""
        total = 0.0
        for _ in range(n):
            total += step
        exact = n * step
        return {
            "accumulated": total,
            "direct_multiply": exact,
            "equal": total == exact,
            "difference": total - exact,
        }

    @staticmethod
    def big_small_precision_loss() -> dict:
        """Adding 1.0 to 1e16 loses the small value."""
        big = 1e16
        result = big + 1.0
        return {
            "big": big,
            "big_plus_one": result,
            "lost_precision": result == big,
        }

    @staticmethod
    def float_overflow() -> dict:
        """Float can overflow to inf."""
        large = 1.7976931348623157e308  # near sys.float_info.max
        overflow = large * 2
        return {
            "large": large,
            "doubled": overflow,
            "is_inf": math.isinf(overflow),
        }

    @staticmethod
    def nan_comparison() -> dict:
        """NaN comparisons are always False (except !=)."""
        nan = float("nan")
        return {
            "nan_eq_nan": nan == nan,
            "nan_ne_nan": nan != nan,
            "nan_lt_zero": nan < 0,
            "nan_gt_zero": nan > 0,
            "nan_isnan": math.isnan(nan),
        }


# ---------------------------------------------------------------------------
# RoundingModeTester
# ---------------------------------------------------------------------------

class RoundingModeTester:
    """Tests various rounding modes with boundary cases."""

    BOUNDARY_VALUES = ["0.5", "1.5", "2.5", "-0.5", "-1.5", "-2.5"]

    @staticmethod
    def round_value(value_str: str, mode) -> Decimal:
        d = Decimal(value_str)
        return d.quantize(Decimal("1"), rounding=mode)

    @classmethod
    def test_half_even(cls) -> dict:
        """Banker's rounding: 0.5 rounds to nearest even."""
        return {
            v: str(cls.round_value(v, ROUND_HALF_EVEN))
            for v in cls.BOUNDARY_VALUES
        }

    @classmethod
    def test_half_up(cls) -> dict:
        """Traditional rounding: 0.5 always rounds up (away from zero)."""
        return {
            v: str(cls.round_value(v, ROUND_HALF_UP))
            for v in cls.BOUNDARY_VALUES
        }

    @classmethod
    def test_floor(cls) -> dict:
        """Floor rounding: always rounds toward negative infinity."""
        return {
            v: str(cls.round_value(v, ROUND_FLOOR))
            for v in cls.BOUNDARY_VALUES
        }

    @classmethod
    def test_ceiling(cls) -> dict:
        """Ceiling rounding: always rounds toward positive infinity."""
        return {
            v: str(cls.round_value(v, ROUND_CEILING))
            for v in cls.BOUNDARY_VALUES
        }


# ---------------------------------------------------------------------------
# CurrencyTester
# ---------------------------------------------------------------------------

class CurrencyTester:
    """Tests currency operations."""

    @staticmethod
    def same_currency_addition() -> Money:
        a = Money("10.00", "USD")
        b = Money("5.00", "USD")
        return a.add(b)

    @staticmethod
    def different_currency_addition_raises() -> bool:
        """Returns True if CurrencyMismatchError is raised."""
        a = Money("10.00", "USD")
        b = Money("10.00", "EUR")
        try:
            a.add(b)
            return False
        except CurrencyMismatchError:
            return True

    @staticmethod
    def same_currency_subtraction() -> Money:
        a = Money("10.00", "USD")
        b = Money("3.50", "USD")
        return a.subtract(b)

    @staticmethod
    def different_currency_subtraction_raises() -> bool:
        a = Money("10.00", "USD")
        b = Money("10.00", "GBP")
        try:
            a.subtract(b)
            return False
        except CurrencyMismatchError:
            return True


# ---------------------------------------------------------------------------
# OverflowTester
# ---------------------------------------------------------------------------

class OverflowTester:
    """Tests numeric overflow behavior."""

    @staticmethod
    def float_overflow_to_inf() -> dict:
        """Float overflows to inf."""
        x = 1.7976931348623157e308
        result = x * 10
        return {
            "input": x,
            "result": result,
            "is_inf": math.isinf(result),
        }

    @staticmethod
    def python_int_no_overflow() -> dict:
        """Python ints are arbitrary precision — no overflow."""
        big = 2 ** 1000
        bigger = big * big
        return {
            "two_pow_1000_digits": len(str(big)),
            "squared_digits": len(str(bigger)),
            "is_int": isinstance(bigger, int),
            "overflowed": False,
        }

    @staticmethod
    def int_factorial_no_overflow(n: int = 100) -> dict:
        """Factorial of 100 — Python handles it exactly."""
        import math as _math
        result = _math.factorial(n)
        return {
            "n": n,
            "digits": len(str(result)),
            "is_int": isinstance(result, int),
        }


# ---------------------------------------------------------------------------
# PrecisionTester
# ---------------------------------------------------------------------------

class PrecisionTester:
    """Compares Decimal exact arithmetic vs float approximations."""

    @staticmethod
    def decimal_exact_sum() -> dict:
        """Sum of 0.1 ten times using Decimal is exact."""
        total = sum(Decimal("0.1") for _ in range(10))
        return {
            "result": str(total),
            "equals_one": total == Decimal("1.0"),
        }

    @staticmethod
    def float_inexact_sum() -> dict:
        """Sum of 0.1 ten times using float is not exactly 1.0."""
        total = sum(0.1 for _ in range(10))
        return {
            "result": total,
            "equals_one": total == 1.0,
        }

    @staticmethod
    def decimal_vs_float_comparison() -> dict:
        """Demonstrate Decimal(0.1) captures float imprecision."""
        from_float = Decimal(0.1)         # captures float imprecision
        from_string = Decimal("0.1")      # exact
        return {
            "from_float": str(from_float),
            "from_string": str(from_string),
            "equal": from_float == from_string,
        }

    @staticmethod
    def decimal_division_precision() -> dict:
        """Decimal division can be set to arbitrary precision."""
        with decimal.localcontext() as ctx:
            ctx.prec = 50
            result = Decimal(1) / Decimal(3)
        return {
            "result": str(result),
            "length": len(str(result).replace("0.", "").replace("-", "")),
        }


# ---------------------------------------------------------------------------
# ComparisonTester
# ---------------------------------------------------------------------------

class ComparisonTester:
    """Tests comparison edge cases with NaN and infinity."""

    @staticmethod
    def nan_not_equal_to_itself() -> bool:
        nan = float("nan")
        return nan != nan

    @staticmethod
    def nan_not_less_than_anything() -> dict:
        nan = float("nan")
        return {
            "nan_lt_0": nan < 0,
            "nan_lt_1": nan < 1,
            "nan_lt_inf": nan < float("inf"),
            "nan_lt_neg_inf": nan < float("-inf"),
            "nan_gt_0": nan > 0,
        }

    @staticmethod
    def inf_comparisons() -> dict:
        pos_inf = float("inf")
        neg_inf = float("-inf")
        return {
            "pos_inf_gt_max": pos_inf > 1.7976931348623157e308,
            "neg_inf_lt_min": neg_inf < -1.7976931348623157e308,
            "pos_inf_eq_pos_inf": pos_inf == pos_inf,
            "neg_inf_lt_pos_inf": neg_inf < pos_inf,
            "pos_inf_is_inf": math.isinf(pos_inf),
            "neg_inf_is_inf": math.isinf(neg_inf),
        }

    @staticmethod
    def decimal_nan_raises_on_comparison() -> bool:
        """Decimal NaN raises InvalidOperation on comparison."""
        d_nan = Decimal("NaN")
        try:
            _ = d_nan < Decimal(0)
            return False
        except InvalidOperation:
            return True

    @staticmethod
    def float_nan_isnan() -> bool:
        return math.isnan(float("nan"))


# ---------------------------------------------------------------------------
# MockNumericHandler — HTTP server
# ---------------------------------------------------------------------------

class MockNumericHandler(http.server.BaseHTTPRequestHandler):
    """Simple HTTP handler that returns numeric computation results as JSON."""

    def log_message(self, format, *args):
        # Suppress default logging
        pass

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/float-pitfalls":
            data = {
                "addition_inequality": FloatPitfallTester.addition_inequality(),
                "accumulation_drift": FloatPitfallTester.accumulation_drift(),
                "big_small_precision_loss": FloatPitfallTester.big_small_precision_loss(),
                "float_overflow": FloatPitfallTester.float_overflow(),
                "nan_comparison": FloatPitfallTester.nan_comparison(),
            }
            self._respond(200, data)
        elif self.path == "/rounding":
            data = {
                "half_even": RoundingModeTester.test_half_even(),
                "half_up": RoundingModeTester.test_half_up(),
                "floor": RoundingModeTester.test_floor(),
                "ceiling": RoundingModeTester.test_ceiling(),
            }
            self._respond(200, data)
        elif self.path == "/precision":
            data = {
                "decimal_exact_sum": PrecisionTester.decimal_exact_sum(),
                "float_inexact_sum": PrecisionTester.float_inexact_sum(),
                "decimal_vs_float": PrecisionTester.decimal_vs_float_comparison(),
            }
            self._respond(200, data)
        elif self.path == "/overflow":
            data = {
                "float_overflow": OverflowTester.float_overflow_to_inf(),
                "int_no_overflow": OverflowTester.python_int_no_overflow(),
            }
            self._respond(200, data)
        else:
            self._respond(404, {"error": "not found", "path": self.path})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        if self.path == "/money/add":
            try:
                a = Money(payload["a"], payload.get("currency", "USD"))
                b = Money(payload["b"], payload.get("currency", "USD"))
                result = a.add(b)
                self._respond(200, {"result": str(result.amount), "currency": result.currency})
            except (KeyError, CurrencyMismatchError, Exception) as e:
                self._respond(400, {"error": str(e)})

        elif self.path == "/money/allocate":
            try:
                m = Money(payload["amount"], payload.get("currency", "USD"))
                ratios = payload["ratios"]
                parts = m.allocate(ratios)
                self._respond(200, {
                    "parts": [str(p.amount) for p in parts],
                    "currency": m.currency,
                })
            except (KeyError, Exception) as e:
                self._respond(400, {"error": str(e)})

        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, data: dict):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class NumericTestServer:
    """Manages the mock HTTP server lifecycle."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        """Start the server and return the actual port."""
        self._server = http.server.HTTPServer((self.host, self.port), MockNumericHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


# ---------------------------------------------------------------------------
# Default server instance (port 0 = dynamic, but default 19090 for docs)
# ---------------------------------------------------------------------------

DEFAULT_PORT = 19090


def create_server(port: int = 0) -> NumericTestServer:
    """Create and start a NumericTestServer on the given port (0 = dynamic)."""
    server = NumericTestServer(port=port)
    server.start()
    return server


if __name__ == "__main__":
    server = NumericTestServer(port=DEFAULT_PORT)
    actual_port = server.start()
    print(f"Numeric Test Harness server running on port {actual_port}")
    print(f"  GET  {server.base_url}/health")
    print(f"  GET  {server.base_url}/float-pitfalls")
    print(f"  GET  {server.base_url}/rounding")
    print(f"  GET  {server.base_url}/precision")
    print(f"  GET  {server.base_url}/overflow")
    print(f"  POST {server.base_url}/money/add")
    print(f"  POST {server.base_url}/money/allocate")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("Server stopped.")
