"""
State Machine Test Harness (Harness 22 of 36)

Validates finite-state-machine correctness with a mock HTTP server.
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# Core FSM types
# ---------------------------------------------------------------------------

@dataclass
class Transition:
    """A directed edge in a finite-state machine."""
    from_state: str
    to_state: str
    trigger: str                              # event name
    guard: Optional[Callable[[], bool]] = None   # optional predicate
    action: Optional[Callable[[], None]] = None  # optional side-effect

    def is_enabled(self) -> bool:
        """Return True if the guard allows this transition (or there is no guard)."""
        if self.guard is None:
            return True
        return bool(self.guard())


class InvalidTransition(Exception):
    """Raised when no valid transition exists for the given (state, event) pair."""

    def __init__(self, state: str, event: str, reason: str = ""):
        self.state = state
        self.event = event
        self.reason = reason
        msg = f"No valid transition from state '{state}' on event '{event}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class StateMachine:
    """Generic finite-state machine."""

    def __init__(
        self,
        states: Set[str],
        initial_state: str,
        transitions: List[Transition],
        terminal_states: Optional[Set[str]] = None,
    ):
        if initial_state not in states:
            raise ValueError(f"initial_state '{initial_state}' not in states")
        self._states = set(states)
        self._initial_state = initial_state
        self._transitions = list(transitions)
        self._terminal_states: Set[str] = set(terminal_states) if terminal_states else set()
        self._state = initial_state

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in self._terminal_states

    @property
    def states(self) -> Set[str]:
        return set(self._states)

    @property
    def transitions(self) -> List[Transition]:
        return list(self._transitions)

    @property
    def terminal_states(self) -> Set[str]:
        return set(self._terminal_states)

    @property
    def initial_state(self) -> str:
        return self._initial_state

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def send(self, event: str) -> str:
        """
        Process *event* and return the new state.

        Raises InvalidTransition if no enabled transition exists.
        State is left unchanged on failure.
        """
        candidates = [
            t for t in self._transitions
            if t.from_state == self._state and t.trigger == event
        ]
        enabled = [t for t in candidates if t.is_enabled()]

        if not enabled:
            raise InvalidTransition(self._state, event)

        # Use the first enabled transition (determinism assumed)
        chosen = enabled[0]
        if chosen.action is not None:
            chosen.action()
        self._state = chosen.to_state
        return self._state

    def reset(self) -> None:
        """Return to the initial state."""
        self._state = self._initial_state


# ---------------------------------------------------------------------------
# Order-lifecycle example machine factory
# ---------------------------------------------------------------------------

def make_order_machine() -> StateMachine:
    """
    Build and return an order-lifecycle state machine.

    States  : CREATED, PAID, SHIPPED, DELIVERED, CANCELLED
    Triggers: pay, ship, deliver, cancel
    Terminal: DELIVERED, CANCELLED
    """
    states = {"CREATED", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"}
    terminal = {"DELIVERED", "CANCELLED"}
    transitions = [
        Transition("CREATED",  "PAID",      "pay"),
        Transition("PAID",     "SHIPPED",   "ship"),
        Transition("SHIPPED",  "DELIVERED", "deliver"),
        Transition("CREATED",  "CANCELLED", "cancel"),
        Transition("PAID",     "CANCELLED", "cancel"),
        Transition("SHIPPED",  "CANCELLED", "cancel"),
    ]
    return StateMachine(
        states=states,
        initial_state="CREATED",
        transitions=transitions,
        terminal_states=terminal,
    )


# ---------------------------------------------------------------------------
# Analysis utilities
# ---------------------------------------------------------------------------

class TransitionTester:
    """Verify that a sequence of (event, expected_state) pairs succeeds."""

    def __init__(self, machine: StateMachine):
        self._machine = machine

    def run(self, steps: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
        """
        Execute each (event, expected_state) step.

        Returns a list of result dicts with keys:
          event, expected, actual, passed, error
        """
        results = []
        for event, expected in steps:
            result: Dict[str, Any] = {"event": event, "expected": expected}
            try:
                actual = self._machine.send(event)
                result["actual"] = actual
                result["passed"] = actual == expected
                result["error"] = None
            except InvalidTransition as exc:
                result["actual"] = None
                result["passed"] = False
                result["error"] = str(exc)
            results.append(result)
        return results


class InvalidTransitionTester:
    """Verify that invalid events raise InvalidTransition and leave state unchanged."""

    def __init__(self, machine: StateMachine):
        self._machine = machine

    def test(self, event: str) -> Dict[str, Any]:
        """
        Attempt *event* from the current state.

        Returns a dict with keys: event, state_before, raised, state_after, passed
        """
        state_before = self._machine.state
        raised = False
        try:
            self._machine.send(event)
        except InvalidTransition:
            raised = True
        state_after = self._machine.state
        return {
            "event": event,
            "state_before": state_before,
            "raised": raised,
            "state_after": state_after,
            "passed": raised and (state_before == state_after),
        }


class ReachabilityAnalyzer:
    """
    Find all states reachable from the initial state via BFS/DFS,
    and identify orphaned (defined but unreachable) states.
    """

    def __init__(self, machine: StateMachine):
        self._machine = machine

    def reachable_states(self) -> Set[str]:
        """Return the set of states reachable from initial_state."""
        visited: Set[str] = set()
        queue = [self._machine.initial_state]
        adj: Dict[str, Set[str]] = {}
        for t in self._machine.transitions:
            adj.setdefault(t.from_state, set()).add(t.to_state)

        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            for neighbour in adj.get(node, set()):
                if neighbour not in visited:
                    queue.append(neighbour)
        return visited

    def orphaned_states(self) -> Set[str]:
        """Return states that are defined but cannot be reached from initial_state."""
        return self._machine.states - self.reachable_states()


class CycleDetector:
    """Detect cycles in the state-transition graph using DFS."""

    def __init__(self, machine: StateMachine):
        self._machine = machine

    def has_cycle(self) -> bool:
        """Return True if the state graph contains at least one cycle."""
        adj: Dict[str, Set[str]] = {}
        for t in self._machine.transitions:
            adj.setdefault(t.from_state, set()).add(t.to_state)

        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {s: WHITE for s in self._machine.states}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for nb in adj.get(node, set()):
                if color.get(nb, WHITE) == GRAY:
                    return True
                if color.get(nb, WHITE) == WHITE and dfs(nb):
                    return True
            color[node] = BLACK
            return False

        for state in self._machine.states:
            if color[state] == WHITE:
                if dfs(state):
                    return True
        return False

    def find_cycles(self) -> List[List[str]]:
        """Return a list of cycles (each cycle is a list of state names)."""
        adj: Dict[str, List[str]] = {}
        for t in self._machine.transitions:
            adj.setdefault(t.from_state, [])
            if t.to_state not in adj[t.from_state]:
                adj[t.from_state].append(t.to_state)

        cycles: List[List[str]] = []
        visited: Set[str] = set()
        path: List[str] = []
        path_set: Set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            path.append(node)
            path_set.add(node)
            for nb in adj.get(node, []):
                if nb in path_set:
                    # Extract the cycle
                    idx = path.index(nb)
                    cycles.append(list(path[idx:]))
                elif nb not in visited:
                    dfs(nb)
            path.pop()
            path_set.discard(node)

        for state in self._machine.states:
            if state not in visited:
                dfs(state)
        return cycles


class CoverageTracker:
    """Track which transitions have been exercised during a test run."""

    def __init__(self, machine: StateMachine):
        self._machine = machine
        self._exercised: Set[Tuple[str, str, str]] = set()   # (from, to, trigger)

    def _key(self, t: Transition) -> Tuple[str, str, str]:
        return (t.from_state, t.to_state, t.trigger)

    def record(self, from_state: str, to_state: str, trigger: str) -> None:
        """Record that a transition was exercised."""
        self._exercised.add((from_state, to_state, trigger))

    def all_transition_keys(self) -> Set[Tuple[str, str, str]]:
        return {self._key(t) for t in self._machine.transitions}

    def covered(self) -> Set[Tuple[str, str, str]]:
        return set(self._exercised)

    def uncovered(self) -> Set[Tuple[str, str, str]]:
        return self.all_transition_keys() - self._exercised

    def coverage_ratio(self) -> float:
        total = len(self.all_transition_keys())
        if total == 0:
            return 1.0
        return len(self._exercised & self.all_transition_keys()) / total

    def reset(self) -> None:
        self._exercised.clear()


class DeterminismChecker:
    """
    Verify that for each (state, event) pair there is at most one enabled
    transition — i.e., the machine is deterministic.
    """

    def __init__(self, machine: StateMachine):
        self._machine = machine

    def check(self) -> Dict[Tuple[str, str], List[Transition]]:
        """
        Return a mapping of (state, event) → [transitions] for any pair
        that has more than one transition defined (potential nondeterminism).

        An empty dict means the machine is deterministic.
        """
        mapping: Dict[Tuple[str, str], List[Transition]] = {}
        for t in self._machine.transitions:
            key = (t.from_state, t.trigger)
            mapping.setdefault(key, []).append(t)
        return {k: v for k, v in mapping.items() if len(v) > 1}

    def is_deterministic(self) -> bool:
        """Return True if no (state, event) pair has more than one transition."""
        return len(self.check()) == 0


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------

class MockStateMachineHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP handler that exposes state-machine operations.

    Routes
    ------
    GET  /state          → {"state": "...", "is_terminal": bool}
    POST /send?event=X   → {"state": "...", "is_terminal": bool}
                           or {"error": "..."} with 409
    POST /reset          → {"state": "...", "is_terminal": bool}
    GET  /transitions    → [{"from_state":…,"to_state":…,"trigger":…}, …]
    GET  /reachable      → {"reachable": [...], "orphaned": [...]}
    GET  /coverage       → {"ratio": float, "covered": [...], "uncovered": [...]}
    GET  /determinism    → {"deterministic": bool, "conflicts": [...]}
    GET  /cycles         → {"has_cycle": bool, "cycles": [[...]]}
    GET  /health         → {"status": "ok"}
    """

    # Shared state — set by MockStateMachineServer before serving
    machine: StateMachine = None  # type: ignore[assignment]
    coverage_tracker: CoverageTracker = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # suppress logs
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _machine_snapshot(self) -> Dict[str, Any]:
        return {
            "state": self.machine.state,
            "is_terminal": self.machine.is_terminal,
        }

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/state":
            self._send_json(200, self._machine_snapshot())

        elif path == "/health":
            self._send_json(200, {"status": "ok"})

        elif path == "/transitions":
            payload = [
                {"from_state": t.from_state, "to_state": t.to_state, "trigger": t.trigger}
                for t in self.machine.transitions
            ]
            self._send_json(200, payload)

        elif path == "/reachable":
            analyzer = ReachabilityAnalyzer(self.machine)
            reachable = sorted(analyzer.reachable_states())
            orphaned = sorted(analyzer.orphaned_states())
            self._send_json(200, {"reachable": reachable, "orphaned": orphaned})

        elif path == "/coverage":
            tracker = self.coverage_tracker
            ratio = tracker.coverage_ratio()
            covered = [list(k) for k in sorted(tracker.covered())]
            uncovered = [list(k) for k in sorted(tracker.uncovered())]
            self._send_json(200, {"ratio": ratio, "covered": covered, "uncovered": uncovered})

        elif path == "/determinism":
            checker = DeterminismChecker(self.machine)
            conflicts = checker.check()
            conflicts_list = [
                {"state": k[0], "event": k[1], "count": len(v)}
                for k, v in conflicts.items()
            ]
            self._send_json(200, {"deterministic": checker.is_deterministic(), "conflicts": conflicts_list})

        elif path == "/cycles":
            detector = CycleDetector(self.machine)
            self._send_json(200, {"has_cycle": detector.has_cycle(), "cycles": detector.find_cycles()})

        else:
            self._send_json(404, {"error": f"Unknown route: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/send":
            params = parse_qs(parsed.query)
            events = params.get("event", [])
            if not events:
                self._send_json(400, {"error": "Missing 'event' query param"})
                return
            event = events[0]
            from_state = self.machine.state
            try:
                new_state = self.machine.send(event)
                if self.coverage_tracker is not None:
                    # Find matching transition to record
                    for t in self.machine.transitions:
                        if t.from_state == from_state and t.to_state == new_state and t.trigger == event:
                            self.coverage_tracker.record(t.from_state, t.to_state, t.trigger)
                            break
                self._send_json(200, self._machine_snapshot())
            except InvalidTransition as exc:
                self._send_json(409, {"error": str(exc)})

        elif path == "/reset":
            self.machine.reset()
            self._send_json(200, self._machine_snapshot())

        else:
            self._send_json(404, {"error": f"Unknown route: {path}"})


class MockStateMachineServer:
    """
    Manages a background HTTP server wrapping a StateMachine instance.

    Usage
    -----
    server = MockStateMachineServer(machine)
    server.start()
    # … make requests to server.url …
    server.stop()

    Or use as a context manager:
    with MockStateMachineServer(machine) as server:
        …
    """

    def __init__(self, machine: Optional[StateMachine] = None, port: int = 0):
        self._machine = machine or make_order_machine()
        self._port = port  # 0 = OS-assigned dynamic port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._coverage_tracker = CoverageTracker(self._machine)

    @property
    def machine(self) -> StateMachine:
        return self._machine

    @property
    def coverage_tracker(self) -> CoverageTracker:
        return self._coverage_tracker

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("Server not started")
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "MockStateMachineServer":
        # Inject shared references into the handler class
        MockStateMachineHandler.machine = self._machine
        MockStateMachineHandler.coverage_tracker = self._coverage_tracker

        self._server = HTTPServer(("127.0.0.1", self._port), MockStateMachineHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "MockStateMachineServer":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()
