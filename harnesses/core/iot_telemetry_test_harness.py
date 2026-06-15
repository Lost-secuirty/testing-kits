#!/usr/bin/env python3
"""
iot_telemetry_test_harness.py — MQTT-like ingest: QoS, ordering, dedupe, skew, sessions.
=========================================================================================

Pure-stdlib. Zero external dependencies. No broker.

Device telemetry corrupts silently: a QoS-2 message delivered twice double-counts;
out-of-order arrivals scramble a time series unless re-sequenced; a retried QoS-1
publish is ingested twice without an idempotency key; a device whose clock is wrong
stamps readings in the future; a non-persistent session silently drops queued
messages on reconnect; stale retained messages resurface; late events past the
watermark are accepted (or fresh ones wrongly dropped); a last-will never fires on
an abnormal disconnect. This harness models an MQTT-like ingest path as pure data
with a deterministic clock, and proves eight buggy ingesters each break one invariant.

In-process, no broker, no socket. A local FakeClock supplies server-ingest time.

Usage:
  python harnesses/core/iot_telemetry_test_harness.py --self-test
  python harnesses/core/iot_telemetry_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import contextlib

from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class FakeClock:
    """Server-side clock; set to each message's arrival time as it is ingested."""

    def __init__(self, start: float = 0.0):
        self._t = start

    def set(self, t: float) -> None:
        self._t = t

    def now(self) -> float:
        return self._t


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    mid: str
    topic: str
    seq: int
    qos: int                 # 0 (at-most-once), 1 (at-least-once), 2 (exactly-once)
    idem_key: str            # "" = none
    device_ts: float         # device-claimed event time
    arrival: float           # server receive time (canonical source of truth)
    payload: str = ""
    retained: bool = False
    will: bool = False
    device_id: str = "d0"


@dataclass(frozen=True)
class Record:
    topic: str
    seq: int
    ts: float                # canonical timestamp (server ingest time)
    idem_key: str
    payload: str
    mid: str
    qos: int


@dataclass(frozen=True)
class DeviceSession:
    device_id: str
    persistent: bool
    queued: tuple[Message, ...] = ()
    will: Message | None = None


@dataclass
class IotReport:
    n_in: int
    n_accepted: int
    duplicates_delivered: int
    out_of_order_pairs: int
    skew_flagged: int
    skew_rejected: int
    qos2_dupes: int
    retained_kept: int
    late_dropped: int

    @property
    def strictly_ordered(self) -> bool:
        return self.out_of_order_pairs == 0

    def meets_invariants(self, config: IotConfig) -> bool:
        return (self.strictly_ordered
                and self.duplicates_delivered == 0
                and self.qos2_dupes == 0)


@dataclass(frozen=True)
class IngestResult:
    accepted: tuple[Record, ...]
    rejected: tuple[str, ...]
    flagged_skew: tuple[str, ...]
    late_dropped: tuple[str, ...]
    retained: tuple[Record, ...]
    report: IotReport


@dataclass
class IotConfig:
    skew_flag_s: float = 60.0
    skew_reject_s: float = 3600.0
    allowed_lateness: float = 30.0


# ---------------------------------------------------------------------------
# Fixtures — one deterministic arrival-ordered stream + device sessions
# ---------------------------------------------------------------------------


