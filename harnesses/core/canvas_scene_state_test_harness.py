#!/usr/bin/env python3
"""
Canvas/WebGL scene-state test harness.

Checks renderer-independent scene data before a browser smoke test ever runs:
viewport bounds, draw ordering, asset fallback, duplicate IDs, and debug-only
nodes leaking into normal play mode.

Self-test:
  python harnesses/core/canvas_scene_state_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class SceneNode:
    node_id: str
    asset: str
    x: int
    y: int
    width: int
    height: int
    z: int
    visible: bool = True
    debug_only: bool = False


@dataclass(frozen=True)
class ScenePolicy:
    viewport_width: int = 800
    viewport_height: int = 600
    debug_enabled: bool = False
    assets: frozenset[str] = frozenset({"player.png", "enemy.png", "fallback.png", "hud.png"})
    fallback_asset: str = "fallback.png"


@dataclass(frozen=True)
class SceneReport:
    ok: bool
    issues: tuple[str, ...]


GOOD_SCENE: tuple[SceneNode, ...] = (
    SceneNode("player", "player.png", 100, 120, 32, 32, 10),
    SceneNode("enemy-a", "enemy.png", 250, 180, 32, 32, 20),
    SceneNode("hud", "hud.png", 0, 0, 220, 40, 100),
)


BAD_SCENE: tuple[SceneNode, ...] = (
    SceneNode("player", "missing.png", 100, 120, 32, 32, 10),
    SceneNode("player", "player.png", 900, 120, 32, 32, 10),
    SceneNode("debug-hitbox", "fallback.png", 100, 120, 32, 32, 30, debug_only=True),
)


def analyze_scene(nodes: tuple[SceneNode, ...], policy: ScenePolicy | None = None) -> SceneReport:
    policy = policy or ScenePolicy()
    issues: list[str] = []
    seen_ids: set[str] = set()
    seen_z: set[int] = set()

    for node in nodes:
        if node.node_id in seen_ids:
            issues.append(f"duplicate node id: {node.node_id}")
        seen_ids.add(node.node_id)
        if node.z in seen_z:
            issues.append(f"ambiguous draw order at z={node.z}")
        seen_z.add(node.z)
        if node.asset not in policy.assets and node.asset != policy.fallback_asset:
            issues.append(f"missing asset without fallback: {node.asset}")
        if node.visible and (node.x < 0 or node.y < 0 or node.x + node.width > policy.viewport_width or node.y + node.height > policy.viewport_height):
            issues.append(f"visible node outside viewport: {node.node_id}")
        if node.visible and node.debug_only and not policy.debug_enabled:
            issues.append(f"debug node visible outside debug mode: {node.node_id}")

    return SceneReport(ok=not issues, issues=tuple(issues))


def _run_self_test() -> int:
    good = analyze_scene(GOOD_SCENE)
    bad = analyze_scene(BAD_SCENE)
    if not good.ok:
        print(f"FAIL good scene: {good.issues}", file=sys.stderr)
        return 1
    if bad.ok:
        print("FAIL proof scene was not rejected", file=sys.stderr)
        return 1
    print("OK: scene-state controls passed and proof scene was rejected.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run canvas/WebGL scene-state controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print("good_scene\nbad_scene")
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
