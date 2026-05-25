"""
Agentic AI / Tool-Calling Test Harness (Harness 33 of 36)

Tests AI-agent control-flow and tool-use correctness using a deterministic
scripted MockAgent. Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# ToolSchema
# ---------------------------------------------------------------------------

@dataclass
class ToolSchema:
    """Schema definition for a single tool."""
    name: str
    description: str
    required_args: List[str] = field(default_factory=list)
    optional_args: List[str] = field(default_factory=list)
    arg_types: Dict[str, str] = field(default_factory=dict)
    enum_constraints: Dict[str, List[Any]] = field(default_factory=dict)
    dangerous: bool = False


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Register and lookup ToolSchema objects by name."""

    def __init__(self) -> None:
        self._registry: Dict[str, ToolSchema] = {}

    def register(self, schema: ToolSchema) -> None:
        self._registry[schema.name] = schema

    def lookup(self, name: str) -> Optional[ToolSchema]:
        return self._registry.get(name)

    def is_known(self, name: str) -> bool:
        return name in self._registry

    def all_schemas(self) -> List[ToolSchema]:
        return list(self._registry.values())

    def unregister(self, name: str) -> None:
        self._registry.pop(name, None)


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """Represents a single tool invocation by an agent."""
    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------

class MockAgent:
    """A deterministic scripted agent that replays a sequence of ToolCalls."""

    def __init__(self) -> None:
        self._script: List[ToolCall] = []

    def add_step(self, tool_call: ToolCall) -> None:
        """Append a ToolCall to the agent's script."""
        self._script.append(tool_call)

    def run(self, max_rounds: int = 10) -> List[ToolCall]:
        """
        Execute the scripted sequence up to max_rounds steps.
        Returns the list of executed ToolCalls.
        """
        executed: List[ToolCall] = []
        for i, call in enumerate(self._script):
            if i >= max_rounds:
                break
            executed.append(call)
        return executed

    def clear(self) -> None:
        self._script.clear()


# ---------------------------------------------------------------------------
# ToolCallFidelityTester
# ---------------------------------------------------------------------------

@dataclass
class FidelityResult:
    valid_calls: int
    total_calls: int
    errors: List[str] = field(default_factory=list)

    @property
    def fidelity_ratio(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.valid_calls / self.total_calls


_PYTHON_TYPE_MAP: Dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
}


def _check_type(value: Any, type_str: str) -> bool:
    """Return True if value matches the named type."""
    py_type = _PYTHON_TYPE_MAP.get(type_str.lower())
    if py_type is None:
        return True  # unknown type — skip check
    # bool is a subclass of int in Python; handle separately
    if py_type is int and isinstance(value, bool):
        return False
    if py_type is float and isinstance(value, bool):
        return False
    return isinstance(value, py_type)


class ToolCallFidelityTester:
    """
    Validates a sequence of ToolCalls against a ToolRegistry.

    Checks:
      - Unknown tool name
      - Missing required args
      - Wrong arg type (per arg_types)
      - Unknown extra args (if strict=True)
      - Out-of-enum values (per enum_constraints)
    """

    def __init__(self, registry: ToolRegistry, strict: bool = False) -> None:
        self.registry = registry
        self.strict = strict

    def evaluate(self, calls: List[ToolCall]) -> FidelityResult:
        valid = 0
        errors: List[str] = []

        for call in calls:
            call_errors = self._validate_call(call)
            if call_errors:
                errors.extend(call_errors)
            else:
                valid += 1

        return FidelityResult(valid_calls=valid, total_calls=len(calls), errors=errors)

    def _validate_call(self, call: ToolCall) -> List[str]:
        errs: List[str] = []

        if not self.registry.is_known(call.tool_name):
            errs.append(f"[{call.call_id}] Unknown tool: '{call.tool_name}'")
            return errs  # can't check further without schema

        schema = self.registry.lookup(call.tool_name)
        assert schema is not None

        # Missing required args
        for req in schema.required_args:
            if req not in call.args:
                errs.append(
                    f"[{call.call_id}] Missing required arg '{req}' for tool '{call.tool_name}'"
                )

        # Type checks
        for arg_name, arg_value in call.args.items():
            if arg_name in schema.arg_types:
                expected_type = schema.arg_types[arg_name]
                if not _check_type(arg_value, expected_type):
                    errs.append(
                        f"[{call.call_id}] Arg '{arg_name}' in '{call.tool_name}': "
                        f"expected {expected_type}, got {type(arg_value).__name__}"
                    )

        # Enum constraints
        for arg_name, allowed in schema.enum_constraints.items():
            if arg_name in call.args and call.args[arg_name] not in allowed:
                errs.append(
                    f"[{call.call_id}] Arg '{arg_name}' in '{call.tool_name}': "
                    f"value {call.args[arg_name]!r} not in enum {allowed}"
                )

        # Strict: unknown extra args
        if self.strict:
            known_args = set(schema.required_args) | set(schema.optional_args)
            for arg_name in call.args:
                if arg_name not in known_args:
                    errs.append(
                        f"[{call.call_id}] Unknown arg '{arg_name}' for tool '{call.tool_name}'"
                    )

        return errs


