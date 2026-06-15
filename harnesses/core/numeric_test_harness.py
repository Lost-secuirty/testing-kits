"""
Numeric / Money Precision Test Harness (harness 23 of 36)
Demonstrates and guards against silent numeric bugs.
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import decimal
import http.server
import json
import math

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
)
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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

    def rounded(self) -> Money:
        return Money(
            self._quantize(self._amount),
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def _check_currency(self, other: Money):
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot operate on {self.currency} and {other.currency}"
            )

    def add(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(
            self._amount + other._amount,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def subtract(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(
            self._amount - other._amount,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def multiply(self, scalar) -> Money:
        """Multiply by a scalar (int, float, or Decimal)."""
        scalar = Decimal(str(scalar)) if isinstance(scalar, float) else Decimal(scalar)
        return Money(
            self._amount * scalar,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def divide(self, scalar) -> Money:
        """Divide by a scalar (int, float, or Decimal)."""
        scalar = Decimal(str(scalar)) if isinstance(scalar, float) else Decimal(scalar)
        return Money(
            self._amount / scalar,
            self.currency,
            self.rounding,
            self.decimal_places,
        )

    def allocate(self, ratios: list) -> list[Money]:
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
        """Accumulating 0.1 ten times with plain float addition is not exactly 1.0.

        Uses an explicit ``+=`` loop rather than the built-in ``sum()``: CPython
        3.12+ special-cases ``sum()`` of floats with Neumaier compensated
        summation, which would yield exactly 1.0 and mask the imprecision this
        harness demonstrates. Plain accumulation is inexact on every CPython.
        """
        total = 0.0
        for _ in range(10):
            total += 0.1
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
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Start the server and return the actual port."""
        self._server = http.server.HTTPServer((self.host, self.port), MockNumericHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._server:
            server = self._server
            server.shutdown()
            server.server_close()
            if self._thread:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None

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


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (total_cents, ratios) -> the exact cent allocation
# a correct money-splitter MUST produce.
#
# A numeric/money harness only has teeth if it CATCHES a splitter that loses or
# creates value when distributing an indivisible total. The contract every
# correct allocator must hold (the "conservation of pennies" invariant):
#
#   * the parts sum to EXACTLY the total — not a cent more, not a cent less;
#   * the largest-remainder method assigns each leftover penny to the share with
#     the largest fractional remainder, ties broken by lowest index (stable).
#
# An impl is a callable ``allocate(total_cents: int, ratios: Tuple[int, ...])
# -> List[int]`` returning the integer-cent share for each ratio. prove() judges
# each impl against the corpus's FROZEN LITERAL expected allocations (hand-
# computed from the largest-remainder contract, NEVER read back from the oracle
# at runtime), so the check is non-circular. prove(impl) is True iff any share
# diverges from the frozen literal — i.e. the planted numeric bug is caught.
#
# Pure + deterministic: integer/Decimal arithmetic only, no RNG, no clock, no
# network, no filesystem, no thread timing. The three planted mutants model
# genuine real-world money-rounding defects (per the campaign hint):
#
#   * float_accumulation_drift — accumulates shares with an explicit ``acc += x``
#     float loop (NOT sum(), which CPython 3.12+ compensates) so rounding drift
#     mis-allocates pennies away from the exact total;
#   * truncate_no_remainder — floors every share and silently drops the leftover
#     pennies (the classic "lost penny" / banker's-fraud bug): parts sum to LESS
#     than the total;
#   * half_up_overallocates — rounds each share independently with ROUND_HALF_UP
#     instead of distributing the exact remainder, creating pennies from nothing:
#     parts sum to MORE than the total.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocCase:
    """One frozen allocation case with a literal, hand-computed expectation."""
    name: str
    total_cents: int
    ratios: tuple[int, ...]
    expected_cents: tuple[int, ...]   # the EXACT cent shares a correct splitter yields
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND at least one
# planted mutant gets each one wrong. Every ``expected_cents`` tuple is computed
# by hand from the largest-remainder contract (extra pennies go to the largest
# remainders, ties to the lowest index) — constants, never derived at runtime.
ALLOC_CORPUS: tuple[AllocCase, ...] = (
    # $10.00 split 1:2 -> 333.33.. / 666.66.. ; the leftover penny goes to the
    # larger remainder (the 2-share), so 333 / 667. A truncate-only splitter
    # drops that penny (333/666 sums to 999); a half-up splitter inflates it.
    AllocCase("ten_split_1_2", 1000, (1, 2), (333, 667),
              "uneven thirds: the extra penny must land on the larger remainder"),
    # $10.00 split 1:2:3 -> 166.66/333.33/500 ; floor gives 166/333/500=999,
    # one penny short. Largest remainder is the 1-share (.66) -> it gets +1.
    AllocCase("ten_split_1_2_3", 1000, (1, 2, 3), (167, 333, 500),
              "two-leftover case: the largest remainder gets the penny"),
    # $1.00 split three ways -> 34/33/33. The single leftover penny goes to the
    # first share (all remainders tie at .33, lowest index wins).
    AllocCase("dollar_three_ways", 100, (1, 1, 1), (34, 33, 33),
              "indivisible by 3: first share rounds up, tie broken by index"),
    # 5 cents split three ways -> 2/2/1. Two leftover pennies, tie-broken by
    # index. A drift/truncation bug loses one; half-up creates an extra.
    AllocCase("nickel_three_ways", 5, (1, 1, 1), (2, 2, 1),
              "tiny total: two pennies distributed to the first two shares"),
    # $100.00 split three ways -> 3334/3333/3333. Large total magnifies float
    # accumulation drift while the exact splitter stays penny-perfect.
    AllocCase("hundred_three_ways", 10000, (1, 1, 1), (3334, 3333, 3333),
              "large total: float accumulation drift is most visible here"),
)


# --- ORACLE: reuse the harness's own correct largest-remainder Money.allocate --

def oracle_allocate(total_cents: int, ratios: tuple[int, ...]) -> list[int]:
    """Correct integer-cent allocation, delegating to the harness's own
    Decimal-backed largest-remainder ``Money.allocate``. Returns the cent share
    for each ratio; the shares sum to exactly ``total_cents``."""
    money = Money(Decimal(total_cents) / 100, "USD", decimal_places=2)
    parts = money.allocate(list(ratios))
    return [int((p.amount * 100).to_integral_value()) for p in parts]


# --- Planted buggy twins (each models a real money-rounding defect) ----------

def float_accumulation_drift(total_cents: int, ratios: tuple[int, ...]) -> list[int]:
    """BUG: distributes the total using binary-float arithmetic accumulated in an
    explicit ``acc += share`` loop, then rounds each share to whole cents.

    Float64 cannot represent most cent fractions exactly, and the running ``acc``
    drifts as the sum grows — so the rounded shares can lose (or gain) a penny
    relative to the exact total. An explicit ``+=`` loop is used deliberately:
    CPython 3.12+ special-cases ``sum()`` of floats with Neumaier compensation,
    which would mask this drift on 3.12-3.14; plain accumulation is inexact on
    every supported CPython (3.10-3.14).
    """
    total_ratio = float(sum(ratios))
    total_dollars = total_cents / 100.0
    shares: list[int] = []
    acc = 0.0  # BUG: float accumulator drifts; never reconciled to the total
    for r in ratios:
        # naive proportional share in dollars, accumulated with float drift
        acc += total_dollars * (r / total_ratio)
        # round the *running accumulation* to cents and diff against prior parts
        running_cents = int(round(acc * 100))
        shares.append(running_cents - sum(shares))
    return shares


def truncate_no_remainder(total_cents: int, ratios: tuple[int, ...]) -> list[int]:
    """BUG: floors every share to whole cents and NEVER redistributes the
    leftover pennies — the classic 'lost penny' defect.

    Each share is ``floor(total * ratio / total_ratio)``; the floored shares sum
    to LESS than the total whenever the division is inexact, silently destroying
    money (a real-world rounding-fraud / reconciliation bug).
    """
    total_ratio = sum(ratios)
    shares: list[int] = []
    for r in ratios:
        # BUG: integer floor division drops the fractional cent, never restored
        shares.append((total_cents * r) // total_ratio)
    return shares


def half_up_overallocates(total_cents: int, ratios: tuple[int, ...]) -> list[int]:
    """BUG: rounds each share INDEPENDENTLY with ROUND_HALF_UP instead of
    distributing the exact remainder — creating pennies from nothing.

    Rounding every share half-up in isolation means the rounded shares can sum to
    MORE than the total (e.g. three .33-ish shares each round up), so the splitter
    hands out money that does not exist — a real over-allocation defect.
    """
    total_ratio = Decimal(sum(ratios))
    total = Decimal(total_cents)
    shares: list[int] = []
    for r in ratios:
        exact = total * Decimal(r) / total_ratio
        # BUG: independent half-up rounding, no remainder reconciliation
        shares.append(int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    return shares


def prove(impl: Callable[[int, tuple[int, ...]], list[int]]) -> bool:
    """True iff ``impl`` MIS-ALLOCATES any frozen corpus case (i.e. the bug is
    caught): a share diverges from the frozen literal, the wrong number of shares
    is returned, or the parts fail to sum to the exact total.

    Non-circular + deterministic: every expectation is a literal baked into
    ALLOC_CORPUS, never read from the oracle; integer/Decimal arithmetic only,
    no RNG/clock/network/filesystem. An impl that raises on a corpus case counts
    as caught.
    """
    for case in ALLOC_CORPUS:
        try:
            shares = impl(case.total_cents, case.ratios)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        # 1. shape must match the frozen expectation
        if len(shares) != len(case.expected_cents):
            return True
        # 2. every share must equal the hand-computed literal
        if tuple(shares) != case.expected_cents:
            return True
        # 3. conservation: the parts must sum to exactly the total
        if sum(shares) != case.total_cents:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_allocate,
    mutants=(
        Mutant("float_accumulation_drift", float_accumulation_drift,
               "splits the total with an explicit float `acc += share` loop -> "
               "binary-float rounding drift mis-allocates pennies off the exact total"),
        Mutant("truncate_no_remainder", truncate_no_remainder,
               "floors every share and never redistributes the leftover pennies -> "
               "parts sum to LESS than the total (the classic lost-penny bug)"),
        Mutant("half_up_overallocates", half_up_overallocates,
               "rounds each share independently with ROUND_HALF_UP -> parts sum to "
               "MORE than the total, creating money from nothing"),
    ),
    corpus_size=len(ALLOC_CORPUS),
    kind="oracle_swap",
    notes="a money splitter must conserve pennies: parts sum to exactly the total, "
          "with leftover pennies assigned by largest remainder (ties by index)",
)


def list_scenarios() -> list[str]:
    """Names of the frozen allocation corpus cases (the teeth scenarios)."""
    return [c.name for c in ALLOC_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Exercise the Money invariants that motivate this harness and assert the
    teeth: Decimal-backed arithmetic (no float drift), banker's rounding,
    currency safety, and penny-conserving allocation."""
    report = Report("core/numeric")

    # 1. Core money invariants the harness exists to guard.
    report.add("0.1 + 0.2 == 0.30 (no float drift)", Decimal("0.30"),
               Money(0.1).add(Money(0.2)).rounded().amount)
    report.add("2.5 -> 2 (banker's half-even)", Decimal("2"),
               Money(Decimal("2.5"), decimal_places=0).rounded().amount)
    report.add("3.5 -> 4 (banker's half-even)", Decimal("4"),
               Money(Decimal("3.5"), decimal_places=0).rounded().amount)
    raised = False
    try:
        Money(1, "USD").add(Money(1, "EUR"))
    except CurrencyMismatchError:
        raised = True
    report.record("cross-currency add raises", raised,
                  detail="adding USD to EUR must raise CurrencyMismatchError")

    # 2. The correct oracle reproduces every frozen allocation literal exactly,
    #    and the shares conserve pennies (sum == total).
    for case in ALLOC_CORPUS:
        shares = oracle_allocate(case.total_cents, case.ratios)
        report.add(f"oracle_alloc:{case.name}", list(case.expected_cents), shares,
                   detail=case.note)
        report.add(f"oracle_conserves:{case.name}", case.total_cents, sum(shares),
                   detail="allocated parts must sum to exactly the total")

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Numeric / money correctness harness")
    p.add_argument("--self-test", action="store_true", help="Run built-in checks")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true",
                   help="list the frozen allocation corpus case names")
    p.add_argument("--serve", action="store_true", help="Run the mock HTTP server")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    if args.serve:
        server = NumericTestServer(port=args.port)
        actual_port = server.start()
        print(f"Numeric Test Harness server running on port {actual_port}")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.stop()
            print("Server stopped.")
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    _sys.exit(main())
