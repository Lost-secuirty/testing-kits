"""Test suite for browser_e2e_test_harness."""

import unittest

from harnesses.core.browser_e2e_test_harness import (
    INITIAL_DOM,
    MUTATED_DOM,
    ROUTES,
    SCENARIOS,
    SUBMIT_SELECTOR,
    UNMOCKED_URL,
    Dom,
    E2EReport,
    EventLoop,
    PrematureAssertionError,
    Selector,
    UnmockedRequestError,
    _run_self_test,
    apply_mutation,
    assert_settled,
    audit,
    brittle_xpath_selector,
    count_event_order_violations,
    event_emitter_oracle,
    event_emitter_reordered,
    hydration_blind_render,
    hydration_diff,
    list_scenarios,
    no_settle,
    oracle_fetch,
    oracle_selector,
    resolve,
    robust_clicker,
    silent_404_fetch,
    stale_clicker,
)


class TestDomAndSelectors(unittest.TestCase):
    def test_resolve_by_role_label_testid(self):
        self.assertEqual(resolve(Selector("role", "button"), INITIAL_DOM), "submit")
        self.assertEqual(resolve(Selector("label", "Email"), INITIAL_DOM), "email")
        self.assertEqual(resolve(Selector("testid", "submit-btn"), INITIAL_DOM), "submit")

    def test_mutation_is_pure(self):
        # original DOM still has the old node after mutation
        self.assertIn("submit", INITIAL_DOM.nodes)
        self.assertNotIn("submit", MUTATED_DOM.nodes)
        self.assertIn("submit_v2", MUTATED_DOM.nodes)

    def test_testid_stable_xpath_brittle(self):
        self.assertEqual(oracle_selector(MUTATED_DOM), "submit_v2")
        nid = brittle_xpath_selector(MUTATED_DOM)
        self.assertNotEqual(MUTATED_DOM.nodes.get(nid).testid if nid else None, "submit-btn")


class TestEventLoop(unittest.TestCase):
    def test_settle_drains(self):
        loop = EventLoop()
        for _ in range(3):
            loop.schedule(lambda: None)
        loop.settle()
        self.assertEqual(loop.pending, [])

    def test_assert_settled_raises_when_pending(self):
        loop = EventLoop()
        loop.schedule(lambda: None)
        with self.assertRaises(PrematureAssertionError):
            assert_settled(loop)

    def test_assert_settled_ok_after_settle(self):
        loop = EventLoop()
        loop.schedule(lambda: None)
        loop.settle()
        assert_settled(loop)  # must not raise


class TestNetworkMock(unittest.TestCase):
    def test_mocked_returns(self):
        self.assertEqual(oracle_fetch("/api/user", ROUTES).status, 200)

    def test_unmocked_raises(self):
        with self.assertRaises(UnmockedRequestError):
            oracle_fetch(UNMOCKED_URL, ROUTES)

    def test_silent_404_does_not_raise(self):
        self.assertEqual(silent_404_fetch(UNMOCKED_URL, ROUTES).status, 404)


class TestEventOrder(unittest.TestCase):
    def test_oracle_in_order(self):
        self.assertEqual(count_event_order_violations(event_emitter_oracle()), 0)

    def test_reordered_violates(self):
        self.assertGreaterEqual(count_event_order_violations(event_emitter_reordered()), 1)


class TestHydration(unittest.TestCase):
    def test_match_clean(self):
        self.assertEqual(hydration_diff(INITIAL_DOM, INITIAL_DOM), 0)

    def test_blind_render_mismatch(self):
        self.assertGreaterEqual(hydration_diff(INITIAL_DOM, hydration_blind_render(INITIAL_DOM)), 1)


class TestAuditOracleAndBuggy(unittest.TestCase):
    def test_oracle_clean(self):
        rep = audit()
        self.assertTrue(rep.meets_floors())
        self.assertEqual(rep.total_failures, 0)

    def test_stale_clicker_caught(self):
        self.assertGreaterEqual(audit(clicker=stale_clicker).stale_clicks, 1)

    def test_eager_asserter_caught(self):
        self.assertGreaterEqual(audit(settle_strategy=no_settle).premature_assertions, 1)

    def test_reordered_caught(self):
        self.assertGreaterEqual(audit(emitter=event_emitter_reordered).event_order_violations, 1)

    def test_silent_404_caught(self):
        self.assertGreaterEqual(audit(fetch_impl=silent_404_fetch).unmocked_silent, 1)

    def test_hydration_blind_caught(self):
        self.assertGreaterEqual(audit(renderer=hydration_blind_render).hydration_mismatches, 1)

    def test_brittle_xpath_caught(self):
        self.assertGreaterEqual(audit(selector=brittle_xpath_selector).selector_breaks, 1)


class TestReportLogic(unittest.TestCase):
    def test_total_and_floors(self):
        self.assertTrue(E2EReport(0, 0, 0, 0, 0, 0).meets_floors())
        self.assertEqual(E2EReport(1, 0, 2, 0, 0, 0).total_failures, 3)
        self.assertFalse(E2EReport(1, 0, 0, 0, 0, 0).meets_floors())


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
