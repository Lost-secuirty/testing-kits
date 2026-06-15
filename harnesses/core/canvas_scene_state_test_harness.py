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

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


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


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (scene -> the exact set of issue CATEGORIES a
# correct analyzer MUST report, and whether the scene is ok).
#
# A scene-state analyzer is a reducer over an ordered list of scene nodes that
# must, in one pass, surface every renderer-independent defect: duplicate node
# ids, ambiguous draw order (two nodes sharing a z-index), a missing asset with
# no fallback, a visible node escaping the viewport, and a debug-only node
# leaking into play mode. It only has teeth if it CATCHES an analyzer that
# silently drops one of those checks — the canonical "reducer loses an op /
# mis-orders draw order / loses state on a no-op" family of bugs.
#
# An impl is a callable ``analyze(nodes, policy) -> SceneReport`` (or any object
# exposing ``.ok`` and an iterable ``.issues`` of strings). prove() classifies
# each reported issue string into one of the frozen ISSUE CATEGORIES, then
# compares the impl's (ok, category-set) against the scene's FROZEN LITERAL
# expectation. Those expectations are hand-authored constants baked into
# SCENE_CORPUS below, NEVER read back from ``analyze_scene`` at runtime, so the
# check is non-circular. prove(impl) is True iff any scene's ok flag or category
# set diverges from its frozen literal — i.e. a planted scene-state bug is caught.
#
# Pure + deterministic: frozen dataclasses and set comparison only, no RNG, no
# clock, no network, no filesystem, no threads. The three planted mutants model
# genuine real-world scene-reducer defects (per the campaign hint):
#
#   * drops_duplicate_id_check  — a dedup/merge reducer that silently swallows a
#     re-declared node id (the "lost / duplicated op" bug): the duplicate-id
#     issue never fires, so the clobbered node is reported as ok.
#   * ignores_z_collisions       — a reducer that mis-tracks draw order and never
#     flags two nodes sharing a z-index, yielding nondeterministic paint order.
#   * leaks_debug_nodes          — a reducer that loses the debug-only guard on a
#     no-op (debug_enabled stays False) so debug overlays leak into play mode.
# ---------------------------------------------------------------------------


# The fixed set of issue categories a correct analyzer can emit, each keyed by a
# stable substring of the issue message. Frozen literal — the prove() classifier
# matches issue strings against these prefixes; it is NOT derived from the oracle.
ISSUE_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("duplicate_id", "duplicate node id"),
    ("ambiguous_z", "ambiguous draw order"),
    ("missing_asset", "missing asset without fallback"),
    ("offscreen", "visible node outside viewport"),
    ("debug_leak", "debug node visible outside debug mode"),
)


def _categorize(issues) -> frozenset[str]:
    """Map an iterable of issue strings to the frozen set of category labels.

    Pure string matching against the frozen ISSUE_CATEGORIES table — never calls
    the oracle. An issue that matches no known category is bucketed as
    ``"unknown"`` so a mutant that invents a bogus category is still caught.
    """
    cats: set[str] = set()
    for issue in issues:
        text = str(issue)
        matched = False
        for label, needle in ISSUE_CATEGORIES:
            if needle in text:
                cats.add(label)
                matched = True
                break
        if not matched:
            cats.add("unknown")
    return frozenset(cats)


@dataclass(frozen=True)
class SceneCase:
    """One frozen scene with a literal, hand-authored expectation."""
    name: str
    nodes: tuple[SceneNode, ...]
    expected_ok: bool
    expected_categories: frozenset[str]   # the EXACT categories a correct analyzer yields
    note: str = ""