# ---------------------------------------------------------------------------
# RunawayLoopDetector
# ---------------------------------------------------------------------------

@dataclass
class LoopDetectionResult:
    exceeded_max_rounds: bool
    repeated_signature: bool
    repeated_signature_details: Optional[str] = None

    @property
    def loop_detected(self) -> bool:
        return self.exceeded_max_rounds or self.repeated_signature


def _call_signature(call: ToolCall) -> str:
    """Stable signature for a ToolCall (tool_name + sorted args)."""
    sorted_args = json.dumps(call.args, sort_keys=True)
    return f"{call.tool_name}:{sorted_args}"


class RunawayLoopDetector:
    """
    Detects non-termination patterns in agent execution.

    - Flags if agent exceeds max_rounds.
    - Detects repeated identical call signatures (stuck loop).
    """

    def __init__(self, max_rounds: int = 10, repeat_threshold: int = 2) -> None:
        self.max_rounds = max_rounds
        self.repeat_threshold = repeat_threshold

    def analyze(self, calls: List[ToolCall]) -> LoopDetectionResult:
        exceeded = len(calls) >= self.max_rounds

        sig_counts: Dict[str, int] = {}
        repeated_sig: Optional[str] = None
        for call in calls:
            sig = _call_signature(call)
            sig_counts[sig] = sig_counts.get(sig, 0) + 1
            if sig_counts[sig] >= self.repeat_threshold:
                repeated_sig = sig
                break

        return LoopDetectionResult(
            exceeded_max_rounds=exceeded,
            repeated_signature=repeated_sig is not None,
            repeated_signature_details=repeated_sig,
        )


# ---------------------------------------------------------------------------
# MultiTurnStateTester
# ---------------------------------------------------------------------------

@dataclass
class StateTurn:
    """A single turn in a multi-turn conversation."""
    tool_call: ToolCall
    # key set in this turn that future turns should use
    state_key: Optional[str] = None
    state_value: Optional[Any] = None
    # if set, verify this key appears in the turn's args
    verify_state_key: Optional[str] = None
    verify_state_value: Optional[Any] = None


@dataclass
class MultiTurnResult:
    passed: bool
    errors: List[str] = field(default_factory=list)


