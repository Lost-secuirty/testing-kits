"""
Tests for agentic_test_harness.py  (~109 tests)
Pure stdlib, zero external dependencies.
"""

import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.client import HTTPConnection

from agentic_test_harness import (
    AgentEvalReport,
    ArgSchemaDriftTester,
    FidelityResult,
    LoopDetectionResult,
    MockAgent,
    MockAgenticServer,
    MultiTurnResult,
    MultiTurnStateTester,
    PlanVsExecutionResult,
    PlanVsExecutionTester,
    RunawayLoopDetector,
    SchemaDriftResult,
    StateTurn,
    ToolCall,
    ToolCallFidelityTester,
    ToolRegistry,
    ToolSchema,
    UnsafeToolUseTester,
    UnsafeToolUseResult,
    GUARD_TOOL_NAME,
    _call_signature,
    _check_type,
    _schema_to_dict,
    _dict_to_schema,
)


# ===========================================================================
# ToolSchema tests
# ===========================================================================

class TestToolSchema(unittest.TestCase):

    def test_create_minimal(self):
        s = ToolSchema(name="foo", description="bar")
        self.assertEqual(s.name, "foo")
        self.assertEqual(s.description, "bar")
        self.assertEqual(s.required_args, [])
        self.assertEqual(s.optional_args, [])
        self.assertEqual(s.arg_types, {})
        self.assertEqual(s.enum_constraints, {})
        self.assertFalse(s.dangerous)

    def test_create_full(self):
        s = ToolSchema(
            name="delete_file",
            description="Deletes a file",
            required_args=["path"],
            optional_args=["force"],
            arg_types={"path": "str", "force": "bool"},
            enum_constraints={"force": [True, False]},
            dangerous=True,
        )
        self.assertTrue(s.dangerous)
        self.assertIn("path", s.required_args)
        self.assertIn("force", s.optional_args)
        self.assertEqual(s.arg_types["path"], "str")

    def test_dangerous_default_false(self):
        s = ToolSchema(name="x", description="y")
        self.assertFalse(s.dangerous)

    def test_independent_field_defaults(self):
        a = ToolSchema(name="a", description="")
        b = ToolSchema(name="b", description="")
        a.required_args.append("x")
        self.assertNotIn("x", b.required_args)


# ===========================================================================
# ToolRegistry tests
# ===========================================================================

class TestToolRegistry(unittest.TestCase):

    def setUp(self):
        self.reg = ToolRegistry()
        self.schema = ToolSchema(name="search", description="Search docs")

    def test_register_and_lookup(self):
        self.reg.register(self.schema)
        result = self.reg.lookup("search")
        self.assertIs(result, self.schema)

    def test_lookup_missing(self):
        self.assertIsNone(self.reg.lookup("nonexistent"))

    def test_is_known_true(self):
        self.reg.register(self.schema)
        self.assertTrue(self.reg.is_known("search"))

    def test_is_known_false(self):
        self.assertFalse(self.reg.is_known("search"))

    def test_all_schemas_empty(self):
        self.assertEqual(self.reg.all_schemas(), [])

    def test_all_schemas_populated(self):
        self.reg.register(self.schema)
        self.assertIn(self.schema, self.reg.all_schemas())

    def test_register_overwrites(self):
        self.reg.register(self.schema)
        new_schema = ToolSchema(name="search", description="Updated")
        self.reg.register(new_schema)
        self.assertEqual(self.reg.lookup("search").description, "Updated")

    def test_unregister(self):
        self.reg.register(self.schema)
        self.reg.unregister("search")
        self.assertFalse(self.reg.is_known("search"))

    def test_unregister_missing_no_error(self):
        self.reg.unregister("nonexistent")  # should not raise


# ===========================================================================
# ToolCall tests
# ===========================================================================