def _build_stream() -> list[Message]:
    return [
        # orders/q: QoS mix, out-of-order seq, duplicate retransmits
        Message("m01", "orders/q", 1, 2, "k1", 1000, 1000, "a"),
        Message("m02", "orders/q", 3, 1, "k3", 1002, 1002, "b"),
        Message("m03", "orders/q", 2, 1, "k2", 1001, 1003, "c"),     # out of order
        Message("m04", "orders/q", 3, 1, "k3", 1002, 1004, "b"),     # dup of m02 (qos1)
        Message("m05", "orders/q", 1, 2, "k1", 1000, 1005, "a"),     # dup of m01 (qos2)
        Message("m06", "orders/q", 4, 0, "", 1006, 1006, "d"),       # qos0, no idem
        Message("m07", "orders/q", 5, 0, "", 1007, 1007, "e"),       # qos0
        Message("m08", "orders/q", 6, 2, "k6", 1008, 1008, "f"),
        Message("m09", "orders/q", 6, 2, "k6", 1008, 1009, "f"),     # dup of m08 (qos2)
        # sensor/s: clock-skew cases
        Message("m10", "sensor/s", 1, 1, "s1", 1300, 1100, "hot"),   # skew 200 -> flag
        Message("m11", "sensor/s", 2, 1, "s2", 20000, 1101, "x"),    # skew huge -> reject
        Message("m12", "sensor/s", 3, 1, "s3", 1102, 1102, "ok"),    # skew 0
        # config/r: retained, latest-only
        Message("m13", "config/r", 1, 1, "r1", 1500, 1500, "v1", retained=True),
        Message("m14", "config/r", 2, 1, "r2", 1501, 1501, "v2", retained=True),
        # sensor/w: watermark / allowed-lateness
        Message("m15", "sensor/w", 1, 1, "w1", 2000, 2000, "p"),
        Message("m16", "sensor/w", 2, 1, "w2", 2040, 2041, "q"),
        Message("m17", "sensor/w", 3, 1, "w3", 2005, 2050, "late"),  # 2005 < 2040-30 -> drop
        Message("m18", "sensor/w", 4, 1, "w4", 2025, 2051, "ok"),    # 2025 >= 2010 -> keep
        Message("m19", "sensor/w", 5, 1, "w5", 2045, 2052, "r"),
    ]


STREAM = _build_stream()


def _q(mid: str, seq: int) -> Message:
    return Message(mid, "orders/q", seq, 1, mid, 1000, 1000, "queued")


def _will(mid: str, dev: str) -> Message:
    return Message(mid, f"status/{dev}", 1, 1, mid, 1000, 1000, "offline", will=True)


SESSIONS: list[DeviceSession] = [
    DeviceSession("dA", True, (_q("qa1", 10), _q("qa2", 11)), _will("wA", "dA")),
    DeviceSession("dB", False, (_q("qb1", 12),), _will("wB", "dB")),
    DeviceSession("dC", True, (), _will("wC", "dC")),
]


# ---------------------------------------------------------------------------
# Oracle ingest (behaviour flags let buggy ingesters disable one rule)
# ---------------------------------------------------------------------------


def ingest(stream: list[Message], clock: FakeClock | None = None,
           config: IotConfig | None = None, *,
           dedupe_qos1: bool = True, dedupe_qos2: bool = True,
           resequence: bool = True, trust_device_clock: bool = False,
           watermark: bool = True, retain_latest: bool = True) -> IngestResult:
    cfg = config or IotConfig()
    clock = clock or FakeClock()
    accepted_by_topic: dict[str, list[Record]] = {}
    seen_idem: set[str] = set()
    wm: dict[str, float] = {}
    retained_map: dict[str, Record] = {}
    retained_list: list[Record] = []
    rejected: list[str] = []
    flagged: list[str] = []
    late: list[str] = []
    dup_delivered = qos2_dupes = 0

    for m in stream:
        clock.set(m.arrival)
        server_ts = clock.now()
        skew = abs(m.device_ts - server_ts)
        is_flagged = False
        if not trust_device_clock:
            if skew > cfg.skew_reject_s:
                rejected.append(m.mid)
                continue
            if skew > cfg.skew_flag_s:
                flagged.append(m.mid)
                is_flagged = True
            canonical_ts = server_ts
        else:
            canonical_ts = m.device_ts

        # watermark / lateness — skew-flagged event time is untrustworthy, so skip it
        if watermark and not is_flagged:
            topic_wm = wm.get(m.topic)
            if topic_wm is not None and m.device_ts < topic_wm - cfg.allowed_lateness:
                late.append(m.mid)
                continue

        # dedupe by idempotency key, per QoS class
        if m.idem_key:
            is_dup = m.idem_key in seen_idem
            should = (m.qos == 1 and dedupe_qos1) or (m.qos == 2 and dedupe_qos2)
            if is_dup and should:
                continue
            if is_dup and not should:
                dup_delivered += 1
                if m.qos == 2:
                    qos2_dupes += 1
            seen_idem.add(m.idem_key)

        rec = Record(m.topic, m.seq, canonical_ts, m.idem_key, m.payload, m.mid, m.qos)
        accepted_by_topic.setdefault(m.topic, []).append(rec)
        if not is_flagged:
            tw = wm.get(m.topic)
            if tw is None or m.device_ts > tw:
                wm[m.topic] = m.device_ts
        if m.retained:
            if retain_latest:
                retained_map[m.topic] = rec
            else:
                retained_list.append(rec)

    final: list[Record] = []
    oop = 0
    for recs in accepted_by_topic.values():
        rs = sorted(recs, key=lambda r: r.seq) if resequence else list(recs)
        final.extend(rs)
        for a, b in zip(rs, rs[1:], strict=False):
            if b.seq < a.seq:
                oop += 1

    retained = tuple(retained_map.values()) if retain_latest else tuple(retained_list)
    report = IotReport(
        n_in=len(stream), n_accepted=len(final),
        duplicates_delivered=dup_delivered, out_of_order_pairs=oop,
        skew_flagged=len(flagged), skew_rejected=len(rejected),
        qos2_dupes=qos2_dupes, retained_kept=len(retained), late_dropped=len(late))
    return IngestResult(tuple(final), tuple(rejected), tuple(flagged),
                        tuple(late), retained, report)


