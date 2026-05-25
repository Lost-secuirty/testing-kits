"""
Test suite for contract_test_harness.py
72 tests covering precondition/postcondition violations, type checking,
interface compliance, invariant checking, and the mock HTTP server.
"""

import inspect
import json
import time
import unittest
import urllib.request
import urllib.error
from urllib.request import urlopen, Request

from contract_test_harness import (
    ViolationType,
    ContractViolation,
    Contract,
    Condition,
    ContractChecker,
    InterfaceSpec,
    InterfaceChecker,
    InvariantChecker,
    MockContractServer,
    ScenarioResult,
    MethodSpec,
    InterfaceCheckResult,
    InvariantResult,
    contract,
)


# ---------------------------------------------------------------------------
# Helper functions used across tests
# ---------------------------------------------------------------------------

def add(a: int, b: int) -> int:
    return a + b


def divide(a: float, b: float) -> float:
    return a / b


def negate(x: int) -> int:
    return -x


def square(x: int) -> int:
    return x * x


# ---------------------------------------------------------------------------
# 1. ViolationType enum
# ---------------------------------------------------------------------------

class TestViolationType(unittest.TestCase):
    """Tests 1-6: ViolationType enum"""

    def test_precondition_member(self):
        self.assertEqual(ViolationType.PRECONDITION.value, "PRECONDITION")

    def test_postcondition_member(self):
        self.assertEqual(ViolationType.POSTCONDITION.value, "POSTCONDITION")

    def test_type_member(self):
        self.assertEqual(ViolationType.TYPE.value, "TYPE")

    def test_invariant_member(self):
        self.assertEqual(ViolationType.INVARIANT.value, "INVARIANT")

    def test_interface_member(self):
        self.assertEqual(ViolationType.INTERFACE.value, "INTERFACE")

    def test_all_members_present(self):
        names = {m.name for m in ViolationType}
        self.assertEqual(names, {"PRECONDITION", "POSTCONDITION", "TYPE", "INVARIANT", "INTERFACE"})


# ---------------------------------------------------------------------------
# 2. ContractViolation dataclass
# ---------------------------------------------------------------------------

class TestContractViolation(unittest.TestCase):
    """Tests 7-14: ContractViolation dataclass"""

    def _make(self, vtype=ViolationType.PRECONDITION, msg="fail", fname="f",
              args=(1,), result=None):
        return ContractViolation(
            violation_type=vtype, message=msg, function_name=fname,
            args=args, result=result,
        )

    def test_is_exception(self):
        cv = self._make()
        self.assertIsInstance(cv, Exception)

    def test_stores_violation_type(self):
        cv = self._make(vtype=ViolationType.TYPE)
        self.assertEqual(cv.violation_type, ViolationType.TYPE)

    def test_stores_message(self):
        cv = self._make(msg="something bad")
        self.assertEqual(cv.message, "something bad")

    def test_stores_function_name(self):
        cv = self._make(fname="my_func")
        self.assertEqual(cv.function_name, "my_func")

    def test_stores_args(self):
        cv = self._make(args=(1, 2, 3))
        self.assertEqual(cv.args, (1, 2, 3))

    def test_stores_result(self):
        cv = self._make(result=42)
        self.assertEqual(cv.result, 42)

    def test_str_contains_type_name(self):
        cv = self._make(vtype=ViolationType.POSTCONDITION)
        self.assertIn("POSTCONDITION", str(cv))

    def test_can_be_raised_and_caught(self):
        with self.assertRaises(ContractViolation):
            raise self._make()


# ---------------------------------------------------------------------------
# 3. Contract – basic functionality
# ---------------------------------------------------------------------------

