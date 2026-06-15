"""
Tests for the State Machine Test Harness (Harness 22 of 36).
~82 tests covering all components.
"""

import json
import time
import unittest
import urllib.request
from urllib.error import HTTPError

from harnesses._teeth import verify
from harnesses.core.statemachine_test_harness import (
    TEETH,
    CoverageTracker,
    CycleDetector,
    DeterminismChecker,
    InvalidTransition,
    InvalidTransitionTester,
    MockStateMachineServer,
    ReachabilityAnalyzer,
    StateMachine,
    Transition,
    TransitionTester,
    make_order_machine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_simple_machine() -> StateMachine:
    """A → B → C (linear, no cycles)."""
    return StateMachine(
        states={"A", "B", "C"},
        initial_state="A",
        transitions=[
            Transition("A", "B", "go"),
            Transition("B", "C", "go"),
        ],
        terminal_states={"C"},
    )


def make_cyclic_machine() -> StateMachine:
    """A ↔ B (cycle)."""
    return StateMachine(
        states={"A", "B"},
        initial_state="A",
        transitions=[
            Transition("A", "B", "fwd"),
            Transition("B", "A", "back"),
        ],
    )


def make_traffic_light_machine() -> StateMachine:
    """RED → GREEN → YELLOW → RED."""
    return StateMachine(
        states={"RED", "GREEN", "YELLOW"},
        initial_state="RED",
        transitions=[
            Transition("RED",    "GREEN",  "change"),
            Transition("GREEN",  "YELLOW", "change"),
            Transition("YELLOW", "RED",    "change"),
        ],
    )


def make_nondeterministic_machine() -> StateMachine:
    """A can go to B or C on 'go' (no guards, so nondeterministic definition)."""
    return StateMachine(
        states={"A", "B", "C"},
        initial_state="A",
        transitions=[
            Transition("A", "B", "go"),
            Transition("A", "C", "go"),
        ],
    )


def http_get(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def http_post(url: str) -> dict:
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def http_post_expect_error(url: str) -> tuple:
    """Return (status_code, body_dict) for any response."""
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ===========================================================================
# 1. Transition dataclass tests (10 tests)
# ===========================================================================

class TestTransitionDataclass(unittest.TestCase):

    def test_basic_fields(self):
        t = Transition("A", "B", "go")
        self.assertEqual(t.from_state, "A")
        self.assertEqual(t.to_state, "B")
        self.assertEqual(t.trigger, "go")

    def test_guard_defaults_none(self):
        t = Transition("A", "B", "go")
        self.assertIsNone(t.guard)

    def test_action_defaults_none(self):
        t = Transition("A", "B", "go")
        self.assertIsNone(t.action)

    def test_guard_true(self):
        t = Transition("A", "B", "go", guard=lambda: True)
        self.assertTrue(t.is_enabled())

    def test_guard_false(self):
        t = Transition("A", "B", "go", guard=lambda: False)
        self.assertFalse(t.is_enabled())

    def test_no_guard_is_enabled(self):
        t = Transition("A", "B", "go")
        self.assertTrue(t.is_enabled())

    def test_action_callable(self):
        called = []
        t = Transition("A", "B", "go", action=lambda: called.append(1))
        t.action()
        self.assertEqual(called, [1])

    def test_guard_with_stateful_lambda(self):
        flag = [True]
        t = Transition("A", "B", "go", guard=lambda: flag[0])
        self.assertTrue(t.is_enabled())
        flag[0] = False
        self.assertFalse(t.is_enabled())

    def test_equality(self):
        t1 = Transition("A", "B", "go")
        t2 = Transition("A", "B", "go")
        self.assertEqual(t1, t2)

    def test_inequality_different_trigger(self):
        t1 = Transition("A", "B", "go")
        t2 = Transition("A", "B", "stop")
        self.assertNotEqual(t1, t2)


# ===========================================================================
# 2. InvalidTransition exception tests (5 tests)
# ===========================================================================

class TestInvalidTransitionException(unittest.TestCase):

    def test_is_exception(self):
        exc = InvalidTransition("A", "go")
        self.assertIsInstance(exc, Exception)

    def test_state_attribute(self):
        exc = InvalidTransition("PAID", "deliver")
        self.assertEqual(exc.state, "PAID")

    def test_event_attribute(self):
        exc = InvalidTransition("PAID", "deliver")
        self.assertEqual(exc.event, "deliver")

    def test_message_contains_state_and_event(self):
        exc = InvalidTransition("PAID", "deliver")
        self.assertIn("PAID", str(exc))
        self.assertIn("deliver", str(exc))

    def test_reason_in_message(self):
        exc = InvalidTransition("A", "go", reason="guard blocked")
        self.assertIn("guard blocked", str(exc))


# ===========================================================================
# 3. StateMachine core tests (15 tests)
# ===========================================================================

class TestStateMachineCore(unittest.TestCase):

    def setUp(self):
        self.machine = make_simple_machine()

    def test_initial_state(self):
        self.assertEqual(self.machine.state, "A")

    def test_send_valid_event(self):
        new_state = self.machine.send("go")
        self.assertEqual(new_state, "B")

    def test_state_updates_after_send(self):
        self.machine.send("go")
        self.assertEqual(self.machine.state, "B")

    def test_chained_transitions(self):
        self.machine.send("go")
        self.machine.send("go")
        self.assertEqual(self.machine.state, "C")

    def test_is_terminal_false_initially(self):
        self.assertFalse(self.machine.is_terminal)

    def test_is_terminal_true_at_terminal(self):
        self.machine.send("go")
        self.machine.send("go")
        self.assertTrue(self.machine.is_terminal)

    def test_invalid_transition_raises(self):
        with self.assertRaises(InvalidTransition):
            self.machine.send("nonexistent")

    def test_state_unchanged_after_invalid(self):
        try:
            self.machine.send("nonexistent")
        except InvalidTransition:
            pass
        self.assertEqual(self.machine.state, "A")

    def test_reset(self):
        self.machine.send("go")
        self.machine.reset()
        self.assertEqual(self.machine.state, "A")

    def test_invalid_initial_state_raises(self):
        with self.assertRaises(ValueError):
            StateMachine(
                states={"A", "B"},
                initial_state="Z",
                transitions=[],
            )

    def test_states_property(self):
        self.assertIn("A", self.machine.states)
        self.assertIn("B", self.machine.states)
        self.assertIn("C", self.machine.states)

    def test_transitions_property_returns_list(self):
        self.assertIsInstance(self.machine.transitions, list)

    def test_terminal_states_property(self):
        self.assertIn("C", self.machine.terminal_states)

    def test_guard_blocks_transition(self):
        m = StateMachine(
            states={"A", "B"},
            initial_state="A",
            transitions=[Transition("A", "B", "go", guard=lambda: False)],
        )
        with self.assertRaises(InvalidTransition):
            m.send("go")

    def test_action_called_on_transition(self):
        called = []
        m = StateMachine(
            states={"A", "B"},
            initial_state="A",
            transitions=[Transition("A", "B", "go", action=lambda: called.append(1))],
        )
        m.send("go")
        self.assertEqual(called, [1])


# ===========================================================================
# 4. Order-lifecycle machine tests (8 tests)
# ===========================================================================

class TestOrderLifecycleMachine(unittest.TestCase):

    def setUp(self):
        self.machine = make_order_machine()

    def test_initial_state_created(self):
        self.assertEqual(self.machine.state, "CREATED")

    def test_pay_transitions_to_paid(self):
        self.machine.send("pay")
        self.assertEqual(self.machine.state, "PAID")

    def test_ship_from_paid(self):
        self.machine.send("pay")
        self.machine.send("ship")
        self.assertEqual(self.machine.state, "SHIPPED")

    def test_deliver_from_shipped(self):
        self.machine.send("pay")
        self.machine.send("ship")
        self.machine.send("deliver")
        self.assertEqual(self.machine.state, "DELIVERED")

    def test_delivered_is_terminal(self):
        self.machine.send("pay")
        self.machine.send("ship")
        self.machine.send("deliver")
        self.assertTrue(self.machine.is_terminal)

    def test_cancel_from_created(self):
        self.machine.send("cancel")
        self.assertEqual(self.machine.state, "CANCELLED")

    def test_cancelled_is_terminal(self):
        self.machine.send("cancel")
        self.assertTrue(self.machine.is_terminal)

    def test_ship_from_created_invalid(self):
        with self.assertRaises(InvalidTransition):
            self.machine.send("ship")


# ===========================================================================
# 5. TransitionTester tests (5 tests)
# ===========================================================================

class TestTransitionTester(unittest.TestCase):

    def setUp(self):
        self.machine = make_order_machine()
        self.tester = TransitionTester(self.machine)

    def test_all_pass(self):
        results = self.tester.run([
            ("pay", "PAID"),
            ("ship", "SHIPPED"),
            ("deliver", "DELIVERED"),
        ])
        self.assertTrue(all(r["passed"] for r in results))

    def test_wrong_expected_state_fails(self):
        results = self.tester.run([("pay", "SHIPPED")])
        self.assertFalse(results[0]["passed"])

    def test_invalid_event_fails(self):
        results = self.tester.run([("nonexistent", "PAID")])
        self.assertFalse(results[0]["passed"])
        self.assertIsNotNone(results[0]["error"])

    def test_results_length_matches_steps(self):
        steps = [("pay", "PAID"), ("ship", "SHIPPED")]
        results = self.tester.run(steps)
        self.assertEqual(len(results), 2)

    def test_empty_steps(self):
        results = self.tester.run([])
        self.assertEqual(results, [])


# ===========================================================================
# 6. InvalidTransitionTester tests (5 tests)
# ===========================================================================

class TestInvalidTransitionTester(unittest.TestCase):

    def setUp(self):
        self.machine = make_order_machine()
        self.tester = InvalidTransitionTester(self.machine)

    def test_invalid_event_passes(self):
        result = self.tester.test("ship")  # ship not valid from CREATED
        self.assertTrue(result["passed"])

    def test_raised_is_true_for_invalid(self):
        result = self.tester.test("ship")
        self.assertTrue(result["raised"])

    def test_state_unchanged_recorded(self):
        result = self.tester.test("ship")
        self.assertEqual(result["state_before"], result["state_after"])

    def test_valid_event_fails_test(self):
        result = self.tester.test("pay")  # pay IS valid from CREATED
        self.assertFalse(result["passed"])

    def test_returns_event_in_result(self):
        result = self.tester.test("deliver")
        self.assertEqual(result["event"], "deliver")


# ===========================================================================
# 7. ReachabilityAnalyzer tests (6 tests)
# ===========================================================================

class TestReachabilityAnalyzer(unittest.TestCase):

    def test_all_reachable_simple(self):
        m = make_simple_machine()
        analyzer = ReachabilityAnalyzer(m)
        self.assertEqual(analyzer.reachable_states(), {"A", "B", "C"})

    def test_orphaned_state(self):
        m = StateMachine(
            states={"A", "B", "C"},
            initial_state="A",
            transitions=[Transition("A", "B", "go")],
        )
        analyzer = ReachabilityAnalyzer(m)
        self.assertIn("C", analyzer.orphaned_states())

    def test_no_orphaned_states(self):
        m = make_simple_machine()
        analyzer = ReachabilityAnalyzer(m)
        self.assertEqual(analyzer.orphaned_states(), set())

    def test_initial_state_always_reachable(self):
        m = make_simple_machine()
        analyzer = ReachabilityAnalyzer(m)
        self.assertIn("A", analyzer.reachable_states())

    def test_order_machine_all_reachable(self):
        m = make_order_machine()
        analyzer = ReachabilityAnalyzer(m)
        self.assertEqual(analyzer.orphaned_states(), set())

    def test_isolated_machine(self):
        m = StateMachine(
            states={"A", "B", "C"},
            initial_state="A",
            transitions=[],
        )
        analyzer = ReachabilityAnalyzer(m)
        reachable = analyzer.reachable_states()
        self.assertEqual(reachable, {"A"})
        self.assertIn("B", analyzer.orphaned_states())
        self.assertIn("C", analyzer.orphaned_states())


# ===========================================================================
# 8. CycleDetector tests (6 tests)
# ===========================================================================

class TestCycleDetector(unittest.TestCase):

    def test_acyclic_machine_no_cycle(self):
        m = make_simple_machine()
        detector = CycleDetector(m)
        self.assertFalse(detector.has_cycle())

    def test_cyclic_machine_has_cycle(self):
        m = make_cyclic_machine()
        detector = CycleDetector(m)
        self.assertTrue(detector.has_cycle())

    def test_traffic_light_has_cycle(self):
        m = make_traffic_light_machine()
        detector = CycleDetector(m)
        self.assertTrue(detector.has_cycle())

    def test_no_cycles_list_empty(self):
        m = make_simple_machine()
        detector = CycleDetector(m)
        self.assertEqual(detector.find_cycles(), [])

    def test_cyclic_machine_find_cycles_nonempty(self):
        m = make_cyclic_machine()
        detector = CycleDetector(m)
        cycles = detector.find_cycles()
        self.assertGreater(len(cycles), 0)

    def test_self_loop_is_cycle(self):
        m = StateMachine(
            states={"A"},
            initial_state="A",
            transitions=[Transition("A", "A", "loop")],
        )
        detector = CycleDetector(m)
        self.assertTrue(detector.has_cycle())


# ===========================================================================
# 9. CoverageTracker tests (8 tests)
# ===========================================================================

class TestCoverageTracker(unittest.TestCase):

    def setUp(self):
        self.machine = make_order_machine()
        self.tracker = CoverageTracker(self.machine)

    def test_initial_coverage_zero(self):
        self.assertEqual(self.tracker.coverage_ratio(), 0.0)

    def test_coverage_after_record(self):
        self.tracker.record("CREATED", "PAID", "pay")
        self.assertGreater(self.tracker.coverage_ratio(), 0.0)

    def test_covered_empty_initially(self):
        self.assertEqual(self.tracker.covered(), set())

    def test_covered_after_record(self):
        self.tracker.record("CREATED", "PAID", "pay")
        self.assertIn(("CREATED", "PAID", "pay"), self.tracker.covered())

    def test_uncovered_contains_all_initially(self):
        self.assertEqual(self.tracker.uncovered(), self.tracker.all_transition_keys())

    def test_full_coverage(self):
        for key in self.tracker.all_transition_keys():
            self.tracker.record(*key)
        self.assertEqual(self.tracker.coverage_ratio(), 1.0)

    def test_reset_clears_coverage(self):
        self.tracker.record("CREATED", "PAID", "pay")
        self.tracker.reset()
        self.assertEqual(self.tracker.coverage_ratio(), 0.0)

    def test_empty_machine_coverage_one(self):
        m = StateMachine(states={"A"}, initial_state="A", transitions=[])
        tracker = CoverageTracker(m)
        self.assertEqual(tracker.coverage_ratio(), 1.0)


# ===========================================================================
# 10. DeterminismChecker tests (6 tests)
# ===========================================================================

class TestDeterminismChecker(unittest.TestCase):

    def test_deterministic_machine(self):
        m = make_simple_machine()
        checker = DeterminismChecker(m)
        self.assertTrue(checker.is_deterministic())

    def test_nondeterministic_machine(self):
        m = make_nondeterministic_machine()
        checker = DeterminismChecker(m)
        self.assertFalse(checker.is_deterministic())

    def test_check_returns_conflicts(self):
        m = make_nondeterministic_machine()
        checker = DeterminismChecker(m)
        conflicts = checker.check()
        self.assertIn(("A", "go"), conflicts)

    def test_check_empty_for_deterministic(self):
        m = make_simple_machine()
        checker = DeterminismChecker(m)
        self.assertEqual(checker.check(), {})

    def test_order_machine_is_deterministic(self):
        m = make_order_machine()
        checker = DeterminismChecker(m)
        self.assertTrue(checker.is_deterministic())

    def test_conflict_count(self):
        m = make_nondeterministic_machine()
        checker = DeterminismChecker(m)
        conflicts = checker.check()
        self.assertEqual(len(conflicts[("A", "go")]), 2)


# ===========================================================================
# 11. MockStateMachineServer / HTTP tests (18 tests)
# ===========================================================================

class TestMockStateMachineServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.machine = make_order_machine()
        cls.server = MockStateMachineServer(machine=cls.machine)
        cls.server.start()
        # Give the server a moment to bind
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        # Reset the machine and coverage before each test
        self.machine.reset()
        self.server.coverage_tracker.reset()

    # --- /health ---
    def test_health_ok(self):
        data = http_get(f"{self.server.url}/health")
        self.assertEqual(data["status"], "ok")

    # --- /state ---
    def test_get_state_initial(self):
        data = http_get(f"{self.server.url}/state")
        self.assertEqual(data["state"], "CREATED")

    def test_get_state_is_terminal_false(self):
        data = http_get(f"{self.server.url}/state")
        self.assertFalse(data["is_terminal"])

    # --- POST /send ---
    def test_post_send_valid_event(self):
        data = http_post(f"{self.server.url}/send?event=pay")
        self.assertEqual(data["state"], "PAID")

    def test_post_send_updates_is_terminal(self):
        http_post(f"{self.server.url}/send?event=pay")
        http_post(f"{self.server.url}/send?event=ship")
        data = http_post(f"{self.server.url}/send?event=deliver")
        self.assertTrue(data["is_terminal"])

    def test_post_send_invalid_event_returns_409(self):
        code, data = http_post_expect_error(f"{self.server.url}/send?event=ship")
        self.assertEqual(code, 409)
        self.assertIn("error", data)

    def test_post_send_missing_event_returns_400(self):
        code, data = http_post_expect_error(f"{self.server.url}/send")
        self.assertEqual(code, 400)

    def test_post_send_state_unchanged_after_invalid(self):
        http_post_expect_error(f"{self.server.url}/send?event=ship")
        data = http_get(f"{self.server.url}/state")
        self.assertEqual(data["state"], "CREATED")

    # --- POST /reset ---
    def test_post_reset(self):
        http_post(f"{self.server.url}/send?event=pay")
        data = http_post(f"{self.server.url}/reset")
        self.assertEqual(data["state"], "CREATED")

    # --- GET /transitions ---
    def test_get_transitions_returns_list(self):
        data = http_get(f"{self.server.url}/transitions")
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_get_transitions_have_required_fields(self):
        data = http_get(f"{self.server.url}/transitions")
        for item in data:
            self.assertIn("from_state", item)
            self.assertIn("to_state", item)
            self.assertIn("trigger", item)

    # --- GET /reachable ---
    def test_get_reachable_returns_lists(self):
        data = http_get(f"{self.server.url}/reachable")
        self.assertIn("reachable", data)
        self.assertIn("orphaned", data)

    def test_get_reachable_no_orphaned_for_order_machine(self):
        data = http_get(f"{self.server.url}/reachable")
        self.assertEqual(data["orphaned"], [])

    # --- GET /coverage ---
    def test_get_coverage_initial_ratio_zero(self):
        data = http_get(f"{self.server.url}/coverage")
        self.assertEqual(data["ratio"], 0.0)

    def test_get_coverage_increases_after_transition(self):
        http_post(f"{self.server.url}/send?event=pay")
        data = http_get(f"{self.server.url}/coverage")
        self.assertGreater(data["ratio"], 0.0)

    # --- GET /determinism ---
    def test_get_determinism_order_machine_true(self):
        data = http_get(f"{self.server.url}/determinism")
        self.assertTrue(data["deterministic"])
        self.assertEqual(data["conflicts"], [])

    # --- GET /cycles ---
    def test_get_cycles_order_machine_no_cycle(self):
        data = http_get(f"{self.server.url}/cycles")
        self.assertFalse(data["has_cycle"])

    # --- unknown route ---
    def test_unknown_get_returns_404(self):
        try:
            http_get(f"{self.server.url}/unknown_route")
            self.fail("Expected HTTPError")
        except HTTPError as exc:
            self.assertEqual(exc.code, 404)


# ===========================================================================
# 12. Integration / end-to-end tests (5 tests)
# ===========================================================================

class TestIntegration(unittest.TestCase):

    def test_full_order_happy_path(self):
        m = make_order_machine()
        m.send("pay")
        m.send("ship")
        m.send("deliver")
        self.assertEqual(m.state, "DELIVERED")
        self.assertTrue(m.is_terminal)

    def test_cancel_path(self):
        m = make_order_machine()
        m.send("pay")
        m.send("cancel")
        self.assertEqual(m.state, "CANCELLED")
        self.assertTrue(m.is_terminal)

    def test_coverage_full_after_exercising_all(self):
        m = make_order_machine()
        tracker = CoverageTracker(m)
        paths = [
            ["pay", "ship", "deliver"],
            ["cancel"],
            ["pay", "cancel"],
            ["pay", "ship", "cancel"],
        ]
        for path in paths:
            m.reset()
            for event in path:
                from_state = m.state
                new_state = m.send(event)
                tracker.record(from_state, new_state, event)
        self.assertEqual(tracker.coverage_ratio(), 1.0)

    def test_guard_prevents_transition_integration(self):
        allowed = [False]
        m = StateMachine(
            states={"LOCKED", "UNLOCKED"},
            initial_state="LOCKED",
            transitions=[
                Transition("LOCKED", "UNLOCKED", "unlock", guard=lambda: allowed[0])
            ],
        )
        with self.assertRaises(InvalidTransition):
            m.send("unlock")
        allowed[0] = True
        m.send("unlock")
        self.assertEqual(m.state, "UNLOCKED")

    def test_server_context_manager(self):
        m = make_order_machine()
        with MockStateMachineServer(machine=m) as srv:
            data = http_get(f"{srv.url}/health")
            self.assertEqual(data["status"], "ok")


# ===========================================================================
# 13. Teeth — the harness must catch a real planted FSM-checker bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted bug (the campaign teeth contract)."""

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        self.assertFalse(TEETH.prove(TEETH.oracle))

    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == "__main__":
    unittest.main()