# --- session lifecycle: oracle + buggy variants ---


def reconnect(session: DeviceSession) -> tuple[Message, ...]:
    """A persistent session replays its queued QoS-1 messages on reconnect."""
    return session.queued if session.persistent else ()


def reconnect_nonpersistent(session: DeviceSession) -> tuple[Message, ...]:
    """Buggy: drops queued messages even for a persistent session."""
    return ()


def on_disconnect(session: DeviceSession, abnormal: bool) -> tuple[Message, ...]:
    """Last-will fires only on abnormal disconnect."""
    return (session.will,) if (abnormal and session.will is not None) else ()


def on_disconnect_no_will(session: DeviceSession, abnormal: bool) -> tuple[Message, ...]:
    """Buggy: never emits the last-will."""
    return ()


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (canonical stream + sensor readings) -> the exact
# ingest fingerprint + rolled-up mean a correct telemetry ingester MUST produce.
#
# A telemetry ingest/rollup harness only has teeth if it CATCHES an ingester
# that mis-windows, double-counts, scrambles ordering, or drifts on a float
# rollup. The impl under test is an aggregator
#
#     agg(stream: list[Message], readings: tuple[float, ...]) -> AggResult
#
# that ingests the stream and rolls a mean over a frozen reading set. prove()
# judges each impl ONLY against AGG_EXPECTED — a literal AggResult hand-computed
# from the ingest contract (server time is canonical; QoS-1/QoS-2 retransmits are
# deduped to exactly-once; out-of-order seq are re-sequenced; events older than
# the watermark minus allowed-lateness are dropped; retained is latest-only) and
# from the exact rational mean of READINGS. The literals are NEVER read back from
# the oracle at runtime, so the check is non-circular. prove(impl) is True iff any
# fingerprint field or the mean diverges from the frozen literal — i.e. the
# planted ingest bug is caught.
#
# Pure + deterministic: a FakeClock supplies server-ingest time, integer/Fraction
# arithmetic for the exact mean, no real threads, no wall-clock, no network, no
# filesystem, no RNG. The four planted mutants model genuine MQTT-ingest defects:
#
#   * accept_late_window — disables the watermark, so an event past the
#     allowed-lateness window (m17) is wrongly accepted -> late_dropped collapses
#     to 0 and the accepted set grows (the classic late/out-of-order corruption);
#   * no_resequence — never re-sequences a topic, so out-of-order arrivals
#     (m03 seq 2 after m02 seq 3) stay scrambled -> out_of_order_pairs >= 1;
#   * no_qos2_dedupe — a retried QoS-2 publish (m09 dup of m08) is ingested twice
#     -> qos2_dupes >= 1 and the duplicate is double-counted (exactly-once broken);
#   * float_mean_drift — rolls the reading mean with an explicit binary-float
#     ``acc += x`` loop (NOT sum(), which CPython 3.12+ Neumaier-compensates),
#     so the mean drifts off the exact rational value by a ULP.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AggResult:
    """One ingest+rollup fingerprint: report counters, mid sets, and a float mean."""
    n_accepted: int
    qos2_dupes: int
    duplicates_delivered: int
    out_of_order_pairs: int
    skew_flagged: int
    skew_rejected: int
    retained_kept: int
    late_dropped: int
    accepted_mids: tuple[str, ...]
    rejected_mids: tuple[str, ...]
    flagged_mids: tuple[str, ...]
    late_mids: tuple[str, ...]
    reading_mean: float