class TestContractBasic(unittest.TestCase):
    """Tests 15-24: Contract basic functionality"""

    def test_contract_calls_underlying_function(self):
        c = Contract(add)
        self.assertEqual(c(2, 3), 5)

    def test_contract_preserves_function_name(self):
        c = Contract(add)
        self.assertEqual(c.__name__, "add")

    def test_no_conditions_always_passes(self):
        c = Contract(add)
        for x in range(5):
            self.assertEqual(c(x, x), x + x)

    def test_precondition_passes_when_satisfied(self):
        c = Contract(
            divide,
            preconditions=[Condition(lambda b: b["b"] != 0, "b != 0")],
        )
        self.assertAlmostEqual(c(10.0, 2.0), 5.0)

    def test_precondition_raises_on_failure(self):
        c = Contract(
            divide,
            preconditions=[Condition(lambda b: b["b"] != 0, "b != 0")],
        )
        with self.assertRaises(ContractViolation) as ctx:
            c(10.0, 0.0)
        self.assertEqual(ctx.exception.violation_type, ViolationType.PRECONDITION)

    def test_precondition_violation_carries_description(self):
        c = Contract(
            divide,
            preconditions=[Condition(lambda b: b["b"] != 0, "b must not be zero")],
        )
        with self.assertRaises(ContractViolation) as ctx:
            c(10.0, 0.0)
        self.assertIn("b must not be zero", ctx.exception.message)

    def test_postcondition_passes_when_satisfied(self):
        c = Contract(
            add,
            postconditions=[Condition(lambda b, r: r > 0, "result positive")],
        )
        self.assertEqual(c(1, 2), 3)

    def test_postcondition_raises_on_failure(self):
        c = Contract(
            add,
            postconditions=[Condition(lambda b, r: r > 100, "result > 100")],
        )
        with self.assertRaises(ContractViolation) as ctx:
            c(1, 2)
        self.assertEqual(ctx.exception.violation_type, ViolationType.POSTCONDITION)

    def test_postcondition_carries_result(self):
        c = Contract(
            add,
            postconditions=[Condition(lambda b, r: r > 100, "result > 100")],
        )
        with self.assertRaises(ContractViolation) as ctx:
            c(1, 2)
        self.assertEqual(ctx.exception.result, 3)

    def test_multiple_preconditions_all_must_pass(self):
        c = Contract(
            add,
            preconditions=[
                Condition(lambda b: b["a"] > 0, "a positive"),
                Condition(lambda b: b["b"] > 0, "b positive"),
            ],
        )
        with self.assertRaises(ContractViolation):
            c(-1, 5)  # first fails
        with self.assertRaises(ContractViolation):
            c(5, -1)  # second fails


# ---------------------------------------------------------------------------
# 4. Contract – type checking
# ---------------------------------------------------------------------------

class TestContractTypeChecking(unittest.TestCase):
    """Tests 25-32: Contract type checking"""

    def test_type_spec_passes_for_correct_types(self):
        c = Contract(add, type_spec={"a": int, "b": int})
        self.assertEqual(c(3, 4), 7)

    def test_type_spec_raises_for_wrong_type(self):
        c = Contract(add, type_spec={"a": int, "b": int})
        with self.assertRaises(ContractViolation) as ctx:
            c("hello", 4)
        self.assertEqual(ctx.exception.violation_type, ViolationType.TYPE)

    def test_type_violation_names_the_parameter(self):
        c = Contract(add, type_spec={"a": int, "b": int})
        with self.assertRaises(ContractViolation) as ctx:
            c(3, "world")
        self.assertIn("b", ctx.exception.message)

    def test_return_type_passes_for_correct_type(self):
        c = Contract(add, return_type=int)
        self.assertEqual(c(2, 3), 5)

    def test_return_type_raises_for_wrong_type(self):
        def returns_str(x: int) -> str:
            return str(x)
        c = Contract(returns_str, return_type=int)
        with self.assertRaises(ContractViolation) as ctx:
            c(5)
        self.assertEqual(ctx.exception.violation_type, ViolationType.TYPE)

    def test_return_type_violation_carries_result(self):
        def returns_str(x: int) -> str:
            return str(x)
        c = Contract(returns_str, return_type=int)
        with self.assertRaises(ContractViolation) as ctx:
            c(42)
        self.assertEqual(ctx.exception.result, "42")

    def test_type_check_happens_before_preconditions(self):
        # Type check should run before precondition checks
        precond_called = []
        def precond(b):
            precond_called.append(True)
            return True
        c = Contract(add, type_spec={"a": int}, preconditions=[Condition(precond, "always ok")])
        with self.assertRaises(ContractViolation) as ctx:
            c("not_an_int", 2)
        self.assertEqual(ctx.exception.violation_type, ViolationType.TYPE)
        self.assertEqual(precond_called, [])  # precondition never ran

    def test_no_type_spec_skips_type_check(self):
        c = Contract(add)
        # Even with "wrong" types, no ContractViolation for type
        result = c("hello", " world")
        self.assertEqual(result, "hello world")