class TestToolCall(unittest.TestCase):

    def test_create_minimal(self):
        tc = ToolCall(tool_name="ping")
        self.assertEqual(tc.tool_name, "ping")
        self.assertEqual(tc.args, {})
        self.assertIsInstance(tc.call_id, str)

    def test_call_id_unique(self):
        a = ToolCall(tool_name="x")
        b = ToolCall(tool_name="x")
        self.assertNotEqual(a.call_id, b.call_id)

    def test_explicit_call_id(self):
        tc = ToolCall(tool_name="x", call_id="abc-123")
        self.assertEqual(tc.call_id, "abc-123")

    def test_args_stored(self):
        tc = ToolCall(tool_name="search", args={"query": "hello"})
        self.assertEqual(tc.args["query"], "hello")


# ===========================================================================
# MockAgent tests
# ===========================================================================

class TestMockAgent(unittest.TestCase):

    def setUp(self):
        self.agent = MockAgent()

    def test_run_empty_script(self):
        result = self.agent.run()
        self.assertEqual(result, [])

    def test_run_single_step(self):
        tc = ToolCall(tool_name="ping")
        self.agent.add_step(tc)
        result = self.agent.run()
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], tc)

    def test_run_multiple_steps(self):
        steps = [ToolCall(tool_name=f"tool{i}") for i in range(5)]
        for s in steps:
            self.agent.add_step(s)
        result = self.agent.run()
        self.assertEqual(len(result), 5)

    def test_max_rounds_limits_execution(self):
        for i in range(10):
            self.agent.add_step(ToolCall(tool_name="x"))
        result = self.agent.run(max_rounds=3)
        self.assertEqual(len(result), 3)

    def test_max_rounds_zero(self):
        self.agent.add_step(ToolCall(tool_name="x"))
        result = self.agent.run(max_rounds=0)
        self.assertEqual(result, [])

    def test_clear(self):
        self.agent.add_step(ToolCall(tool_name="x"))
        self.agent.clear()
        result = self.agent.run()
        self.assertEqual(result, [])

    def test_order_preserved(self):
        names = ["alpha", "beta", "gamma"]
        for n in names:
            self.agent.add_step(ToolCall(tool_name=n))
        result = self.agent.run()
        self.assertEqual([c.tool_name for c in result], names)


# ===========================================================================
# _check_type helper tests
# ===========================================================================

class TestCheckType(unittest.TestCase):

    def test_str_valid(self):
        self.assertTrue(_check_type("hello", "str"))

    def test_str_invalid(self):
        self.assertFalse(_check_type(123, "str"))

    def test_int_valid(self):
        self.assertTrue(_check_type(42, "int"))

    def test_int_bool_invalid(self):
        # bool is subclass of int but we treat it as separate
        self.assertFalse(_check_type(True, "int"))

    def test_float_valid(self):
        self.assertTrue(_check_type(3.14, "float"))

    def test_float_bool_invalid(self):
        self.assertFalse(_check_type(False, "float"))

    def test_bool_valid(self):
        self.assertTrue(_check_type(True, "bool"))

    def test_list_valid(self):
        self.assertTrue(_check_type([1, 2], "list"))

    def test_dict_valid(self):
        self.assertTrue(_check_type({"a": 1}, "dict"))

    def test_unknown_type_permissive(self):
        self.assertTrue(_check_type("anything", "CustomType"))

    def test_number_alias(self):
        self.assertTrue(_check_type(1.5, "number"))

    def test_string_alias(self):
        self.assertTrue(_check_type("x", "string"))

    def test_array_alias(self):
        self.assertTrue(_check_type([], "array"))

    def test_object_alias(self):
        self.assertTrue(_check_type({}, "object"))


# ===========================================================================
# ToolCallFidelityTester tests
# ===========================================================================

def _make_registry():
    reg = ToolRegistry()
    reg.register(ToolSchema(
        name="search",
        description="Search",
        required_args=["query"],
        optional_args=["limit"],
        arg_types={"query": "str", "limit": "int"},
        enum_constraints={},
    ))
    reg.register(ToolSchema(
        name="get_item",
        description="Get item",
        required_args=["id"],
        optional_args=[],
        arg_types={"id": "str"},
        enum_constraints={"id": ["a", "b", "c"]},
    ))
    return reg