# A frozen reading set chosen so an exact-arithmetic mean lands on a clean float
# literal (0.19) while a naive ``acc += x`` float rollup drifts to a different
# bit pattern (0.19000000000000003). Decimal-string fractions that float64 cannot
# represent exactly are what make the drift visible.
READINGS: tuple[float, ...] = (0.1, 0.2, 0.3, 0.1, 0.2, 0.3, 0.1, 0.2, 0.3, 0.1)


# The ONE frozen literal expectation. Every field is hand-computed from the ingest
# contract against the canonical STREAM (19 messages), NEVER read from the oracle:
#
#   n_accepted=14 : 19 in, minus m04/m05/m09 (QoS-1/2 dup retransmits), m11
#                   (skew > 1h -> rejected), m17 (older than watermark -> late).
#   qos2_dupes=0, duplicates_delivered=0 : every retransmit deduped to once.
#   out_of_order_pairs=0 : orders/q re-sequenced to seq 1..6.
#   skew_flagged=1 (m10, skew 200s), skew_rejected=1 (m11, skew 18899s).
#   retained_kept=1 (config/r latest-only -> m14), late_dropped=1 (m17).
#   reading_mean=0.19 : exact rational mean of READINGS (19/100) as a float.
AGG_EXPECTED = AggResult(
    n_accepted=14,
    qos2_dupes=0,
    duplicates_delivered=0,
    out_of_order_pairs=0,
    skew_flagged=1,
    skew_rejected=1,
    retained_kept=1,
    late_dropped=1,
    accepted_mids=("m01", "m02", "m03", "m06", "m07", "m08",
                   "m10", "m12", "m13", "m14", "m15", "m16", "m18", "m19"),
    rejected_mids=("m11",),
    flagged_mids=("m10",),
    late_mids=("m17",),
    reading_mean=0.19,
)


def _exact_reading_mean(readings: tuple[float, ...]) -> float:
    """Exact rational mean of the readings, returned as a float. Uses ``Fraction``
    on the decimal *string* of each reading so no binary-float drift enters before
    the final division — the correct rollup the drift mutant must miss."""
    from fractions import Fraction
    total = Fraction(0)
    for x in readings:
        total += Fraction(str(x))
    return float(total / len(readings))


def _fingerprint(res: IngestResult, mean: float) -> AggResult:
    """Reduce a full IngestResult + rollup mean to the frozen-comparable fingerprint."""
    r = res.report
    return AggResult(
        n_accepted=r.n_accepted,
        qos2_dupes=r.qos2_dupes,
        duplicates_delivered=r.duplicates_delivered,
        out_of_order_pairs=r.out_of_order_pairs,
        skew_flagged=r.skew_flagged,
        skew_rejected=r.skew_rejected,
        retained_kept=r.retained_kept,
        late_dropped=r.late_dropped,
        accepted_mids=tuple(sorted(rec.mid for rec in res.accepted)),
        rejected_mids=tuple(res.rejected),
        flagged_mids=tuple(res.flagged_skew),
        late_mids=tuple(res.late_dropped),
        reading_mean=mean,
    )


# --- ORACLE: reuse the harness's own correct ingest + an exact rollup mean ----

def oracle_aggregate(stream: list[Message], readings: tuple[float, ...]) -> AggResult:
    """Correct telemetry fingerprint, delegating to the harness's own ``ingest``
    (server-time-canonical, dedupe, re-sequence, watermark, retained-latest) and
    an exact-arithmetic reading mean."""
    res = ingest(stream)
    return _fingerprint(res, _exact_reading_mean(readings))


