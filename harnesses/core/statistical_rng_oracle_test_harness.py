#!/usr/bin/env python3
"""
Statistical RNG oracle test harness.

Provides deterministic, replayable checks for game/slot/drop-table math. It is
not a casino certification tool; it is a CI-sized guard against biased random
selection, non-replayable seeds, and impossible confidence windows.

Self-test:
  python harnesses/core/statistical_rng_oracle_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedOutcome:
    name: str
    weight: int


@dataclass(frozen=True)
class RngReport:
    counts: dict[str, int]
    expected: dict[str, float]
    observed: dict[str, float]
    ok: bool
    detail: str


class LcgRng:
    def __init__(self, seed: int) -> None:
        self.state = seed & 0x7FFFFFFF

    def next_float(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state / 0x80000000


class BiasedRng:
    def next_float(self) -> float:
        return 0.01


TABLE: tuple[WeightedOutcome, ...] = (
    WeightedOutcome("common", 70),
    WeightedOutcome("rare", 25),
    WeightedOutcome("jackpot", 5),
)


def pick_outcome(rng: object, table: tuple[WeightedOutcome, ...] = TABLE) -> str:
    total = sum(item.weight for item in table)
    roll = rng.next_float() * total  # type: ignore[attr-defined]
    cursor = 0.0
    for item in table:
        cursor += item.weight
        if roll < cursor:
            return item.name
    return table[-1].name


def sample(seed: int = 12345, draws: int = 20_000, rng: object | None = None) -> dict[str, int]:
    rng = rng or LcgRng(seed)
    counts = {item.name: 0 for item in TABLE}
    for _ in range(draws):
        counts[pick_outcome(rng)] += 1
    return counts


def evaluate_distribution(counts: dict[str, int], tolerance: float = 0.02) -> RngReport:
    draws = sum(counts.values())
    expected = {item.name: item.weight / sum(entry.weight for entry in TABLE) for item in TABLE}
    observed = {name: count / draws for name, count in counts.items()}
    deviations = {name: abs(observed[name] - expected[name]) for name in expected}
    ok = all(delta <= tolerance for delta in deviations.values())
    detail = ", ".join(f"{name}: obs={observed[name]:.4f} exp={expected[name]:.4f}" for name in expected)
    return RngReport(counts=counts, expected=expected, observed=observed, ok=ok, detail=detail)


def check_seed_replay(seed: int = 999, draws: int = 250) -> bool:
    return [pick_outcome(LcgRng(seed + i)) for i in range(draws)] == [
        pick_outcome(LcgRng(seed + i)) for i in range(draws)
    ]


def _run_self_test() -> int:
    good = evaluate_distribution(sample())
    biased = evaluate_distribution(sample(rng=BiasedRng()))
    replay = check_seed_replay()
    if not good.ok:
        print(f"FAIL good distribution: {good.detail}", file=sys.stderr)
        return 1
    if biased.ok:
        print("FAIL proof did not catch biased RNG", file=sys.stderr)
        return 1
    if not replay:
        print("FAIL seed replay is not deterministic", file=sys.stderr)
        return 1
    print("OK: RNG distribution, replay, and biased proof controls passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run statistical RNG oracle controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--draws", type=int, default=20_000)
    args = parser.parse_args(argv)
    if args.self_test:
        return _run_self_test()
    report = evaluate_distribution(sample(draws=args.draws))
    print(report.detail)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
