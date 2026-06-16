#!/usr/bin/env python3
"""
Deterministic game-loop simulation test harness.

Validates replayable tick-loop invariants without a renderer: input release,
pause freeze, restart reset, collision damage, and progression gates.

Self-test:
  python harnesses/core/game_loop_simulation_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


@dataclass
class GameState:
    x: int = 0
    y: int = 0
    hp: int = 3
    paused: bool = False
    boss_defeated: bool = False
    room: int = 1
    pressed: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class EngineBugs:
    sticky_input: bool = False
    moves_while_paused: bool = False
    skips_boss_gate: bool = False
    restart_keeps_damage: bool = False


class MiniGameEngine:
    def __init__(self, bugs: EngineBugs | None = None) -> None:
        self.bugs = bugs or EngineBugs()
        self.state = GameState()

    def key_down(self, key: str) -> None:
        self.state.pressed.add(key)

    def key_up(self, key: str) -> None:
        if not self.bugs.sticky_input:
            self.state.pressed.discard(key)

    def pause(self) -> None:
        self.state.paused = True

    def resume(self) -> None:
        self.state.paused = False

    def collide_enemy(self) -> None:
        self.state.hp -= 1

    def defeat_boss(self) -> None:
        self.state.boss_defeated = True

    def try_advance_room(self) -> bool:
        if self.state.boss_defeated or self.bugs.skips_boss_gate:
            self.state.room += 1
            return True
        return False

    def restart(self) -> None:
        hp = self.state.hp if self.bugs.restart_keeps_damage else 3
        self.state = GameState(hp=hp)

    def tick(self) -> GameState:
        if self.state.paused and not self.bugs.moves_while_paused:
            return self.state
        if "right" in self.state.pressed:
            self.state.x += 1
        if "left" in self.state.pressed:
            self.state.x -= 1
        if "up" in self.state.pressed:
            self.state.y -= 1
        if "down" in self.state.pressed:
            self.state.y += 1
        return self.state


@dataclass(frozen=True)
class SimulationResult:
    name: str
    ok: bool
    detail: str


def check_input_release(bugs: EngineBugs | None = None) -> SimulationResult:
    engine = MiniGameEngine(bugs)
    engine.key_down("right")
    engine.tick()
    engine.key_up("right")
    before = engine.state.x
    engine.tick()
    return SimulationResult("input_release", engine.state.x == before, f"x={engine.state.x}, before={before}")


def check_pause_freeze(bugs: EngineBugs | None = None) -> SimulationResult:
    engine = MiniGameEngine(bugs)
    engine.key_down("right")
    engine.pause()
    engine.tick()
    return SimulationResult("pause_freeze", engine.state.x == 0, f"x={engine.state.x}")


def check_progression_gate(bugs: EngineBugs | None = None) -> SimulationResult:
    engine = MiniGameEngine(bugs)
    advanced_before = engine.try_advance_room()
    engine.defeat_boss()
    advanced_after = engine.try_advance_room()
    return SimulationResult(
        "progression_gate",
        not advanced_before and advanced_after and engine.state.room == 2,
        f"before={advanced_before}, after={advanced_after}, room={engine.state.room}",
    )


def check_restart_reset(bugs: EngineBugs | None = None) -> SimulationResult:
    engine = MiniGameEngine(bugs)
    engine.collide_enemy()
    engine.restart()
    return SimulationResult("restart_reset", engine.state.hp == 3 and engine.state.room == 1, f"hp={engine.state.hp}")


def run_all(bugs: EngineBugs | None = None) -> list[SimulationResult]:
    return [
        check_input_release(bugs),
        check_pause_freeze(bugs),
        check_progression_gate(bugs),
        check_restart_reset(bugs),
    ]


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (initial position, held inputs, N ticks) -> the
# exact end position a CORRECT deterministic tick loop MUST produce.
#
# A game-loop harness only has teeth if it CATCHES a loop that advances the
# wrong number of times, double-applies the per-tick step, or drops a held
# input (collapsing a non-deterministic event drain). The contract every
# correct fixed-step loop must hold:
#
#   * exactly N ``tick``s are applied — not N-1 (a dropped frame), not 2N (a
#     double-stepped frame);
#   * each tick applies EVERY held input once (right:+x, left:-x, up:-y,
#     down:+y), so the end position is initial + N * (per-tick delta).
#
# An impl is a callable ``simulate(x0, y0, pressed, n_ticks) -> (x, y)``
# returning the final integer position. prove() judges each impl against the
# corpus's FROZEN LITERAL end positions (hand-computed from the fixed-step
# contract, NEVER read back from the oracle at runtime), so the check is
# non-circular. prove(impl) is True iff any end position diverges from the
# frozen literal — i.e. the planted loop bug is caught.
#
# Pure + deterministic: integer arithmetic only, no real clock/sleep, no RNG,
# no threads, no network, no filesystem. Non-deterministic input ordering is
# modeled as a fixed deterministic interleaving (sorted, take-first), never
# real concurrency. The three planted mutants model genuine real-world
# tick-loop defects (per the campaign hint):
#
#   * double_step_per_tick — runs the per-tick physics TWICE inside one tick
#     (the classic "called update() in both the fixed and the render path"
#     bug): movement ends at 2x the correct displacement;
#   * skip_last_tick — an off-by-one ``range(n - 1)`` loop that drops the final
#     frame (a dropped tick / early-exit bug): movement ends one frame short;
#   * input_starvation — drains only ONE held key per tick (first in a fixed
#     sorted order) instead of applying every pressed key, modeling a
#     non-deterministic event-queue drain pinned to a deterministic
#     interleaving: multi-key movement loses an axis.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickCase:
    """One frozen tick-simulation case with a literal, hand-computed end position."""
    name: str
    x0: int
    y0: int
    pressed: tuple[str, ...]
    n_ticks: int
    expected: tuple[int, int]   # the EXACT (x, y) a correct N-tick loop yields
    note: str = ""


# Cases chosen so the correct oracle matches every literal AND at least one
# planted mutant gets each one wrong. Every ``expected`` tuple is hand-computed
# from the fixed-step contract (end = initial + N * per-tick delta) — constants,
# never derived at runtime. Idle / fully-opposing cases are decoys that the
# count/ordering bugs cannot distinguish, ensuring the teeth come from real
# movement, not coincidence.
TICK_CORPUS: tuple[TickCase, ...] = (
    # Hold right for 5 ticks: x advances +1 each tick -> (5, 0). A double-step
    # loop lands on 10; a skip-last loop lands on 4.
    TickCase("right_5", 0, 0, ("right",), 5, (5, 0),
             "single axis: exactly N steps of +1 in x"),
    # Hold right+down for 3 ticks -> (3, 3). Input starvation drops one axis
    # (only 'down' survives the sorted take-first), landing on (0, 3).
    TickCase("down_right_3", 0, 0, ("right", "down"), 3, (3, 3),
             "two axes per tick: input starvation loses the un-first axis"),
    # Hold up+left for 4 ticks from (5, 5) -> (1, 1). Exercises negative deltas
    # and a non-origin start.
    TickCase("up_left_4", 5, 5, ("up", "left"), 4, (1, 1),
             "negative deltas from a non-origin start"),
    # No keys held for 7 ticks: position is unchanged -> (2, -3). A decoy: the
    # count/ordering bugs cannot move an idle entity, so movement bugs alone
    # cannot rely on this one.
    TickCase("idle_7", 2, -3, (), 7, (2, -3),
             "idle decoy: no movement regardless of tick count"),
    # All four keys held for 6 ticks: opposing inputs cancel every tick -> stays
    # (0, 0). A decoy for the count bugs (any tick count yields 0), but input
    # starvation breaks the cancellation and drifts off zero.
    TickCase("opposing_6", 0, 0, ("down", "left", "right", "up"), 6, (0, 0),
             "opposing-input decoy: cancels each tick unless an input is dropped"),
)


# --- ORACLE: reuse the harness's own correct MiniGameEngine.tick loop --------

def oracle_simulate(x0: int, y0: int, pressed: tuple[str, ...], n_ticks: int) -> tuple[int, int]:
    """Correct fixed-step simulation, delegating to the harness's own
    ``MiniGameEngine.tick``. Holds ``pressed`` down, advances exactly
    ``n_ticks`` ticks from the initial position, and returns the final
    ``(x, y)``."""
    engine = MiniGameEngine()
    engine.state.x = x0
    engine.state.y = y0
    for key in pressed:
        engine.key_down(key)
    for _ in range(n_ticks):
        engine.tick()
    return (engine.state.x, engine.state.y)


# --- Planted buggy twins (each models a real tick-loop defect) ---------------

def double_step_per_tick(x0: int, y0: int, pressed: tuple[str, ...], n_ticks: int) -> tuple[int, int]:
    """BUG: advances the per-tick physics TWICE inside a single tick.

    Models the classic "update() got called from both the fixed-timestep path
    and the render path" defect: every frame moves the entity twice, so after N
    ticks it has travelled 2x the correct displacement.
    """
    engine = MiniGameEngine()
    engine.state.x = x0
    engine.state.y = y0
    for key in pressed:
        engine.key_down(key)
    for _ in range(n_ticks):
        engine.tick()
        engine.tick()  # BUG: double-stepped frame
    return (engine.state.x, engine.state.y)


def skip_last_tick(x0: int, y0: int, pressed: tuple[str, ...], n_ticks: int) -> tuple[int, int]:
    """BUG: an off-by-one ``range(n_ticks - 1)`` loop drops the final frame.

    Models a dropped-tick / early-exit bug (e.g. the loop's exit condition fires
    one iteration too soon): movement ends exactly one frame short of the
    requested duration.
    """
    engine = MiniGameEngine()
    engine.state.x = x0
    engine.state.y = y0
    for key in pressed:
        engine.key_down(key)
    for _ in range(max(0, n_ticks - 1)):  # BUG: drops the last tick
        engine.tick()
    return (engine.state.x, engine.state.y)


def input_starvation(x0: int, y0: int, pressed: tuple[str, ...], n_ticks: int) -> tuple[int, int]:
    """BUG: drains only ONE held key per tick instead of applying every pressed
    input, so multi-key movement loses an axis.

    Models a non-deterministic event-queue drain (only one input event consumed
    per frame) pinned to a deterministic interleaving: the keys are sorted and
    only the first survives each tick. Single-axis movement is unaffected, but
    any tick with two-or-more held keys silently drops the rest.
    """
    engine = MiniGameEngine()
    engine.state.x = x0
    engine.state.y = y0
    keep = sorted(pressed)[:1]  # BUG: starve all but the first input
    for key in keep:
        engine.key_down(key)
    for _ in range(n_ticks):
        engine.tick()
    return (engine.state.x, engine.state.y)


def prove(impl: Callable[[int, int, tuple[str, ...], int], tuple[int, int]]) -> bool:
    """True iff ``impl`` produces the WRONG end position for any frozen corpus
    case (i.e. the loop bug is caught): the final ``(x, y)`` diverges from the
    hand-computed literal, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    TICK_CORPUS, never read from the oracle; integer arithmetic only, no
    RNG/clock/threads/network/filesystem. An impl that raises on a corpus case
    counts as caught.
    """
    for case in TICK_CORPUS:
        try:
            end = impl(case.x0, case.y0, case.pressed, case.n_ticks)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if tuple(end) != case.expected:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_simulate,
    mutants=(
        Mutant("double_step_per_tick", double_step_per_tick,
               "advances the per-tick physics twice in one tick -> after N ticks "
               "the entity has moved 2x the correct displacement"),
        Mutant("skip_last_tick", skip_last_tick,
               "off-by-one range(n-1) loop drops the final frame -> movement "
               "ends one tick short of the requested duration"),
        Mutant("input_starvation", input_starvation,
               "drains only one held key per tick (deterministic sorted take-first) "
               "-> multi-key movement silently loses an axis"),
    ),
    corpus_size=len(TICK_CORPUS),
    kind="oracle_swap",
    notes="a deterministic fixed-step loop must apply exactly N ticks, each "
          "applying every held input once: end = initial + N * per-tick delta",
)


def list_teeth_scenarios() -> list[str]:
    """Names of the frozen tick-simulation corpus cases (the teeth scenarios)."""
    return [c.name for c in TICK_CORPUS]


def _run_self_test(as_json: bool = False) -> int:
    """Exercise the game-loop invariants this harness exists to guard and assert
    the teeth: the good engine holds every invariant, each planted ``EngineBugs``
    defect is caught, the oracle reproduces every frozen tick-end literal, and
    the universal swap-check passes (oracle clean, every mutant caught)."""
    report = Report("core/game_loop_simulation")

    # 1. The good engine must hold every invariant the harness guards.
    for result in run_all():
        report.record(f"invariant:{result.name}", result.ok, detail=result.detail)

    # 2. Each planted EngineBugs defect must break exactly the invariant it
    #    targets (these checks predate the teeth and stay meaningful).
    bad = {
        "sticky_input": check_input_release(EngineBugs(sticky_input=True)),
        "moves_while_paused": check_pause_freeze(EngineBugs(moves_while_paused=True)),
        "skips_boss_gate": check_progression_gate(EngineBugs(skips_boss_gate=True)),
        "restart_keeps_damage": check_restart_reset(EngineBugs(restart_keeps_damage=True)),
    }
    for name, result in bad.items():
        report.record(f"engine_bug_caught:{name}", not result.ok,
                      detail=f"planted EngineBugs.{name} must break its invariant")

    # 3. The correct oracle reproduces every frozen tick-end literal exactly.
    for case in TICK_CORPUS:
        end = oracle_simulate(case.x0, case.y0, case.pressed, case.n_ticks)
        report.add(f"oracle_tick:{case.name}", list(case.expected), list(end),
                   detail=case.note)

    # 4. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic game-loop simulation controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print("input_release\npause_freeze\nprogression_gate\nrestart_reset")
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
