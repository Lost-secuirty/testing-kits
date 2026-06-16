"""Test suite for payments_test_harness."""

import dataclasses
import unittest
from decimal import Decimal

from harnesses._teeth import verify
from harnesses.core.payments_test_harness import (
    CURRENCIES,
    DECLINE_TAXONOMY,
    PAY_CORPUS,
    SCENARIOS,
    TEETH,
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
    _run_probe,
    _run_self_test,
    classify_decline,
    list_scenarios,
    money_sum,
    oracle_processor,
    prove,
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


# ===========================================================================
# Teeth — the harness must catch a real planted payment bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct processor must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_processor))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 4)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(PAY_CORPUS))

    def test_oracle_matches_frozen_literals(self):
        # The frozen probe expectations are non-circular constants the oracle
        # must reproduce exactly.
        for probe in PAY_CORPUS:
            got = _run_probe(oracle_processor, probe)
            self.assertEqual(got["charges"], probe.expected_charges, probe.name)
            self.assertEqual(got["replays"], probe.expected_replays, probe.name)
            self.assertEqual(got["captured_cents"], probe.expected_captured_cents, probe.name)
            self.assertEqual(got["refunded_cents"], probe.expected_refunded_cents, probe.name)
            self.assertEqual(got["state"], probe.expected_state, probe.name)
            self.assertEqual(got["forbidden_rejected"], probe.expected_forbidden_rejected,
                             probe.name)

    def test_noncircular_corpus(self):
        # Corrupting one frozen literal must flip prove(oracle) False -> True,
        # proving prove judges against the baked-in constants, not the oracle.
        self.assertFalse(prove(oracle_processor))
        import harnesses.core.payments_test_harness as h
        original = h.PAY_CORPUS
        try:
            corrupt = dataclasses.replace(original[0], expected_captured_cents=999)
            h.PAY_CORPUS = (corrupt,) + original[1:]
            self.assertTrue(prove(oracle_processor),
                            "prove(oracle) must flip True when a frozen literal is corrupted")
        finally:
            h.PAY_CORPUS = original
        # Restored corpus: oracle is clean again.
        self.assertFalse(prove(oracle_processor))


if __name__ == "__main__":
    unittest.main()