# ---------------------------------------------------------------------------
# 5. Contract – add_precondition / add_postcondition fluent API
# ---------------------------------------------------------------------------

class TestContractFluentAPI(unittest.TestCase):
    """Tests 33-36: fluent API"""

    def test_add_precondition_returns_contract(self):
        c = Contract(add)
        ret = c.add_precondition(lambda b: True, "always ok")
        self.assertIs(ret, c)

    def test_add_postcondition_returns_contract(self):
        c = Contract(add)
        ret = c.add_postcondition(lambda b, r: True, "always ok")
        self.assertIs(ret, c)

    def test_add_precondition_enforced(self):
        c = Contract(add)
        c.add_precondition(lambda b: b["a"] >= 0, "a >= 0")
        with self.assertRaises(ContractViolation):
            c(-1, 0)

    def test_add_postcondition_enforced(self):
        c = Contract(add)
        c.add_postcondition(lambda b, r: r != 0, "result != 0")
        with self.assertRaises(ContractViolation):
            c(0, 0)


# ---------------------------------------------------------------------------
# 6. ContractChecker
# ---------------------------------------------------------------------------

class TestContractChecker(unittest.TestCase):
    """Tests 37-44: ContractChecker"""

    def _make_checker(self):
        c = Contract(
            divide,
            preconditions=[Condition(lambda b: b["b"] != 0, "b != 0")],
        )
        return ContractChecker(c)

    def test_all_passed_for_valid_scenarios(self):
        cc = self._make_checker()
        cc.check([((1.0, 2.0),), ((3.0, 4.0),)])
        self.assertTrue(cc.all_passed())

    def test_failures_for_invalid_scenarios(self):
        cc = self._make_checker()
        cc.check([((1.0, 0.0),)])
        self.assertFalse(cc.all_passed())
        self.assertEqual(len(cc.failures()), 1)

    def test_expect_violation_marks_as_pass(self):
        cc = self._make_checker()
        cc.check([((1.0, 0.0),)], expect_violation=ViolationType.PRECONDITION)
        self.assertTrue(cc.all_passed())

    def test_expect_violation_wrong_type_marks_fail(self):
        cc = self._make_checker()
        # violation will be PRECONDITION, not POSTCONDITION
        cc.check([((1.0, 0.0),)], expect_violation=ViolationType.POSTCONDITION)
        self.assertFalse(cc.all_passed())

    def test_summary_string_format(self):
        cc = self._make_checker()
        cc.check([((1.0, 2.0),)])
        s = cc.summary()
        self.assertIn("1/1", s)

    def test_mixed_scenarios(self):
        cc = self._make_checker()
        cc.check([((1.0, 2.0),), ((5.0, 0.0),)])
        self.assertEqual(len(cc.failures()), 1)

    def test_results_count_matches_scenarios(self):
        cc = self._make_checker()
        cc.check([((1.0, 1.0),), ((2.0, 2.0),), ((3.0, 3.0),)])
        self.assertEqual(len(cc.results), 3)

    def test_scenario_result_has_result_value_on_success(self):
        cc = self._make_checker()
        cc.check([((4.0, 2.0),)])
        self.assertAlmostEqual(cc.results[0].result, 2.0)


# ---------------------------------------------------------------------------
# 7. InterfaceSpec
# ---------------------------------------------------------------------------

class TestInterfaceSpec(unittest.TestCase):
    """Tests 45-48: InterfaceSpec"""

    def test_add_method_returns_spec(self):
        spec = InterfaceSpec("MyInterface")
        ret = spec.add_method("foo")
        self.assertIs(ret, spec)

    def test_methods_stored(self):
        spec = InterfaceSpec("MyInterface")
        spec.add_method("bar", args=["x", "y"], return_type=int)
        self.assertIn("bar", spec.methods)
        self.assertEqual(spec.methods["bar"].args, ["x", "y"])
        self.assertEqual(spec.methods["bar"].return_type, int)

    def test_repr_contains_name(self):
        spec = InterfaceSpec("TestIface")
        self.assertIn("TestIface", repr(spec))

    def test_required_default_true(self):
        spec = InterfaceSpec("I")
        spec.add_method("m")
        self.assertTrue(spec.methods["m"].required)


# ---------------------------------------------------------------------------
# 8. InterfaceChecker
# ---------------------------------------------------------------------------