# Each scene isolates ONE invariant (apart from the all-clean baseline), so a
# mutant that drops a single check is caught by exactly the scene targeting it
# while the correct oracle reproduces every frozen (ok, categories) literal.
# Defaults: 800x600 viewport, debug disabled, assets {player,enemy,fallback,hud}.
SCENE_CORPUS: tuple[SceneCase, ...] = (
    # Baseline: three distinct ids, distinct z, valid assets, all in-viewport,
    # no debug nodes -> a clean scene with zero issues.
    SceneCase(
        "clean_scene",
        (
            SceneNode("player", "player.png", 100, 120, 32, 32, 10),
            SceneNode("enemy-a", "enemy.png", 250, 180, 32, 32, 20),
            SceneNode("hud", "hud.png", 0, 0, 220, 40, 100),
        ),
        expected_ok=True,
        expected_categories=frozenset(),
        note="well-formed scene: an analyzer must report it as ok with no issues",
    ),
    # Same node id declared twice (distinct z so ONLY the duplicate-id check
    # fires). A dedup/merge reducer that swallows the redeclaration misses this.
    SceneCase(
        "duplicate_id",
        (
            SceneNode("sprite", "player.png", 10, 10, 32, 32, 1),
            SceneNode("sprite", "enemy.png", 50, 50, 32, 32, 2),
        ),
        expected_ok=False,
        expected_categories=frozenset({"duplicate_id"}),
        note="a re-declared node id must be flagged, not silently merged",
    ),
    # Two distinct nodes share z=5 (distinct ids, valid assets, in viewport) so
    # ONLY the draw-order check fires. A reducer that mis-tracks z misses it.
    SceneCase(
        "ambiguous_draw_order",
        (
            SceneNode("a", "player.png", 10, 10, 32, 32, 5),
            SceneNode("b", "enemy.png", 50, 50, 32, 32, 5),
        ),
        expected_ok=False,
        expected_categories=frozenset({"ambiguous_z"}),
        note="two nodes at the same z-index are an ambiguous-paint-order defect",
    ),
    # One node references an asset not in the registry and not the fallback, so
    # ONLY the missing-asset check fires.
    SceneCase(
        "missing_asset",
        (
            SceneNode("a", "player.png", 10, 10, 32, 32, 1),
            SceneNode("b", "missing.png", 50, 50, 32, 32, 2),
        ),
        expected_ok=False,
        expected_categories=frozenset({"missing_asset"}),
        note="an unknown asset with no fallback must be flagged before render",
    ),
    # A visible node whose right edge (790+32=822) exceeds the 800px viewport, so
    # ONLY the offscreen check fires.
    SceneCase(
        "offscreen_node",
        (
            SceneNode("a", "player.png", 10, 10, 32, 32, 1),
            SceneNode("off", "enemy.png", 790, 50, 32, 32, 2),
        ),
        expected_ok=False,
        expected_categories=frozenset({"offscreen"}),
        note="a visible node escaping the viewport must be flagged",
    ),
    # A debug-only, visible node with debug mode disabled, so ONLY the
    # debug-leak check fires. A reducer that loses the debug guard misses it.
    SceneCase(
        "debug_leak",
        (
            SceneNode("a", "player.png", 10, 10, 32, 32, 1),
            SceneNode("dbg", "hud.png", 50, 50, 32, 32, 2, debug_only=True),
        ),
        expected_ok=False,
        expected_categories=frozenset({"debug_leak"}),
        note="a debug-only node must not leak into play mode (debug disabled)",
    ),
)


# --- ORACLE: reuse the harness's own correct analyzer -----------------------

def oracle_analyze(nodes: tuple[SceneNode, ...], policy: ScenePolicy | None = None) -> SceneReport:
    """The correct scene-state reducer: the harness's own ``analyze_scene``."""
    return analyze_scene(nodes, policy)


# --- Planted buggy twins (each models a real scene-reducer defect) ----------

def drops_duplicate_id_check(nodes: tuple[SceneNode, ...], policy: ScenePolicy | None = None) -> SceneReport:
    """BUG: a dedup/merge reducer that silently swallows a re-declared node id.

    Every other check is intact, but the duplicate-id guard is gone, so a scene
    that re-declares a node id (clobbering the earlier one) is reported as ok —
    the classic 'reducer loses / duplicates an op' state-loss defect.
    """
    policy = policy or ScenePolicy()
    issues: list[str] = []
    seen_z: set[int] = set()
    for node in nodes:
        # BUG: no duplicate-id tracking — re-declarations are silently merged.
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


def ignores_z_collisions(nodes: tuple[SceneNode, ...], policy: ScenePolicy | None = None) -> SceneReport:
    """BUG: a reducer that mis-tracks draw order and never flags a z collision.

    Two nodes sharing a z-index paint in an implementation-defined order, but
    this analyzer drops the seen-z check entirely, so the ambiguous-draw-order
    defect is reported as ok — nondeterministic rendering ships.
    """
    policy = policy or ScenePolicy()
    issues: list[str] = []
    seen_ids: set[str] = set()
    for node in nodes:
        if node.node_id in seen_ids:
            issues.append(f"duplicate node id: {node.node_id}")
        seen_ids.add(node.node_id)
        # BUG: z-index collisions are never tracked, so draw order is ambiguous.
        if node.asset not in policy.assets and node.asset != policy.fallback_asset:
            issues.append(f"missing asset without fallback: {node.asset}")
        if node.visible and (node.x < 0 or node.y < 0 or node.x + node.width > policy.viewport_width or node.y + node.height > policy.viewport_height):
            issues.append(f"visible node outside viewport: {node.node_id}")
        if node.visible and node.debug_only and not policy.debug_enabled:
            issues.append(f"debug node visible outside debug mode: {node.node_id}")
    return SceneReport(ok=not issues, issues=tuple(issues))


