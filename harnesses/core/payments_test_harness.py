#!/usr/bin/env python3
"""
payments_test_harness.py — Checkout accounting: capture/refund, idempotent replay, 3DS, precision.
===================================================================================================

Pure-stdlib. Zero external dependencies.

Checkout code loses money in characteristic ways: capturing more than was
authorized, refunding more than was captured, replaying an idempotency key into
a double charge, float drift on currency, mishandling minor-unit precision
(JPY has 0 decimals, BHD has 3), or treating a 3DS challenge as a success. This
harness composes a Decimal `Money` (with exact remainder allocation), a payment
state machine with money guards, and an idempotency-key replay contract into one
oracle, then proves five buggy processors each break a money invariant.

Self-contained per repo convention (a local `Money` / FSM, not an import).
Port 19300 reserved; oracle runs in-process.

Usage:
  python harnesses/core/payments_test_harness.py --self-test
  python harnesses/core/payments_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


class CurrencyMismatchError(Exception):
    pass


@dataclass(frozen=True)
class Currency:
    code: str
    minor_units: int


CURRENCIES: dict[str, Currency] = {
    "USD": Currency("USD", 2),
    "JPY": Currency("JPY", 0),
    "BHD": Currency("BHD", 3),
}


class Money:
    """Decimal money quantized to a currency's minor units (banker's rounding)."""

    def __init__(self, amount, currency: Currency):
        self.currency = currency
        self._q = Decimal(10) ** -currency.minor_units
        self.amount = Decimal(str(amount)).quantize(self._q, rounding=ROUND_HALF_EVEN)

    def _check(self, other: "Money") -> None:
        if self.currency.code != other.currency.code:
            raise CurrencyMismatchError(f"{self.currency.code} vs {other.currency.code}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Money)
                and self.currency.code == other.currency.code
                and self.amount == other.amount)

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount >= other.amount

    def __repr__(self) -> str:
        return f"Money({self.amount}, {self.currency.code})"

    def is_zero(self) -> bool:
        return self.amount == 0

    def allocate(self, ratios: list[int]) -> list["Money"]:
        """Split into parts by integer ratios; distribute the remainder so the
        parts sum back to exactly this amount (largest-remainder method)."""
        total = self.amount
        weights = [Decimal(r) for r in ratios]
        wsum = sum(weights)
        raw = [total * w / wsum for w in weights]
        floored = [r.quantize(self._q, rounding=ROUND_FLOOR) for r in raw]
        remainder = total - sum(floored)
        units = int((remainder / self._q).to_integral_value())
        order = sorted(range(len(raw)), key=lambda i: raw[i] - floored[i], reverse=True)
        for k in range(units):
            floored[order[k % len(floored)]] += self._q
        return [Money(f, self.currency) for f in floored]


def money_sum(parts: list[Money], currency: Currency) -> Money:
    total = Money(0, currency)
    for p in parts:
        total = total + p
    return total


# ---------------------------------------------------------------------------
# Decline taxonomy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeclineCode:
    code: str
    category: str  # soft | hard | fraud
    retryable: bool


DECLINE_TAXONOMY: dict[str, DeclineCode] = {
    "insufficient_funds": DeclineCode("insufficient_funds", "soft", True),
    "do_not_honor": DeclineCode("do_not_honor", "soft", True),
    "expired_card": DeclineCode("expired_card", "hard", False),
    "invalid_card": DeclineCode("invalid_card", "hard", False),
    "stolen_card": DeclineCode("stolen_card", "fraud", False),
    "lost_card": DeclineCode("lost_card", "fraud", False),
    "pickup_card": DeclineCode("pickup_card", "fraud", False),
}


def classify_decline(code: str) -> DeclineCode:
    if code not in DECLINE_TAXONOMY:
        raise KeyError(f"unknown decline code: {code}")
    return DECLINE_TAXONOMY[code]


# ---------------------------------------------------------------------------
# Payment processor (oracle)
# ---------------------------------------------------------------------------


class PaymentState(Enum):
    AUTHORIZED = "authorized"
    PARTIALLY_CAPTURED = "partially_captured"
    CAPTURED = "captured"
    VOIDED = "voided"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    DECLINED = "declined"
    CHALLENGE_PENDING = "challenge_pending"


class PaymentError(Exception):
    pass


class PaymentConflict(Exception):
    pass


@dataclass
class LedgerReport:
    authorized: Money
    captured: Money
    refunded: Money
    transitions: int
    idempotent_replays: int
    conflicts: int

    @property
    def net(self) -> Money:
        return self.captured - self.refunded

    @property
    def over_captured(self) -> bool:
        return self.captured > self.authorized

    @property
    def over_refunded(self) -> bool:
        return self.refunded > self.captured

    @property
    def reconciles(self) -> bool:
        return not self.over_captured and not self.over_refunded


class PaymentProcessor:
    CAPTURE_BLOCKED = frozenset({
        PaymentState.VOIDED, PaymentState.DECLINED, PaymentState.REFUNDED,
        PaymentState.CHALLENGE_PENDING,
    })

    def __init__(self):
        self.charges: dict[str, dict] = {}
        self._idem: dict[str, tuple[str, str, str]] = {}
        self._next = 1
        self.idempotent_replays = 0
        self.conflicts = 0
        self.transitions: list[tuple[str, str, PaymentState]] = []

    def authorize(self, amount: Money, idempotency_key: Optional[str] = None) -> str:
        if idempotency_key is not None and idempotency_key in self._idem:
            cid, amt, cur = self._idem[idempotency_key]
            if amt != str(amount.amount) or cur != amount.currency.code:
                self.conflicts += 1
                raise PaymentConflict("idempotency key reused with a different amount")
            self.idempotent_replays += 1
            return cid
        cid = f"ch_{self._next}"
        self._next += 1
        zero = Money(0, amount.currency)
        self.charges[cid] = {
            "state": PaymentState.AUTHORIZED, "currency": amount.currency,
            "authorized": amount, "captured": zero, "refunded": zero,
        }
        self.transitions.append((cid, "authorize", PaymentState.AUTHORIZED))
        if idempotency_key is not None:
            self._idem[idempotency_key] = (cid, str(amount.amount), amount.currency.code)
        return cid

    def _guard_capture(self, new_captured: Money, authorized: Money) -> None:
        if new_captured > authorized:
            raise PaymentError("overcapture: captured would exceed authorized")

    def capture(self, cid: str, amount: Money) -> None:
        c = self.charges[cid]
        if c["state"] in self.CAPTURE_BLOCKED:
            raise PaymentError(f"cannot capture in state {c['state'].value}")
        self._check_currency(c, amount)
        new_captured = c["captured"] + amount
        self._guard_capture(new_captured, c["authorized"])
        c["captured"] = new_captured
        c["state"] = (PaymentState.CAPTURED if new_captured == c["authorized"]
                      else PaymentState.PARTIALLY_CAPTURED)
        self.transitions.append((cid, "capture", c["state"]))

    def void(self, cid: str) -> None:
        c = self.charges[cid]
        if c["state"] != PaymentState.AUTHORIZED:
            raise PaymentError("can only void an authorized, uncaptured charge")
        c["state"] = PaymentState.VOIDED
        self.transitions.append((cid, "void", PaymentState.VOIDED))

    def _guard_refund(self, new_refunded: Money, captured: Money) -> None:
        if new_refunded > captured:
            raise PaymentError("over-refund: refunded would exceed captured")

    def refund(self, cid: str, amount: Money) -> None:
        c = self.charges[cid]
        if c["state"] not in (PaymentState.CAPTURED, PaymentState.PARTIALLY_CAPTURED,
                              PaymentState.PARTIALLY_REFUNDED):
            raise PaymentError("can only refund a captured charge")
        self._check_currency(c, amount)
        new_refunded = c["refunded"] + amount
        self._guard_refund(new_refunded, c["captured"])
        c["refunded"] = new_refunded
        c["state"] = (PaymentState.REFUNDED if new_refunded == c["captured"]
                      else PaymentState.PARTIALLY_REFUNDED)
        self.transitions.append((cid, "refund", c["state"]))

    def challenge_3ds(self, cid: str) -> None:
        c = self.charges[cid]
        if c["state"] != PaymentState.AUTHORIZED:
            raise PaymentError("can only challenge an authorized charge")
        c["state"] = PaymentState.CHALLENGE_PENDING
        self.transitions.append((cid, "challenge_3ds", PaymentState.CHALLENGE_PENDING))

    def resolve_3ds(self, cid: str, success: bool) -> None:
        c = self.charges[cid]
        if c["state"] != PaymentState.CHALLENGE_PENDING:
            raise PaymentError("no challenge pending")
        c["state"] = PaymentState.AUTHORIZED if success else PaymentState.DECLINED
        self.transitions.append((cid, "resolve_3ds", c["state"]))

    def _check_currency(self, c: dict, amount: Money) -> None:
        if c["currency"].code != amount.currency.code:
            raise CurrencyMismatchError(f"{c['currency'].code} vs {amount.currency.code}")

    def state(self, cid: str) -> PaymentState:
        return self.charges[cid]["state"]

    def ledger(self, cid: str) -> LedgerReport:
        c = self.charges[cid]
        return LedgerReport(
            authorized=c["authorized"], captured=c["captured"], refunded=c["refunded"],
            transitions=sum(1 for t in self.transitions if t[0] == cid),
            idempotent_replays=self.idempotent_replays, conflicts=self.conflicts,
        )


# ---------------------------------------------------------------------------
# Buggy processors
# ---------------------------------------------------------------------------


class OvercaptureProcessor(PaymentProcessor):
    def _guard_capture(self, new_captured: Money, authorized: Money) -> None:
        return  # bug: no Σcaptures <= authorized check


class DoubleRefundProcessor(PaymentProcessor):
    def _guard_refund(self, new_refunded: Money, captured: Money) -> None:
        return  # bug: no Σrefunds <= captured check


class ReplayChargesTwiceProcessor(PaymentProcessor):
    def authorize(self, amount: Money, idempotency_key: Optional[str] = None) -> str:
        return super().authorize(amount, idempotency_key=None)  # bug: ignores the key


class ChallengeIsSuccessProcessor(PaymentProcessor):
    CAPTURE_BLOCKED = frozenset({
        PaymentState.VOIDED, PaymentState.DECLINED, PaymentState.REFUNDED,
    })  # bug: CHALLENGE_PENDING omitted → captures an unverified 3DS charge


class FloatProcessor:
    """Tracks amounts as floats → accumulation drift breaks reconciliation."""

    def __init__(self):
        self.authorized = 0.0
        self.captured = 0.0

    def authorize(self, amount: float) -> None:
        self.authorized = amount

    def capture(self, amount: float) -> None:
        self.captured += amount  # bug: float accumulation

    @property
    def over_captured(self) -> bool:
        return self.captured > self.authorized


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

USD = CURRENCIES["USD"]
JPY = CURRENCIES["JPY"]
BHD = CURRENCIES["BHD"]


@dataclass
class PayCheck:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> PayCheck:
    return PayCheck(name, bool(cond), detail)


def _raises(fn: Callable[[], object], exc: type) -> bool:
    try:
        fn()
        return False
    except exc:
        return True


def s_authorize_then_full_capture() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(100, USD))
    return _chk("authorize_then_full_capture",
                p.state(cid) == PaymentState.CAPTURED and p.ledger(cid).reconciles, "")


def s_authorize_then_partial_capture() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(40, USD))
    return _chk("authorize_then_partial_capture",
                p.state(cid) == PaymentState.PARTIALLY_CAPTURED
                and p.charges[cid]["captured"] == Money(40, USD), "")


def s_partial_capture_then_capture_remainder() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(40, USD))
    p.capture(cid, Money(60, USD))
    return _chk("partial_capture_then_capture_remainder",
                p.state(cid) == PaymentState.CAPTURED, "")


def s_overcapture_rejected() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    return _chk("overcapture_rejected",
                _raises(lambda: p.capture(cid, Money(120, USD)), PaymentError), "")


def s_authorize_then_void() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.void(cid)
    return _chk("authorize_then_void", p.state(cid) == PaymentState.VOIDED, "")


def s_void_after_capture_rejected() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(100, USD))
    return _chk("void_after_capture_rejected", _raises(lambda: p.void(cid), PaymentError), "")


def s_full_refund_after_full_capture() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(100, USD))
    p.refund(cid, Money(100, USD))
    return _chk("full_refund_after_full_capture", p.state(cid) == PaymentState.REFUNDED, "")


def s_multi_partial_refund_sums_to_capture() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(100, USD))
    p.refund(cid, Money(30, USD))
    p.refund(cid, Money(70, USD))
    return _chk("multi_partial_refund_sums_to_capture",
                p.state(cid) == PaymentState.REFUNDED
                and p.charges[cid]["refunded"] == Money(100, USD), "")


def s_refund_exceeds_capture_rejected() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(50, USD))
    return _chk("refund_exceeds_capture_rejected",
                _raises(lambda: p.refund(cid, Money(60, USD)), PaymentError), "")


def s_refund_before_capture_rejected() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    return _chk("refund_before_capture_rejected",
                _raises(lambda: p.refund(cid, Money(10, USD)), PaymentError), "")


def s_idempotent_replay_same_amount_no_double_charge() -> PayCheck:
    p = PaymentProcessor()
    c1 = p.authorize(Money(100, USD), idempotency_key="k1")
    c2 = p.authorize(Money(100, USD), idempotency_key="k1")
    return _chk("idempotent_replay_same_amount_no_double_charge",
                c1 == c2 and p.idempotent_replays == 1 and len(p.charges) == 1,
                f"replays={p.idempotent_replays} charges={len(p.charges)}")


def s_idempotency_key_amount_mismatch_conflict() -> PayCheck:
    p = PaymentProcessor()
    p.authorize(Money(100, USD), idempotency_key="k1")
    raised = _raises(lambda: p.authorize(Money(200, USD), idempotency_key="k1"),
                     PaymentConflict)
    return _chk("idempotency_key_amount_mismatch_conflict", raised and p.conflicts == 1, "")


def s_decline_insufficient_funds_soft_retryable() -> PayCheck:
    d = classify_decline("insufficient_funds")
    return _chk("decline_insufficient_funds_soft_retryable",
                d.category == "soft" and d.retryable, "")


def s_decline_stolen_card_fraud_not_retryable() -> PayCheck:
    d = classify_decline("stolen_card")
    return _chk("decline_stolen_card_fraud_not_retryable",
                d.category == "fraud" and not d.retryable, "")


def s_decline_expired_card_hard() -> PayCheck:
    d = classify_decline("expired_card")
    return _chk("decline_expired_card_hard", d.category == "hard" and not d.retryable, "")


def s_3ds_challenge_pending_not_captured() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.challenge_3ds(cid)
    return _chk("3ds_challenge_pending_not_captured",
                _raises(lambda: p.capture(cid, Money(100, USD)), PaymentError), "")


def s_3ds_resolve_success_then_capture() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.challenge_3ds(cid)
    p.resolve_3ds(cid, success=True)
    p.capture(cid, Money(100, USD))
    return _chk("3ds_resolve_success_then_capture", p.state(cid) == PaymentState.CAPTURED, "")


def s_3ds_resolve_fail_declines() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.challenge_3ds(cid)
    p.resolve_3ds(cid, success=False)
    return _chk("3ds_resolve_fail_declines", p.state(cid) == PaymentState.DECLINED, "")


def s_jpy_zero_decimal_precision() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(1000, JPY))
    p.capture(cid, Money(1000, JPY))
    amt = p.charges[cid]["captured"].amount
    return _chk("jpy_zero_decimal_precision",
                amt == Decimal("1000") and amt.as_tuple().exponent == 0, f"{amt}")


def s_bhd_three_decimal_precision() -> PayCheck:
    m = Money("1.234", BHD)
    return _chk("bhd_three_decimal_precision",
                m.amount == Decimal("1.234") and -m.amount.as_tuple().exponent == 3, f"{m}")


def s_currency_mismatch_refund_rejected() -> PayCheck:
    p = PaymentProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(100, USD))
    return _chk("currency_mismatch_refund_rejected",
                _raises(lambda: p.refund(cid, Money(10, BHD)), CurrencyMismatchError), "")


def s_allocate_split_capture_sums_exact() -> PayCheck:
    parts = Money(100, USD).allocate([1, 1, 1])
    total = money_sum(parts, USD)
    amounts = [p.amount for p in parts]
    return _chk("allocate_split_capture_sums_exact",
                total == Money(100, USD)
                and amounts == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")],
                f"{amounts}")


def s_overcapture_processor_detected() -> PayCheck:
    p = OvercaptureProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(120, USD))
    return _chk("overcapture_processor_detected", p.ledger(cid).over_captured, "")


def s_double_refund_processor_detected() -> PayCheck:
    p = DoubleRefundProcessor()
    cid = p.authorize(Money(100, USD))
    p.capture(cid, Money(100, USD))
    p.refund(cid, Money(70, USD))   # → PARTIALLY_REFUNDED (still refundable)
    p.refund(cid, Money(70, USD))   # bug: no Σrefunds<=captured guard → 140 > 100
    return _chk("double_refund_processor_detected", p.ledger(cid).over_refunded, "")


def s_float_processor_breaks_reconciliation() -> PayCheck:
    fp = FloatProcessor()
    fp.authorize(0.30)
    fp.capture(0.10)
    fp.capture(0.10)
    fp.capture(0.10)
    oracle = PaymentProcessor()
    cid = oracle.authorize(Money("0.30", USD))
    for _ in range(3):
        oracle.capture(cid, Money("0.10", USD))
    return _chk("float_processor_breaks_reconciliation",
                fp.over_captured and oracle.ledger(cid).reconciles,
                f"float_captured={fp.captured!r}")


def s_replay_charges_twice_processor_detected() -> PayCheck:
    p = ReplayChargesTwiceProcessor()
    p.authorize(Money(100, USD), idempotency_key="k1")
    p.authorize(Money(100, USD), idempotency_key="k1")
    return _chk("replay_charges_twice_processor_detected",
                len(p.charges) == 2 and p.idempotent_replays == 0, f"charges={len(p.charges)}")


def s_challenge_is_success_processor_detected() -> PayCheck:
    bug = ChallengeIsSuccessProcessor()
    cid = bug.authorize(Money(100, USD))
    bug.challenge_3ds(cid)
    bug.capture(cid, Money(100, USD))  # bug allows capture of unverified charge
    oracle = PaymentProcessor()
    ocid = oracle.authorize(Money(100, USD))
    oracle.challenge_3ds(ocid)
    oracle_blocks = _raises(lambda: oracle.capture(ocid, Money(100, USD)), PaymentError)
    return _chk("challenge_is_success_processor_detected",
                bug.state(cid) == PaymentState.CAPTURED and oracle_blocks, "")


SCENARIOS: dict[str, Callable[[], PayCheck]] = {
    f.__name__[2:]: f
    for f in [
        s_authorize_then_full_capture,
        s_authorize_then_partial_capture,
        s_partial_capture_then_capture_remainder,
        s_overcapture_rejected,
        s_authorize_then_void,
        s_void_after_capture_rejected,
        s_full_refund_after_full_capture,
        s_multi_partial_refund_sums_to_capture,
        s_refund_exceeds_capture_rejected,
        s_refund_before_capture_rejected,
        s_idempotent_replay_same_amount_no_double_charge,
        s_idempotency_key_amount_mismatch_conflict,
        s_decline_insufficient_funds_soft_retryable,
        s_decline_stolen_card_fraud_not_retryable,
        s_decline_expired_card_hard,
        s_3ds_challenge_pending_not_captured,
        s_3ds_resolve_success_then_capture,
        s_3ds_resolve_fail_declines,
        s_jpy_zero_decimal_precision,
        s_bhd_three_decimal_precision,
        s_currency_mismatch_refund_rejected,
        s_allocate_split_capture_sums_exact,
        s_overcapture_processor_detected,
        s_double_refund_processor_detected,
        s_float_processor_breaks_reconciliation,
        s_replay_charges_twice_processor_detected,
        s_challenge_is_success_processor_detected,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    failures = [r for r in results if not r.passed]
    for r in results:
        if verbose or not r.passed:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:48s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Payments/checkout accounting harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