class TestInterfaceChecker(unittest.TestCase):
    """Tests 49-60: InterfaceChecker"""

    def _make_spec(self):
        spec = InterfaceSpec("Stack")
        spec.add_method("push", args=["item"])
        spec.add_method("pop")
        spec.add_method("peek")
        spec.add_method("is_empty")
        return spec

    class GoodStack:
        def push(self, item): self._items = getattr(self, "_items", []); self._items.append(item)
        def pop(self): return self._items.pop()
        def peek(self): return self._items[-1]
        def is_empty(self): return len(getattr(self, "_items", [])) == 0

    class BadStack:
        def push(self, item): pass
        # missing pop, peek, is_empty

    def test_compliant_object_passes(self):
        spec = self._make_spec()
        checker = InterfaceChecker(spec)
        results = checker.check(self.GoodStack())
        self.assertTrue(checker.all_compliant())

    def test_non_compliant_object_fails(self):
        spec = self._make_spec()
        checker = InterfaceChecker(spec)
        checker.check(self.BadStack())
        self.assertFalse(checker.all_compliant())

    def test_violations_listed(self):
        spec = self._make_spec()
        checker = InterfaceChecker(spec)
        checker.check(self.BadStack())
        violations = checker.violations()
        vnames = {v.method_name for v in violations}
        self.assertIn("pop", vnames)

    def test_missing_method_violation_type_is_interface(self):
        spec = self._make_spec()
        checker = InterfaceChecker(spec)
        checker.check(self.BadStack())
        for v in checker.violations():
            self.assertEqual(v.violation_type, ViolationType.INTERFACE)

    def test_summary_contains_fraction(self):
        spec = self._make_spec()
        checker = InterfaceChecker(spec)
        checker.check(self.GoodStack())
        self.assertIn("/", checker.summary())

    def test_non_callable_attribute_fails(self):
        class Obj:
            push = "not a method"
        spec = InterfaceSpec("X")
        spec.add_method("push")
        checker = InterfaceChecker(spec)
        checker.check(Obj())
        self.assertFalse(checker.all_compliant())

    def test_optional_missing_method_is_ok(self):
        spec = InterfaceSpec("Optional")
        spec.add_method("optional_method", required=False)
        checker = InterfaceChecker(spec)

        class NoMethod:
            pass

        checker.check(NoMethod())
        self.assertTrue(checker.all_compliant())

    def test_check_class_directly(self):
        spec = self._make_spec()
        checker = InterfaceChecker(spec)
        checker.check(self.GoodStack)
        # class has the methods (unbound), should pass
        self.assertTrue(checker.all_compliant())

    def test_return_type_mismatch_fails(self):
        def bad_is_empty(self) -> int:
            return 1

        class BadReturnStack(self.GoodStack):
            def is_empty(self) -> int:  # annotated as int, not bool
                return 1

        spec = InterfaceSpec("S")
        spec.add_method("is_empty", return_type=bool)
        checker = InterfaceChecker(spec)
        checker.check(BadReturnStack())
        # int != bool annotation → violation
        self.assertFalse(checker.all_compliant())

    def test_correct_return_type_annotation_passes(self):
        class GoodReturn:
            def count(self) -> int:
                return 0

        spec = InterfaceSpec("R")
        spec.add_method("count", return_type=int)
        checker = InterfaceChecker(spec)
        checker.check(GoodReturn())
        self.assertTrue(checker.all_compliant())

    def test_interface_check_result_dataclass(self):
        r = InterfaceCheckResult(method_name="foo", compliant=True)
        self.assertEqual(r.method_name, "foo")
        self.assertTrue(r.compliant)


# ---------------------------------------------------------------------------
# 9. InvariantChecker
# ---------------------------------------------------------------------------

