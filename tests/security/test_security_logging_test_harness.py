"""test_security_logging_test_harness.py — unittest suite."""

import unittest

from harnesses.security.security_logging_test_harness import (
    AlertThreshold,
    AuditCoverageChecker,
    HashChainLog,
    LogFinding,
    LogInjectionChecker,
    list_scenarios,
    run_all_scenarios,
)

REQUIRED = ["login_success", "login_failure", "authz_denied", "admin_action"]
FULL = ["login_success", "login_failure", "authz_denied", "admin_action", "logout"]
PARTIAL = ["login_success", "logout"]


class TestAuditCoverageChecker(unittest.TestCase):
    def setUp(self):
        self.c = AuditCoverageChecker()

    def test_full_coverage_clean(self):
        self.assertEqual(self.c.missing(REQUIRED, FULL), [])

    def test_missing_events_listed(self):
        self.assertEqual(set(self.c.missing(REQUIRED, PARTIAL)),
                         {"login_failure", "authz_denied", "admin_action"})

    def test_check_emits_high_findings(self):
        findings = self.c.check(REQUIRED, PARTIAL)
        self.assertTrue(findings)
        self.assertTrue(all(isinstance(f, LogFinding) for f in findings))
        self.assertTrue(all(f.severity == "HIGH" for f in findings))


class TestLogInjectionChecker(unittest.TestCase):
    def setUp(self):
        self.c = LogInjectionChecker()

    def test_clean_message(self):
        self.assertFalse(self.c.check("user alice logged in")[0])

    def test_newline_flagged(self):
        self.assertTrue(self.c.check("ok\nADMIN: deleted all")[0])

    def test_bare_cr_flagged(self):
        self.assertTrue(self.c.check("ok\rADMIN: deleted all")[0])

    def test_ansi_escape_flagged(self):
        self.assertTrue(self.c.check("ok\x1b[31mERROR")[0])

    def test_escape_neutralizes(self):
        forged = "ok\nADMIN: x"
        self.assertFalse(self.c.check(self.c.escape(forged))[0])


class TestAlertThreshold(unittest.TestCase):
    def test_below_threshold_no_alert(self):
        a = AlertThreshold()
        for t in (1000, 1001, 1002):
            a.record(t)
        self.assertFalse(a.should_alert(now=1003, window_s=60, threshold=5))

    def test_at_threshold_alerts(self):
        a = AlertThreshold()
        for t in range(5):
            a.record(1000 + t)
        self.assertTrue(a.should_alert(now=1004, window_s=60, threshold=5))

    def test_over_threshold_alerts(self):
        a = AlertThreshold()
        for t in range(6):
            a.record(1000 + t)
        self.assertTrue(a.should_alert(now=1006, window_s=60, threshold=5))

    def test_window_excludes_stale_events(self):
        a = AlertThreshold()
        for t in range(6):
            a.record(1000 + t)
        # All six events are far outside a 10s window ending at now=2000.
        self.assertFalse(a.should_alert(now=2000, window_s=10, threshold=5))


class TestHashChainLog(unittest.TestCase):
    def test_intact_chain_verifies(self):
        log = HashChainLog()
        for d in ("e1", "e2", "e3"):
            log.append(d)
        ok, idx = log.verify()
        self.assertTrue(ok)
        self.assertEqual(idx, -1)

    def test_tampered_chain_detected_at_index(self):
        log = HashChainLog()
        for d in ("e1", "e2", "e3"):
            log.append(d)
        log.entries[1]["data"] = "forged"
        ok, idx = log.verify()
        self.assertFalse(ok)
        self.assertEqual(idx, 1)

    def test_empty_chain_verifies(self):
        self.assertTrue(HashChainLog().verify()[0])


class TestLogFinding(unittest.TestCase):
    def test_invalid_severity_raises(self):
        with self.assertRaises(ValueError):
            LogFinding("X", "BOGUS", "desc")


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        failed = [r for r in run_all_scenarios() if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 10)


if __name__ == "__main__":
    unittest.main()