class TestToolCallFidelityTester(unittest.TestCase):

    def setUp(self):
        self.reg = _make_registry()
        self.tester = ToolCallFidelityTester(self.reg)
        self.strict_tester = ToolCallFidelityTester(self.reg, strict=True)

    def test_all_valid(self):
        calls = [
            ToolCall(tool_name="search", args={"query": "hello"}),
            ToolCall(tool_name="get_item", args={"id": "a"}),
        ]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.fidelity_ratio, 1.0)
        self.assertEqual(result.valid_calls, 2)

    def test_unknown_tool(self):
        calls = [ToolCall(tool_name="unknown_tool", args={})]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.valid_calls, 0)
        self.assertTrue(any("Unknown tool" in e for e in result.errors))

    def test_missing_required_arg(self):
        calls = [ToolCall(tool_name="search", args={})]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.valid_calls, 0)
        self.assertTrue(any("Missing required arg" in e for e in result.errors))

    def test_wrong_arg_type(self):
        calls = [ToolCall(tool_name="search", args={"query": 123})]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.valid_calls, 0)
        self.assertTrue(any("expected str" in e for e in result.errors))

    def test_enum_violation(self):
        calls = [ToolCall(tool_name="get_item", args={"id": "z"})]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.valid_calls, 0)
        self.assertTrue(any("not in enum" in e for e in result.errors))

    def test_strict_unknown_extra_arg(self):
        calls = [ToolCall(tool_name="search", args={"query": "hi", "extra": "nope"})]
        result = self.strict_tester.evaluate(calls)
        self.assertEqual(result.valid_calls, 0)
        self.assertTrue(any("Unknown arg" in e for e in result.errors))

    def test_non_strict_extra_arg_ok(self):
        calls = [ToolCall(tool_name="search", args={"query": "hi", "extra": "nope"})]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.valid_calls, 1)

    def test_empty_calls(self):
        result = self.tester.evaluate([])
        self.assertEqual(result.fidelity_ratio, 1.0)
        self.assertEqual(result.total_calls, 0)

    def test_partial_validity(self):
        calls = [
            ToolCall(tool_name="search", args={"query": "ok"}),
            ToolCall(tool_name="search", args={}),  # missing required
        ]
        result = self.tester.evaluate(calls)
        self.assertAlmostEqual(result.fidelity_ratio, 0.5)

    def test_fidelity_ratio_zero(self):
        calls = [
            ToolCall(tool_name="bad1"),
            ToolCall(tool_name="bad2"),
        ]
        result = self.tester.evaluate(calls)
        self.assertEqual(result.fidelity_ratio, 0.0)


# ===========================================================================
# FidelityResult tests
# ===========================================================================

class TestFidelityResult(unittest.TestCase):

    def test_ratio_full(self):
        r = FidelityResult(valid_calls=5, total_calls=5)
        self.assertEqual(r.fidelity_ratio, 1.0)

    def test_ratio_zero_total(self):
        r = FidelityResult(valid_calls=0, total_calls=0)
        self.assertEqual(r.fidelity_ratio, 1.0)

    def test_ratio_partial(self):
        r = FidelityResult(valid_calls=3, total_calls=4)
        self.assertAlmostEqual(r.fidelity_ratio, 0.75)


# ===========================================================================
# RunawayLoopDetector tests
# ===========================================================================