class TestInvariantChecker(unittest.TestCase):
    """Tests 61-68: InvariantChecker"""

    class Counter:
        def __init__(self):
            self.value = 0

        def increment(self):
            self.value += 1

        def decrement(self):
            self.value -= 1

        def reset(self):
            self.value = 0

    def test_invariant_holds_after_all_ops(self):
        checker = InvariantChecker()
        checker.add_invariant(lambda obj: obj.value >= 0, "value >= 0")
        obj = self.Counter()
        ops = [
            ("increment", lambda o: o.increment()),
            ("increment", lambda o: o.increment()),
        ]
        checker.check_sequence(obj, ops)
        self.assertTrue(checker.all_hold())

    def test_invariant_violated_by_decrement_below_zero(self):
        checker = InvariantChecker()
        checker.add_invariant(lambda obj: obj.value >= 0, "value >= 0")
        obj = self.Counter()
        ops = [
            ("decrement", lambda o: o.decrement()),
        ]
        checker.check_sequence(obj, ops)
        self.assertFalse(checker.all_hold())

    def test_violations_reported(self):
        checker = InvariantChecker()
        checker.add_invariant(lambda obj: obj.value >= 0, "value >= 0")
        obj = self.Counter()
        checker.check_sequence(obj, [("dec", lambda o: o.decrement())])
        self.assertEqual(len(checker.violations()), 1)

    def test_multiple_invariants(self):
        checker = InvariantChecker()
        checker.add_invariant(lambda obj: isinstance(obj.value, int), "value is int")
        checker.add_invariant(lambda obj: obj.value >= 0, "value >= 0")
        obj = self.Counter()
        ops = [("inc", lambda o: o.increment())]
        checker.check_sequence(obj, ops)
        self.assertTrue(checker.all_hold())

    def test_summary_string(self):
        checker = InvariantChecker()
        checker.add_invariant(lambda obj: True, "always")
        obj = self.Counter()
        checker.check_sequence(obj, [("noop", lambda o: None)])
        self.assertIn("/", checker.summary())

    def test_results_count(self):
        checker = InvariantChecker()
        checker.add_invariant(lambda obj: True, "always")
        checker.add_invariant(lambda obj: True, "also always")
        obj = self.Counter()
        ops = [("a", lambda o: None), ("b", lambda o: None)]
        checker.check_sequence(obj, ops)
        # 2 operations * 2 invariants = 4 results
        self.assertEqual(len(checker.results), 4)

    def test_add_invariant_returns_checker(self):
        checker = InvariantChecker()
        ret = checker.add_invariant(lambda obj: True, "ok")
        self.assertIs(ret, checker)

    def test_invariant_result_dataclass(self):
        r = InvariantResult(
            operation_index=0,
            operation_name="test",
            invariant_description="desc",
            holds=True,
        )
        self.assertEqual(r.operation_index, 0)
        self.assertTrue(r.holds)


# ---------------------------------------------------------------------------
# 10. MockContractServer HTTP
# ---------------------------------------------------------------------------

class TestMockContractServer(unittest.TestCase):
    """Tests 69-72: MockContractServer HTTP"""

    def setUp(self):
        self.server = MockContractServer()
        self.server.start()
        # brief wait for server thread to start
        time.sleep(0.05)

    def tearDown(self):
        self.server.stop()

    def _get(self, path: str):
        url = self.server.base_url + path
        with urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def _post(self, path: str, payload: dict):
        url = self.server.base_url + path
        data = json.dumps(payload).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def test_health_endpoint(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_check_contract_records_result(self):
        status, body = self._post("/check_contract", {
            "function": "divide",
            "args": [10, 2],
            "violation": None,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "recorded")
        _, results = self._get("/results")
        self.assertEqual(len(results["results"]), 1)

    def test_violation_recorded_in_violations(self):
        self._post("/check_contract", {
            "function": "divide",
            "args": [1, 0],
            "violation": {"type": "PRECONDITION", "message": "b != 0"},
        })
        _, v = self._get("/violations")
        self.assertEqual(len(v["violations"]), 1)

    def test_reset_clears_results(self):
        self._post("/check_contract", {"function": "f", "args": [], "violation": None})
        self._post("/reset", {})
        _, results = self._get("/results")
        self.assertEqual(results["results"], [])


# ---------------------------------------------------------------------------
# 11. contract() decorator
# ---------------------------------------------------------------------------

class TestContractDecorator(unittest.TestCase):
    """Additional decorator tests"""

    def test_decorator_wraps_correctly(self):
        @contract(preconditions=[(lambda b: b["x"] > 0, "x > 0")])
        def double(x):
            return x * 2

        self.assertEqual(double(5), 10)

    def test_decorator_enforces_precondition(self):
        @contract(preconditions=[(lambda b: b["x"] > 0, "x > 0")])
        def double(x):
            return x * 2

        with self.assertRaises(ContractViolation):
            double(-1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
