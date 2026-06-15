"""
Contract / Interface Test Harness (Harness 14 of 36)
Validates function contracts, interface compliance, and invariants.
Pure stdlib, zero external dependencies.

Self-test:
  python harnesses/core/contract_test_harness.py --self-test
"""

import argparse
import enum
import inspect
import json
import logging
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
from urllib.parse import urlparse, parse_qs

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Enums & Core Data Structures
# ---------------------------------------------------------------------------

class ViolationType(enum.Enum):
    PRECONDITION = "PRECONDITION"
    POSTCONDITION = "POSTCONDITION"
    TYPE = "TYPE"
    INVARIANT = "INVARIANT"
    INTERFACE = "INTERFACE"


@dataclass
class ContractViolation(Exception):
    violation_type: ViolationType
    message: str
    function_name: str
    args: Tuple = field(default_factory=tuple)
    result: Any = None

    def __str__(self) -> str:
        return (
            f"ContractViolation({self.violation_type.value}) in '{self.function_name}': "
            f"{self.message} | args={self.args!r} result={self.result!r}"
        )

    def __post_init__(self):
        # Save our 'args' field value before Exception.__init__ clobbers it
        _saved_args = self.args
        super().__init__(str(self))
        # Restore our dataclass 'args' field (Exception.__init__ sets self.args to a tuple)
        object.__setattr__(self, "args", _saved_args)


