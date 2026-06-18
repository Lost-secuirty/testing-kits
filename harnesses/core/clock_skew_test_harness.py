#!/usr/bin/env python3
"""
clock_skew_test_harness.py — Distributed-time bugs: skew, jumps, leap, monotonic regression.
==============================================================================================

Pure-stdlib. Zero external dependencies.

Riak-style silent-write-drops, TTL caches that "expire" in the future,
last-write-wins merges that reorder under cross-node skew — these come from
treating wall-clock time as truth (Bhayani 2025; Scalar Dynamic 2025).

This harness provides:
  - FakeClock: a programmable clock with per-node offsets, freeze, and jumps.
  - Scenarios for the canonical time bugs:
      * NTP jump forward / backward
      * Monotonic-clock regression on VM resume
      * Cross-node skew exceeding TTL
      * Leap-second
      * Future-dated expiry
      * Last-write-wins merge reordering

Usage:
  python harnesses/core/clock_skew_test_harness.py --self-test
  python harnesses/core/clock_skew_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import Mutant, Report, Teeth

# ---------------------------------------------------------------------------
# FakeClock
# ---------------------------------------------------------------------------


class FakeClock:
    """A programmable clock with per-node offsets."""

    def __init__(self, start: float = 1_700_000_000.0):
        self._wall = start
        self._mono = start
        self._frozen = False
        self._node_offsets: dict[str, float] = {}

    def time(self, node: str = "local") -> float:
        return self._wall + self._node_offsets.get(node, 0.0)

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        if self._frozen:
            return
        self._wall += seconds
        self._mono += seconds

    def jump_forward(self, seconds: float) -> None:
        """An NTP jump pulls only wall time forward; monotonic does NOT jump."""
        self._wall += seconds

    def jump_back(self, seconds: float) -> None:
        """Wall time jumps backward; monotonic stays put."""
        self._wall -= seconds

    def regress_monotonic(self, seconds: float) -> None:
        """Simulate a buggy 'monotonic' that went backwards (the OS bug class)."""
        self._mono -= seconds

    def set_node_offset(self, node: str, seconds: float) -> None:
        self._node_offsets[node] = seconds

    def freeze(self) -> None:
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False


# ---------------------------------------------------------------------------
# Targets under test — small implementations that may or may not be skew-safe
# ---------------------------------------------------------------------------


@dataclass
class TTLEntry:
    value: Any
    expires_at: float


class TTLCache:
    """A TTL cache.

    clock_fn returns wall-clock "now"; mono_fn returns a monotonic reading.
    In safe mode, expiry is decided by monotonic time so a wall-clock jump
    cannot prematurely expire (or wrongly retain) entries. Unsafe mode
    trusts wall time and exhibits the classic bug.
    """

    def __init__(self, clock_fn: Callable[[], float],
                 mono_fn: Callable[[], float] | None = None,
                 ttl: float = 60.0, safe: bool = True):
        self._clock = clock_fn
        self._mono = mono_fn or clock_fn
        self._ttl = ttl
        self._store: dict[str, tuple[Any, float, float]] = {}  # value, mono_expiry, wall_expiry
        self._safe = safe

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, self._mono() + self._ttl, self._clock() + self._ttl)

    def get(self, key: str) -> Any | None:
        if key not in self._store:
            return None
        value, mono_expiry, wall_expiry = self._store[key]
        if self._safe:
            now = self._mono()
            if now >= mono_expiry:
                del self._store[key]
                return None
            return value
        now = self._clock()
        if now >= wall_expiry:
            del self._store[key]
            return None
        return value


@dataclass
class WriteOp:
    node: str
    timestamp: float
    key: str
    value: Any


def last_write_wins(ops: list[WriteOp], safe: bool = True) -> dict[str, Any]:
    """Merge writes by timestamp.

    safe=True uses (timestamp, node) for tie-breaking and rejects writes whose
    timestamp is implausibly far from the median (likely a clock-skew victim).
    safe=False naively trusts wall-time across nodes.
    """
    if not ops:
        return {}
    if safe:
        sorted_ts = sorted(o.timestamp for o in ops)
        median = sorted_ts[len(sorted_ts) // 2]
        threshold = 60.0  # 1 minute deviation from median is suspect
        filtered = [o for o in ops if abs(o.timestamp - median) <= threshold]
        ops = filtered or ops
    out: dict[str, tuple[float, str, Any]] = {}
    for op in ops:
        key = op.key
        existing = out.get(key)
        if existing is None or (op.timestamp, op.node) > (existing[0], existing[1]):
            out[key] = (op.timestamp, op.node, op.value)
    return {k: v[2] for k, v in out.items()}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class SkewResult:
    name: str
    passed: bool
    detail: str = ""


def scenario_ttl_jump_back() -> SkewResult:
    """Wall clock jumps back 5 minutes — both caches should keep the entry."""
    clock = FakeClock()
    safe_cache = TTLCache(lambda: clock.time(), lambda: clock.monotonic(),
                          ttl=60.0, safe=True)
    safe_cache.set("k", "v")
    clock.jump_back(300.0)
    safe_value = safe_cache.get("k")

    clock2 = FakeClock()
    unsafe_cache = TTLCache(lambda: clock2.time(), lambda: clock2.monotonic(),
                            ttl=60.0, safe=False)
    unsafe_cache.set("k", "v")
    clock2.jump_back(300.0)
    unsafe_value = unsafe_cache.get("k")

    return SkewResult(
        name="ttl_jump_back",
        passed=safe_value == "v" and unsafe_value == "v",
        detail=f"safe={safe_value!r}, unsafe={unsafe_value!r}",
    )


def scenario_ttl_jump_forward() -> SkewResult:
    """Wall clock jumps forward 5 minutes — unsafe cache prematurely expires."""
    clock = FakeClock()
    safe_cache = TTLCache(lambda: clock.time(), lambda: clock.monotonic(),
                          ttl=60.0, safe=True)
    safe_cache.set("k", "v")
    clock.jump_forward(300.0)  # only wall jumps; monotonic doesn't move
    safe_value = safe_cache.get("k")

    clock2 = FakeClock()
    unsafe_cache = TTLCache(lambda: clock2.time(), lambda: clock2.monotonic(),
                            ttl=60.0, safe=False)
    unsafe_cache.set("k", "v")
    clock2.jump_forward(300.0)
    unsafe_value = unsafe_cache.get("k")

    return SkewResult(
        name="ttl_jump_forward",
        passed=safe_value == "v" and unsafe_value is None,
        detail=f"safe={safe_value!r}, unsafe={unsafe_value!r}",
    )


def scenario_monotonic_regression() -> SkewResult:
    """Monotonic clock went backwards (VM resume bug). Should be detectable."""
    clock = FakeClock()
    t1 = clock.monotonic()
    clock.regress_monotonic(10.0)
    t2 = clock.monotonic()
    detected = t2 < t1
    return SkewResult(
        name="monotonic_regression",
        passed=detected,
        detail=f"t1={t1:.2f}, t2={t2:.2f}, detected={detected}",
    )


def scenario_cross_node_skew() -> SkewResult:
    """Two nodes 30s apart write to the same key. LWW must pick a winner deterministically."""
    clock = FakeClock(start=1_700_000_000.0)
    clock.set_node_offset("nodeA", 0.0)
    clock.set_node_offset("nodeB", -30.0)
    ops = [
        WriteOp(node="nodeA", timestamp=clock.time("nodeA"), key="k", value="from_A"),
        WriteOp(node="nodeB", timestamp=clock.time("nodeB"), key="k", value="from_B"),
    ]
    result = last_write_wins(ops, safe=True)
    return SkewResult(
        name="cross_node_skew",
        passed=result.get("k") in ("from_A", "from_B"),
        detail=f"winner={result.get('k')!r}",
    )


def scenario_lww_implausible_skew() -> SkewResult:
    """Three writes; one has +1 hour clock skew. Safe LWW should drop it."""
    base = 1_700_000_000.0
    ops = [
        WriteOp("A", base, "k", "A"),
        WriteOp("B", base + 5, "k", "B"),
        WriteOp("C", base + 3600, "k", "C"),  # 1-hour skew victim
    ]
    safe_result = last_write_wins(ops, safe=True)
    unsafe_result = last_write_wins(ops, safe=False)
    return SkewResult(
        name="lww_implausible_skew",
        passed=safe_result.get("k") == "B" and unsafe_result.get("k") == "C",
        detail=f"safe={safe_result.get('k')!r}, unsafe={unsafe_result.get('k')!r}",
    )


def scenario_future_dated_expiry() -> SkewResult:
    """Entry's wall-expiry is hours in the future due to set-time skew.

    Safe cache uses monotonic time, so the jumps don't affect it — entry
    expires when its monotonic deadline passes (60s in this scenario).
    """
    clock = FakeClock()
    cache = TTLCache(lambda: clock.time(), lambda: clock.monotonic(),
                     ttl=60.0, safe=True)
    clock.jump_forward(3600.0)
    cache.set("k", "v")
    clock.jump_back(3600.0)
    # Set the wall back to its original, but monotonic only advanced via .advance().
    # Entry is set in monotonic time at start+0, expires at +60s. Wall jumps
    # cancel each other out, and monotonic never moved.
    value = cache.get("k")
    return SkewResult(
        name="future_dated_expiry",
        passed=value == "v",
        detail=f"value={value!r}",
    )


SCENARIOS: dict[str, Callable[[], SkewResult]] = {
    "ttl_jump_back": scenario_ttl_jump_back,
    "ttl_jump_forward": scenario_ttl_jump_forward,
    "monotonic_regression": scenario_monotonic_regression,
    "cross_node_skew": scenario_cross_node_skew,
    "lww_implausible_skew": scenario_lww_implausible_skew,
    "future_dated_expiry": scenario_future_dated_expiry,
}


# ---------------------------------------------------------------------------
# TEETH: frozen clock-skew corpus + planted analyzer defects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClockSkewAuditCase:
    """One frozen distributed-time observation with literal expected events."""

    name: str
    kind: str
    expected_events: tuple[str, ...]


def _value_label(value: Any) -> str:
    return "None" if value is None else str(value)


CLOCK_SKEW_AUDIT_CORPUS: tuple[ClockSkewAuditCase, ...] = (
    ClockSkewAuditCase(
        name="ttl_jump_forward_safe_vs_unsafe",
        kind="ttl_jump_forward",
        expected_events=("safe:v", "unsafe:None"),
    ),
    ClockSkewAuditCase(
        name="monotonic_regression_detected",
        kind="monotonic_regression",
        expected_events=("monotonic_regressed:yes",),
    ),
    ClockSkewAuditCase(
        name="lww_implausible_skew_rejected",
        kind="lww_implausible_skew",
        expected_events=("safe:B", "unsafe:C"),
    ),
    ClockSkewAuditCase(
        name="future_dated_expiry_uses_monotonic",
        kind="future_dated_expiry",
        expected_events=("value:v",),
    ),
)


def _audit_ttl_jump_forward(_case: ClockSkewAuditCase) -> tuple[str, ...]:
    clock = FakeClock()
    safe_cache = TTLCache(lambda: clock.time(), lambda: clock.monotonic(), ttl=60.0, safe=True)
    safe_cache.set("k", "v")
    clock.jump_forward(300.0)
    safe_value = safe_cache.get("k")

    clock2 = FakeClock()
    unsafe_cache = TTLCache(lambda: clock2.time(), lambda: clock2.monotonic(),
                            ttl=60.0, safe=False)
    unsafe_cache.set("k", "v")
    clock2.jump_forward(300.0)
    unsafe_value = unsafe_cache.get("k")
    return (f"safe:{_value_label(safe_value)}", f"unsafe:{_value_label(unsafe_value)}")


def _audit_monotonic_regression(_case: ClockSkewAuditCase) -> tuple[str, ...]:
    clock = FakeClock()
    before = clock.monotonic()
    clock.regress_monotonic(10.0)
    after = clock.monotonic()
    return (f"monotonic_regressed:{'yes' if after < before else 'no'}",)


def _lww_skew_ops() -> list[WriteOp]:
    base = 1_700_000_000.0
    return [
        WriteOp("A", base, "k", "A"),
        WriteOp("B", base + 5, "k", "B"),
        WriteOp("C", base + 3600, "k", "C"),
    ]


def _audit_lww_implausible_skew(_case: ClockSkewAuditCase) -> tuple[str, ...]:
    ops = _lww_skew_ops()
    safe_result = last_write_wins(ops, safe=True)
    unsafe_result = last_write_wins(ops, safe=False)
    return (
        f"safe:{_value_label(safe_result.get('k'))}",
        f"unsafe:{_value_label(unsafe_result.get('k'))}",
    )


def _audit_future_dated_expiry(_case: ClockSkewAuditCase) -> tuple[str, ...]:
    clock = FakeClock()
    cache = TTLCache(lambda: clock.time(), lambda: clock.monotonic(), ttl=60.0, safe=True)
    clock.jump_forward(3600.0)
    cache.set("k", "v")
    clock.jump_back(3600.0)
    return (f"value:{_value_label(cache.get('k'))}",)


CLOCK_SKEW_AUDITORS: dict[str, Callable[[ClockSkewAuditCase], tuple[str, ...]]] = {
    "ttl_jump_forward": _audit_ttl_jump_forward,
    "monotonic_regression": _audit_monotonic_regression,
    "lww_implausible_skew": _audit_lww_implausible_skew,
    "future_dated_expiry": _audit_future_dated_expiry,
}


def oracle_clock_skew_audit(case: ClockSkewAuditCase) -> tuple[str, ...]:
    """Correct pure analyzer over frozen distributed-time cases."""
    try:
        auditor = CLOCK_SKEW_AUDITORS[case.kind]
    except KeyError as exc:
        raise ValueError(f"unknown clock-skew audit kind: {case.kind}") from exc
    return auditor(case)


def wall_clock_ttl_auditor(case: ClockSkewAuditCase) -> tuple[str, ...]:
    """BUG: uses wall-clock TTL checks even for the supposedly safe path."""
    if case.kind == "ttl_jump_forward":
        clock = FakeClock()
        safe_cache = TTLCache(lambda: clock.time(), lambda: clock.monotonic(), ttl=60.0, safe=False)
        safe_cache.set("k", "v")
        clock.jump_forward(300.0)
        safe_value = safe_cache.get("k")

        clock2 = FakeClock()
        unsafe_cache = TTLCache(lambda: clock2.time(), lambda: clock2.monotonic(),
                                ttl=60.0, safe=False)
        unsafe_cache.set("k", "v")
        clock2.jump_forward(300.0)
        unsafe_value = unsafe_cache.get("k")
        return (f"safe:{_value_label(safe_value)}", f"unsafe:{_value_label(unsafe_value)}")
    return oracle_clock_skew_audit(case)


def monotonic_blind_auditor(case: ClockSkewAuditCase) -> tuple[str, ...]:
    """BUG: never detects monotonic-clock regression."""
    if case.kind == "monotonic_regression":
        return ("monotonic_regressed:no",)
    return oracle_clock_skew_audit(case)


def trusts_lww_outlier_auditor(case: ClockSkewAuditCase) -> tuple[str, ...]:
    """BUG: accepts implausibly future-dated writes during LWW merge."""
    if case.kind == "lww_implausible_skew":
        result = last_write_wins(_lww_skew_ops(), safe=False)
        return (f"safe:{_value_label(result.get('k'))}", f"unsafe:{_value_label(result.get('k'))}")
    return oracle_clock_skew_audit(case)


def prove(impl: Callable[[ClockSkewAuditCase], tuple[str, ...]]) -> bool:
    """True iff the analyzer diverges from any frozen clock-skew expectation."""
    for case in CLOCK_SKEW_AUDIT_CORPUS:
        try:
            if tuple(impl(case)) != case.expected_events:
                return True
        except Exception:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_clock_skew_audit,
    mutants=(
        Mutant("wall_clock_ttl_auditor", wall_clock_ttl_auditor,
               "uses wall time for TTL expiry and misses NTP jumps"),
        Mutant("monotonic_blind_auditor", monotonic_blind_auditor,
               "misses monotonic regression after VM resume / clock anomalies"),
        Mutant("trusts_lww_outlier_auditor", trusts_lww_outlier_auditor,
               "accepts implausible future timestamps in last-write-wins merge"),
    ),
    corpus_size=len(CLOCK_SKEW_AUDIT_CORPUS),
    kind="oracle_swap",
    notes="Frozen TTL, monotonic regression, future expiry, and LWW skew corpus.",
)


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def list_teeth_scenarios() -> list[str]:
    return [case.name for case in CLOCK_SKEW_AUDIT_CORPUS]


def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    if verbose and not as_json:
        for r in results:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:25s} {r.detail}")
    report = Report("core/clock_skew")
    for result in results:
        report.record(f"scenario:{result.name}", result.passed, detail=result.detail)
    for case in CLOCK_SKEW_AUDIT_CORPUS:
        report.add(
            f"oracle_clock_skew_audit:{case.name}",
            list(case.expected_events),
            list(oracle_clock_skew_audit(case)),
        )
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Clock-skew bug harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test or args.json:
        return _run_self_test(verbose=args.verbose, as_json=args.json)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
