"""Test suite for payments_test_harness."""

import unittest
from decimal import Decimal

from harnesses.core.payments_test_harness import (
    CURRENCIES,
    DECLINE_TAXONOMY,
    SCENARIOS,
    ChallengeIsSuccessProcessor,
    CurrencyMismatchError,
    DoubleRefundProcessor,
    FloatProcessor,
    LedgerReport,
    Money,
    OvercaptureProcessor,
    PaymentConflict,
    PaymentError,
    PaymentProcessor,
    PaymentState,
    ReplayChargesTwiceProcessor,
    _run_self_test,
    classify_decline,
    list_scenarios,
    money_sum,
)

USD = CURRENCIES["USD"]
JPY = CURRENCIES["JPY"]
BHD = CURRENCIES["BHD"]


class TestMoney(unittest.TestCase):
    def test_quantizes_to_minor_units(self):
        self.assertEqual(Money("1.005", USD).amount, Decimal("1.00"))  # banker's rounding
        self.assertEqual(Money(1000, JPY).amount, Decimal("1000"))
        self.assertEqual(Money("1.234", BHD).amount, Decimal("1.234"))

    def test_add_sub_and_compare(self):
        self.assertEqual(Money(40, USD) + Money(60, USD), Money(100, USD))
        self.assertTrue(Money(50, USD) < Money(60, USD))

    def test_currency_mismatch_raises(self):
        with self.assertRaises(CurrencyMismatchError):
            Money(1, USD) + Money(1, BHD)

    def test_allocate_sums_exactly(self):
        parts = Money(100, USD).allocate([1, 1, 1])
        self.assertEqual(money_sum(parts, USD), Money(100, USD))
        self.assertEqual([p.amount for p in parts],
                         [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")])

    def test_allocate_uneven_ratio_sums_exactly(self):
        parts = Money("10.00", USD).allocate([1, 2, 3])
        self.assertEqual(money_sum(parts, USD), Money("10.00", USD))


class TestDeclineTaxonomy(unittest.TestCase):
    def test_every_code_classifies(self):
        for code, dc in DECLINE_TAXONOMY.items():
            self.assertEqual(classify_decline(code), dc)
            self.assertIn(dc.category, ("soft", "hard", "fraud"))
            self.assertIsInstance(dc.retryable, bool)

    def test_unknown_code_raises(self):
        with self.assertRaises(KeyError):
            classify_decline("definitely_not_a_code")


class TestPaymentFSM(unittest.TestCase):
    def test_full_capture(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        p.capture(cid, Money(100, USD))
        self.assertEqual(p.state(cid), PaymentState.CAPTURED)

    def test_overcapture_raises(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        with self.assertRaises(PaymentError):
            p.capture(cid, Money(120, USD))

    def test_void_after_capture_raises(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        p.capture(cid, Money(100, USD))
        with self.assertRaises(PaymentError):
            p.void(cid)

    def test_refund_before_capture_raises(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        with self.assertRaises(PaymentError):
            p.refund(cid, Money(10, USD))

    def test_3ds_pending_blocks_capture(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        p.challenge_3ds(cid)
        with self.assertRaises(PaymentError):
            p.capture(cid, Money(100, USD))

    def test_3ds_fail_declines(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        p.challenge_3ds(cid)
        p.resolve_3ds(cid, success=False)
        self.assertEqual(p.state(cid), PaymentState.DECLINED)


class TestOracleAccounting(unittest.TestCase):
    def test_reconciles_after_partial_lifecycle(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        p.capture(cid, Money(60, USD))
        p.refund(cid, Money(20, USD))
        led = p.ledger(cid)
        self.assertTrue(led.reconciles)
        self.assertEqual(led.net, Money(40, USD))

    def test_currency_mismatch_refund(self):
        p = PaymentProcessor()
        cid = p.authorize(Money(100, USD))
        p.capture(cid, Money(100, USD))
        with self.assertRaises(CurrencyMismatchError):
            p.refund(cid, Money(10, BHD))


class TestIdempotentReplay(unittest.TestCase):
    def test_replay_same_amount(self):
        p = PaymentProcessor()
        c1 = p.authorize(Money(100, USD), idempotency_key="k")
        c2 = p.authorize(Money(100, USD), idempotency_key="k")
        self.assertEqual(c1, c2)
        self.assertEqual(p.idempotent_replays, 1)
        self.assertEqual(len(p.charges), 1)

    def test_amount_mismatch_conflict(self):
        p = PaymentProcessor()
        p.authorize(Money(100, USD), idempotency_key="k")
        with self.assertRaises(PaymentConflict):
            p.authorize(Money(200, USD), idempotency_key="k")
        self.assertEqual(p.conflicts, 1)


class TestBuggyImplsCaught(unittest.TestCase):
    def test_overcapture_processor(self):
        p = OvercaptureProcessor()
        cid = p.authorize(Money(100, USD))
        p.capture(cid, Money(120, USD))
        self.assertTrue(p.ledger(cid).over_captured)

    def test_double_refund_processor(self):
        p = DoubleRefundProcessor()
        cid = p.authorize(Money(100, USD))
        p.capture(cid, Money(100, USD))
        p.refund(cid, Money(70, USD))
        p.refund(cid, Money(70, USD))
        self.assertTrue(p.ledger(cid).over_refunded)

    def test_float_processor_drifts(self):
        fp = FloatProcessor()
        fp.authorize(0.30)
        for _ in range(3):
            fp.capture(0.10)
        self.assertTrue(fp.over_captured)

    def test_replay_charges_twice(self):
        p = ReplayChargesTwiceProcessor()
        p.authorize(Money(100, USD), idempotency_key="k")
        p.authorize(Money(100, USD), idempotency_key="k")
        self.assertEqual(len(p.charges), 2)

    def test_challenge_is_success_captures_unverified(self):
        p = ChallengeIsSuccessProcessor()
        cid = p.authorize(Money(100, USD))
        p.challenge_3ds(cid)
        p.capture(cid, Money(100, USD))
        self.assertEqual(p.state(cid), PaymentState.CAPTURED)


class TestLedgerReport(unittest.TestCase):
    def test_reconcile_flags(self):
        ok = LedgerReport(Money(100, USD), Money(100, USD), Money(0, USD), 2, 0, 0)
        self.assertTrue(ok.reconciles)
        over_cap = LedgerReport(Money(100, USD), Money(120, USD), Money(0, USD), 2, 0, 0)
        self.assertTrue(over_cap.over_captured)
        self.assertFalse(over_cap.reconciles)
        over_ref = LedgerReport(Money(100, USD), Money(100, USD), Money(140, USD), 2, 0, 0)
        self.assertTrue(over_ref.over_refunded)


class TestSelfTest(unittest.TestCase):
    def test_at_least_22_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 22)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
