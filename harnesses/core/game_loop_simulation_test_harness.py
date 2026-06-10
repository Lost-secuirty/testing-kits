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
from dataclasses import dataclass, field


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


def _run_self_test() -> int:
    good = run_all()
    bad = {
        "sticky_input": check_input_release(EngineBugs(sticky_input=True)),
        "moves_while_paused": check_pause_freeze(EngineBugs(moves_while_paused=True)),
        "skips_boss_gate": check_progression_gate(EngineBugs(skips_boss_gate=True)),
        "restart_keeps_damage": check_restart_reset(EngineBugs(restart_keeps_damage=True)),
    }
    failures = [result for result in good if not result.ok]
    proof_failures = [name for name, result in bad.items() if result.ok]
    if failures or proof_failures:
        for result in failures:
            print(f"FAIL {result.name}: {result.detail}", file=sys.stderr)
        for name in proof_failures:
            print(f"FAIL proof did not catch bug: {name}", file=sys.stderr)
        return 1
    print("OK: game-loop invariants passed and planted bugs were caught.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic game-loop simulation controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print("input_release\npause_freeze\nprogression_gate\nrestart_reset")
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
