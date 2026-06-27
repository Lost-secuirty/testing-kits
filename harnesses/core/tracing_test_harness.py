#!/usr/bin/env python3
"""
tracing_test_harness.py — Distributed-tracing span-tree + W3C traceparent validity.
====================================================================================

Pure-stdlib. Zero external dependencies.

LLM-written OpenTelemetry glue routinely emits malformed `traceparent` headers,
children pointing at the wrong (or a cross-trace) parent, spans that never close
(orphans), inconsistent head-sampling (a sampled child under an unsampled
parent → partial traces), attribute-schema drift, and negative span durations
from cross-node clock skew. This harness validates a span set against those
failure modes with a deterministic oracle, and proves it catches a battery of
intentionally-broken traces.

Distinct from `logging` (log record/level/PII) and `clock_skew` (TTL/LWW merge).

Usage:
  python harnesses/core/tracing_test_harness.py --self-test
  python harnesses/core/tracing_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402

_HEX = re.compile(r"\A[0-9a-f]+\Z")
_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16


# ---------------------------------------------------------------------------
# W3C traceparent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceParent:
    """A W3C `traceparent`: version-trace_id-parent_id-flags (2-32-16-2 lower hex)."""

    version: str
    trace_id: str
    span_id: str
    flags: str

    @property
    def sampled(self) -> bool:
        return bool(int(self.flags, 16) & 0x01)

    def format(self) -> str:
        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.flags}"

    @classmethod
    def parse(cls, header: str) -> TraceParent:
        parts = header.split("-")
        if len(parts) != 4:
            raise ValueError(f"traceparent must have 4 fields, got {len(parts)}")
        version, trace_id, span_id, flags = parts
        if len(version) != 2 or not _HEX.match(version):
            raise ValueError(f"bad version field: {version!r}")
        if version == "ff":
            raise ValueError("version 'ff' is forbidden")
        if len(trace_id) != 32 or not _HEX.match(trace_id):
            raise ValueError(f"bad trace_id: {trace_id!r}")
        if trace_id == _ZERO_TRACE:
            raise ValueError("all-zero trace_id is invalid")
        if len(span_id) != 16 or not _HEX.match(span_id):
            raise ValueError(f"bad span_id: {span_id!r}")
        if span_id == _ZERO_SPAN:
            raise ValueError("all-zero span_id is invalid")
        if len(flags) != 2 or not _HEX.match(flags):
            raise ValueError(f"bad flags: {flags!r}")
        return cls(version=version, trace_id=trace_id, span_id=span_id, flags=flags)


class Propagator:
    """Correctly injects/extracts the parent context as a traceparent header."""

    @staticmethod
    def inject(span: Span) -> dict[str, str]:
        flags = "01" if span.sampled else "00"
        tp = TraceParent("00", span.trace_id, span.span_id, flags)
        return {"traceparent": tp.format()}

    @staticmethod
    def extract(headers: dict[str, str]) -> TraceParent | None:
        raw = headers.get("traceparent")
        return TraceParent.parse(raw) if raw else None


class BuggyPropagator(Propagator):
    """Drops the span_id, so downstream parent resolution breaks (orphans)."""

    @staticmethod
    def inject(span: Span) -> dict[str, str]:
        return {}  # forgets to propagate context entirely


# ---------------------------------------------------------------------------
# Spans + validation oracle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_ns: int
    end_ns: int
    sampled: bool = True
    attrs: tuple[tuple[str, str], ...] = ()

    def attr_keys(self) -> frozenset[str]:
        return frozenset(k for k, _ in self.attrs)


@dataclass
class TraceConfig:
    required_attrs: tuple[str, ...] = ("service.name", "http.method")
    max_clock_skew_ns: int = 1_000


@dataclass
class TraceReport:
    span_count: int
    root_count: int
    orphans: int
    cycles: int
    negative_durations: int
    cross_trace_parents: int
    sampling_inconsistencies: int
    missing_attr_spans: int
    skew_violations: int

    @property
    def is_valid(self) -> bool:
        return (
            self.root_count == 1
            and self.orphans == 0
            and self.cycles == 0
            and self.negative_durations == 0
            and self.cross_trace_parents == 0
            and self.sampling_inconsistencies == 0
            and self.missing_attr_spans == 0
            and self.skew_violations == 0
        )


def validate_trace(spans: list[Span], config: TraceConfig | None = None) -> TraceReport:
    """Validate a span set (assumed to be one logical trace) against the oracle."""
    config = config or TraceConfig()
    by_id: dict[str, Span] = {s.span_id: s for s in spans}

    root_count = sum(1 for s in spans if s.parent_id is None)
    orphans = 0
    cross_trace = 0
    negative = 0
    sampling_bad = 0
    missing_attr = 0
    skew = 0

    for s in spans:
        if s.end_ns < s.start_ns:
            negative += 1
        if not set(config.required_attrs).issubset(s.attr_keys()):
            missing_attr += 1
        if s.parent_id is not None:
            parent = by_id.get(s.parent_id)
            if parent is None:
                orphans += 1
            else:
                if parent.trace_id != s.trace_id:
                    cross_trace += 1
                if s.sampled and not parent.sampled:
                    sampling_bad += 1
                if s.start_ns < parent.start_ns - config.max_clock_skew_ns:
                    skew += 1

    cycles = _count_cycle_members(spans, by_id)

    return TraceReport(
        span_count=len(spans),
        root_count=root_count,
        orphans=orphans,
        cycles=cycles,
        negative_durations=negative,
        cross_trace_parents=cross_trace,
        sampling_inconsistencies=sampling_bad,
        missing_attr_spans=missing_attr,
        skew_violations=skew,
    )


def _count_cycle_members(spans: list[Span], by_id: dict[str, Span]) -> int:
    """Count spans whose parent-chain revisits a node (i.e. is part of a cycle)."""
    members = 0
    for s in spans:
        seen: set[str] = set()
        cur: Span | None = s
        while cur is not None and cur.parent_id is not None:
            if cur.span_id in seen:
                members += 1
                break
            seen.add(cur.span_id)
            cur = by_id.get(cur.parent_id)
    return members


# ---------------------------------------------------------------------------
# Fixtures — one valid trace + a battery of intentionally-broken ones
# ---------------------------------------------------------------------------

_TID = "4bf92f3577b34da6a3ce929d0e0e4736"
_TID2 = "00f067aa0ba902b7aaaaaaaaaaaaaaaa"
_ATTRS = (("service.name", "checkout"), ("http.method", "GET"))


def _span(span_id: str, parent: str | None, start: int, end: int,
          sampled: bool = True, trace: str = _TID,
          attrs: tuple[tuple[str, str], ...] = _ATTRS, name: str = "op") -> Span:
    return Span(trace, span_id, parent, name, start, end, sampled, attrs)


def valid_trace() -> list[Span]:
    """Root + two children + a grandchild; single trace, sampled, attrs present."""
    return [
        _span("a" * 16, None, 100, 900),
        _span("b" * 16, "a" * 16, 150, 400),
        _span("c" * 16, "a" * 16, 420, 880),
        _span("d" * 16, "c" * 16, 450, 700),
    ]


def _trace_two_roots() -> list[Span]:
    return [_span("a" * 16, None, 100, 900), _span("b" * 16, None, 120, 800)]


def _trace_orphan() -> list[Span]:
    return [_span("a" * 16, None, 100, 900),
            _span("b" * 16, "f" * 16, 150, 400)]  # parent f… absent


def _trace_cross_trace() -> list[Span]:
    root = _span("a" * 16, None, 100, 900)
    child = Span(_TID2, "b" * 16, "a" * 16, "op", 150, 400, True, _ATTRS)  # other trace
    return [root, child]


def _trace_cycle() -> list[Span]:
    return [_span("a" * 16, "b" * 16, 100, 900),
            _span("b" * 16, "a" * 16, 120, 800)]


def _trace_negative_duration() -> list[Span]:
    return [_span("a" * 16, None, 100, 900),
            _span("b" * 16, "a" * 16, 500, 400)]  # end < start


def _trace_unsampled_parent() -> list[Span]:
    return [_span("a" * 16, None, 100, 900, sampled=False),
            _span("b" * 16, "a" * 16, 150, 400, sampled=True)]  # child sampled


def _trace_missing_attr() -> list[Span]:
    return [_span("a" * 16, None, 100, 900),
            _span("b" * 16, "a" * 16, 150, 400, attrs=(("service.name", "x"),))]


BUGGY_TRACES: dict[str, tuple[Callable[[], list[Span]], str]] = {
    "two_roots": (_trace_two_roots, "root_count"),
    "orphan": (_trace_orphan, "orphans"),
    "cross_trace_parent": (_trace_cross_trace, "cross_trace_parents"),
    "cycle": (_trace_cycle, "cycles"),
    "negative_duration": (_trace_negative_duration, "negative_durations"),
    "unsampled_parent": (_trace_unsampled_parent, "sampling_inconsistencies"),
    "missing_attr": (_trace_missing_attr, "missing_attr_spans"),
}


# ---------------------------------------------------------------------------
# Teeth: the propagator must preserve span context across an inject->extract
# round-trip for every span in the frozen corpus. The correct Propagator does;
# the BuggyPropagator drops the context entirely (extract -> None), so a span's
# trace_id/span_id/sampled flag cannot be recovered downstream and the trace
# orphans. `prove` returns True iff an inject impl loses that context.
# ---------------------------------------------------------------------------
_TEETH_CORPUS: tuple[Span, ...] = tuple(valid_trace())


def _prove(inject: Callable[[Span], dict[str, str]]) -> bool:
    """True iff `inject` fails to round-trip span context for any corpus span."""
    for span in _TEETH_CORPUS:
        try:
            tp = Propagator.extract(inject(span))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if tp is None:
            return True
        if (tp.trace_id != span.trace_id
                or tp.span_id != span.span_id
                or tp.sampled != span.sampled):
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["Propagator.inject"]

TEETH = Teeth(
    prove=_prove,
    oracle=Propagator.inject,
    mutants=(
        Mutant("propagator_drops_context", BuggyPropagator.inject,
               "propagator that forgets to emit a traceparent orphans every child span"),
    ),
    corpus_size=len(_TEETH_CORPUS),
    kind="oracle_swap",
    notes="inject->extract must preserve trace_id/span_id/sampled for every span",
)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class TraceCheck:
    name: str
    passed: bool
    detail: str = ""


def _expect(name: str, cond: bool, detail: str = "") -> TraceCheck:
    return TraceCheck(name, bool(cond), detail)


def _valid_tp() -> TraceParent:
    return TraceParent("00", _TID, "b7ad6b7169203331", "01")


def scenario_traceparent_roundtrip() -> TraceCheck:
    tp = _valid_tp()
    rt = TraceParent.parse(tp.format())
    return _expect("traceparent_roundtrip", rt == tp and rt.sampled, rt.format())


def _rejects(name: str, header: str) -> TraceCheck:
    try:
        TraceParent.parse(header)
        return _expect(name, False, f"accepted bad header {header!r}")
    except ValueError as exc:
        return _expect(name, True, str(exc))


def scenario_traceparent_rejects_short() -> TraceCheck:
    return _rejects("traceparent_rejects_short", "00-abcd-ef-01")


def scenario_traceparent_rejects_nonhex() -> TraceCheck:
    return _rejects("traceparent_rejects_nonhex", f"00-{'g' * 32}-{'b' * 16}-01")


def scenario_traceparent_rejects_all_zero_trace() -> TraceCheck:
    return _rejects("traceparent_rejects_all_zero_trace", f"00-{_ZERO_TRACE}-{'b' * 16}-01")


def scenario_traceparent_rejects_all_zero_span() -> TraceCheck:
    return _rejects("traceparent_rejects_all_zero_span", f"00-{_TID}-{_ZERO_SPAN}-01")


def scenario_traceparent_version_ff_rejected() -> TraceCheck:
    return _rejects("traceparent_version_ff_rejected", f"ff-{_TID}-{'b' * 16}-01")


def scenario_propagator_roundtrip() -> TraceCheck:
    span = valid_trace()[0]
    tp = Propagator.extract(Propagator.inject(span))
    ok = tp is not None and tp.trace_id == span.trace_id and tp.span_id == span.span_id
    return _expect("propagator_roundtrip", ok, tp.format() if tp else "None")


def scenario_buggy_propagator_drops_context() -> TraceCheck:
    span = valid_trace()[0]
    tp = BuggyPropagator.extract(BuggyPropagator.inject(span))
    return _expect("buggy_propagator_drops_context_detected", tp is None, repr(tp))


def scenario_single_root_ok() -> TraceCheck:
    r = validate_trace(valid_trace())
    return _expect("single_root_ok", r.is_valid and r.root_count == 1, repr(r.is_valid))


def scenario_deep_chain_no_false_orphan() -> TraceCheck:
    r = validate_trace(valid_trace())
    return _expect("deep_chain_no_false_orphan", r.orphans == 0 and r.span_count == 4,
                   f"orphans={r.orphans}")


def scenario_child_starts_after_parent_ok() -> TraceCheck:
    r = validate_trace(valid_trace())
    return _expect("child_starts_after_parent_ok", r.skew_violations == 0,
                   f"skew={r.skew_violations}")


def scenario_clock_skew_within_tolerance_ok() -> TraceCheck:
    spans = [_span("a" * 16, None, 1000, 9000),
             _span("b" * 16, "a" * 16, 1000 - 500, 4000)]  # 500ns < 1000 tol
    r = validate_trace(spans, TraceConfig(max_clock_skew_ns=1000))
    return _expect("clock_skew_within_tolerance_ok", r.skew_violations == 0,
                   f"skew={r.skew_violations}")


def scenario_clock_skew_exceeds_tolerance_flagged() -> TraceCheck:
    spans = [_span("a" * 16, None, 10_000, 90_000),
             _span("b" * 16, "a" * 16, 10_000 - 5_000, 40_000)]  # 5000ns > 1000 tol
    r = validate_trace(spans, TraceConfig(max_clock_skew_ns=1000))
    return _expect("clock_skew_exceeds_tolerance_flagged", r.skew_violations >= 1,
                   f"skew={r.skew_violations}")


def scenario_sampling_consistent_ok() -> TraceCheck:
    r = validate_trace(valid_trace())
    return _expect("sampling_consistent_ok", r.sampling_inconsistencies == 0, "")


def scenario_required_attrs_present_ok() -> TraceCheck:
    r = validate_trace(valid_trace())
    return _expect("required_attrs_present_ok", r.missing_attr_spans == 0, "")


def _buggy_scenario(name: str) -> Callable[[], TraceCheck]:
    builder, field_name = BUGGY_TRACES[name]

    def run() -> TraceCheck:
        r = validate_trace(builder())
        count = getattr(r, field_name)
        flagged = (count != 1) if field_name == "root_count" else (count >= 1)
        return _expect(f"{name}_detected", flagged and not r.is_valid,
                       f"{field_name}={count}")

    return run


SCENARIOS: dict[str, Callable[[], TraceCheck]] = {
    "traceparent_roundtrip": scenario_traceparent_roundtrip,
    "traceparent_rejects_short": scenario_traceparent_rejects_short,
    "traceparent_rejects_nonhex": scenario_traceparent_rejects_nonhex,
    "traceparent_rejects_all_zero_trace": scenario_traceparent_rejects_all_zero_trace,
    "traceparent_rejects_all_zero_span": scenario_traceparent_rejects_all_zero_span,
    "traceparent_version_ff_rejected": scenario_traceparent_version_ff_rejected,
    "propagator_roundtrip": scenario_propagator_roundtrip,
    "buggy_propagator_drops_context_detected": scenario_buggy_propagator_drops_context,
    "single_root_ok": scenario_single_root_ok,
    "deep_chain_no_false_orphan": scenario_deep_chain_no_false_orphan,
    "child_starts_after_parent_ok": scenario_child_starts_after_parent_ok,
    "clock_skew_within_tolerance_ok": scenario_clock_skew_within_tolerance_ok,
    "clock_skew_exceeds_tolerance_flagged": scenario_clock_skew_exceeds_tolerance_flagged,
    "sampling_consistent_ok": scenario_sampling_consistent_ok,
    "required_attrs_present_ok": scenario_required_attrs_present_ok,
    "two_roots_detected": _buggy_scenario("two_roots"),
    "orphan_detected": _buggy_scenario("orphan"),
    "cross_trace_parent_detected": _buggy_scenario("cross_trace_parent"),
    "cycle_detected": _buggy_scenario("cycle"),
    "negative_duration_detected": _buggy_scenario("negative_duration"),
    "unsampled_parent_detected": _buggy_scenario("unsampled_parent"),
    "missing_attr_detected": _buggy_scenario("missing_attr"),
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    failures = [r for r in results if not r.passed]
    for r in results:
        if verbose or not r.passed:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:42s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Distributed-tracing validity harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