def leaks_debug_nodes(nodes: tuple[SceneNode, ...], policy: ScenePolicy | None = None) -> SceneReport:
    """BUG: a reducer that loses the debug-only guard, leaking debug overlays.

    The analyzer forgets that ``debug_enabled`` is False (a 'lost state on a
    no-op' bug), so a visible debug-only node passes through and the hitbox /
    overlay ships in normal play mode.
    """
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
        # BUG: debug-only visibility guard dropped — debug nodes leak into play.
    return SceneReport(ok=not issues, issues=tuple(issues))


def prove(impl) -> bool:
    """True iff ``impl`` MIS-ANALYZES any frozen corpus scene (i.e. the bug is
    caught): its ``ok`` flag or its set of issue CATEGORIES diverges from the
    scene's frozen literal expectation.

    Non-circular + deterministic: every expectation is a literal baked into
    SCENE_CORPUS, never read from the oracle; pure dataclass/set comparison, no
    RNG/clock/network/filesystem. An impl that raises on a corpus scene, or
    returns something without ``.ok`` / ``.issues``, counts as caught.
    """
    for case in SCENE_CORPUS:
        try:
            report = impl(case.nodes)
            actual_ok = bool(report.ok)
            actual_cats = _categorize(report.issues)
        except Exception:  # noqa: BLE001 — raising/shape errors on a case count as caught
            return True
        # 1. the ok verdict must match the frozen literal
        if actual_ok != case.expected_ok:
            return True
        # 2. the exact set of issue categories must match the frozen literal
        if actual_cats != case.expected_categories:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_analyze,
    mutants=(
        Mutant("drops_duplicate_id_check", drops_duplicate_id_check,
               "dedup/merge reducer swallows a re-declared node id -> the "
               "duplicate-id defect is reported as ok (lost/duplicated op)"),
        Mutant("ignores_z_collisions", ignores_z_collisions,
               "reducer never tracks z-index collisions -> ambiguous draw order "
               "ships and is reported as ok (mis-ordered draw order)"),
        Mutant("leaks_debug_nodes", leaks_debug_nodes,
               "reducer drops the debug-only guard -> a debug overlay leaks into "
               "play mode and is reported as ok (lost state on a no-op)"),
    ),
    corpus_size=len(SCENE_CORPUS),
    kind="oracle_swap",
    notes="a scene-state reducer must surface every renderer-independent defect "
          "in one pass: duplicate ids, ambiguous z-order, missing assets, "
          "offscreen nodes, and debug nodes leaking into play mode",
)


def list_scenarios() -> list[str]:
    """Names of the frozen scene corpus cases (the teeth scenarios)."""
    return [c.name for c in SCENE_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Exercise the scene-state invariants this harness guards and assert the
    teeth: the curated GOOD/BAD scenes behave, the frozen corpus literals are
    reproduced by the oracle, and every planted reducer bug is caught."""
    report = Report("core/canvas_scene_state")

    # 1. The curated scenes the harness has always shipped with.
    good = analyze_scene(GOOD_SCENE)
    report.record("good scene passes", good.ok,
                  detail=f"unexpected issues: {good.issues}")
    bad = analyze_scene(BAD_SCENE)
    report.record("bad scene rejected", not bad.ok,
                  detail="the proof scene must be rejected")
    bad_joined = "\n".join(bad.issues)
    for needle in ("duplicate node id", "missing asset",
                   "outside viewport", "debug node visible"):
        report.record(f"bad scene flags: {needle}", needle in bad_joined,
                      detail=f"missing from issues: {bad.issues}")

    # 2. The correct oracle reproduces every frozen scene literal exactly.
    for case in SCENE_CORPUS:
        rep = oracle_analyze(case.nodes)
        report.add(f"oracle_ok:{case.name}", case.expected_ok, bool(rep.ok),
                   detail=case.note)
        report.add(f"oracle_categories:{case.name}",
                   sorted(case.expected_categories),
                   sorted(_categorize(rep.issues)),
                   detail=case.note)

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run canvas/WebGL scene-state controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)
    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
