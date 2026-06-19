#!/usr/bin/env python3
"""
queue_test_harness.py — Message-queue delivery semantics (at-least/exactly-once, DLQ, ordering).
================================================================================================

Pure-stdlib. Zero external dependencies.

Queue/worker glue written by LLMs routinely claims "exactly-once" while really
being at-least-once (no dedup on redelivery → double side effects), drops poison
messages instead of routing them to a dead-letter queue, breaks per-key ordering
when a consumer group rebalances, and redelivers after an ack-timeout while the
original consumer is still working. This harness models a broker with an
injectable clock and proves an oracle holds the delivery contract while four
intentionally-broken brokers each violate one guarantee.

Distinct from `idempotency` (the dedup-store primitive) and `concurrency`
(race detection). In-process, no networked server.

Usage:
  python harnesses/core/queue_test_harness.py --self-test
  python harnesses/core/queue_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys

# Make the shared teeth contract importable whether run as a module or a script.
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402


class Delivery(Enum):
    AT_LEAST_ONCE = "at_least_once"
    EXACTLY_ONCE = "exactly_once"


@dataclass(frozen=True)
class Message:
    id: str
    key: str
    body: str = ""


@dataclass
class QueueConfig:
    max_deliveries: int = 3
    ack_timeout_s: float = 30.0
    max_in_flight: int = 10
    max_queue_depth: int = 1_000
    delivery: Delivery = Delivery.AT_LEAST_ONCE


class Clock:
    """Minimal injectable clock (seconds). Local to keep the harness self-contained."""

    def __init__(self, start: float = 0.0):
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@dataclass
class _Record:
    msg: Message
    deliveries: int = 0
    in_flight: bool = False
    done: bool = False
    visible_at: float = 0.0


# ---------------------------------------------------------------------------
# Oracle broker
# ---------------------------------------------------------------------------


class InMemoryBroker:
    """Reference broker that honors the delivery contract."""

    def __init__(self, config: QueueConfig, clock: Clock):
        self.config = config
        self.clock = clock
        self._records: list[_Record] = []
        self.dlq: list[Message] = []
        self.publish_log: list[Message] = []
        self._processed: set[str] = set()
        self.max_in_flight_observed = 0

    # -- producer side --
    def _backlog(self) -> int:
        return sum(1 for r in self._records if not r.done)

    def publish(self, msg: Message) -> bool:
        if self._backlog() >= self.config.max_queue_depth:
            return False  # backpressure: producer must slow down
        self._records.append(_Record(msg=msg, visible_at=self.clock.now()))
        self.publish_log.append(msg)
        return True

    # -- consumer side --
    def in_flight(self) -> int:
        return sum(1 for r in self._records if r.in_flight)

    def _is_head_of_key(self, rec: _Record) -> bool:
        for r in self._records:
            if r is rec:
                return True
            if r.msg.key == rec.msg.key and not r.done:
                return False
        return True

    def poll(self, consumer: str = "c") -> _Record | None:
        if self.in_flight() >= self.config.max_in_flight:
            return None
        now = self.clock.now()
        for r in self._records:
            if r.done or r.in_flight or r.visible_at > now:
                continue
            if not self._is_head_of_key(r):
                continue
            r.in_flight = True
            r.deliveries += 1
            r.visible_at = now + self.config.ack_timeout_s
            self.max_in_flight_observed = max(self.max_in_flight_observed, self.in_flight())
            return r
        return None

    def process(self, rec: _Record) -> bool:
        """Run the side effect. EXACTLY_ONCE suppresses a repeat for a seen id."""
        if self.config.delivery == Delivery.EXACTLY_ONCE and rec.msg.id in self._processed:
            return False
        self._processed.add(rec.msg.id)
        return True

    def ack(self, rec: _Record) -> bool:
        rec.in_flight = False
        if rec.done:
            return False
        rec.done = True
        return True

    def nack(self, rec: _Record) -> None:
        rec.in_flight = False
        rec.visible_at = self.clock.now()
        if rec.deliveries > self.config.max_deliveries:
            self._to_dlq(rec)

    def heartbeat(self, rec: _Record) -> None:
        rec.visible_at = self.clock.now() + self.config.ack_timeout_s

    def tick(self) -> None:
        now = self.clock.now()
        for r in self._records:
            if r.in_flight and r.visible_at <= now:
                r.in_flight = False  # ack timed out → becomes redeliverable
                if r.deliveries > self.config.max_deliveries:
                    self._to_dlq(r)

    def rebalance(self, consumers: int) -> None:
        """Reassign partitions. The oracle's head-of-key rule is consumer-independent,
        so ordering is preserved regardless of how keys map to consumers."""
        return None

    def _to_dlq(self, rec: _Record) -> None:
        if not rec.done:
            rec.done = True
            rec.in_flight = False
            self.dlq.append(rec.msg)

    def deliveries_of(self, msg_id: str) -> int:
        return sum(r.deliveries for r in self._records if r.msg.id == msg_id)


# ---------------------------------------------------------------------------
# Intentionally-broken brokers
# ---------------------------------------------------------------------------


class NaiveBroker(InMemoryBroker):
    """Acks on delivery (poll). A crash before processing loses the message."""

    def poll(self, consumer: str = "c") -> _Record | None:
        rec = super().poll(consumer)
        if rec is not None:
            rec.done = True  # bug: considered handled the instant it's delivered
            rec.in_flight = False
        return rec


class LossyExactlyOnce(InMemoryBroker):
    """Configured EXACTLY_ONCE but never dedups → redelivery double-processes."""

    def process(self, rec: _Record) -> bool:
        self._processed.add(rec.msg.id)
        return True  # bug: ignores the seen-set


class OrderBreakingRebalance(InMemoryBroker):
    """Delivers non-head-of-key messages → same-key work runs concurrently/out of order."""

    def _is_head_of_key(self, rec: _Record) -> bool:
        return True  # bug: no per-key serialization


class NoDlqBroker(InMemoryBroker):
    """Never routes to the DLQ → poison messages redeliver forever."""

    def _to_dlq(self, rec: _Record) -> None:
        rec.in_flight = False  # bug: drops the DLQ step, leaves it redeliverable


# ---------------------------------------------------------------------------
# Workload driver + report
# ---------------------------------------------------------------------------


@dataclass
class DeliveryReport:
    published: int
    total_processed: int
    processed_unique: int
    duplicates: int
    acked: int
    dlq_count: int
    ordering_violations: int
    max_in_flight_observed: int

    @property
    def is_exactly_once(self) -> bool:
        return self.duplicates == 0

    @property
    def ordering_preserved(self) -> bool:
        return self.ordering_violations == 0


def consume_all(broker: InMemoryBroker, *, max_steps: int = 2_000,
                crash_once: tuple[str, ...] = (), nack_always: tuple[str, ...] = (),
                lose_ack_once: tuple[str, ...] = ()) -> list[Message]:
    """Drive a single consumer until the queue drains. Returns processed order."""
    crashed: set[str] = set()
    lost: set[str] = set()
    processed: list[Message] = []
    steps = 0
    while steps < max_steps:
        steps += 1
        rec = broker.poll()
        if rec is None:
            if broker.in_flight() == 0:
                break
            broker.clock.advance(broker.config.ack_timeout_s + 1.0)
            broker.tick()
            continue
        mid = rec.msg.id
        if mid in nack_always:
            broker.nack(rec)
            continue
        if mid in crash_once and mid not in crashed:
            crashed.add(mid)
            continue  # consumer died before processing or acking
        ran = broker.process(rec)
        if ran:
            processed.append(rec.msg)
        if mid in lose_ack_once and mid not in lost:
            lost.add(mid)
            continue  # ack lost in transit; message will time out and redeliver
        broker.ack(rec)
    return processed


def build_report(broker: InMemoryBroker, processed: list[Message]) -> DeliveryReport:
    seen: list[str] = []
    for m in processed:
        if m.id not in seen:
            seen.append(m.id)
    by_key_processed: dict[str, list[str]] = {}
    for m in processed:
        lst = by_key_processed.setdefault(m.key, [])
        if m.id not in lst:
            lst.append(m.id)
    by_key_published: dict[str, list[str]] = {}
    for m in broker.publish_log:
        by_key_published.setdefault(m.key, []).append(m.id)

    violations = 0
    for key, proc_ids in by_key_processed.items():
        expected = [i for i in by_key_published.get(key, []) if i in proc_ids]
        if proc_ids != expected:
            violations += 1

    return DeliveryReport(
        published=len(broker.publish_log),
        total_processed=len(processed),
        processed_unique=len(seen),
        duplicates=len(processed) - len(seen),
        acked=sum(1 for r in broker._records if r.done and r.msg not in broker.dlq),
        dlq_count=len(broker.dlq),
        ordering_violations=violations,
        max_in_flight_observed=broker.max_in_flight_observed,
    )


# ---------------------------------------------------------------------------
# Teeth: a frozen corpus of delivery-contract cases parametrized by broker
# CLASS. The oracle (InMemoryBroker) satisfies every case; each broken broker
# violates exactly one guarantee, so the corpus catches every planted mutant.
# `prove(impl)` is pure/deterministic (injected Clock, no I/O, no RNG).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ContractCase:
    """One delivery-contract expectation, judged against a broker class."""

    name: str
    # check(broker_cls) -> bool : True iff the broker HONORS this guarantee.
    check: Callable[[type], bool]
    expected: bool


def _c_message_not_lost_on_crash(broker_cls: type) -> bool:
    b = broker_cls(QueueConfig(), Clock())
    b.publish(Message("m1", "k"))
    proc = consume_all(b, crash_once=("m1",))
    return "m1" in {m.id for m in proc}


def _c_exactly_once_no_duplicates(broker_cls: type) -> bool:
    b = broker_cls(QueueConfig(delivery=Delivery.EXACTLY_ONCE), Clock())
    b.publish(Message("m1", "k"))
    proc = consume_all(b, lose_ack_once=("m1",))
    return build_report(b, proc).duplicates == 0


def _c_head_of_key_blocks_second(broker_cls: type) -> bool:
    b = broker_cls(QueueConfig(), Clock())
    b.publish(Message("m0", "K"))
    b.publish(Message("m1", "K"))
    b.poll()                 # m0 in flight
    second = b.poll()        # head-of-key: m1 must NOT be deliverable yet
    return second is None


def _c_poison_routes_to_dlq(broker_cls: type) -> bool:
    b = broker_cls(QueueConfig(max_deliveries=3), Clock())
    b.publish(Message("poison", "k"))
    consume_all(b, nack_always=("poison",))
    return [m.id for m in b.dlq] == ["poison"]


_CONTRACT_CASES: tuple[_ContractCase, ...] = (
    _ContractCase("message_not_lost_on_crash", _c_message_not_lost_on_crash, True),
    _ContractCase("exactly_once_no_duplicates", _c_exactly_once_no_duplicates, True),
    _ContractCase("head_of_key_blocks_second", _c_head_of_key_blocks_second, True),
    _ContractCase("poison_routes_to_dlq", _c_poison_routes_to_dlq, True),
)


def _prove(impl: type) -> bool:
    """True iff broker class `impl` violates the delivery contract on any case.

    Returns True (caught) when an observed guarantee disagrees with the frozen
    corpus expectation, or when driving the broker raises.
    """
    for case in _CONTRACT_CASES:
        try:
            observed = case.check(impl)
        except Exception:  # noqa: BLE001 — a broker that crashes on a case is caught
            return True
        if observed != case.expected:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["InMemoryBroker"]

TEETH = Teeth(
    prove=_prove,
    oracle=InMemoryBroker,
    mutants=(
        Mutant("acks_on_poll_loses_on_crash", NaiveBroker,
               "acks at delivery time; a crash before processing loses the message"),
        Mutant("exactly_once_never_dedups", LossyExactlyOnce,
               "EXACTLY_ONCE configured but redelivery double-processes (no seen-set)"),
        Mutant("no_per_key_serialization", OrderBreakingRebalance,
               "delivers non-head-of-key messages → same-key work runs out of order"),
        Mutant("never_routes_to_dlq", NoDlqBroker,
               "poison messages redeliver forever instead of going to the DLQ"),
    ),
    corpus_size=len(_CONTRACT_CASES),
    kind="oracle_swap",
    notes="reference broker honors at-least/exactly-once, head-of-key order, and DLQ; "
          "each planted broker violates exactly one of those guarantees",
)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class QueueCheck:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> QueueCheck:
    return QueueCheck(name, bool(cond), detail)


def _broker(delivery: Delivery = Delivery.AT_LEAST_ONCE, **cfg) -> InMemoryBroker:
    return InMemoryBroker(QueueConfig(delivery=delivery, **cfg), Clock())


def s_at_least_once_redelivers_on_nack() -> QueueCheck:
    b = _broker()
    b.publish(Message("m1", "k"))
    r = b.poll()
    b.nack(r)              # consumer rejects → redeliver
    r = b.poll()
    ran = b.process(r)
    b.ack(r)
    return _chk("at_least_once_redelivers_on_nack",
                ran and b.deliveries_of("m1") >= 2, f"deliveries={b.deliveries_of('m1')}")


def s_at_least_once_redelivers_after_ack_timeout() -> QueueCheck:
    b = _broker(ack_timeout_s=30.0)
    b.publish(Message("m1", "k"))
    proc = consume_all(b, crash_once=("m1",))
    return _chk("at_least_once_redelivers_after_ack_timeout",
                [m.id for m in proc] == ["m1"] and b.deliveries_of("m1") >= 2,
                f"deliveries={b.deliveries_of('m1')}")


def s_exactly_once_suppresses_duplicate() -> QueueCheck:
    b = _broker(Delivery.EXACTLY_ONCE)
    b.publish(Message("m1", "k"))
    proc = consume_all(b, lose_ack_once=("m1",))
    rep = build_report(b, proc)
    return _chk("exactly_once_suppresses_duplicate",
                rep.processed_unique == 1 and rep.duplicates == 0,
                f"unique={rep.processed_unique} dups={rep.duplicates}")


def s_exactly_once_survives_redelivery() -> QueueCheck:
    b = _broker(Delivery.EXACTLY_ONCE)
    for i in range(3):
        b.publish(Message(f"m{i}", f"k{i}"))
    proc = consume_all(b, lose_ack_once=("m0", "m1", "m2"))
    rep = build_report(b, proc)
    return _chk("exactly_once_survives_redelivery", rep.is_exactly_once,
                f"dups={rep.duplicates}")


def s_at_least_once_double_processes_on_lost_ack() -> QueueCheck:
    b = _broker(Delivery.AT_LEAST_ONCE)
    b.publish(Message("m1", "k"))
    proc = consume_all(b, lose_ack_once=("m1",))
    rep = build_report(b, proc)
    return _chk("at_least_once_double_processes_on_lost_ack",
                rep.duplicates >= 1 and not rep.is_exactly_once, f"dups={rep.duplicates}")


def s_dlq_after_max_deliveries() -> QueueCheck:
    b = _broker(max_deliveries=3)
    b.publish(Message("poison", "k"))
    consume_all(b, nack_always=("poison",))
    return _chk("dlq_after_max_deliveries",
                [m.id for m in b.dlq] == ["poison"], f"dlq={[m.id for m in b.dlq]}")


def s_dlq_routes_poison_only() -> QueueCheck:
    b = _broker(max_deliveries=2)
    b.publish(Message("g1", "a"))
    b.publish(Message("poison", "b"))
    b.publish(Message("g2", "c"))
    proc = consume_all(b, nack_always=("poison",))
    good = {m.id for m in proc}
    return _chk("dlq_routes_poison_only",
                [m.id for m in b.dlq] == ["poison"] and good == {"g1", "g2"},
                f"dlq={[m.id for m in b.dlq]} good={sorted(good)}")


def s_fifo_within_key_preserved() -> QueueCheck:
    b = _broker()
    for i in range(4):
        b.publish(Message(f"m{i}", "same"))
    proc = consume_all(b)
    rep = build_report(b, proc)
    return _chk("fifo_within_key_preserved",
                [m.id for m in proc] == ["m0", "m1", "m2", "m3"] and rep.ordering_preserved,
                f"order={[m.id for m in proc]}")


def s_interleave_across_keys_allowed() -> QueueCheck:
    b = _broker()
    b.publish(Message("a1", "A"))
    b.publish(Message("b1", "B"))
    b.publish(Message("a2", "A"))
    b.publish(Message("b2", "B"))
    proc = consume_all(b)
    rep = build_report(b, proc)
    return _chk("interleave_across_keys_allowed", rep.ordering_preserved, "")


def s_rebalance_preserves_per_key_order() -> QueueCheck:
    b = _broker()
    for i in range(4):
        b.publish(Message(f"m{i}", "K"))
    r = b.poll()  # m0 in flight
    b.rebalance(consumers=3)
    blocked = b.poll()  # head-of-key: m1 must NOT be deliverable while m0 unacked
    b.process(r)
    b.ack(r)
    proc = [r.msg] + consume_all(b)
    rep = build_report(b, proc)
    return _chk("rebalance_preserves_per_key_order",
                blocked is None and rep.ordering_preserved,
                f"blocked={blocked} order={[m.id for m in proc]}")


def s_rebalance_no_message_loss() -> QueueCheck:
    b = _broker()
    for i in range(5):
        b.publish(Message(f"m{i}", f"k{i % 2}"))
    r = b.poll()
    b.rebalance(consumers=2)
    b.process(r)
    b.ack(r)
    proc = [r.msg] + consume_all(b)
    return _chk("rebalance_no_message_loss",
                {m.id for m in proc} == {f"m{i}" for i in range(5)},
                f"got={sorted(m.id for m in proc)}")


def s_rebalance_no_double_delivery_for_acked() -> QueueCheck:
    b = _broker()
    b.publish(Message("m0", "k"))
    b.publish(Message("m1", "k"))
    r = b.poll()
    b.process(r)
    b.ack(r)  # m0 done
    b.rebalance(consumers=2)
    consume_all(b)
    return _chk("rebalance_no_double_delivery_for_acked",
                b.deliveries_of("m0") == 1, f"m0_deliveries={b.deliveries_of('m0')}")


def s_ack_timeout_extends_on_heartbeat() -> QueueCheck:
    b = _broker(ack_timeout_s=30.0)
    b.publish(Message("m1", "k"))
    r = b.poll()
    b.clock.advance(20.0)
    b.heartbeat(r)
    b.clock.advance(20.0)
    b.tick()  # 40s total but heartbeat reset the deadline
    return _chk("ack_timeout_extends_on_heartbeat",
                r.in_flight and b.deliveries_of("m1") == 1,
                f"in_flight={r.in_flight} deliveries={b.deliveries_of('m1')}")


def s_backpressure_caps_in_flight() -> QueueCheck:
    b = _broker(max_in_flight=2)
    for i in range(3):
        b.publish(Message(f"m{i}", f"k{i}"))
    b.poll()
    b.poll()
    third = b.poll()
    return _chk("backpressure_caps_in_flight",
                third is None and b.max_in_flight_observed <= 2,
                f"third={third} max_if={b.max_in_flight_observed}")


def s_backpressure_blocks_publish_when_full() -> QueueCheck:
    b = _broker(max_queue_depth=2)
    ok1 = b.publish(Message("m0", "a"))
    ok2 = b.publish(Message("m1", "b"))
    ok3 = b.publish(Message("m2", "c"))  # backlog already 2 → rejected
    return _chk("backpressure_blocks_publish_when_full",
                ok1 and ok2 and not ok3, f"ok=[{ok1},{ok2},{ok3}]")


def s_crash_before_ack_redelivers() -> QueueCheck:
    b = _broker()
    b.publish(Message("m1", "k"))
    proc = consume_all(b, crash_once=("m1",))
    return _chk("crash_before_ack_redelivers",
                [m.id for m in proc] == ["m1"], f"order={[m.id for m in proc]}")


def s_naive_broker_loses_message_detected() -> QueueCheck:
    b = NaiveBroker(QueueConfig(), Clock())
    b.publish(Message("m1", "k"))
    proc = consume_all(b, crash_once=("m1",))  # acked-on-poll then crash → lost
    lost = "m1" not in {m.id for m in proc}
    return _chk("naive_broker_loses_message_detected", lost,
                f"processed={[m.id for m in proc]}")


def s_lossy_eo_double_processes_detected() -> QueueCheck:
    b = LossyExactlyOnce(QueueConfig(delivery=Delivery.EXACTLY_ONCE), Clock())
    b.publish(Message("m1", "k"))
    proc = consume_all(b, lose_ack_once=("m1",))
    rep = build_report(b, proc)
    return _chk("lossy_eo_double_processes_detected", rep.duplicates >= 1,
                f"dups={rep.duplicates}")


def s_order_breaking_rebalance_detected() -> QueueCheck:
    oracle = _broker()
    oracle.publish(Message("m0", "K"))
    oracle.publish(Message("m1", "K"))
    oracle.poll()
    oracle_blocked = oracle.poll()  # oracle blocks m1 while m0 unacked

    bug = OrderBreakingRebalance(QueueConfig(), Clock())
    bug.publish(Message("m0", "K"))
    bug.publish(Message("m1", "K"))
    r0 = bug.poll()
    r1 = bug.poll()  # bug delivers m1 concurrently
    # consumer 2 finishes m1 before consumer 1 finishes m0 → out of order
    bug.process(r1)
    bug.ack(r1)
    bug.process(r0)
    bug.ack(r0)
    rep = build_report(bug, [r1.msg, r0.msg])
    return _chk("order_breaking_rebalance_detected",
                oracle_blocked is None and r1 is not None and rep.ordering_violations >= 1,
                f"oracle_blocked={oracle_blocked} viol={rep.ordering_violations}")


def s_no_dlq_loops_forever_detected() -> QueueCheck:
    b = NoDlqBroker(QueueConfig(max_deliveries=3), Clock())
    b.publish(Message("poison", "k"))
    # nack it 6 times; a correct broker would DLQ after max; this one never does
    for _ in range(6):
        r = b.poll()
        if r is None:
            break
        b.nack(r)
    return _chk("no_dlq_loops_forever_detected",
                len(b.dlq) == 0 and b.deliveries_of("poison") > b.config.max_deliveries,
                f"dlq={len(b.dlq)} deliveries={b.deliveries_of('poison')}")


SCENARIOS: dict[str, Callable[[], QueueCheck]] = {
    f.__name__[2:]: f
    for f in [
        s_at_least_once_redelivers_on_nack,
        s_at_least_once_redelivers_after_ack_timeout,
        s_exactly_once_suppresses_duplicate,
        s_exactly_once_survives_redelivery,
        s_at_least_once_double_processes_on_lost_ack,
        s_dlq_after_max_deliveries,
        s_dlq_routes_poison_only,
        s_fifo_within_key_preserved,
        s_interleave_across_keys_allowed,
        s_rebalance_preserves_per_key_order,
        s_rebalance_no_message_loss,
        s_rebalance_no_double_delivery_for_acked,
        s_ack_timeout_extends_on_heartbeat,
        s_backpressure_caps_in_flight,
        s_backpressure_blocks_publish_when_full,
        s_crash_before_ack_redelivers,
        s_naive_broker_loses_message_detected,
        s_lossy_eo_double_processes_detected,
        s_order_breaking_rebalance_detected,
        s_no_dlq_loops_forever_detected,
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
    p = argparse.ArgumentParser(description="Message-queue delivery-semantics harness")
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
