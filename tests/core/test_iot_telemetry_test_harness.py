"""Test suite for iot_telemetry_test_harness."""

import dataclasses
import unittest

from harnesses._teeth import verify
from harnesses.core.iot_telemetry_test_harness import (
    AGG_EXPECTED,
    READINGS,
    SCENARIOS,
    SESSIONS,
    STREAM,
    TEETH,
    IotConfig,
    IotReport,
    Message,
    _run_self_test,
    ingest,
    list_scenarios,
    on_disconnect,
    on_disconnect_no_will,
    oracle_aggregate,
    prove,
    reconnect,
    reconnect_nonpersistent,
)


class TestCorpusShape(unittest.TestCase):
    def test_stream_size(self):
        self.assertGreaterEqual(len(STREAM), 18)

    def test_sessions_present(self):
        self.assertGreaterEqual(len(SESSIONS), 3)
        self.assertTrue(any(s.persistent for s in SESSIONS))
        self.assertTrue(any(not s.persistent for s in SESSIONS))

    def test_qos_mix(self):
        qoss = {m.qos for m in STREAM}
        self.assertEqual(qoss, {0, 1, 2})


class TestOracleInvariants(unittest.TestCase):
    def test_meets_invariants(self):
        cfg = IotConfig()
        r = ingest(STREAM, config=cfg).report
        self.assertTrue(r.meets_invariants(cfg))
        self.assertTrue(r.strictly_ordered)
        self.assertEqual(r.duplicates_delivered, 0)
        self.assertEqual(r.qos2_dupes, 0)

    def test_skew_flag_and_reject(self):
        res = ingest(STREAM)
        self.assertIn("m10", res.flagged_skew)   # skew 200 -> flag
        self.assertIn("m11", res.rejected)        # skew huge -> reject

    def test_server_time_is_canonical(self):
        res = ingest(STREAM)
        rec = next(r for r in res.accepted if r.mid == "m10")
        self.assertEqual(rec.ts, 1100)            # server arrival, not device_ts 1300

    def test_late_event_dropped_fresh_kept(self):
        res = ingest(STREAM)
        self.assertIn("m17", res.late_dropped)
        self.assertIn("m18", {r.mid for r in res.accepted})

    def test_retained_latest_only(self):
        res = ingest(STREAM)
        self.assertEqual(res.report.retained_kept, 1)
        self.assertEqual([r.mid for r in res.retained if r.topic == "config/r"], ["m14"])


class TestBuggyIngestersCaught(unittest.TestCase):
    def test_qos2_at_least_once(self):
        self.assertGreaterEqual(ingest(STREAM, dedupe_qos2=False).report.qos2_dupes, 1)

    def test_no_dedupe(self):
        self.assertGreaterEqual(
            ingest(STREAM, dedupe_qos1=False, dedupe_qos2=False)
            .report.duplicates_delivered, 1)

    def test_no_resequence(self):
        self.assertGreaterEqual(ingest(STREAM, resequence=False).report.out_of_order_pairs, 1)

    def test_clock_truster(self):
        r = ingest(STREAM, trust_device_clock=True).report
        self.assertEqual(r.skew_flagged, 0)
        self.assertEqual(r.skew_rejected, 0)

    def test_no_watermark(self):
        self.assertEqual(ingest(STREAM, watermark=False).report.late_dropped, 0)

    def test_retain_all(self):
        self.assertGreater(ingest(STREAM, retain_latest=False).report.retained_kept, 1)

    def test_non_persistent_session(self):
        self.assertEqual(len(reconnect(SESSIONS[0])), 2)
        self.assertEqual(len(reconnect_nonpersistent(SESSIONS[0])), 0)

    def test_no_will(self):
        self.assertEqual(len(on_disconnect(SESSIONS[2], True)), 1)
        self.assertEqual(len(on_disconnect_no_will(SESSIONS[2], True)), 0)


class TestSessionLifecycle(unittest.TestCase):
    def test_persistent_replays(self):
        self.assertEqual(len(reconnect(SESSIONS[0])), 2)

    def test_nonpersistent_drops(self):
        self.assertEqual(len(reconnect(SESSIONS[1])), 0)

    def test_will_only_on_abnormal(self):
        self.assertEqual(len(on_disconnect(SESSIONS[2], True)), 1)
        self.assertEqual(len(on_disconnect(SESSIONS[2], False)), 0)


class TestReportLogic(unittest.TestCase):
    def test_meets_invariants_predicate(self):
        cfg = IotConfig()
        clean = IotReport(19, 14, 0, 0, 1, 1, 0, 1, 1)
        self.assertTrue(clean.meets_invariants(cfg))
        self.assertFalse(IotReport(19, 14, 1, 0, 1, 1, 0, 1, 1).meets_invariants(cfg))
        self.assertFalse(IotReport(19, 14, 0, 2, 1, 1, 0, 1, 1).meets_invariants(cfg))
        self.assertFalse(IotReport(19, 14, 0, 0, 1, 1, 1, 1, 1).meets_invariants(cfg))


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


# ===========================================================================
# Teeth — the harness must catch a real planted ingest bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct aggregator must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_aggregate))

    def test_every_mutant_is_caught(self):
        # Each planted defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 4)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)

    def test_oracle_matches_frozen_literal(self):
        # The frozen expectation is a non-circular constant the oracle reproduces.
        self.assertEqual(oracle_aggregate(STREAM, READINGS), AGG_EXPECTED)

    def test_noncircular_corpus(self):
        # Corrupt one frozen literal and confirm prove(oracle) flips False -> True.
        # If it does not flip, the corpus is circular (read back from the oracle).
        import harnesses.core.iot_telemetry_test_harness as mod
        original = mod.AGG_EXPECTED
        self.assertFalse(mod.prove(mod.oracle_aggregate))  # clean before
        try:
            mod.AGG_EXPECTED = dataclasses.replace(original, n_accepted=original.n_accepted + 1)
            self.assertTrue(mod.prove(mod.oracle_aggregate),
                            "prove did not flip on a corrupted literal -> corpus is circular")
        finally:
            mod.AGG_EXPECTED = original
        self.assertFalse(mod.prove(mod.oracle_aggregate))  # clean after restore


if __name__ == "__main__":
    unittest.main()