class TestRunawayLoopDetector(unittest.TestCase):

    def setUp(self):
        self.detector = RunawayLoopDetector(max_rounds=5, repeat_threshold=2)

    def test_no_loop_short(self):
        calls = [ToolCall(tool_name=f"t{i}") for i in range(3)]
        result = self.detector.analyze(calls)
        self.assertFalse(result.loop_detected)
        self.assertFalse(result.exceeded_max_rounds)
        self.assertFalse(result.repeated_signature)

    def test_exceeds_max_rounds(self):
        calls = [ToolCall(tool_name="x") for _ in range(5)]
        result = self.detector.analyze(calls)
        self.assertTrue(result.exceeded_max_rounds)
        self.assertTrue(result.loop_detected)

    def test_repeated_signature_detected(self):
        call = ToolCall(tool_name="ping", args={"n": 1})
        call2 = ToolCall(tool_name="ping", args={"n": 1})
        result = self.detector.analyze([call, call2])
        self.assertTrue(result.repeated_signature)
        self.assertTrue(result.loop_detected)

    def test_different_args_not_repeated(self):
        calls = [
            ToolCall(tool_name="ping", args={"n": 1}),
            ToolCall(tool_name="ping", args={"n": 2}),
        ]
        result = self.detector.analyze(calls)
        self.assertFalse(result.repeated_signature)

    def test_loop_detected_property(self):
        r = LoopDetectionResult(exceeded_max_rounds=False, repeated_signature=False)
        self.assertFalse(r.loop_detected)

    def test_loop_detected_exceeded(self):
        r = LoopDetectionResult(exceeded_max_rounds=True, repeated_signature=False)
        self.assertTrue(r.loop_detected)

    def test_loop_detected_repeated(self):
        r = LoopDetectionResult(exceeded_max_rounds=False, repeated_signature=True)
        self.assertTrue(r.loop_detected)

    def test_call_signature_stable(self):
        c1 = ToolCall(tool_name="t", args={"a": 1, "b": 2}, call_id="x")
        c2 = ToolCall(tool_name="t", args={"b": 2, "a": 1}, call_id="y")
        self.assertEqual(_call_signature(c1), _call_signature(c2))

    def test_empty_calls(self):
        result = self.detector.analyze([])
        self.assertFalse(result.loop_detected)


# ===========================================================================
# MultiTurnStateTester tests
# ===========================================================================

class TestMultiTurnStateTester(unittest.TestCase):

    def test_state_propagated(self):
        tester = MultiTurnStateTester()
        # Turn 1: set session_id = "abc"
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="login", args={"user": "alice"}),
            state_key="session_id",
            state_value="abc",
        ))
        # Turn 2: unrelated
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="noop", args={}),
        ))
        # Turn 3: use session_id
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="fetch", args={"session_id": "abc"}),
            verify_state_key="session_id",
        ))
        result = tester.run()
        self.assertTrue(result.passed)
        self.assertEqual(result.errors, [])

    def test_state_not_propagated(self):
        tester = MultiTurnStateTester()
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="login", args={}),
            state_key="session_id",
            state_value="abc",
        ))
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="fetch", args={"session_id": "WRONG"}),
            verify_state_key="session_id",
        ))
        result = tester.run()
        self.assertFalse(result.passed)
        self.assertTrue(len(result.errors) > 0)

    def test_no_turns(self):
        tester = MultiTurnStateTester()
        result = tester.run()
        self.assertTrue(result.passed)

    def test_explicit_verify_value(self):
        tester = MultiTurnStateTester()
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="set", args={"token": "tok123"}),
            verify_state_key="token",
            verify_state_value="tok123",
        ))
        result = tester.run()
        self.assertTrue(result.passed)

    def test_explicit_verify_value_wrong(self):
        tester = MultiTurnStateTester()
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="set", args={"token": "wrong"}),
            verify_state_key="token",
            verify_state_value="tok123",
        ))
        result = tester.run()
        self.assertFalse(result.passed)

    def test_multiple_state_keys(self):
        tester = MultiTurnStateTester()
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="init", args={}),
            state_key="user", state_value="bob",
        ))
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="init2", args={}),
            state_key="role", state_value="admin",
        ))
        tester.add_turn(StateTurn(
            tool_call=ToolCall(tool_name="act", args={"user": "bob", "role": "admin"}),
            verify_state_key="user",
        ))
        result = tester.run()
        self.assertTrue(result.passed)


# ===========================================================================
# ArgSchemaDriftTester tests
# ===========================================================================

