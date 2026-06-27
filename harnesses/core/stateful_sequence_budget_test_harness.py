#!/usr/bin/env python3
"""
stateful_sequence_budget_test_harness.py — Bounded stateful-sequence exploration.
=================================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib). Deterministic — no clock, RNG, network, or filesystem.

Model-based / stateful testing (Hypothesis ``RuleBasedStateMachine``, RapidCheck at
Spotify, TLA+ for reference) finds failures that only appear once *action order*
matters. An explorer that drives such a model must stay **bounded** (respect a step
budget), **terminal-aware** (never act from a terminal state), and **honest about what
it visited** (track visited state/action edges from real transitions, not from action
names alone) — otherwise it loops, over-reports coverage, or claims paths it never took.

This harness proves that exploration discipline over a tiny frozen state machine. The
oracle ``explore`` does a deterministic, dedup'd breadth-first walk, recording each
``(state, action)`` edge it actually traverses and stopping on budget, exhaustion, or a
safety ceiling. The planted mutants are realistic explorer bugs: ignore the budget,
expand a terminal state, drop visited-state tracking, or fabricate coverage from the
action names. Because state dedup and the budget bound every variant, ``prove`` never
has to run a non-terminating explorer.

Run:
  python harnesses/core/stateful_sequence_budget_test_harness.py --self-test
  python harnesses/core/stateful_sequence_budget_test_harness.py --json
  python harnesses/core/stateful_sequence_budget_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# Absolute backstop so even a dedup-free explorer terminates (never reached by the oracle).
_SAFETY_CEILING = 1000

Edge = tuple[str, str]  # (state, action)


@dataclass(frozen=True)
class Machine:
    """A tiny deterministic state machine. ``transitions`` is ordered per state."""

    initial: str
    terminals: frozenset[str]
    transitions: tuple[tuple[str, tuple[Edge, ...]], ...]

    def _table(self) -> dict[str, tuple[Edge, ...]]:
        return dict(self.transitions)

    def is_terminal(self, state: str) -> bool:
        return state in self.terminals

    def actions_from(self, state: str) -> tuple[str, ...]:
        return tuple(action for action, _next in self._table().get(state, ()))

    def next(self, state: str, action: str) -> str:
        for act, nxt in self._table().get(state, ()):
            if act == action:
                return nxt
        raise KeyError(f"no action {action!r} from state {state!r}")

    def all_actions(self) -> tuple[str, ...]:
        seen: list[str] = []
        for _state, edges in self.transitions:
            for action, _next in edges:
                if action not in seen:
                    seen.append(action)
        return tuple(sorted(seen))


# Frozen model: start -> a|b -> done(terminal). "done" carries a self-loop the oracle
# must refuse to take (terminal-awareness); the mutant that expands terminals takes it.
MACHINE = Machine(
    initial="start",
    terminals=frozenset({"done"}),
    transitions=(
        ("start", (("go_a", "a"), ("go_b", "b"))),
        ("a", (("back", "start"), ("finish", "done"))),
        ("b", (("back", "start"), ("finish", "done"))),
        ("done", (("noop", "done"),)),
    ),
)


@dataclass(frozen=True)
class Exploration:
    stop_reason: str            # "exhausted" | "budget" | "ceiling"
    steps: int
    edges: tuple[Edge, ...]


def _as_tuple(result: Exploration) -> tuple[str, int, tuple[Edge, ...]]:
    return (result.stop_reason, result.steps, result.edges)


# --------------------------------------------------------------------------- #
# Oracle (correct) and the intentionally buggy twins.
# --------------------------------------------------------------------------- #
def explore(machine: Machine, budget: int) -> Exploration:
    """ORACLE: bounded, dedup'd BFS recording the (state, action) edges traversed."""
    seen = {machine.initial}
    queue: deque[str] = deque([machine.initial])
    edges: list[Edge] = []
    steps = 0
    while queue:
        state = queue.popleft()
        if machine.is_terminal(state):
            continue
        for action in machine.actions_from(state):
            if steps >= budget:
                return Exploration("budget", steps, tuple(edges))
            if steps >= _SAFETY_CEILING:
                return Exploration("ceiling", steps, tuple(edges))
            steps += 1
            edges.append((state, action))
            nxt = machine.next(state, action)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return Exploration("exhausted", steps, tuple(edges))


def _bug_ignore_budget(machine: Machine, budget: int) -> Exploration:
    """BUG: never checks the budget, so it explores past the allowed step count."""
    seen = {machine.initial}
    queue: deque[str] = deque([machine.initial])
    edges: list[Edge] = []
    steps = 0
    while queue:
        state = queue.popleft()
        if machine.is_terminal(state):
            continue
        for action in machine.actions_from(state):
            if steps >= _SAFETY_CEILING:
                return Exploration("ceiling", steps, tuple(edges))
            steps += 1
            edges.append((state, action))
            nxt = machine.next(state, action)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return Exploration("exhausted", steps, tuple(edges))


