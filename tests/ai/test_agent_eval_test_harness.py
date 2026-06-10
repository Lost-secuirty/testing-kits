"""Test suite for agent_eval_test_harness."""

import unittest

from harnesses.ai.agent_eval_test_harness import (
    BAD_TRANSCRIPTS,
    GOOD_TRANSCRIPTS,
    SCENARIOS,
    TOOLS,
    AgentEvalConfig,
    AgentEvalReport,
    ToolCall,
    Transcript,
    Turn,
    _recovery,
    _recovery_blind,
    _resolved,
    _resolved_trust_claim,
    _retention,
    _retention_first_turn_only,
    _run_self_test,
    _unsafe,
    _unsafe_blind,
    _validity,
    _validity_name_only,
    _validity_no_hallucination_check,
    evaluate,
    list_scenarios,
)


class TestCorpusShape(unittest.TestCase):
    def test_tools_present(self):
        self.assertGreaterEqual(len(TOOLS), 5)
        self.assertTrue(any(t.dangerous for t in TOOLS))

    def test_good_and_bad_sets(self):
        self.assertGreaterEqual(len(GOOD_TRANSCRIPTS), 4)
        self.assertGreaterEqual(len(BAD_TRANSCRIPTS), 5)

    def test_transcripts_have_goal_states(self):
        for t in GOOD_TRANSCRIPTS + BAD_TRANSCRIPTS:
            self.assertTrue(t.goal_state)
            self.assertGreaterEqual(len(t.turns), 1)


class TestOracleMetrics(unittest.TestCase):
    def test_resolved(self):
        self.assertTrue(_resolved(GOOD_TRANSCRIPTS[0], TOOLS))
        self.assertFalse(_resolved(BAD_TRANSCRIPTS[0], TOOLS))

    def test_validity_counts(self):
        v, tot, hal = _validity(GOOD_TRANSCRIPTS[0], TOOLS)
        self.assertEqual(v, tot)
        self.assertEqual(hal, 0)

    def test_recovery(self):
        # good[1] errors once then succeeds -> recovered
        self.assertEqual(_recovery(GOOD_TRANSCRIPTS[1], TOOLS), (1, 1))
        # bad[2] errors and never recovers
        rec, err = _recovery(BAD_TRANSCRIPTS[2], TOOLS)
        self.assertEqual(rec, 0)
        self.assertGreaterEqual(err, 1)

    def test_retention(self):
        self.assertEqual(_retention(GOOD_TRANSCRIPTS[2], TOOLS), (True, True))
        self.assertEqual(_retention(BAD_TRANSCRIPTS[5], TOOLS), (False, True))

    def test_unsafe(self):
        self.assertEqual(_unsafe(GOOD_TRANSCRIPTS[3], TOOLS), 0)  # confirmed
        self.assertGreaterEqual(_unsafe(BAD_TRANSCRIPTS[4], TOOLS), 1)  # unconfirmed


class TestOracleAggregate(unittest.TestCase):
    def test_good_set_meets_floors(self):
        cfg = AgentEvalConfig()
        rep = evaluate(GOOD_TRANSCRIPTS, TOOLS, cfg)
        self.assertTrue(rep.meets_floors(cfg))
        self.assertGreaterEqual(rep.task_resolved_rate, cfg.resolved_floor)
        self.assertGreaterEqual(rep.tool_validity_rate, cfg.validity_floor)
        self.assertGreaterEqual(rep.recovery_rate, cfg.recovery_floor)
        self.assertGreaterEqual(rep.retention_rate, cfg.retention_floor)
        self.assertLessEqual(rep.loop_rate, cfg.loop_ceil)
        self.assertEqual(rep.hallucinated_tools, 0)
        self.assertEqual(rep.premature_claims, 0)
        self.assertEqual(rep.unsafe_actions, 0)

    def test_bad_set_fails_floors(self):
        cfg = AgentEvalConfig()
        rep = evaluate(BAD_TRANSCRIPTS, TOOLS, cfg)
        self.assertFalse(rep.meets_floors(cfg))
        self.assertGreaterEqual(rep.hallucinated_tools, 1)
        self.assertGreaterEqual(rep.premature_claims, 1)
        self.assertGreaterEqual(rep.unsafe_actions, 1)


