"""Test suite for drift_detection_test_harness."""

import unittest

from harnesses.ai.drift_detection_test_harness import (
    CENTROID_ALERT,
    COSINE_DROP_ALERT,
    DRIFT_BASE_DIST,
    DRIFT_CUR_DIST,
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
    SCENARIOS,
    SPEARMAN_ALERT,
    STABLE_CASE,
    VERSION_MISMATCH_CASE,
    DriftReport,
    _run_self_test,
    centroid_averaged,
    centroid_distance,
    compute_drift,
    cosine_mean_drop,
    cosine_unnormalized_drop,
    COS_BASE_DOCS,
    COS_CUR_DOCS,
    COS_QUERY,
    hellinger,
    js_div,
    kl_div,
    list_scenarios,
    psi,
    psi_zero_floor,
    rank_overlap_only,
    RANK_BASE,
    RANK_REVERSED,
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


if __name__ == "__main__":
    unittest.main()
