#!/usr/bin/env python3
"""
grpc_contract_test_harness.py — gRPC/proto contract: evolution, deadlines, streams, status.
=============================================================================================

Pure-stdlib. Zero external dependencies. No grpc/protobuf libraries.

Proto/RPC contracts break in ways a green unit test misses: a removed field number
is reused without `reserved` (old wire bytes silently pollute a new field); a closed
enum is read like an open one (an unknown value is mistaken for a real one); a
server forwards its original deadline downstream instead of the time remaining; a
streaming handler keeps emitting after the client half-closes; a quota error is
reported as PERMISSION_DENIED instead of RESOURCE_EXHAUSTED, so clients retry auth
instead of backing off; tracing metadata is dropped across a hop; send/recv size
limits are asymmetric; a retried unary call applies its side effect twice. This
harness models protos + a mock service as pure data and proves nine buggy
implementations each break one contract rule.

In-process, no socket. A local millisecond clock supplies deadline arithmetic.

Usage:
  python harnesses/core/grpc_contract_test_harness.py --self-test
  python harnesses/core/grpc_contract_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class MsClock:
    """Millisecond clock for deadline arithmetic."""

    def __init__(self, start: float = 0.0):
        self._t = start

    def advance(self, ms: float) -> None:
        self._t += ms

    def now(self) -> float:
        return self._t


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDescriptor:
    number: int
    name: str
    wire_type: str            # varint | len | fixed64 | fixed32


@dataclass(frozen=True)
class MessageDescriptor:
    name: str
    fields: tuple[FieldDescriptor, ...]
    reserved: tuple[int, ...] = ()


@dataclass(frozen=True)
class EnumDescriptor:
    name: str
    values: tuple[tuple[str, int], ...]
    closed: bool


@dataclass(frozen=True)
class RpcSpec:
    name: str
    deadline_ms: int
    idempotent: bool
    send_limit: int
    recv_limit: int


@dataclass(frozen=True)
class WireField:
    number: int
    wire_type: str
    value: object


# The 17 canonical gRPC status codes: OK (0) plus 16 error codes (1..16).
STATUS_CODES: dict[str, int] = {
    "OK": 0, "CANCELLED": 1, "UNKNOWN": 2, "INVALID_ARGUMENT": 3,
    "DEADLINE_EXCEEDED": 4, "NOT_FOUND": 5, "ALREADY_EXISTS": 6,
    "PERMISSION_DENIED": 7, "RESOURCE_EXHAUSTED": 8, "FAILED_PRECONDITION": 9,
    "ABORTED": 10, "OUT_OF_RANGE": 11, "UNIMPLEMENTED": 12, "INTERNAL": 13,
    "UNAVAILABLE": 14, "DATA_LOSS": 15, "UNAUTHENTICATED": 16,
}


# ---------------------------------------------------------------------------
# Fixtures — a v1 message and three v2 evolutions (good / reuse / wire-change)
# ---------------------------------------------------------------------------


V1 = MessageDescriptor("User", (
    FieldDescriptor(1, "id", "varint"),
    FieldDescriptor(2, "age", "varint"),
    FieldDescriptor(3, "name", "len"),
))
# good evolution: drop age(2) + reserve it, add email(4)
V2_GOOD = MessageDescriptor("User", (
    FieldDescriptor(1, "id", "varint"),
    FieldDescriptor(3, "name", "len"),
    FieldDescriptor(4, "email", "len"),
), reserved=(2,))
# bad: reuse number 2 for a new field with the SAME wire type -> silent pollution
V2_REUSE = MessageDescriptor("User", (
    FieldDescriptor(1, "id", "varint"),
    FieldDescriptor(2, "email", "varint"),
    FieldDescriptor(3, "name", "len"),
))
# bad: change a kept field's wire type
V2_WIRE = MessageDescriptor("User", (
    FieldDescriptor(1, "id", "len"),
    FieldDescriptor(2, "age", "varint"),
    FieldDescriptor(3, "name", "len"),
))

OPEN_ENUM = EnumDescriptor("Color", (("RED", 0), ("GREEN", 1), ("BLUE", 2)), closed=False)
CLOSED_ENUM = EnumDescriptor("Status", (("ACTIVE", 0), ("INACTIVE", 1)), closed=True)

RPCS: list[RpcSpec] = [
    RpcSpec("GetUser", 100, True, 4_000_000, 4_000_000),
    RpcSpec("ListUsers", 200, True, 4_000_000, 4_000_000),
    RpcSpec("CreateUser", 150, False, 4_000_000, 4_000_000),
    RpcSpec("StreamEvents", 5000, True, 1_000_000, 1_000_000),
    RpcSpec("DeleteUser", 100, True, 2_000_000, 2_000_000),
]
ASYMMETRIC_RPC = RpcSpec("BadRpc", 100, True, 8_000_000, 4_000_000)

INBOUND_MD: tuple[tuple[str, str], ...] = (
    ("x-request-id", "abc123"), ("user-agent", "grpc-py"),
)

ORIGINAL_DEADLINE_MS = 100
ELAPSED_MS = 30
DEADLINE_TOLERANCE_MS = 5
STREAM_STEPS = 5
CLOSE_AT = 2


# ---------------------------------------------------------------------------
# Oracle implementations + checks
# ---------------------------------------------------------------------------


def roundtrip(wire_fields: list[WireField],
              desc: MessageDescriptor) -> tuple[list[WireField], list[WireField]]:
    """Decode wire fields against a descriptor. A field whose number+wire_type match
    a known field is interpreted; anything else is preserved as an unknown field."""
    known_by_num = {f.number: f for f in desc.fields}
    known: list[WireField] = []
    unknown: list[WireField] = []
    for wf in wire_fields:
        f = known_by_num.get(wf.number)
        if f is not None and f.wire_type == wf.wire_type:
            known.append(wf)
        else:
            unknown.append(wf)
    return known, unknown


def validate_evolution(v1: MessageDescriptor,
                       v2: MessageDescriptor) -> tuple[int, int, int]:
    """Return (reuse, wire_change, unreserved_removal) violation counts."""
    v1n = {f.number: f for f in v1.fields}
    v2n = {f.number: f for f in v2.fields}
    reuse = wire = unreserved = 0
    for num, f1 in v1n.items():
        if num in v2n:
            f2 = v2n[num]
            if f2.name != f1.name:
                reuse += 1
            elif f2.wire_type != f1.wire_type:
                wire += 1
        elif num not in v2.reserved:
            unreserved += 1
    return reuse, wire, unreserved


def enum_accessor_oracle(enum: EnumDescriptor, wire_int: int) -> tuple[int, bool]:
    """Return (value, is_unknown). Closed enum maps an unknown value to default(0)
    + unknown flag; open enum returns the actual value."""
    known = {v for _, v in enum.values}
    if wire_int in known:
        return wire_int, False
    if enum.closed:
        return 0, True
    return wire_int, False


def enum_accessor_buggy(enum: EnumDescriptor, wire_int: int) -> tuple[int, bool]:
    """Buggy: treats every enum as open — returns the raw int even for a closed enum."""
    known = {v for _, v in enum.values}
    if wire_int in known:
        return wire_int, False
    return wire_int, False


def propagate_deadline_oracle(original_ms: float, elapsed_ms: float) -> float:
    return original_ms - elapsed_ms


def propagate_deadline_ignoring(original_ms: float, elapsed_ms: float) -> float:
    """Buggy: forwards the original deadline downstream unchanged."""
    return original_ms


def stream_handler_oracle(step: int, cancelled: bool) -> bool:
    """Emit unless the stream has been (half-)closed/cancelled."""
    return not cancelled


def stream_handler_non_cancelling(step: int, cancelled: bool) -> bool:
    """Buggy: keeps emitting after CloseSend."""
    return True


def _emissions_after_close(handler: Callable[[int, bool], bool]) -> int:
    return sum(1 for step in range(STREAM_STEPS)
               if step >= CLOSE_AT and handler(step, True))


def status_service_oracle(scenario: str) -> str:
    if scenario == "resource_limit":
        return "RESOURCE_EXHAUSTED"
    return "OK"


def status_service_misuse(scenario: str) -> str:
    """Buggy: returns PERMISSION_DENIED for a quota/resource-limit condition."""
    if scenario == "resource_limit":
        return "PERMISSION_DENIED"
    return "OK"


def metadata_hop_oracle(inbound: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return inbound


def metadata_hop_dropping(inbound: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """Buggy: drops x-request-id across the hop."""
    return tuple((k, v) for k, v in inbound if k != "x-request-id")


def idempotency_handler_oracle(key: str, store: set) -> int:
    """Return 1 if a new side effect was applied, else 0."""
    if key in store:
        return 0
    store.add(key)
    return 1


def idempotency_handler_ignoring(key: str, store: set) -> int:
    """Buggy: applies a side effect on every call, ignoring the idempotency key."""
    store.add(key)
    return 1


def _idempotent_effects(handler: Callable[[str, set], int]) -> int:
    store: set = set()
    return sum(handler("req-1", store) for _ in range(2))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@dataclass
class GrpcReport:
    roundtrip_pollution: int
    enum_mishandled: int
    deadline_violations: int
    stream_overruns: int
    status_misuses: int
    metadata_drops: int
    size_asymmetries: int
    idempotency_breaches: int
    wire_type_changes: int

    @property
    def total_violations(self) -> int:
        return (self.roundtrip_pollution + self.enum_mishandled + self.deadline_violations
                + self.stream_overruns + self.status_misuses + self.metadata_drops
                + self.size_asymmetries + self.idempotency_breaches + self.wire_type_changes)

    def meets_contract(self) -> bool:
        return self.total_violations == 0


def audit(*, evolved_desc: MessageDescriptor = V2_GOOD,
          enum_accessor: Callable[[EnumDescriptor, int], tuple[int, bool]]
          = enum_accessor_oracle,
          deadline_propagator: Callable[[float, float], float]
          = propagate_deadline_oracle,
          stream_handler: Callable[[int, bool], bool] = stream_handler_oracle,
          status_service: Callable[[str], str] = status_service_oracle,
          metadata_hop: Callable[[tuple], tuple] = metadata_hop_oracle,
          rpcs: list[RpcSpec] | None = None,
          idempotency_handler: Callable[[str, set], int] = idempotency_handler_oracle
          ) -> GrpcReport:
    rpcs = RPCS if rpcs is None else rpcs

    reuse, wire, unreserved = validate_evolution(V1, evolved_desc)

    enum_bad = 0
    value, is_unknown = enum_accessor(CLOSED_ENUM, 99)
    if not is_unknown or value != 0:
        enum_bad = 1

    downstream = deadline_propagator(ORIGINAL_DEADLINE_MS, ELAPSED_MS)
    deadline_bad = 1 if downstream > (ORIGINAL_DEADLINE_MS - ELAPSED_MS
                                      + DEADLINE_TOLERANCE_MS) else 0

    overruns = _emissions_after_close(stream_handler)

    status_bad = 0 if status_service("resource_limit") == "RESOURCE_EXHAUSTED" else 1

    md_bad = 0 if "x-request-id" in dict(metadata_hop(INBOUND_MD)) else 1

    size_bad = sum(1 for r in rpcs if r.send_limit > r.recv_limit)

    idem_bad = 0 if _idempotent_effects(idempotency_handler) == 1 else 1

    return GrpcReport(
        roundtrip_pollution=reuse + unreserved,
        enum_mishandled=enum_bad,
        deadline_violations=deadline_bad,
        stream_overruns=overruns,
        status_misuses=status_bad,
        metadata_drops=md_bad,
        size_asymmetries=size_bad,
        idempotency_breaches=idem_bad,
        wire_type_changes=wire,
    )


# ---------------------------------------------------------------------------
# Teeth: each buggy twin, wired into its audit slot, must raise that slot's
# violation count above zero; the matching oracle leaves the contract clean.
# ---------------------------------------------------------------------------


# Map each swappable callable to (audit keyword, violation field) for its slot.
# Every buggy twin is judged only in its own slot against the frozen fixture
# corpus baked into audit(); a defect is "caught" when it pushes that slot's
# count above zero, while the matching oracle leaves the contract clean.
_TEETH_SLOT_FOR: dict[Callable[..., object], tuple[str, str]] = {
    enum_accessor_oracle: ("enum_accessor", "enum_mishandled"),
    enum_accessor_buggy: ("enum_accessor", "enum_mishandled"),
    propagate_deadline_oracle: ("deadline_propagator", "deadline_violations"),
    propagate_deadline_ignoring: ("deadline_propagator", "deadline_violations"),
    stream_handler_oracle: ("stream_handler", "stream_overruns"),
    stream_handler_non_cancelling: ("stream_handler", "stream_overruns"),
    status_service_oracle: ("status_service", "status_misuses"),
    status_service_misuse: ("status_service", "status_misuses"),
    metadata_hop_oracle: ("metadata_hop", "metadata_drops"),
    metadata_hop_dropping: ("metadata_hop", "metadata_drops"),
    idempotency_handler_oracle: ("idempotency_handler", "idempotency_breaches"),
    idempotency_handler_ignoring: ("idempotency_handler", "idempotency_breaches"),
}


def _prove(impl: Callable[..., object]) -> bool:
    """True iff `impl` is caught when judged against the frozen fixture corpus.

    The impl is wired into its own audit slot and run over audit()'s baked-in
    corpus; it is caught if that slot reports a violation (or raises). A correct
    oracle leaves its slot clean, so this returns False for it.
    """
    slot = _TEETH_SLOT_FOR.get(impl)
    if slot is None:
        # An unknown impl that fits no slot is treated as caught, never silently
        # clean.
        return True
    keyword, field_name = slot
    try:
        report = audit(**{keyword: impl})
    except Exception:
        # Any failure on a corpus case counts as caught.
        return True
    return getattr(report, field_name) >= 1


TEETH = Teeth(
    prove=_prove,
    oracle=enum_accessor_oracle,
    mutants=(
        Mutant("enum_treated_as_open", enum_accessor_buggy,
               "closed enum read like an open one: unknown value mistaken for real"),
        Mutant("deadline_not_decremented", propagate_deadline_ignoring,
               "forwards the original deadline downstream instead of time remaining"),
        Mutant("stream_ignores_halfclose", stream_handler_non_cancelling,
               "streaming handler keeps emitting after the client half-closes"),
        Mutant("quota_as_permission_denied", status_service_misuse,
               "quota error reported as PERMISSION_DENIED, not RESOURCE_EXHAUSTED"),
        Mutant("metadata_dropped_across_hop", metadata_hop_dropping,
               "tracing metadata (x-request-id) dropped across a service hop"),
        Mutant("idempotency_key_ignored", idempotency_handler_ignoring,
               "retried unary call applies its side effect twice"),
    ),
    corpus_size=len(RPCS),
    kind="auditor",
    notes="each contract-breaking twin must raise its audit violation count above zero",
)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> Check:
    return Check(name, bool(cond), detail)


def s_oracle_contract_clean() -> Check:
    rep = audit()
    return _chk("oracle_contract_clean", rep.meets_contract() and rep.total_violations == 0,
                f"total_violations={rep.total_violations}")


def s_canonical_status_codes_count() -> Check:
    return _chk("canonical_status_codes_count", len(STATUS_CODES) == 17,
                f"n={len(STATUS_CODES)}")


def s_resource_exhausted_distinct_from_permission() -> Check:
    return _chk("resource_exhausted_distinct_from_permission",
                STATUS_CODES["RESOURCE_EXHAUSTED"] != STATUS_CODES["PERMISSION_DENIED"])


def s_roundtrip_clean_on_good_evolution() -> Check:
    reuse, wire, unreserved = validate_evolution(V1, V2_GOOD)
    return _chk("roundtrip_clean_on_good_evolution",
                reuse == 0 and wire == 0 and unreserved == 0,
                f"reuse={reuse} wire={wire} unreserved={unreserved}")


def s_reserved_number_blocks_reuse() -> Check:
    old_wire = [WireField(2, "varint", 42)]
    _, unknown_good = roundtrip(old_wire, V2_GOOD)
    known_reuse, _ = roundtrip(old_wire, V2_REUSE)
    return _chk("reserved_number_blocks_reuse",
                len(unknown_good) == 1 and len(known_reuse) == 1,
                f"good_unknown={len(unknown_good)} reuse_polluted={len(known_reuse)}")


def s_field_reuse_without_reserved_caught() -> Check:
    rep = audit(evolved_desc=V2_REUSE)
    return _chk("field_reuse_without_reserved_caught", rep.roundtrip_pollution >= 1,
                f"pollution={rep.roundtrip_pollution}")


def s_wire_type_change_detected() -> Check:
    rep = audit(evolved_desc=V2_WIRE)
    return _chk("wire_type_change_detected", rep.wire_type_changes >= 1,
                f"wire_changes={rep.wire_type_changes}")


def s_open_enum_returns_actual() -> Check:
    return _chk("open_enum_returns_actual", enum_accessor_oracle(OPEN_ENUM, 99) == (99, False))


def s_closed_enum_returns_default_plus_unknown() -> Check:
    return _chk("closed_enum_returns_default_plus_unknown",
                enum_accessor_oracle(CLOSED_ENUM, 99) == (0, True))


def s_closed_enum_buggy_accessor_caught() -> Check:
    rep = audit(enum_accessor=enum_accessor_buggy)
    return _chk("closed_enum_buggy_accessor_caught", rep.enum_mishandled >= 1,
                f"enum_mishandled={rep.enum_mishandled}")


def s_deadline_strictly_decreases() -> Check:
    d = propagate_deadline_oracle(ORIGINAL_DEADLINE_MS, ELAPSED_MS)
    return _chk("deadline_strictly_decreases", d == 70 and d <= ORIGINAL_DEADLINE_MS - ELAPSED_MS,
                f"downstream={d}")


def s_deadline_within_5ms_tolerance() -> Check:
    clock = MsClock(1000)
    clock.advance(ELAPSED_MS)
    elapsed = clock.now() - 1000
    d = propagate_deadline_oracle(ORIGINAL_DEADLINE_MS, elapsed)
    return _chk("deadline_within_5ms_tolerance",
                d <= ORIGINAL_DEADLINE_MS - elapsed + DEADLINE_TOLERANCE_MS,
                f"downstream={d} elapsed={elapsed}")


def s_deadline_ignoring_propagator_caught() -> Check:
    rep = audit(deadline_propagator=propagate_deadline_ignoring)
    return _chk("deadline_ignoring_propagator_caught", rep.deadline_violations >= 1,
                f"deadline_violations={rep.deadline_violations}")


def s_stream_halfclose_stops_handler() -> Check:
    return _chk("stream_halfclose_stops_handler",
                _emissions_after_close(stream_handler_oracle) == 0)


def s_non_cancelling_handler_caught() -> Check:
    rep = audit(stream_handler=stream_handler_non_cancelling)
    return _chk("non_cancelling_handler_caught", rep.stream_overruns >= 1,
                f"stream_overruns={rep.stream_overruns}")


def s_status_resource_exhausted_correct() -> Check:
    return _chk("status_resource_exhausted_correct",
                status_service_oracle("resource_limit") == "RESOURCE_EXHAUSTED")


def s_status_misuse_caught() -> Check:
    rep = audit(status_service=status_service_misuse)
    return _chk("status_misuse_caught", rep.status_misuses >= 1,
                f"status_misuses={rep.status_misuses}")


def s_metadata_request_id_survives_hop() -> Check:
    out = dict(metadata_hop_oracle(INBOUND_MD))
    return _chk("metadata_request_id_survives_hop", out.get("x-request-id") == "abc123")


def s_metadata_drop_caught() -> Check:
    rep = audit(metadata_hop=metadata_hop_dropping)
    return _chk("metadata_drop_caught", rep.metadata_drops >= 1,
                f"metadata_drops={rep.metadata_drops}")


def s_send_recv_limit_symmetric() -> Check:
    return _chk("send_recv_limit_symmetric",
                all(r.send_limit <= r.recv_limit for r in RPCS))


def s_asymmetric_limit_caught() -> Check:
    rep = audit(rpcs=[ASYMMETRIC_RPC])
    return _chk("asymmetric_limit_caught", rep.size_asymmetries >= 1,
                f"size_asymmetries={rep.size_asymmetries}")


def s_idempotency_exactly_one_effect() -> Check:
    return _chk("idempotency_exactly_one_effect",
                _idempotent_effects(idempotency_handler_oracle) == 1)


def s_idempotency_breach_caught() -> Check:
    rep = audit(idempotency_handler=idempotency_handler_ignoring)
    return _chk("idempotency_breach_caught", rep.idempotency_breaches >= 1,
                f"idempotency_breaches={rep.idempotency_breaches}")


SCENARIOS: dict[str, Callable[[], Check]] = {
    f.__name__[2:]: f
    for f in [
        s_oracle_contract_clean,
        s_canonical_status_codes_count,
        s_resource_exhausted_distinct_from_permission,
        s_roundtrip_clean_on_good_evolution,
        s_reserved_number_blocks_reuse,
        s_field_reuse_without_reserved_caught,
        s_wire_type_change_detected,
        s_open_enum_returns_actual,
        s_closed_enum_returns_default_plus_unknown,
        s_closed_enum_buggy_accessor_caught,
        s_deadline_strictly_decreases,
        s_deadline_within_5ms_tolerance,
        s_deadline_ignoring_propagator_caught,
        s_stream_halfclose_stops_handler,
        s_non_cancelling_handler_caught,
        s_status_resource_exhausted_correct,
        s_status_misuse_caught,
        s_metadata_request_id_survives_hop,
        s_metadata_drop_caught,
        s_send_recv_limit_symmetric,
        s_asymmetric_limit_caught,
        s_idempotency_exactly_one_effect,
        s_idempotency_breach_caught,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    failures = [r for r in results if not r.passed]
    for r in results:
        if verbose or not r.passed:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:44s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="gRPC/proto contract harness")
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
