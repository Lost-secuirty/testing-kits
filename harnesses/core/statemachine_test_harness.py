"""
State Machine Test Harness (Harness 22 of 36)

Validates finite-state-machine correctness with a mock HTTP server.
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from typing import Any
from urllib.parse import parse_qs, urlparse

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Core FSM types
# ---------------------------------------------------------------------------

@dataclass
class Transition:
    """A directed edge in a finite-state machine."""
    from_state: str
    to_state: str
    trigger: str                              # event name
    guard: Callable[[], bool] | None = None   # optional predicate
    action: Callable[[], None] | None = None  # optional side-effect

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
        states: set[str],
        initial_state: str,
        transitions: list[Transition],
        terminal_states: set[str] | None = None,
    ):
        if initial_state not in states:
            raise ValueError(f"initial_state '{initial_state}' not in states")
        self._states = set(states)
        self._initial_state = initial_state
        self._transitions = list(transitions)
        self._terminal_states: set[str] = set(terminal_states) if terminal_states else set()
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
    def states(self) -> set[str]:
        return set(self._states)

    @property
    def transitions(self) -> list[Transition]:
        return list(self._transitions)

    @property
    def terminal_states(self) -> set[str]:
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

    def run(self, steps: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """
        Execute each (event, expected_state) step.

        Returns a list of result dicts with keys:
          event, expected, actual, passed, error
        """
        results = []
        for event, expected in steps:
            result: dict[str, Any] = {"event": event, "expected": expected}
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

    def test(self, event: str) -> dict[str, Any]:
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

    def reachable_states(self) -> set[str]:
        """Return the set of states reachable from initial_state."""
        visited: set[str] = set()
        queue = [self._machine.initial_state]
        adj: dict[str, set[str]] = {}
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

    def orphaned_states(self) -> set[str]:
        """Return states that are defined but cannot be reached from initial_state."""
        return self._machine.states - self.reachable_states()


class CycleDetector:
    """Detect cycles in the state-transition graph using DFS."""

    def __init__(self, machine: StateMachine):
        self._machine = machine

    def has_cycle(self) -> bool:
        """Return True if the state graph contains at least one cycle."""
        adj: dict[str, set[str]] = {}
        for t in self._machine.transitions:
            adj.setdefault(t.from_state, set()).add(t.to_state)

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {s: WHITE for s in self._machine.states}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for nb in adj.get(node, set()):
                if color.get(nb, WHITE) == GRAY:
                    return True
                if color.get(nb, WHITE) == WHITE and dfs(nb):
                    return True
            color[node] = BLACK
            return False

        return any(color[state] == WHITE and dfs(state) for state in self._machine.states)

    def find_cycles(self) -> list[list[str]]:
        """Return a list of cycles (each cycle is a list of state names)."""
        adj: dict[str, list[str]] = {}
        for t in self._machine.transitions:
            adj.setdefault(t.from_state, [])
            if t.to_state not in adj[t.from_state]:
                adj[t.from_state].append(t.to_state)

        cycles: list[list[str]] = []
        visited: set[str] = set()
        path: list[str] = []
        path_set: set[str] = set()

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
        self._exercised: set[tuple[str, str, str]] = set()   # (from, to, trigger)

    def _key(self, t: Transition) -> tuple[str, str, str]:
        return (t.from_state, t.to_state, t.trigger)

    def record(self, from_state: str, to_state: str, trigger: str) -> None:
        """Record that a transition was exercised."""
        self._exercised.add((from_state, to_state, trigger))

    def all_transition_keys(self) -> set[tuple[str, str, str]]:
        return {self._key(t) for t in self._machine.transitions}

    def covered(self) -> set[tuple[str, str, str]]:
        return set(self._exercised)

    def uncovered(self) -> set[tuple[str, str, str]]:
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

    def check(self) -> dict[tuple[str, str], list[Transition]]:
        """
        Return a mapping of (state, event) → [transitions] for any pair
        that has more than one transition defined (potential nondeterminism).

        An empty dict means the machine is deterministic.
        """
        mapping: dict[tuple[str, str], list[Transition]] = {}
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

    def _machine_snapshot(self) -> dict[str, Any]:
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

    def __init__(self, machine: StateMachine | None = None, port: int = 0):
        self._machine = machine or make_order_machine()
        self._port = port  # 0 = OS-assigned dynamic port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
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

    def start(self) -> MockStateMachineServer:
        # Inject shared references into the handler class
        MockStateMachineHandler.machine = self._machine
        MockStateMachineHandler.coverage_tracker = self._coverage_tracker

        self._server = HTTPServer(("127.0.0.1", self._port), MockStateMachineHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        if server:
            server.shutdown()
            server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> MockStateMachineServer:
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of FSM specs with pre-computed expected verdicts.
#
# The unit under test is an FSM *checker*: a function mapping a frozen spec to a
# verdict (reachable set, orphaned set, deterministic flag). The oracle reuses
# the harness's own correct ReachabilityAnalyzer + DeterminismChecker. Each
# Mutant is a faithful real-world checker bug:
#   * nondeterminism not flagged,
#   * an unreachable (orphaned) state not detected,
#   * an orphan made to look reachable by following edges backwards.
#
# prove(impl) drives `impl` over CHECKER_CORPUS and compares each verdict to the
# LITERAL expected verdict baked into the corpus. It is NON-CIRCULAR: the
# expectations are hand-computed constants, never read back from the oracle
# object at runtime. Pure + deterministic: no clock/network/filesystem I/O, no
# RNG. prove(impl) is True iff `impl` diverges from any frozen expectation (the
# planted bug is caught).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FsmSpec:
    """An immutable finite-state-machine specification for the checker corpus."""
    name: str
    states: frozenset[str]
    initial: str
    # (from_state, to_state, trigger) triples — guards/actions are irrelevant to
    # the structural checks (reachability/determinism), so the corpus omits them.
    edges: tuple[tuple[str, str, str], ...]

    def to_machine(self) -> StateMachine:
        return StateMachine(
            states=set(self.states),
            initial_state=self.initial,
            transitions=[Transition(f, t, trig) for (f, t, trig) in self.edges],
        )


@dataclass(frozen=True)
class CheckerVerdict:
    """The structural verdict a checker returns for one spec."""
    reachable: frozenset[str]
    orphaned: frozenset[str]
    deterministic: bool


@dataclass(frozen=True)
class CheckerCase:
    """One spec paired with its hand-computed expected verdict (literal)."""
    spec: FsmSpec
    expected: CheckerVerdict
    note: str = ""


def check_fsm(spec: FsmSpec) -> CheckerVerdict:
    """ORACLE checker — the correct structural analysis of an FSM spec.

    Reuses the harness's own ReachabilityAnalyzer and DeterminismChecker so the
    teeth exercise the *shipped* logic, not a parallel re-implementation.
    """
    machine = spec.to_machine()
    reach = ReachabilityAnalyzer(machine)
    det = DeterminismChecker(machine)
    return CheckerVerdict(
        reachable=frozenset(reach.reachable_states()),
        orphaned=frozenset(reach.orphaned_states()),
        deterministic=det.is_deterministic(),
    )


# --- Planted buggy checkers (each models a real, common FSM-checker defect) ---

def check_fsm_nondeterminism_blind(spec: FsmSpec) -> CheckerVerdict:
    """BUG: determinism check keys on (from_state, to_state) instead of
    (from_state, trigger), so two transitions with the *same trigger but
    different targets* are not recognised as a conflict.

    A real and subtle checker bug: it only flags duplicate *edges*, missing the
    actual nondeterminism (one event -> two possible next states) that makes the
    machine ill-defined. Reachability is computed correctly here.
    """
    machine = spec.to_machine()
    reach = ReachabilityAnalyzer(machine)
    # Wrong grouping key: (from, to) — never collides for distinct targets.
    seen: dict[tuple[str, str], int] = {}
    for t in machine.transitions:
        key = (t.from_state, t.to_state)
        seen[key] = seen.get(key, 0) + 1
    deterministic = all(c <= 1 for c in seen.values())
    return CheckerVerdict(
        reachable=frozenset(reach.reachable_states()),
        orphaned=frozenset(reach.orphaned_states()),
        deterministic=deterministic,
    )


def check_fsm_assume_all_reachable(spec: FsmSpec) -> CheckerVerdict:
    """BUG: reachability assumes every *defined* state is reachable, so it never
    reports an orphaned (unreachable) state.

    Models the common mistake of validating the declared state set instead of
    actually traversing the transition graph from the initial state — dead/
    unreachable states slip through review. Determinism is computed correctly.
    """
    machine = spec.to_machine()
    det = DeterminismChecker(machine)
    all_states = frozenset(machine.states)
    return CheckerVerdict(
        reachable=all_states,        # WRONG: claims everything is reachable
        orphaned=frozenset(),        # WRONG: never finds an orphan
        deterministic=det.is_deterministic(),
    )


def check_fsm_undirected_reachability(spec: FsmSpec) -> CheckerVerdict:
    """BUG: builds the reachability graph as UNDIRECTED (follows edges in both
    directions), so a state that can only reach the initial state — but is never
    reached *from* it — is wrongly counted as reachable.

    Models treating a transition table as a symmetric relation: an orphan with an
    inbound-only path to the start is reported reachable, hiding a genuinely
    unreachable state. Determinism is computed correctly.
    """
    machine = spec.to_machine()
    det = DeterminismChecker(machine)
    adj: dict[str, set[str]] = {}
    for t in machine.transitions:
        adj.setdefault(t.from_state, set()).add(t.to_state)
        adj.setdefault(t.to_state, set()).add(t.from_state)  # BUG: reverse edge

    visited: set[str] = set()
    queue = [machine.initial_state]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        for nb in adj.get(node, set()):
            if nb not in visited:
                queue.append(nb)
    reachable = frozenset(visited)
    return CheckerVerdict(
        reachable=reachable,
        orphaned=frozenset(machine.states) - reachable,
        deterministic=det.is_deterministic(),
    )


# --- Frozen corpus: spec -> expected verdict (literal, hand-computed) -------

CHECKER_CORPUS: tuple[CheckerCase, ...] = (
    # Linear A->B->C: fully reachable, deterministic.
    CheckerCase(
        FsmSpec(
            "linear",
            frozenset({"A", "B", "C"}),
            "A",
            (("A", "B", "go"), ("B", "C", "go")),
        ),
        CheckerVerdict(
            reachable=frozenset({"A", "B", "C"}),
            orphaned=frozenset(),
            deterministic=True,
        ),
        note="clean linear machine: no orphans, deterministic",
    ),
    # Nondeterministic: A --go--> B and A --go--> C (same trigger, two targets).
    # Catches nondeterminism-blind. Both targets reachable.
    CheckerCase(
        FsmSpec(
            "nondeterministic",
            frozenset({"A", "B", "C"}),
            "A",
            (("A", "B", "go"), ("A", "C", "go")),
        ),
        CheckerVerdict(
            reachable=frozenset({"A", "B", "C"}),
            orphaned=frozenset(),
            deterministic=False,
        ),
        note="one event with two targets must be flagged nondeterministic",
    ),
    # Orphaned state: C is defined but never targeted. Catches assume-all-reachable.
    CheckerCase(
        FsmSpec(
            "orphan",
            frozenset({"A", "B", "C"}),
            "A",
            (("A", "B", "go"),),
        ),
        CheckerVerdict(
            reachable=frozenset({"A", "B"}),
            orphaned=frozenset({"C"}),
            deterministic=True,
        ),
        note="a defined-but-untargeted state must be reported orphaned",
    ),
    # Inbound-only orphan: X --back--> A reaches the start, but nothing reaches X.
    # A directed BFS from A finds only {A}; an undirected one wrongly adds X.
    # Catches undirected-reachability.
    CheckerCase(
        FsmSpec(
            "inbound_only_orphan",
            frozenset({"A", "X"}),
            "A",
            (("X", "A", "back"),),
        ),
        CheckerVerdict(
            reachable=frozenset({"A"}),
            orphaned=frozenset({"X"}),
            deterministic=True,
        ),
        note="a state with only an inbound-to-start edge is still unreachable",
    ),
    # Order-lifecycle machine: all reachable, deterministic (a realistic spec).
    CheckerCase(
        FsmSpec(
            "order_lifecycle",
            frozenset({"CREATED", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"}),
            "CREATED",
            (
                ("CREATED", "PAID", "pay"),
                ("PAID", "SHIPPED", "ship"),
                ("SHIPPED", "DELIVERED", "deliver"),
                ("CREATED", "CANCELLED", "cancel"),
                ("PAID", "CANCELLED", "cancel"),
                ("SHIPPED", "CANCELLED", "cancel"),
            ),
        ),
        CheckerVerdict(
            reachable=frozenset(
                {"CREATED", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"}
            ),
            orphaned=frozenset(),
            deterministic=True,
        ),
        note="realistic order machine: all reachable, deterministic",
    ),
)


def _diverges(impl: Callable[[FsmSpec], CheckerVerdict], case: CheckerCase) -> bool:
    """True iff ``impl`` produces a verdict differing from the case's frozen
    expectation on any of the three structural checks. Never compares to the
    oracle object — only to literal constants."""
    got = impl(case.spec)
    return (
        got.reachable != case.expected.reachable
        or got.orphaned != case.expected.orphaned
        or got.deterministic != case.expected.deterministic
    )


def prove(impl: Callable[[FsmSpec], CheckerVerdict]) -> bool:
    """True iff FSM checker ``impl`` MISHANDLES any frozen corpus case (the
    planted bug is caught).

    Deterministic and side-effect-free: drives ``impl`` over the frozen specs and
    compares each verdict to literal expectations. An impl that raises on a
    corpus case counts as caught.
    """
    for case in CHECKER_CORPUS:
        try:
            if _diverges(impl, case):
                return True
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=check_fsm,
    mutants=(
        Mutant(
            "nondeterminism_blind",
            check_fsm_nondeterminism_blind,
            "determinism check keys on (from,to) not (from,trigger) -> one event "
            "with two targets is not flagged nondeterministic",
        ),
        Mutant(
            "assume_all_reachable",
            check_fsm_assume_all_reachable,
            "reachability validates the declared state set instead of traversing "
            "from initial -> an orphaned/dead state is never detected",
        ),
        Mutant(
            "undirected_reachability",
            check_fsm_undirected_reachability,
            "reachability follows edges in both directions -> an inbound-only "
            "orphan is wrongly counted reachable",
        ),
    ),
    corpus_size=len(CHECKER_CORPUS),
    kind="oracle_swap",
    notes="reachability must be directed-from-initial and one event with two "
          "targets must be flagged nondeterministic",
)


def list_scenarios() -> list[str]:
    """Names of the frozen checker-corpus specs (the teeth scenarios)."""
    return [c.spec.name for c in CHECKER_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/statemachine")

    # 1. The correct oracle checker must match every frozen corpus verdict.
    for case in CHECKER_CORPUS:
        report.record(f"oracle_case:{case.spec.name}",
                      not _diverges(check_fsm, case), detail=case.note)

    # 2. Harness-specific smoke checks of the shipped FSM logic.
    order = make_order_machine()
    report.record("order_happy_path",
                  TransitionTester(order).run(
                      [("pay", "PAID"), ("ship", "SHIPPED"), ("deliver", "DELIVERED")]
                  )[-1]["passed"],
                  detail="CREATED -> PAID -> SHIPPED -> DELIVERED")
    order.reset()
    report.record("order_invalid_rejected",
                  InvalidTransitionTester(order).test("ship")["passed"],
                  detail="'ship' from CREATED must raise and leave state unchanged")

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="State machine correctness controls")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen checker-corpus spec names")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