# ---------------------------------------------------------------------------
# Precondition / Postcondition descriptors
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    check: Callable
    description: str


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class Contract:
    """
    Wraps a function with preconditions, postconditions, type checks, and
    return-type verification. Calling an instance raises ContractViolation
    on the first failure encountered.
    """

    def __init__(
        self,
        func: Callable,
        preconditions: Optional[List[Condition]] = None,
        postconditions: Optional[List[Condition]] = None,
        type_spec: Optional[Dict[str, Type]] = None,
        return_type: Optional[Type] = None,
    ):
        self.func = func
        self.preconditions: List[Condition] = preconditions or []
        self.postconditions: List[Condition] = postconditions or []
        self.type_spec: Dict[str, Type] = type_spec or {}
        self.return_type: Optional[Type] = return_type
        # Expose callable name for diagnostics
        self.__name__ = getattr(func, "__name__", repr(func))
        self.__doc__ = getattr(func, "__doc__", "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _bind_args(self, args: Tuple, kwargs: Dict) -> Dict[str, Any]:
        """Return a mapping of parameter name → value using inspect."""
        try:
            sig = inspect.signature(self.func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return dict(bound.arguments)
        except TypeError:
            return {}

    def _check_types(self, bound: Dict[str, Any], raw_args: Tuple) -> None:
        for param_name, expected_type in self.type_spec.items():
            if param_name in bound:
                value = bound[param_name]
                if not isinstance(value, expected_type):
                    raise ContractViolation(
                        violation_type=ViolationType.TYPE,
                        message=(
                            f"Parameter '{param_name}' expected {expected_type.__name__}, "
                            f"got {type(value).__name__}"
                        ),
                        function_name=self.__name__,
                        args=raw_args,
                        result=None,
                    )

    def _check_return_type(self, result: Any, raw_args: Tuple) -> None:
        if self.return_type is not None and not isinstance(result, self.return_type):
            raise ContractViolation(
                violation_type=ViolationType.TYPE,
                message=(
                    f"Return value expected {self.return_type.__name__}, "
                    f"got {type(result).__name__}"
                ),
                function_name=self.__name__,
                args=raw_args,
                result=result,
            )

    def _check_preconditions(self, bound: Dict[str, Any], raw_args: Tuple) -> None:
        for cond in self.preconditions:
            try:
                ok = cond.check(bound)
            except Exception as exc:
                raise ContractViolation(
                    violation_type=ViolationType.PRECONDITION,
                    message=f"Precondition '{cond.description}' raised: {exc}",
                    function_name=self.__name__,
                    args=raw_args,
                    result=None,
                ) from exc
            if not ok:
                raise ContractViolation(
                    violation_type=ViolationType.PRECONDITION,
                    message=f"Precondition failed: {cond.description}",
                    function_name=self.__name__,
                    args=raw_args,
                    result=None,
                )

    def _check_postconditions(self, bound: Dict[str, Any], result: Any, raw_args: Tuple) -> None:
        for cond in self.postconditions:
            try:
                ok = cond.check(bound, result)
            except Exception as exc:
                raise ContractViolation(
                    violation_type=ViolationType.POSTCONDITION,
                    message=f"Postcondition '{cond.description}' raised: {exc}",
                    function_name=self.__name__,
                    args=raw_args,
                    result=result,
                ) from exc
            if not ok:
                raise ContractViolation(
                    violation_type=ViolationType.POSTCONDITION,
                    message=f"Postcondition failed: {cond.description}",
                    function_name=self.__name__,
                    args=raw_args,
                    result=result,
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __call__(self, *args, **kwargs) -> Any:
        bound = self._bind_args(args, kwargs)

        # 1. Type-check inputs
        self._check_types(bound, args)

        # 2. Preconditions
        self._check_preconditions(bound, args)

        # 3. Execute the wrapped function
        result = self.func(*args, **kwargs)

        # 4. Return type check
        self._check_return_type(result, args)

        # 5. Postconditions
        self._check_postconditions(bound, result, args)

        return result

    def add_precondition(self, check: Callable, description: str) -> "Contract":
        self.preconditions.append(Condition(check=check, description=description))
        return self

    def add_postcondition(self, check: Callable, description: str) -> "Contract":
        self.postconditions.append(Condition(check=check, description=description))
        return self


# ---------------------------------------------------------------------------
# ContractChecker
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    scenario_args: Tuple
    scenario_kwargs: Dict
    passed: bool
    violation: Optional[ContractViolation] = None
    exception: Optional[Exception] = None
    result: Any = None


class ContractChecker:
    """
    Validates a Contract by testing it against multiple input scenarios.
    Distinguishes between expected violations and unexpected failures.
    """

    def __init__(self, contract: Contract):
        self.contract = contract
        self.results: List[ScenarioResult] = []

    def check(
        self,
        scenarios: List[Tuple],
        *,
        expect_violation: Optional[ViolationType] = None,
    ) -> List[ScenarioResult]:
        """
        Run each scenario through the contract.

        Parameters
        ----------
        scenarios:
            List of (args_tuple,) or (args_tuple, kwargs_dict) tuples.
        expect_violation:
            If provided, a scenario is "passed" only when it raises a
            ContractViolation of that type.  Otherwise "passed" means no
            exception was raised.
        """
        self.results = []
        for scenario in scenarios:
            if isinstance(scenario, tuple) and len(scenario) == 2 and isinstance(scenario[1], dict):
                # (args_tuple, kwargs_dict) format
                s_args, s_kwargs = scenario
            elif isinstance(scenario, tuple) and len(scenario) == 1 and isinstance(scenario[0], tuple):
                # ((arg1, arg2, ...),) format — unwrap the inner args tuple
                s_args = scenario[0]
                s_kwargs = {}
            else:
                # bare args tuple: (arg1, arg2, ...)
                s_args, s_kwargs = scenario, {}

            sr = ScenarioResult(scenario_args=s_args, scenario_kwargs=s_kwargs, passed=False)
            try:
                sr.result = self.contract(*s_args, **s_kwargs)
                if expect_violation is None:
                    sr.passed = True
                else:
                    # Expected a violation but none was raised
                    sr.passed = False
            except ContractViolation as cv:
                sr.violation = cv
                if expect_violation is not None and cv.violation_type == expect_violation:
                    sr.passed = True
                elif expect_violation is None:
                    sr.passed = False
            except Exception as exc:
                sr.exception = exc
                sr.passed = False
            self.results.append(sr)
        return self.results

    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def failures(self) -> List[ScenarioResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        return f"ContractChecker: {passed}/{total} scenarios passed."


# ---------------------------------------------------------------------------
# InterfaceSpec
# ---------------------------------------------------------------------------

@dataclass
class MethodSpec:
    name: str
    args: List[str] = field(default_factory=list)          # positional param names (excluding self)
    return_type: Optional[Type] = None
    required: bool = True


class InterfaceSpec:
    """
    Defines a required interface as a mapping of method_name → MethodSpec.
    """

    def __init__(self, name: str):
        self.name = name
        self.methods: Dict[str, MethodSpec] = {}

    def add_method(
        self,
        method_name: str,
        args: Optional[List[str]] = None,
        return_type: Optional[Type] = None,
        required: bool = True,
    ) -> "InterfaceSpec":
        self.methods[method_name] = MethodSpec(
            name=method_name,
            args=args or [],
            return_type=return_type,
            required=required,
        )
        return self

    def __repr__(self) -> str:
        return f"InterfaceSpec(name={self.name!r}, methods={list(self.methods.keys())})"


# ---------------------------------------------------------------------------
# InterfaceChecker
# ---------------------------------------------------------------------------

@dataclass
class InterfaceCheckResult:
    method_name: str
    compliant: bool
    violation_type: Optional[ViolationType] = None
    message: str = ""


class InterfaceChecker:
    """
    Checks whether an object (or class) implements an InterfaceSpec.
    Uses inspect to verify method existence and parameter compatibility.
    """

    def __init__(self, spec: InterfaceSpec):
        self.spec = spec
        self.results: List[InterfaceCheckResult] = []

    def check(self, obj: Any) -> List[InterfaceCheckResult]:
        self.results = []

        for method_name, method_spec in self.spec.methods.items():
            # Does the method exist?
            method = getattr(obj, method_name, None)
            if method is None:
                if method_spec.required:
                    self.results.append(InterfaceCheckResult(
                        method_name=method_name,
                        compliant=False,
                        violation_type=ViolationType.INTERFACE,
                        message=f"Required method '{method_name}' is missing.",
                    ))
                else:
                    self.results.append(InterfaceCheckResult(
                        method_name=method_name,
                        compliant=True,
                        message=f"Optional method '{method_name}' is absent (OK).",
                    ))
                continue

            if not callable(method):
                self.results.append(InterfaceCheckResult(
                    method_name=method_name,
                    compliant=False,
                    violation_type=ViolationType.INTERFACE,
                    message=f"'{method_name}' exists but is not callable.",
                ))
                continue

            # Check parameter names
            try:
                sig = inspect.signature(method)
                params = [
                    p for p in sig.parameters.values()
                    if p.name != "self"
                    and p.kind not in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    )
                ]
                actual_param_names = [p.name for p in params]
            except (ValueError, TypeError):
                actual_param_names = []

            expected_args = method_spec.args
            if expected_args and actual_param_names:
                missing = set(expected_args) - set(actual_param_names)
                if missing:
                    self.results.append(InterfaceCheckResult(
                        method_name=method_name,
                        compliant=False,
                        violation_type=ViolationType.INTERFACE,
                        message=(
                            f"Method '{method_name}' missing parameters: {missing}. "
                            f"Expected {expected_args}, got {actual_param_names}."
                        ),
                    ))
                    continue

            # Check return type annotation if specified
            if method_spec.return_type is not None:
                try:
                    sig = inspect.signature(method)
                    ann = sig.return_annotation
                    if ann is not inspect.Parameter.empty and ann != method_spec.return_type:
                        self.results.append(InterfaceCheckResult(
                            method_name=method_name,
                            compliant=False,
                            violation_type=ViolationType.INTERFACE,
                            message=(
                                f"Method '{method_name}' return annotation is {ann!r}, "
                                f"expected {method_spec.return_type!r}."
                            ),
                        ))
                        continue
                except (ValueError, TypeError):
                    pass

            self.results.append(InterfaceCheckResult(
                method_name=method_name,
                compliant=True,
                message=f"Method '{method_name}' complies with spec.",
            ))

        return self.results

    def all_compliant(self) -> bool:
        return all(r.compliant for r in self.results)

    def violations(self) -> List[InterfaceCheckResult]:
        return [r for r in self.results if not r.compliant]

    def summary(self) -> str:
        total = len(self.results)
        compliant = sum(1 for r in self.results if r.compliant)
        return (
            f"InterfaceChecker({self.spec.name}): {compliant}/{total} methods compliant."
        )


# ---------------------------------------------------------------------------
# InvariantChecker
# ---------------------------------------------------------------------------

@dataclass
class InvariantResult:
    operation_index: int
    operation_name: str
    invariant_description: str
    holds: bool
    message: str = ""


class InvariantChecker:
    """
    Checks that a set of invariants hold on an object after each operation
    in a sequence.
    """

    def __init__(self, invariants: Optional[List[Condition]] = None):
        self.invariants: List[Condition] = invariants or []
        self.results: List[InvariantResult] = []

    def add_invariant(self, check: Callable, description: str) -> "InvariantChecker":
        self.invariants.append(Condition(check=check, description=description))
        return self

    def check_sequence(
        self,
        obj: Any,
        operations: List[Tuple[str, Callable]],
    ) -> List[InvariantResult]:
        """
        Apply each operation to obj and verify all invariants afterwards.

        Parameters
        ----------
        obj:
            The object under test.
        operations:
            List of (name, callable) where callable takes obj as its only arg.
        """
        self.results = []
        for idx, (op_name, op_callable) in enumerate(operations):
            try:
                op_callable(obj)
            except Exception as exc:
                # Record failure for every invariant
                for inv in self.invariants:
                    self.results.append(InvariantResult(
                        operation_index=idx,
                        operation_name=op_name,
                        invariant_description=inv.description,
                        holds=False,
                        message=f"Operation raised: {exc}",
                    ))
                continue

            for inv in self.invariants:
                try:
                    holds = inv.check(obj)
                except Exception as exc:
                    self.results.append(InvariantResult(
                        operation_index=idx,
                        operation_name=op_name,
                        invariant_description=inv.description,
                        holds=False,
                        message=f"Invariant check raised: {exc}",
                    ))
                    continue

                self.results.append(InvariantResult(
                    operation_index=idx,
                    operation_name=op_name,
                    invariant_description=inv.description,
                    holds=bool(holds),
                    message="" if holds else f"Invariant violated after '{op_name}'.",
                ))

        return self.results

    def all_hold(self) -> bool:
        return all(r.holds for r in self.results)

    def violations(self) -> List[InvariantResult]:
        return [r for r in self.results if not r.holds]

    def summary(self) -> str:
        total = len(self.results)
        hold = sum(1 for r in self.results if r.holds)
        return f"InvariantChecker: {hold}/{total} invariant checks passed."


# ---------------------------------------------------------------------------
# MockContractHandler – HTTP server
# ---------------------------------------------------------------------------

_SERVER_REGISTRY: Dict[str, "MockContractServer"] = {}


class MockContractHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP request handler that exposes contract-checking endpoints.

    GET  /health                → 200 {"status": "ok"}
    POST /check_contract        → body: {"function": ..., "args": [...]}
    GET  /results               → current checker results
    POST /reset                 → clears results
    GET  /violations            → list of violations
    """

    def log_message(self, fmt: str, *args) -> None:  # suppress default stderr noise
        logger.debug("HTTP %s", fmt % args)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        routes = {
            "/health": self._handle_health,
            "/results": self._handle_results,
            "/violations": self._handle_violations,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json(404, {"error": f"Unknown path: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        routes = {
            "/check_contract": self._handle_check_contract,
            "/reset": self._handle_reset,
            "/register_violation": self._handle_register_violation,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json(404, {"error": f"Unknown path: {path}"})

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
        self._send_json(200, {"status": "ok", "harness": "contract_test_harness"})

    def _handle_results(self) -> None:
        server_obj: MockContractServer = self.server  # type: ignore[assignment]
        self._send_json(200, {"results": server_obj.results})

    def _handle_violations(self) -> None:
        server_obj: MockContractServer = self.server  # type: ignore[assignment]
        self._send_json(200, {"violations": server_obj.violations})

    def _handle_reset(self) -> None:
        if self._read_body() is None:
            return
        server_obj: MockContractServer = self.server  # type: ignore[assignment]
        server_obj.results = []
        server_obj.violations = []
        self._send_json(200, {"status": "reset"})

    def _handle_check_contract(self) -> None:
        body = self._read_body()
        if body is None:
            return
        function_name = body.get("function", "unknown")
        args = body.get("args", [])
        violation = body.get("violation")

        server_obj: MockContractServer = self.server  # type: ignore[assignment]
        record: Dict[str, Any] = {
            "function": function_name,
            "args": args,
            "timestamp": time.time(),
            "violation": violation,
        }
        server_obj.results.append(record)
        if violation:
            server_obj.violations.append(record)
        self._send_json(200, {"status": "recorded", "record": record})

    def _handle_register_violation(self) -> None:
        body = self._read_body()
        if body is None:
            return
        server_obj: MockContractServer = self.server  # type: ignore[assignment]
        server_obj.violations.append(body)
        self._send_json(200, {"status": "violation_registered"})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_body(self) -> Optional[Dict]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
            return None

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockContractServer(HTTPServer):
    """HTTPServer subclass that carries state for the mock contract handler."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        # port=0 → OS assigns a free port
        super().__init__((host, port), MockContractHandler)
        self.results: List[Dict] = []
        self.violations: List[Dict] = []
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def start(self) -> "MockContractServer":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None


# ---------------------------------------------------------------------------
# Convenience factory / decorator
# ---------------------------------------------------------------------------

def contract(
    preconditions: Optional[List[Tuple[Callable, str]]] = None,
    postconditions: Optional[List[Tuple[Callable, str]]] = None,
    type_spec: Optional[Dict[str, Type]] = None,
    return_type: Optional[Type] = None,
) -> Callable:
    """
    Decorator factory that wraps a function in a Contract.

    Usage::

        @contract(
            preconditions=[(lambda b: b["x"] > 0, "x must be positive")],
            return_type=int,
        )
        def double(x: int) -> int:
            return x * 2
    """
    def decorator(func: Callable) -> Contract:
        pre = [Condition(check=c, description=d) for c, d in (preconditions or [])]
        post = [Condition(check=c, description=d) for c, d in (postconditions or [])]
        return Contract(func, preconditions=pre, postconditions=post,
                        type_spec=type_spec, return_type=return_type)
    return decorator


# ---------------------------------------------------------------------------
# Utility: find a free TCP port
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Module-level demo / smoke test
# ---------------------------------------------------------------------------

def _demo() -> None:
    """Quick interactive smoke test of the contract + mock-server machinery."""
    def divide(a: float, b: float) -> float:
        return a / b

    c = Contract(
        divide,
        preconditions=[Condition(lambda bound: bound["b"] != 0, "b must not be zero")],
        postconditions=[Condition(lambda bound, r: isinstance(r, float), "result must be float")],
        type_spec={"a": (int, float), "b": (int, float)},
        return_type=float,
    )

    print("divide(10, 2) =", c(10.0, 2.0))

    try:
        c(10.0, 0.0)
    except ContractViolation as cv:
        print("Caught:", cv)

    # HTTP server smoke test
    server = MockContractServer()
    server.start()
    print(f"Server running at {server.base_url}")
    time.sleep(0.1)
    server.stop()
    print("Done.")


# ===========================================================================
# TEETH: a backward-compatibility contract checker + planted buggy twins.
#
# The harness's real subject is *contract enforcement*: does a checker catch a
# violation of a declared contract/invariant? The teeth model the single most
# common contract-testing failure mode in the wild — a consumer-driven /
# schema-compatibility checker that SILENTLY ACCEPTS a backward-incompatible
# change (a removed required field, a narrowed/changed type, a newly-required
# field a producer didn't have to send before, or a tightened enum).
#
# The oracle is the CORRECT checker. Each Mutant is a checker that misses one
# real class of breaking change. prove() judges a checker's verdict against a
# FROZEN corpus of (old_contract, new_contract) -> expected "compatible" |
# "incompatible" literals — never by comparing to the oracle object at runtime,
# so the check is non-circular. prove(impl) is True iff impl's verdict differs
# from the frozen expectation on any case (i.e. the planted bug is caught).
#
# The oracle is also wired through the harness's own Contract/InvariantChecker
# machinery in the self-test so the existing logic is exercised, not bypassed.
# ===========================================================================

# A "contract" here is a tiny schema describing the fields a producer promises a
# consumer. Each field has a declared type and a required flag, plus an optional
# enum of allowed literal values. Backward compatibility is defined from the
# CONSUMER's standpoint (the party already coded against `old`):
#
#   COMPATIBLE changes (a consumer keeps working):
#     - adding a new OPTIONAL field
#     - relaxing a required field to optional
#     - widening an enum (allowing MORE values)
#     - keeping a field's type and requiredness the same
#
#   INCOMPATIBLE changes (a consumer breaks):
#     - removing a field the consumer reads
#     - changing a field's declared type
#     - making a previously-optional field REQUIRED (producer may omit it... but
#       more importantly a NEW required field forces every producer to send it)
#     - adding a NEW required field
#     - narrowing an enum (removing a previously-allowed value)

_TYPES = ("string", "number", "boolean", "object", "array")


@dataclass(frozen=True)
class FieldSpec:
    """One field in a contract schema."""
    type: str
    required: bool = True
    enum: Optional[Tuple[Any, ...]] = None  # allowed literal values, if constrained


# A contract schema maps field name -> FieldSpec.
ContractSchema = Dict[str, FieldSpec]


def check_compatibility(old: ContractSchema, new: ContractSchema) -> bool:
    """ORACLE: return True iff ``new`` is backward-compatible with ``old``.

    Backward-compatible == a consumer already coded against ``old`` keeps working
    against a producer that now emits ``new``. This is the correct contract
    invariant the harness exists to enforce.
    """
    # 1. No field the consumer reads may disappear.
    for name in old:
        if name not in new:
            return False

    for name, new_spec in new.items():
        old_spec = old.get(name)

        if old_spec is None:
            # A brand-new field is only safe if it is OPTIONAL — a new required
            # field forces every existing producer to start sending it.
            if new_spec.required:
                return False
            continue

        # 2. A field's type may not change.
        if new_spec.type != old_spec.type:
            return False

        # 3. A previously-optional field may not become required.
        if new_spec.required and not old_spec.required:
            return False

        # 4. An enum may be WIDENED but not NARROWED — every value the consumer
        #    might already accept must still be allowed. Adding an enum to a
        #    previously unconstrained field also narrows it.
        if old_spec.enum is None and new_spec.enum is not None:
            return False
        if old_spec.enum is not None:
            if new_spec.enum is None:
                # Constraint dropped entirely => still allows the old values.
                continue
            removed = set(old_spec.enum) - set(new_spec.enum)
            if removed:
                return False

    return True


# --- Planted buggy twins (each misses one real class of breaking change) -----

def check_ignores_removed_field(old: ContractSchema, new: ContractSchema) -> bool:
    """BUG: never checks for removed fields.

    Models a compatibility gate that only validates fields present in the NEW
    schema and forgets that a consumer reading a now-deleted field will break.
    A classic real-world omission in home-grown schema-diff tooling.
    """
    for name, new_spec in new.items():
        old_spec = old.get(name)
        if old_spec is None:
            if new_spec.required:
                return False
            continue
        if new_spec.type != old_spec.type:
            return False
        if new_spec.required and not old_spec.required:
            return False
        if old_spec.enum is not None and new_spec.enum is not None:
            if set(old_spec.enum) - set(new_spec.enum):
                return False
    return True  # BUG: removed fields never make it incompatible


def check_ignores_type_change(old: ContractSchema, new: ContractSchema) -> bool:
    """BUG: treats a field type change as compatible.

    Models a checker that compares only field *names* (presence/requiredness)
    and never the declared type, so a producer switching ``id`` from number to
    string sails through and breaks every consumer that parses it as a number.
    """
    for name in old:
        if name not in new:
            return False
    for name, new_spec in new.items():
        old_spec = old.get(name)
        if old_spec is None:
            if new_spec.required:
                return False
            continue
        # BUG: type mismatch is never flagged.
        if new_spec.required and not old_spec.required:
            return False
        if old_spec.enum is not None and new_spec.enum is not None:
            if set(old_spec.enum) - set(new_spec.enum):
                return False
    return True


def check_allows_new_required_field(old: ContractSchema, new: ContractSchema) -> bool:
    """BUG: accepts a newly-added REQUIRED field as compatible.

    Models the off-by-one in a diff tool that only guards EXISTING fields and
    forgets that adding a brand-new required field forces every producer to
    start emitting it — a backward-incompatible change that this checker passes.
    """
    for name in old:
        if name not in new:
            return False
    for name, new_spec in new.items():
        old_spec = old.get(name)
        if old_spec is None:
            # BUG: a new required field is silently accepted.
            continue
        if new_spec.type != old_spec.type:
            return False
        if new_spec.required and not old_spec.required:
            return False
        if old_spec.enum is not None and new_spec.enum is not None:
            if set(old_spec.enum) - set(new_spec.enum):
                return False
    return True


# --- Frozen corpus: (old, new) -> expected compatibility verdict -------------

@dataclass(frozen=True)
class CompatCase:
    name: str
    old: ContractSchema
    new: ContractSchema
    expected_compatible: bool  # literal, hand-derived from the contract rules
    note: str = ""


COMPAT_CASES: Tuple[CompatCase, ...] = (
    # --- compatible changes the oracle must NOT flag ----------------------
    CompatCase(
        "identical_schema",
        {"id": FieldSpec("number"), "name": FieldSpec("string")},
        {"id": FieldSpec("number"), "name": FieldSpec("string")},
        expected_compatible=True,
        note="no change is always compatible",
    ),
    CompatCase(
        "add_optional_field",
        {"id": FieldSpec("number")},
        {"id": FieldSpec("number"), "nickname": FieldSpec("string", required=False)},
        expected_compatible=True,
        note="adding an optional field is safe",
    ),
    CompatCase(
        "relax_required_to_optional",
        {"id": FieldSpec("number"), "email": FieldSpec("string", required=True)},
        {"id": FieldSpec("number"), "email": FieldSpec("string", required=False)},
        expected_compatible=True,
        note="loosening a requirement is safe for the consumer",
    ),
    CompatCase(
        "widen_enum",
        {"role": FieldSpec("string", enum=("admin", "user"))},
        {"role": FieldSpec("string", enum=("admin", "user", "guest"))},
        expected_compatible=True,
        note="allowing more enum values is safe",
    ),
    # --- incompatible changes the oracle MUST flag ------------------------
    CompatCase(
        "remove_required_field",
        {"id": FieldSpec("number"), "name": FieldSpec("string")},
        {"id": FieldSpec("number")},
        expected_compatible=False,
        note="removing a field a consumer reads breaks it (catches ignores_removed)",
    ),
    CompatCase(
        "change_field_type",
        {"id": FieldSpec("number")},
        {"id": FieldSpec("string")},
        expected_compatible=False,
        note="changing a declared type breaks parsers (catches ignores_type_change)",
    ),
    CompatCase(
        "add_new_required_field",
        {"id": FieldSpec("number")},
        {"id": FieldSpec("number"), "tenant": FieldSpec("string", required=True)},
        expected_compatible=False,
        note="a new required field forces all producers (catches allows_new_required)",
    ),
    CompatCase(
        "narrow_enum",
        {"role": FieldSpec("string", enum=("admin", "user", "guest"))},
        {"role": FieldSpec("string", enum=("admin", "user"))},
        expected_compatible=False,
        note="removing an allowed enum value rejects payloads the consumer sent",
    ),
    CompatCase(
        "constrain_unbounded_enum",
        {"role": FieldSpec("string")},
        {"role": FieldSpec("string", enum=("admin", "user"))},
        expected_compatible=False,
        note="adding an enum to a previously unconstrained field narrows it",
    ),
    CompatCase(
        "make_optional_required",
        {"id": FieldSpec("number"), "phone": FieldSpec("string", required=False)},
        {"id": FieldSpec("number"), "phone": FieldSpec("string", required=True)},
        expected_compatible=False,
        note="promoting an optional field to required is breaking",
    ),
)


def prove(impl: Callable[[ContractSchema, ContractSchema], bool]) -> bool:
    """True iff checker ``impl`` MISJUDGES any frozen corpus case.

    Non-circular: each verdict is compared against the case's frozen literal
    ``expected_compatible`` (never against the oracle object). A checker that
    raises on a corpus case counts as caught. Deterministic and side-effect
    free: no RNG, clock, network, or filesystem I/O.
    """
    for case in COMPAT_CASES:
        try:
            verdict = impl(case.old, case.new)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if bool(verdict) != case.expected_compatible:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=check_compatibility,
    mutants=(
        Mutant("ignores_removed_field", check_ignores_removed_field,
               "compatibility checker never flags a removed field a consumer reads"),
        Mutant("ignores_type_change", check_ignores_type_change,
               "compatibility checker accepts a backward-incompatible field type change"),
        Mutant("allows_new_required_field", check_allows_new_required_field,
               "compatibility checker accepts a newly-added required field as compatible"),
    ),
    corpus_size=len(COMPAT_CASES),
    kind="oracle_swap",
    notes="a removed field, a changed type, and a new required field must all be incompatible",
)


def list_scenarios() -> List[str]:
    """Names of the frozen compatibility corpus cases (the teeth scenarios)."""
    return [c.name for c in COMPAT_CASES]


# ---------------------------------------------------------------------------
# Oracle wired through the harness's OWN Contract / InvariantChecker machinery,
# so the self-test exercises the existing contract-enforcement logic rather than
# only the standalone compatibility function.
# ---------------------------------------------------------------------------

def _oracle_via_contract(case: CompatCase) -> bool:
    """Run check_compatibility through a Contract (precond + return-type guard)
    and an InvariantChecker, returning the compatibility verdict.

    Demonstrates the harness's own primitives enforcing the checker's contract:
    a precondition on input shape, a bool return-type guard, and an invariant
    that the verdict matches what a direct call produces.
    """
    guarded = Contract(
        check_compatibility,
        preconditions=[Condition(lambda b: isinstance(b["old"], dict)
                                 and isinstance(b["new"], dict),
                                 "old and new must be dict schemas")],
        return_type=bool,
    )
    verdict = guarded(case.old, case.new)

    inv = InvariantChecker()
    inv.add_invariant(lambda obj: obj["verdict"] == check_compatibility(obj["old"], obj["new"]),
                      "Contract-guarded verdict equals direct verdict")
    inv.check_sequence(
        {"old": case.old, "new": case.new, "verdict": verdict},
        [("verify", lambda o: None)],
    )
    if not inv.all_hold():
        raise AssertionError("contract-guarded oracle diverged from direct oracle")
    return verdict


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False, *, networked: bool = True) -> int:
    """Report-based self-test.

    1. The correct oracle matches every frozen compatibility expectation, both
       directly and routed through the harness's Contract/InvariantChecker.
    2. Teeth: the oracle is clean and every planted mutant is caught.
    3. (optional) A live socket smoke test of MockContractServer.
    """
    report = Report("core/contract")

    # 1. The correct oracle agrees with every frozen expectation.
    for case in COMPAT_CASES:
        report.add(f"oracle:{case.name}",
                   case.expected_compatible,
                   check_compatibility(case.old, case.new),
                   detail=case.note)
        # ...and the same verdict when routed through the harness's own Contract.
        report.add(f"oracle_via_contract:{case.name}",
                   case.expected_compatible,
                   _oracle_via_contract(case),
                   detail="routed through Contract + InvariantChecker")

    # 2. Teeth: oracle is not flagged and every planted mutant IS flagged.
    report.assert_teeth(TEETH)

    # 3. Live mock-server smoke test (uses a socket; opt-out for pure runs).
    if networked:
        report.record("mock_server_smoke", _mock_server_smoke(),
                      detail="MockContractServer /health over a socket")

    return report.emit(as_json=as_json)


def _mock_server_smoke() -> bool:
    """Start MockContractServer, hit /health and /check_contract, return ok."""
    import urllib.request
    server = MockContractServer()
    server.start()
    try:
        base = server.base_url
        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
            health = json.loads(resp.read())
        if health.get("status") != "ok":
            return False
        payload = json.dumps({"function": "divide", "args": [1, 0],
                              "violation": {"type": "PRECONDITION"}}).encode()
        req = urllib.request.Request(
            f"{base}/check_contract", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            rec = json.loads(resp.read())
        return rec.get("status") == "recorded"
    except Exception:  # noqa: BLE001
        return False
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Contract / interface controls")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen compatibility corpus case names")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the live socket smoke test (teeth/oracle checks only)")
    parser.add_argument("--demo", action="store_true",
                        help="run the interactive contract + mock-server demo")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    if args.demo:
        _demo()
        return 0
    if args.self_test or args.json:
        return _run_self_test(as_json=args.json, networked=not args.no_network)
    # Default: run the self-test (repo convention).
    return _run_self_test(networked=not args.no_network)


if __name__ == "__main__":
    sys.exit(main())
