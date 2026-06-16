"""Test suite for drift_detection_test_harness."""

import dataclasses
import unittest

from harnesses._teeth import verify
from harnesses.ai.drift_detection_test_harness import (
    CENTROID_ALERT,
    COS_BASE_DOCS,
    COS_CUR_DOCS,
    COS_QUERY,
    COSINE_DROP_ALERT,
    DRIFT_BASE_DIST,
    DRIFT_CUR_DIST,
    DRIFT_VERDICT_CORPUS,
    DRIFTED_CASE,
    EMB_BASE,
    EMB_CUR,
    HELLINGER_ALERT,
    JS_ALERT,
    KL_ALERT,
    KL_SWAP_BASE,
    KL_SWAP_CUR,
    PSI_ALERT,
    PSI_ZB_BASE,
    PSI_ZB_CUR,
    RANK_BASE,
    RANK_REVERSED,
    SCENARIOS,
    SPEARMAN_ALERT,
    STABLE_CASE,
    TEETH,
    VERSION_MISMATCH_CASE,
    DriftReport,
    _run_self_test,
    centroid_averaged,
    centroid_distance,
    compute_drift,
    cosine_mean_drop,
    cosine_unnormalized_drop,
    hellinger,
    js_div,
    kl_div,
    list_scenarios,
    oracle_drift_detector,
    prove,
    psi,
    psi_zero_floor,
    rank_overlap_only,
    spearman,
)


class TestMetricsOnFixtures(unittest.TestCase):
    def test_psi_drift_vs_stable(self):
        self.assertGreater(psi(DRIFT_BASE_DIST, DRIFT_CUR_DIST), PSI_ALERT)

    def test_kl_js_hellinger_drift(self):
        self.assertGreater(kl_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST), KL_ALERT)
        self.assertGreater(js_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST), JS_ALERT)
        self.assertGreater(hellinger(DRIFT_BASE_DIST, DRIFT_CUR_DIST), HELLINGER_ALERT)

    def test_js_symmetric_and_bounded(self):
        a = js_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
        b = js_div(DRIFT_CUR_DIST, DRIFT_BASE_DIST)
        self.assertAlmostEqual(a, b, places=9)
        self.assertTrue(0.0 <= a <= 1.0)

    def test_centroid_and_cosine(self):
        self.assertGreater(centroid_distance(EMB_BASE, EMB_CUR), CENTROID_ALERT)
        self.assertGreater(cosine_mean_drop(COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS),
                           COSINE_DROP_ALERT)

    def test_spearman_reversed_is_minus_one(self):
        self.assertAlmostEqual(spearman(RANK_BASE, RANK_REVERSED), -1.0, places=9)


class TestBuggyDetectorsCaught(unittest.TestCase):
    def test_psi_zero_floor_misses(self):
        self.assertGreater(psi(PSI_ZB_BASE, PSI_ZB_CUR), PSI_ALERT)
        self.assertLess(psi_zero_floor(PSI_ZB_BASE, PSI_ZB_CUR), PSI_ALERT)

    def test_kl_swap_misses(self):
        self.assertGreater(kl_div(KL_SWAP_BASE, KL_SWAP_CUR), KL_ALERT)
        self.assertLess(kl_div(KL_SWAP_CUR, KL_SWAP_BASE), KL_ALERT)

    def test_centroid_averaged_misses(self):
        self.assertGreater(centroid_distance(EMB_BASE, EMB_CUR), CENTROID_ALERT)
        self.assertLess(centroid_averaged(EMB_BASE, EMB_CUR), CENTROID_ALERT)

    def test_cosine_unnormalized_misses(self):
        self.assertGreater(cosine_mean_drop(COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS),
                           COSINE_DROP_ALERT)
        self.assertLess(cosine_unnormalized_drop(COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS),
                        COSINE_DROP_ALERT)

    def test_rank_overlap_misses(self):
        self.assertLess(spearman(RANK_BASE, RANK_REVERSED), SPEARMAN_ALERT)
        self.assertGreaterEqual(rank_overlap_only(RANK_BASE, RANK_REVERSED), SPEARMAN_ALERT)

    def test_version_blind_misses(self):
        o = compute_drift(VERSION_MISMATCH_CASE)
        b = compute_drift(VERSION_MISMATCH_CASE, version_aware=False)
        self.assertTrue(o.version_mismatch and o.any_drift())
        self.assertTrue(not b.version_mismatch and b.is_stable())

    def test_false_alarm_on_stable(self):
        self.assertTrue(compute_drift(STABLE_CASE).is_stable())
        self.assertTrue(compute_drift(STABLE_CASE, psi_fn=lambda base, cur: 0.5).any_drift())


class TestHolisticReport(unittest.TestCase):
    def test_drifted_any_drift(self):
        r = compute_drift(DRIFTED_CASE)
        self.assertTrue(r.any_drift())
        for flag in (r.psi_alert, r.kl_alert, r.js_alert, r.hellinger_alert,
                     r.centroid_alert, r.cosine_alert, r.rank_alert, r.churn_alert):
            self.assertTrue(flag)

    def test_stable_is_stable(self):
        self.assertTrue(compute_drift(STABLE_CASE).is_stable())

    def test_report_alert_thresholds(self):
        clean = DriftReport(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, False)
        self.assertTrue(clean.is_stable())
        self.assertTrue(DriftReport(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, True).any_drift())
        self.assertTrue(DriftReport(0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, False).psi_alert)


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


# ===========================================================================
# Teeth — the harness must catch a real planted drift-detector bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct PSI@0.25 detector must NOT be flagged by prove.
        self.assertFalse(TEETH.prove(TEETH.oracle))
        self.assertFalse(prove(oracle_drift_detector))

    def test_every_mutant_is_caught(self):
        # Each planted asleep-/trigger-happy detector must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)
        self.assertEqual(TEETH.corpus_size, len(DRIFT_VERDICT_CORPUS))

    def test_oracle_matches_frozen_literals(self):
        # The frozen verdicts are non-circular constants the oracle must reproduce.
        for case in DRIFT_VERDICT_CORPUS:
            self.assertEqual(
                oracle_drift_detector(case.base_dist, case.cur_dist),
                case.expected_drift, case.name)

    def test_noncircular_corpus(self):
        # Corrupt ONE frozen verdict literal and confirm prove(oracle) flips
        # False -> True. If it does not flip, the corpus is circular (re-derived
        # from the oracle at runtime) rather than judged against frozen literals.
        self.assertFalse(prove(oracle_drift_detector))
        original = DRIFT_VERDICT_CORPUS[0]
        corrupted = dataclasses.replace(original, expected_drift=not original.expected_drift)
        patched = (corrupted,) + DRIFT_VERDICT_CORPUS[1:]
        import harnesses.ai.drift_detection_test_harness as mod
        saved = mod.DRIFT_VERDICT_CORPUS
        try:
            mod.DRIFT_VERDICT_CORPUS = patched
            self.assertTrue(prove(oracle_drift_detector),
                            "prove(oracle) must flip to True when a frozen verdict "
                            "literal is corrupted; otherwise the corpus is circular")
        finally:
            mod.DRIFT_VERDICT_CORPUS = saved
        # restored: the oracle is clean again
        self.assertFalse(prove(oracle_drift_detector))


if __name__ == "__main__":
    unittest.main()