class MultiTurnStateTester:
    """
    Runs a scripted multi-turn conversation and verifies that state set
    in an earlier turn is propagated to a later turn.
    """

    def __init__(self) -> None:
        self._turns: List[StateTurn] = []

    def add_turn(self, turn: StateTurn) -> None:
        self._turns.append(turn)

    def run(self) -> MultiTurnResult:
        state: Dict[str, Any] = {}
        errors: List[str] = []

        for i, turn in enumerate(self._turns):
            # Store any state emitted by this turn
            if turn.state_key is not None:
                state[turn.state_key] = turn.state_value

            # Verify expected state in this turn's args
            if turn.verify_state_key is not None:
                actual = turn.tool_call.args.get(turn.verify_state_key)
                expected = (
                    turn.verify_state_value
                    if turn.verify_state_value is not None
                    else state.get(turn.verify_state_key)
                )
                if actual != expected:
                    errors.append(
                        f"Turn {i}: arg '{turn.verify_state_key}' expected "
                        f"{expected!r}, got {actual!r}"
                    )

        return MultiTurnResult(passed=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# ArgSchemaDriftTester
# ---------------------------------------------------------------------------

@dataclass
class SchemaDriftResult:
    drifts: List[str] = field(default_factory=list)

    @property
    def has_drifts(self) -> bool:
        return len(self.drifts) > 0


class ArgSchemaDriftTester:
    """
    Detects when a tool schema changes after the agent is scripted.
    A schema drift means previously-valid calls may become invalid.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._snapshots: Dict[str, ToolSchema] = {}

    def snapshot(self) -> None:
        """Take a snapshot of all current schemas."""
        for schema in self._registry.all_schemas():
            self._snapshots[schema.name] = copy.deepcopy(schema)

    def detect_drifts(self) -> SchemaDriftResult:
        """Compare current schemas against snapshots."""
        drifts: List[str] = []

        for name, old in self._snapshots.items():
            new = self._registry.lookup(name)
            if new is None:
                drifts.append(f"Tool '{name}' was removed from registry")
                continue
            if new.required_args != old.required_args:
                drifts.append(
                    f"Tool '{name}' required_args changed: "
                    f"{old.required_args} -> {new.required_args}"
                )
            if new.optional_args != old.optional_args:
                drifts.append(
                    f"Tool '{name}' optional_args changed: "
                    f"{old.optional_args} -> {new.optional_args}"
                )
            if new.arg_types != old.arg_types:
                drifts.append(
                    f"Tool '{name}' arg_types changed: "
                    f"{old.arg_types} -> {new.arg_types}"
                )
            if new.enum_constraints != old.enum_constraints:
                drifts.append(
                    f"Tool '{name}' enum_constraints changed: "
                    f"{old.enum_constraints} -> {new.enum_constraints}"
                )
            if new.dangerous != old.dangerous:
                drifts.append(
                    f"Tool '{name}' dangerous changed: "
                    f"{old.dangerous} -> {new.dangerous}"
                )

        # Check for newly added tools not in snapshot
        for schema in self._registry.all_schemas():
            if schema.name not in self._snapshots:
                drifts.append(f"Tool '{schema.name}' was added after snapshot")

        return SchemaDriftResult(drifts=drifts)


# ---------------------------------------------------------------------------
# PlanVsExecutionTester
# ---------------------------------------------------------------------------

@dataclass
class PlanVsExecutionResult:
    matches: bool
    violations: List[str] = field(default_factory=list)


class PlanVsExecutionTester:
    """
    Given a plan (ordered list of tool names), checks that actual execution:
    - Matches the expected order
    - Does not skip steps
    """

    def __init__(self, plan: List[str]) -> None:
        self.plan = plan

    def verify(self, calls: List[ToolCall]) -> PlanVsExecutionResult:
        violations: List[str] = []
        executed_names = [c.tool_name for c in calls]

        # Check length
        if len(executed_names) != len(self.plan):
            violations.append(
                f"Plan has {len(self.plan)} steps but {len(executed_names)} were executed"
            )

        # Check each step
        for i, expected in enumerate(self.plan):
            if i < len(executed_names):
                actual = executed_names[i]
                if actual != expected:
                    violations.append(
                        f"Step {i}: expected '{expected}', got '{actual}'"
                    )
            else:
                violations.append(f"Step {i}: expected '{expected}', but execution stopped")

        # Check for extra steps beyond the plan
        for i in range(len(self.plan), len(executed_names)):
            violations.append(
                f"Step {i}: unexpected extra call '{executed_names[i]}' beyond plan"
            )

        return PlanVsExecutionResult(matches=len(violations) == 0, violations=violations)


# ---------------------------------------------------------------------------
# UnsafeToolUseTester
# ---------------------------------------------------------------------------

# The name of the guard/confirmation tool that must precede dangerous calls
GUARD_TOOL_NAME = "__confirm__"


@dataclass
class UnsafeToolUseResult:
    unsafe_calls: List[str] = field(default_factory=list)

    @property
    def has_unsafe_calls(self) -> bool:
        return len(self.unsafe_calls) > 0


class UnsafeToolUseTester:
    """
    Flags calls to dangerous=True tools that were made without a guard
    confirmation step immediately preceding them.
    """

    def __init__(self, registry: ToolRegistry, guard_tool: str = GUARD_TOOL_NAME) -> None:
        self.registry = registry
        self.guard_tool = guard_tool

    def analyze(self, calls: List[ToolCall]) -> UnsafeToolUseResult:
        unsafe: List[str] = []

        for i, call in enumerate(calls):
            schema = self.registry.lookup(call.tool_name)
            if schema is None or not schema.dangerous:
                continue
            # Check that the immediately preceding call is the guard
            if i == 0 or calls[i - 1].tool_name != self.guard_tool:
                unsafe.append(
                    f"[{call.call_id}] Dangerous tool '{call.tool_name}' called "
                    f"without preceding guard '{self.guard_tool}'"
                )

        return UnsafeToolUseResult(unsafe_calls=unsafe)


# ---------------------------------------------------------------------------
# AgentEvalReport
# ---------------------------------------------------------------------------

@dataclass
class AgentEvalReport:
    fidelity_ratio: float
    loop_detected: bool
    schema_drifts: List[str]
    plan_violations: List[str]
    unsafe_calls: List[str]

    def is_clean(self) -> bool:
        return (
            self.fidelity_ratio == 1.0
            and not self.loop_detected
            and not self.schema_drifts
            and not self.plan_violations
            and not self.unsafe_calls
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fidelity_ratio": self.fidelity_ratio,
            "loop_detected": self.loop_detected,
            "schema_drifts": self.schema_drifts,
            "plan_violations": self.plan_violations,
            "unsafe_calls": self.unsafe_calls,
        }


# ---------------------------------------------------------------------------
# MockAgenticHandler — HTTP server
# ---------------------------------------------------------------------------

class MockAgenticHandler(BaseHTTPRequestHandler):
    """
    Minimal HTTP handler that simulates an agentic API endpoint.

    Supported routes:
      POST /tool_call          — validate and record a tool call
      GET  /tool_calls         — list all recorded tool calls
      POST /register_tool      — register a tool schema
      GET  /tool/<name>        — get schema for a named tool
      POST /reset              — clear state
      GET  /health             — health check
    """

    # Shared server state; populated by MockAgenticServer
    _registry: ToolRegistry
    _calls: List[Dict[str, Any]]
    _lock: threading.Lock

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json(200, {"status": "ok"})
        elif path == "/tool_calls":
            with self._lock:
                self._send_json(200, {"calls": self._calls})
        elif path.startswith("/tool/"):
            name = path[len("/tool/"):]
            schema = self._registry.lookup(name)
            if schema is None:
                self._send_json(404, {"error": f"Tool '{name}' not found"})
            else:
                self._send_json(200, _schema_to_dict(schema))
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/tool_call":
            self._handle_tool_call(body)
        elif path == "/register_tool":
            self._handle_register_tool(body)
        elif path == "/reset":
            with self._lock:
                self._calls.clear()
            self._send_json(200, {"status": "reset"})
        else:
            self._send_json(404, {"error": "Not found"})

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_tool_call(self, body: Dict[str, Any]) -> None:
        tool_name = body.get("tool_name", "")
        args = body.get("args", {})
        call_id = body.get("call_id", str(uuid.uuid4()))

        call = ToolCall(tool_name=tool_name, args=args, call_id=call_id)

        if not self._registry.is_known(tool_name):
            self._send_json(400, {"error": f"Unknown tool '{tool_name}'", "call_id": call_id})
            return

        schema = self._registry.lookup(tool_name)
        assert schema is not None
        missing = [r for r in schema.required_args if r not in args]
        if missing:
            self._send_json(
                400,
                {"error": f"Missing required args: {missing}", "call_id": call_id},
            )
            return

        record = {"tool_name": tool_name, "args": args, "call_id": call_id}
        with self._lock:
            self._calls.append(record)
        self._send_json(200, {"status": "ok", "call_id": call_id})

    def _handle_register_tool(self, body: Dict[str, Any]) -> None:
        try:
            schema = _dict_to_schema(body)
            self._registry.register(schema)
            self._send_json(200, {"status": "registered", "name": schema.name})
        except (KeyError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _schema_to_dict(schema: ToolSchema) -> Dict[str, Any]:
    return {
        "name": schema.name,
        "description": schema.description,
        "required_args": schema.required_args,
        "optional_args": schema.optional_args,
        "arg_types": schema.arg_types,
        "enum_constraints": schema.enum_constraints,
        "dangerous": schema.dangerous,
    }


def _dict_to_schema(d: Dict[str, Any]) -> ToolSchema:
    return ToolSchema(
        name=d["name"],
        description=d.get("description", ""),
        required_args=d.get("required_args", []),
        optional_args=d.get("optional_args", []),
        arg_types=d.get("arg_types", {}),
        enum_constraints=d.get("enum_constraints", {}),
        dangerous=d.get("dangerous", False),
    )


# ---------------------------------------------------------------------------
# MockAgenticServer — convenience wrapper
# ---------------------------------------------------------------------------

class MockAgenticServer:
    """
    Starts the MockAgenticHandler on a dynamic port.

    Usage:
        server = MockAgenticServer()
        server.start()
        url = server.url
        ...
        server.stop()
    """

    DEFAULT_PORT = 19190

    def __init__(self, port: int = 0) -> None:
        self._port = port
        self._registry = ToolRegistry()
        self._calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        registry = self._registry
        calls = self._calls
        lock = self._lock

        class _Handler(MockAgenticHandler):
            _registry = registry
            _calls = calls
            _lock = lock

        self._server = HTTPServer(("127.0.0.1", self._port), _Handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def recorded_calls(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._calls)