def _bug_expand_terminal(machine: Machine, budget: int) -> Exploration:
    """BUG: expands terminal states too, taking actions that should never be taken."""
    seen = {machine.initial}
    queue: deque[str] = deque([machine.initial])
    edges: list[Edge] = []
    steps = 0
    while queue:
        state = queue.popleft()
        for action in machine.actions_from(state):  # no terminal check
            if steps >= budget:
                return Exploration("budget", steps, tuple(edges))
            if steps >= _SAFETY_CEILING:
                return Exploration("ceiling", steps, tuple(edges))
            steps += 1
            edges.append((state, action))
            nxt = machine.next(state, action)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return Exploration("exhausted", steps, tuple(edges))


def _bug_skip_visited(machine: Machine, budget: int) -> Exploration:
    """BUG: drops visited-state tracking, re-enqueuing seen states (only the budget
    keeps it finite) and over-reporting traversal."""
    queue: deque[str] = deque([machine.initial])
    edges: list[Edge] = []
    steps = 0
    while queue:
        state = queue.popleft()
        if machine.is_terminal(state):
            continue
        for action in machine.actions_from(state):
            if steps >= budget:
                return Exploration("budget", steps, tuple(edges))
            if steps >= _SAFETY_CEILING:
                return Exploration("ceiling", steps, tuple(edges))
            steps += 1
            edges.append((state, action))
            queue.append(machine.next(state, action))  # always re-enqueue
    return Exploration("exhausted", steps, tuple(edges))


def _bug_action_names_only(machine: Machine, budget: int) -> Exploration:
    """BUG: claims coverage from the action names alone, pairing them all with the
    initial state instead of traversing real transitions."""
    actions = machine.all_actions()
    edges = tuple((machine.initial, action) for action in actions)
    return Exploration("exhausted", len(edges), edges)


# --------------------------------------------------------------------------- #
# Frozen corpus — (budget, expected). Expected results are computed BY HAND from the
# machine + oracle contract (independent of the live oracle), so a neutered explore
# disagrees and the self-test goes red.
# --------------------------------------------------------------------------- #
_FULL_EDGES: tuple[Edge, ...] = (
    ("start", "go_a"), ("start", "go_b"),
    ("a", "back"), ("a", "finish"),
    ("b", "back"), ("b", "finish"),
)

CORPUS: tuple[tuple[int, tuple[str, int, tuple[Edge, ...]]], ...] = (
    # Budget 10 fully explores the 4 states: 6 real edges, then the queue empties.
    (10, ("exhausted", 6, _FULL_EDGES)),
    # Budget 3 stops mid-walk after three edges.
    (3, ("budget", 3, (("start", "go_a"), ("start", "go_b"), ("a", "back")))),
    # Budget 0 stops before the first action.
    (0, ("budget", 0, ())),
)


def prove(impl: Callable[[Machine, int], Exploration]) -> bool:
    """True iff `impl` (an explorer) deviates from the frozen exploration on any case."""
    for budget, expected in CORPUS:
        try:
            if _as_tuple(impl(MACHINE, budget)) != expected:
                return True
        except Exception:  # noqa: BLE001 — a crash on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["explore"]

TEETH = Teeth(
    prove=prove,
    oracle=explore,
    mutants=(
        Mutant("ignore_budget", _bug_ignore_budget,
               "never checks the budget, exploring past the allowed step count"),
        Mutant("expand_terminal", _bug_expand_terminal,
               "expands terminal states, taking actions that should never be taken"),
        Mutant("skip_visited", _bug_skip_visited,
               "drops visited-state tracking and over-reports traversal"),
        Mutant("action_names_only", _bug_action_names_only,
               "fabricates coverage from action names instead of real transitions"),
    ),
    corpus_size=len(CORPUS),
    kind="oracle_swap",
    notes="exploration must stay bounded, terminal-aware, and honest about visited edges",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [f"budget_{budget}" for budget, _exp in CORPUS] + [m.name for m in TEETH.mutants]


def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/stateful_sequence_budget")

    # ORACLE STRENGTH (vacuity gate): anchor explore's EXACT result against the
    # hand-computed expectations. A neutered explore disagrees with these literals.
    for budget, expected in CORPUS:
        actual = _as_tuple(explore(MACHINE, budget))
        report.add(f"explore:budget_{budget}", expected, actual,
                   detail=f"{expected[1]} steps, stop={expected[0]}")
        print(f"explore budget={budget:<3} stop={actual[0]:<9} steps={actual[1]} "
              f"edges={len(actual[2])}")

    # Bounded: the recorded step count never exceeds the budget.
    for budget, _exp in CORPUS:
        report.record(f"bounded:budget_{budget}", explore(MACHINE, budget).steps <= budget,
                      detail="steps must not exceed the budget")

    # Terminal-aware: no recorded edge originates from a terminal state.
    full = explore(MACHINE, 10)
    report.record("no_edge_from_terminal",
                  all(not MACHINE.is_terminal(s) for s, _a in full.edges),
                  detail="terminal states are never expanded")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bounded stateful-sequence exploration")
    p.add_argument("--self-test", action="store_true", help="run built-in checks")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true")
    args = p.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