class TestArgSchemaDriftTester(unittest.TestCase):

    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(ToolSchema(
            name="fetch",
            description="Fetch data",
            required_args=["url"],
            optional_args=["timeout"],
            arg_types={"url": "str", "timeout": "int"},
        ))

    def test_no_drift(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        result = drifter.detect_drifts()
        self.assertFalse(result.has_drifts)

    def test_required_args_drift(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        schema = self.reg.lookup("fetch")
        schema.required_args.append("auth_token")
        result = drifter.detect_drifts()
        self.assertTrue(result.has_drifts)
        self.assertTrue(any("required_args" in d for d in result.drifts))

    def test_arg_types_drift(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        # Replace schema with changed arg_types
        self.reg.register(ToolSchema(
            name="fetch",
            description="Fetch data",
            required_args=["url"],
            optional_args=["timeout"],
            arg_types={"url": "str", "timeout": "str"},  # changed
        ))
        result = drifter.detect_drifts()
        self.assertTrue(result.has_drifts)
        self.assertTrue(any("arg_types" in d for d in result.drifts))

    def test_tool_removed(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        self.reg.unregister("fetch")
        result = drifter.detect_drifts()
        self.assertTrue(result.has_drifts)
        self.assertTrue(any("removed" in d for d in result.drifts))

    def test_tool_added_after_snapshot(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        self.reg.register(ToolSchema(name="new_tool", description="New"))
        result = drifter.detect_drifts()
        self.assertTrue(result.has_drifts)
        self.assertTrue(any("added" in d for d in result.drifts))

    def test_dangerous_flag_drift(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        self.reg.register(ToolSchema(
            name="fetch",
            description="Fetch data",
            required_args=["url"],
            optional_args=["timeout"],
            arg_types={"url": "str", "timeout": "int"},
            dangerous=True,
        ))
        result = drifter.detect_drifts()
        self.assertTrue(result.has_drifts)
        self.assertTrue(any("dangerous" in d for d in result.drifts))

    def test_enum_constraints_drift(self):
        drifter = ArgSchemaDriftTester(self.reg)
        drifter.snapshot()
        self.reg.register(ToolSchema(
            name="fetch",
            description="Fetch data",
            required_args=["url"],
            optional_args=["timeout"],
            arg_types={"url": "str", "timeout": "int"},
            enum_constraints={"url": ["http", "https"]},
        ))
        result = drifter.detect_drifts()
        self.assertTrue(result.has_drifts)

    def test_schema_drift_result_has_drifts_false(self):
        r = SchemaDriftResult(drifts=[])
        self.assertFalse(r.has_drifts)

    def test_schema_drift_result_has_drifts_true(self):
        r = SchemaDriftResult(drifts=["something changed"])
        self.assertTrue(r.has_drifts)


# ===========================================================================
# PlanVsExecutionTester tests
# ===========================================================================

class TestPlanVsExecutionTester(unittest.TestCase):

    def test_exact_match(self):
        plan = ["search", "get_item", "format"]
        tester = PlanVsExecutionTester(plan)
        calls = [ToolCall(tool_name=n) for n in plan]
        result = tester.verify(calls)
        self.assertTrue(result.matches)
        self.assertEqual(result.violations, [])

    def test_wrong_order(self):
        tester = PlanVsExecutionTester(["search", "format"])
        calls = [ToolCall(tool_name="format"), ToolCall(tool_name="search")]
        result = tester.verify(calls)
        self.assertFalse(result.matches)
        self.assertTrue(len(result.violations) > 0)

    def test_skipped_step(self):
        tester = PlanVsExecutionTester(["search", "filter", "format"])
        calls = [ToolCall(tool_name="search"), ToolCall(tool_name="format")]
        result = tester.verify(calls)
        self.assertFalse(result.matches)

    def test_extra_step(self):
        tester = PlanVsExecutionTester(["search"])
        calls = [ToolCall(tool_name="search"), ToolCall(tool_name="extra")]
        result = tester.verify(calls)
        self.assertFalse(result.matches)
        self.assertTrue(any("extra" in v for v in result.violations))

    def test_empty_plan_empty_execution(self):
        tester = PlanVsExecutionTester([])
        result = tester.verify([])
        self.assertTrue(result.matches)

    def test_empty_plan_with_execution(self):
        tester = PlanVsExecutionTester([])
        calls = [ToolCall(tool_name="unexpected")]
        result = tester.verify(calls)
        self.assertFalse(result.matches)

    def test_plan_with_no_execution(self):
        tester = PlanVsExecutionTester(["search"])
        result = tester.verify([])
        self.assertFalse(result.matches)
        self.assertTrue(any("stopped" in v for v in result.violations))

    def test_plan_vs_execution_result_defaults(self):
        r = PlanVsExecutionResult(matches=True)
        self.assertEqual(r.violations, [])


# ===========================================================================
# UnsafeToolUseTester tests
# ===========================================================================

class TestUnsafeToolUseTester(unittest.TestCase):

    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(ToolSchema(name="safe_op", description="Safe", dangerous=False))
        self.reg.register(ToolSchema(name="nuke", description="Delete all", dangerous=True))
        self.reg.register(ToolSchema(name=GUARD_TOOL_NAME, description="Confirm"))
        self.tester = UnsafeToolUseTester(self.reg)

    def test_safe_call_ok(self):
        calls = [ToolCall(tool_name="safe_op")]
        result = self.tester.analyze(calls)
        self.assertFalse(result.has_unsafe_calls)

    def test_dangerous_without_guard(self):
        calls = [ToolCall(tool_name="nuke")]
        result = self.tester.analyze(calls)
        self.assertTrue(result.has_unsafe_calls)
        self.assertTrue(any("nuke" in u for u in result.unsafe_calls))

    def test_dangerous_with_guard(self):
        calls = [
            ToolCall(tool_name=GUARD_TOOL_NAME),
            ToolCall(tool_name="nuke"),
        ]
        result = self.tester.analyze(calls)
        self.assertFalse(result.has_unsafe_calls)

    def test_dangerous_with_non_adjacent_guard(self):
        calls = [
            ToolCall(tool_name=GUARD_TOOL_NAME),
            ToolCall(tool_name="safe_op"),
            ToolCall(tool_name="nuke"),
        ]
        result = self.tester.analyze(calls)
        self.assertTrue(result.has_unsafe_calls)

    def test_unknown_tool_skipped(self):
        calls = [ToolCall(tool_name="unknown_dangerous")]
        result = self.tester.analyze(calls)
        self.assertFalse(result.has_unsafe_calls)

    def test_multiple_dangerous_calls_guarded(self):
        calls = [
            ToolCall(tool_name=GUARD_TOOL_NAME),
            ToolCall(tool_name="nuke"),
            ToolCall(tool_name=GUARD_TOOL_NAME),
            ToolCall(tool_name="nuke"),
        ]
        result = self.tester.analyze(calls)
        self.assertFalse(result.has_unsafe_calls)

    def test_unsafe_tool_use_result_defaults(self):
        r = UnsafeToolUseResult()
        self.assertFalse(r.has_unsafe_calls)
        self.assertEqual(r.unsafe_calls, [])


# ===========================================================================
# AgentEvalReport tests
# ===========================================================================

class TestAgentEvalReport(unittest.TestCase):

    def _clean_report(self):
        return AgentEvalReport(
            fidelity_ratio=1.0,
            loop_detected=False,
            schema_drifts=[],
            plan_violations=[],
            unsafe_calls=[],
        )

    def test_is_clean_all_good(self):
        r = self._clean_report()
        self.assertTrue(r.is_clean())

    def test_is_clean_fidelity_issue(self):
        r = self._clean_report()
        r.fidelity_ratio = 0.8
        self.assertFalse(r.is_clean())

    def test_is_clean_loop_detected(self):
        r = self._clean_report()
        r.loop_detected = True
        self.assertFalse(r.is_clean())

    def test_is_clean_schema_drift(self):
        r = self._clean_report()
        r.schema_drifts = ["drift"]
        self.assertFalse(r.is_clean())

    def test_is_clean_plan_violation(self):
        r = self._clean_report()
        r.plan_violations = ["step 1 skipped"]
        self.assertFalse(r.is_clean())

    def test_is_clean_unsafe_calls(self):
        r = self._clean_report()
        r.unsafe_calls = ["nuke called without guard"]
        self.assertFalse(r.is_clean())

    def test_to_dict_keys(self):
        r = self._clean_report()
        d = r.to_dict()
        for key in ("fidelity_ratio", "loop_detected", "schema_drifts",
                    "plan_violations", "unsafe_calls"):
            self.assertIn(key, d)

    def test_to_dict_values(self):
        r = self._clean_report()
        d = r.to_dict()
        self.assertEqual(d["fidelity_ratio"], 1.0)
        self.assertFalse(d["loop_detected"])


# ===========================================================================
# Helper function tests
# ===========================================================================

class TestHelpers(unittest.TestCase):

    def test_schema_to_dict(self):
        s = ToolSchema(
            name="foo", description="bar",
            required_args=["x"], optional_args=["y"],
            arg_types={"x": "str"}, enum_constraints={"x": ["a"]},
            dangerous=True,
        )
        d = _schema_to_dict(s)
        self.assertEqual(d["name"], "foo")
        self.assertEqual(d["required_args"], ["x"])
        self.assertTrue(d["dangerous"])

    def test_dict_to_schema(self):
        d = {
            "name": "bar",
            "description": "baz",
            "required_args": ["a"],
            "optional_args": [],
            "arg_types": {},
            "enum_constraints": {},
            "dangerous": False,
        }
        s = _dict_to_schema(d)
        self.assertEqual(s.name, "bar")
        self.assertEqual(s.required_args, ["a"])
        self.assertFalse(s.dangerous)

    def test_dict_to_schema_defaults(self):
        s = _dict_to_schema({"name": "x"})
        self.assertEqual(s.required_args, [])
        self.assertFalse(s.dangerous)


# ===========================================================================
# MockAgenticServer / HTTP tests
# ===========================================================================

def _http_get(url: str) -> tuple:
    """Returns (status_code, body_dict)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _http_post(url: str, payload: dict) -> tuple:
    """Returns (status_code, body_dict)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestMockAgenticServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = MockAgenticServer(port=0)
        cls.server.start()
        cls.url = cls.server.url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        # Reset state between tests
        _http_post(f"{self.url}/reset", {})
        # Register a default tool
        _http_post(f"{self.url}/register_tool", {
            "name": "greet",
            "description": "Say hello",
            "required_args": ["name"],
            "optional_args": [],
            "arg_types": {"name": "str"},
            "enum_constraints": {},
            "dangerous": False,
        })

    def test_health_check(self):
        status, body = _http_get(f"{self.url}/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_register_and_get_tool(self):
        _http_post(f"{self.url}/register_tool", {
            "name": "my_tool",
            "description": "Test tool",
            "required_args": ["x"],
        })
        status, body = _http_get(f"{self.url}/tool/my_tool")
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "my_tool")

    def test_get_unknown_tool(self):
        status, body = _http_get(f"{self.url}/tool/nonexistent")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_valid_tool_call(self):
        status, body = _http_post(f"{self.url}/tool_call", {
            "tool_name": "greet",
            "args": {"name": "Alice"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_tool_call_unknown_tool(self):
        status, body = _http_post(f"{self.url}/tool_call", {
            "tool_name": "unknown_tool",
            "args": {},
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_tool_call_missing_required_arg(self):
        status, body = _http_post(f"{self.url}/tool_call", {
            "tool_name": "greet",
            "args": {},
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_list_tool_calls_empty(self):
        status, body = _http_get(f"{self.url}/tool_calls")
        self.assertEqual(status, 200)
        self.assertIsInstance(body["calls"], list)

    def test_list_tool_calls_after_call(self):
        _http_post(f"{self.url}/tool_call", {
            "tool_name": "greet",
            "args": {"name": "Bob"},
        })
        status, body = _http_get(f"{self.url}/tool_calls")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["calls"]), 1)
        self.assertEqual(body["calls"][0]["tool_name"], "greet")

    def test_reset_clears_calls(self):
        _http_post(f"{self.url}/tool_call", {
            "tool_name": "greet",
            "args": {"name": "Charlie"},
        })
        _http_post(f"{self.url}/reset", {})
        status, body = _http_get(f"{self.url}/tool_calls")
        self.assertEqual(len(body["calls"]), 0)

    def test_tool_call_with_call_id(self):
        cid = "test-call-id-123"
        status, body = _http_post(f"{self.url}/tool_call", {
            "tool_name": "greet",
            "args": {"name": "Dave"},
            "call_id": cid,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["call_id"], cid)

    def test_unknown_get_route(self):
        status, body = _http_get(f"{self.url}/nonexistent")
        self.assertEqual(status, 404)

    def test_unknown_post_route(self):
        status, body = _http_post(f"{self.url}/nonexistent", {})
        self.assertEqual(status, 404)

    def test_multiple_tools_registered(self):
        _http_post(f"{self.url}/register_tool", {
            "name": "tool_a", "description": "A",
        })
        _http_post(f"{self.url}/register_tool", {
            "name": "tool_b", "description": "B",
        })
        sa, ba = _http_get(f"{self.url}/tool/tool_a")
        sb, bb = _http_get(f"{self.url}/tool/tool_b")
        self.assertEqual(sa, 200)
        self.assertEqual(sb, 200)

    def test_recorded_calls_via_server_object(self):
        _http_post(f"{self.url}/tool_call", {
            "tool_name": "greet",
            "args": {"name": "Eve"},
        })
        calls = self.server.recorded_calls
        self.assertEqual(len(calls), 1)

    def test_server_url_format(self):
        self.assertTrue(self.server.url.startswith("http://127.0.0.1:"))


# ===========================================================================
# Integration tests
# ===========================================================================

class TestIntegration(unittest.TestCase):
    """End-to-end integration scenarios."""

    def _make_full_registry(self):
        reg = ToolRegistry()
        reg.register(ToolSchema(
            name="search",
            description="Search",
            required_args=["query"],
            optional_args=["limit"],
            arg_types={"query": "str", "limit": "int"},
        ))
        reg.register(ToolSchema(
            name="delete",
            description="Delete something",
            required_args=["id"],
            arg_types={"id": "str"},
            dangerous=True,
        ))
        reg.register(ToolSchema(name=GUARD_TOOL_NAME, description="Confirm action"))
        return reg

    def test_full_eval_clean(self):
        reg = self._make_full_registry()

        agent = MockAgent()
        agent.add_step(ToolCall(tool_name="search", args={"query": "test"}))
        agent.add_step(ToolCall(tool_name=GUARD_TOOL_NAME, args={}))
        agent.add_step(ToolCall(tool_name="delete", args={"id": "item1"}))

        calls = agent.run(max_rounds=10)

        fidelity = ToolCallFidelityTester(reg).evaluate(calls)
        loop = RunawayLoopDetector(max_rounds=10).analyze(calls)
        drift_tester = ArgSchemaDriftTester(reg)
        drift_tester.snapshot()
        drift = drift_tester.detect_drifts()
        plan = PlanVsExecutionTester(["search", GUARD_TOOL_NAME, "delete"]).verify(calls)
        unsafe = UnsafeToolUseTester(reg).analyze(calls)

        report = AgentEvalReport(
            fidelity_ratio=fidelity.fidelity_ratio,
            loop_detected=loop.loop_detected,
            schema_drifts=drift.drifts,
            plan_violations=plan.violations,
            unsafe_calls=unsafe.unsafe_calls,
        )
        self.assertTrue(report.is_clean())

    def test_full_eval_with_issues(self):
        reg = self._make_full_registry()

        agent = MockAgent()
        # Bad call: missing required arg
        agent.add_step(ToolCall(tool_name="search", args={}))
        # Dangerous without guard
        agent.add_step(ToolCall(tool_name="delete", args={"id": "x"}))

        calls = agent.run(max_rounds=10)

        fidelity = ToolCallFidelityTester(reg).evaluate(calls)
        unsafe = UnsafeToolUseTester(reg).analyze(calls)

        self.assertLess(fidelity.fidelity_ratio, 1.0)
        self.assertTrue(unsafe.has_unsafe_calls)

    def test_loop_detection_integration(self):
        agent = MockAgent()
        for _ in range(6):
            agent.add_step(ToolCall(tool_name="stuck", args={"x": 1}))

        calls = agent.run(max_rounds=10)
        detector = RunawayLoopDetector(max_rounds=5, repeat_threshold=2)
        result = detector.analyze(calls)
        self.assertTrue(result.loop_detected)


if __name__ == "__main__":
    unittest.main()