# --- Planted buggy twins (each models a real MQTT-ingest defect) --------------

def accept_late_window(stream: list[Message], readings: tuple[float, ...]) -> AggResult:
    """BUG: disables the watermark, so a late event past the allowed-lateness
    window (m17, device_ts 2005 < 2040-30) is wrongly accepted instead of dropped
    — late/out-of-order data silently corrupts the windowed time series."""
    res = ingest(stream, watermark=False)
    return _fingerprint(res, _exact_reading_mean(readings))


def no_resequence(stream: list[Message], readings: tuple[float, ...]) -> AggResult:
    """BUG: never re-sequences a topic, so out-of-order arrivals (m03 seq 2 lands
    after m02 seq 3) stay scrambled — the rollup reads a jumbled time series."""
    res = ingest(stream, resequence=False)
    return _fingerprint(res, _exact_reading_mean(readings))


def no_qos2_dedupe(stream: list[Message], readings: tuple[float, ...]) -> AggResult:
    """BUG: skips QoS-2 dedupe, so a retried exactly-once publish (m09 dup of m08)
    is ingested twice — telemetry is double-counted (exactly-once violated)."""
    res = ingest(stream, dedupe_qos2=False)
    return _fingerprint(res, _exact_reading_mean(readings))


def float_mean_drift(stream: list[Message], readings: tuple[float, ...]) -> AggResult:
    """BUG: rolls the reading mean with an explicit binary-float ``acc += x`` loop
    instead of exact arithmetic. float64 cannot represent the decimal-fraction
    readings exactly, so the accumulator drifts and the mean lands a ULP off the
    exact value. An explicit ``+=`` loop is used deliberately: CPython 3.12+
    special-cases ``sum()`` of floats with Neumaier compensation, which would mask
    this drift; plain accumulation is inexact on every supported CPython."""
    res = ingest(stream)
    acc = 0.0  # BUG: float accumulator drifts; never reconciled
    for x in readings:
        acc += x
    return _fingerprint(res, acc / len(readings))


def prove(impl: Callable[[list[Message], tuple[float, ...]], AggResult]) -> bool:
    """True iff ``impl`` produces a fingerprint that DIVERGES from the frozen
    literal ``AGG_EXPECTED`` (i.e. the bug is caught): any report counter, mid
    set, or the rolled-up mean differs from the hand-computed expectation.

    Non-circular + deterministic: every expectation is a literal baked into
    AGG_EXPECTED, never read from the oracle; ingest runs against a FakeClock with
    integer/Fraction arithmetic — no RNG/clock/network/filesystem/threads. An impl
    that raises on the corpus counts as caught.
    """
    try:
        got = impl(STREAM, READINGS)
    except Exception:  # noqa: BLE001 — raising on the corpus counts as caught
        return True
    return got != AGG_EXPECTED