class TestBuggyGradersCaught(unittest.TestCase):
    def test_claim_trusting_misses_premature(self):
        bad = [BAD_TRANSCRIPTS[0]]
        o = evaluate(bad, TOOLS)
        b = evaluate(bad, TOOLS, resolved_fn=_resolved_trust_claim,
                     premature_fn=lambda t, tl: 0)
        self.assertGreaterEqual(o.premature_claims, 1)
        self.assertEqual(b.premature_claims, 0)
        self.assertEqual(b.task_resolved_rate, 1.0)

    def test_name_only_misses_bad_type(self):
        wrong = [Transcript("wt", goal_state="s", turns=(
            Turn(0, "x", (ToolCall(0, "get_order", (("order_id", "17"),)),),
                 None, False, "s"),))]
        cfg = AgentEvalConfig()
        self.assertLess(evaluate(wrong, TOOLS, cfg).tool_validity_rate, cfg.validity_floor)
        self.assertEqual(
            evaluate(wrong, TOOLS, cfg, validity_fn=_validity_name_only).tool_validity_rate,
            1.0)

    def test_no_hallucination_check_misses(self):
        hall = [Transcript("h", goal_state="s", turns=(
            Turn(0, "x", (ToolCall(0, "magic_fix", ()),), None, False, "s"),))]
        self.assertGreaterEqual(evaluate(hall, TOOLS).hallucinated_tools, 1)
        self.assertEqual(
            evaluate(hall, TOOLS, validity_fn=_validity_no_hallucination_check)
            .hallucinated_tools, 0)

    def test_recovery_blind_misses(self):
        cfg = AgentEvalConfig()
        bad = [BAD_TRANSCRIPTS[2]]
        self.assertLess(evaluate(bad, TOOLS, cfg).recovery_rate, cfg.recovery_floor)
        self.assertGreaterEqual(
            evaluate(bad, TOOLS, cfg, recovery_fn=_recovery_blind).recovery_rate,
            cfg.recovery_floor)

    def test_loop_ignoring_misses(self):
        cfg = AgentEvalConfig()
        bad = [BAD_TRANSCRIPTS[3]]
        self.assertGreater(evaluate(bad, TOOLS, cfg).loop_rate, cfg.loop_ceil)
        self.assertEqual(
            evaluate(bad, TOOLS, cfg, loop_fn=lambda t, tl: 0.0).loop_rate, 0.0)

    def test_constraint_amnesiac_misses_late(self):
        cfg = AgentEvalConfig()
        bad = [BAD_TRANSCRIPTS[5]]
        self.assertLess(evaluate(bad, TOOLS, cfg).retention_rate, cfg.retention_floor)
        self.assertGreaterEqual(
            evaluate(bad, TOOLS, cfg, retention_fn=_retention_first_turn_only)
            .retention_rate, cfg.retention_floor)

    def test_confirmation_blind_misses(self):
        bad = [BAD_TRANSCRIPTS[4]]
        self.assertGreaterEqual(evaluate(bad, TOOLS).unsafe_actions, 1)
        self.assertEqual(evaluate(bad, TOOLS, unsafe_fn=_unsafe_blind).unsafe_actions, 0)


class TestReportLogic(unittest.TestCase):
    def test_meets_floors_predicate(self):
        cfg = AgentEvalConfig()
        clean = AgentEvalReport(4, 1.0, 1.0, 0, 1.0, 0.0, 1.0, 0, 0)
        self.assertTrue(clean.meets_floors(cfg))
        self.assertFalse(AgentEvalReport(4, 0.5, 1.0, 0, 1.0, 0.0, 1.0, 0, 0)
                         .meets_floors(cfg))
        self.assertFalse(AgentEvalReport(4, 1.0, 1.0, 0, 1.0, 0.0, 1.0, 1, 0)
                         .meets_floors(cfg))
        self.assertFalse(AgentEvalReport(4, 1.0, 1.0, 0, 1.0, 0.5, 1.0, 0, 0)
                         .meets_floors(cfg))


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