TEETH = Teeth(
    prove=prove,
    oracle=oracle_aggregate,
    mutants=(
        Mutant("accept_late_window", accept_late_window,
               "disables the watermark so a late event past the allowed-lateness "
               "window is wrongly accepted -> late_dropped collapses to 0 and the "
               "windowed time series is corrupted by out-of-order/late data"),
        Mutant("no_resequence", no_resequence,
               "never re-sequences a topic, so out-of-order arrivals stay scrambled "
               "-> out_of_order_pairs >= 1 and the rollup reads a jumbled series"),
        Mutant("no_qos2_dedupe", no_qos2_dedupe,
               "skips QoS-2 dedupe so a retried exactly-once publish is ingested "
               "twice -> qos2_dupes >= 1, telemetry double-counted"),
        Mutant("float_mean_drift", float_mean_drift,
               "rolls the reading mean with an explicit float `acc += x` loop -> "
               "binary-float drift puts the mean a ULP off the exact rational value"),
    ),
    corpus_size=len(STREAM),
    kind="oracle_swap",
    notes="a telemetry ingester must keep server time canonical, dedupe QoS-1/2 "
          "retransmits to exactly-once, re-sequence out-of-order arrivals, drop "
          "events past the watermark, and roll aggregates without float drift",
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


def _mids(recs: tuple[Record, ...]) -> set[str]:
    return {r.mid for r in recs}


def _find(result: IngestResult, mid: str) -> Record | None:
    for r in result.accepted:
        if r.mid == mid:
            return r
    return None


def s_oracle_ingest_meets_invariants() -> Check:
    cfg = IotConfig()
    res = ingest(STREAM, config=cfg)
    r = res.report
    return _chk("oracle_ingest_meets_invariants", r.meets_invariants(cfg),
                f"ordered={r.strictly_ordered} dups={r.duplicates_delivered} "
                f"qos2_dupes={r.qos2_dupes} flagged={r.skew_flagged} "
                f"rejected={r.skew_rejected} late={r.late_dropped}")


def s_qos2_exactly_once() -> Check:
    r = ingest(STREAM).report
    return _chk("qos2_exactly_once", r.qos2_dupes == 0, f"qos2_dupes={r.qos2_dupes}")


def s_qos2_at_least_once_caught() -> Check:
    r = ingest(STREAM, dedupe_qos2=False).report
    return _chk("qos2_at_least_once_caught", r.qos2_dupes >= 1, f"qos2_dupes={r.qos2_dupes}")


def s_qos1_deduped_to_once() -> Check:
    r = ingest(STREAM).report
    return _chk("qos1_deduped_to_once", r.duplicates_delivered == 0,
                f"dups={r.duplicates_delivered}")


def s_no_dedupe_caught() -> Check:
    r = ingest(STREAM, dedupe_qos1=False, dedupe_qos2=False).report
    return _chk("no_dedupe_caught", r.duplicates_delivered >= 1,
                f"dups={r.duplicates_delivered}")


def s_qos0_best_effort_accepted() -> Check:
    res = ingest(STREAM)
    return _chk("qos0_best_effort_accepted", "m06" in _mids(res.accepted))


def s_out_of_order_resequenced() -> Check:
    r = ingest(STREAM).report
    return _chk("out_of_order_resequenced", r.out_of_order_pairs == 0,
                f"oop={r.out_of_order_pairs}")


def s_final_order_strictly_increasing() -> Check:
    r = ingest(STREAM).report
    return _chk("final_order_strictly_increasing", r.strictly_ordered)


def s_no_resequence_caught() -> Check:
    r = ingest(STREAM, resequence=False).report
    return _chk("no_resequence_caught", r.out_of_order_pairs >= 1,
                f"oop={r.out_of_order_pairs}")


def s_skew_over_60s_flagged() -> Check:
    res = ingest(STREAM)
    return _chk("skew_over_60s_flagged",
                res.report.skew_flagged >= 1 and "m10" in res.flagged_skew,
                f"flagged={res.flagged_skew}")


def s_skew_over_1h_rejected() -> Check:
    res = ingest(STREAM)
    return _chk("skew_over_1h_rejected",
                res.report.skew_rejected >= 1 and "m11" in res.rejected,
                f"rejected={res.rejected}")


def s_clock_truster_caught() -> Check:
    r = ingest(STREAM, trust_device_clock=True).report
    return _chk("clock_truster_caught", r.skew_flagged == 0 and r.skew_rejected == 0,
                f"flagged={r.skew_flagged} rejected={r.skew_rejected}")


def s_server_time_canonical() -> Check:
    res = ingest(STREAM)
    rec = _find(res, "m10")
    return _chk("server_time_canonical", rec is not None and rec.ts == 1100,
                f"ts={getattr(rec, 'ts', None)} (device_ts was 1300)")


def s_late_beyond_window_dropped() -> Check:
    res = ingest(STREAM)
    return _chk("late_beyond_window_dropped",
                res.report.late_dropped >= 1 and "m17" in res.late_dropped,
                f"late={res.late_dropped}")


def s_late_within_window_kept() -> Check:
    res = ingest(STREAM)
    return _chk("late_within_window_kept", "m18" in _mids(res.accepted))


def s_no_watermark_caught() -> Check:
    r = ingest(STREAM, watermark=False).report
    return _chk("no_watermark_caught", r.late_dropped == 0, f"late={r.late_dropped}")


def s_retained_latest_only() -> Check:
    res = ingest(STREAM)
    latest = [r.mid for r in res.retained if r.topic == "config/r"]
    return _chk("retained_latest_only",
                res.report.retained_kept == 1 and latest == ["m14"],
                f"kept={res.report.retained_kept} latest={latest}")


def s_retain_all_caught() -> Check:
    r = ingest(STREAM, retain_latest=False).report
    return _chk("retain_all_caught", r.retained_kept > 1, f"kept={r.retained_kept}")


def s_qos1_replayed_on_reconnect() -> Check:
    return _chk("qos1_replayed_on_reconnect", len(reconnect(SESSIONS[0])) == 2)


def s_non_persistent_drops_queue() -> Check:
    return _chk("non_persistent_drops_queue", len(reconnect(SESSIONS[1])) == 0)


def s_non_persistent_session_caught() -> Check:
    o = len(reconnect(SESSIONS[0]))
    b = len(reconnect_nonpersistent(SESSIONS[0]))
    return _chk("non_persistent_session_caught", o == 2 and b == 0, f"oracle={o} buggy={b}")


def s_last_will_on_abnormal_disconnect() -> Check:
    abnormal = len(on_disconnect(SESSIONS[2], True))
    graceful = len(on_disconnect(SESSIONS[2], False))
    return _chk("last_will_on_abnormal_disconnect", abnormal == 1 and graceful == 0,
                f"abnormal={abnormal} graceful={graceful}")


def s_no_will_caught() -> Check:
    o = len(on_disconnect(SESSIONS[2], True))
    b = len(on_disconnect_no_will(SESSIONS[2], True))
    return _chk("no_will_caught", o == 1 and b == 0, f"oracle={o} buggy={b}")


def s_ingest_counts_consistent() -> Check:
    res = ingest(STREAM)
    return _chk("ingest_counts_consistent",
                res.report.n_in == len(STREAM)
                and res.report.n_accepted == len(res.accepted),
                f"n_in={res.report.n_in} n_accepted={res.report.n_accepted}")


SCENARIOS: dict[str, Callable[[], Check]] = {
    f.__name__[2:]: f
    for f in [
        s_oracle_ingest_meets_invariants,
        s_qos2_exactly_once,
        s_qos2_at_least_once_caught,
        s_qos1_deduped_to_once,
        s_no_dedupe_caught,
        s_qos0_best_effort_accepted,
        s_out_of_order_resequenced,
        s_final_order_strictly_increasing,
        s_no_resequence_caught,
        s_skew_over_60s_flagged,
        s_skew_over_1h_rejected,
        s_clock_truster_caught,
        s_server_time_canonical,
        s_late_beyond_window_dropped,
        s_late_within_window_kept,
        s_no_watermark_caught,
        s_retained_latest_only,
        s_retain_all_caught,
        s_qos1_replayed_on_reconnect,
        s_non_persistent_drops_queue,
        s_non_persistent_session_caught,
        s_last_will_on_abnormal_disconnect,
        s_no_will_caught,
        s_ingest_counts_consistent,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    """Run every scenario through a Report (fail-loud + structured findings), then
    assert the teeth: the correct oracle is clean and every planted mutant caught."""
    report = Report("core/iot_telemetry")

    # 1. The harness's existing meaningful scenario checks (QoS, ordering, dedupe,
    #    skew, watermark, retained, session lifecycle) — kept verbatim.
    for fn in SCENARIOS.values():
        chk = fn()
        report.record(chk.name, chk.passed, detail=chk.detail)

    # 2. The frozen aggregate fingerprint the correct oracle must reproduce, and
    #    the exact rational reading mean (non-circular literals).
    got = oracle_aggregate(STREAM, READINGS)
    report.add("oracle_fingerprint_matches_frozen", AGG_EXPECTED, got,
               detail="oracle ingest+rollup must equal the hand-computed literal")
    report.add("oracle_reading_mean_exact", AGG_EXPECTED.reading_mean,
               got.reading_mean, detail="exact rational mean, no float drift")

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MQTT-like IoT telemetry ingest harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
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
