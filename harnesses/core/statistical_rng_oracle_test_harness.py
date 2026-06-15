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
from typing import Callable, Dict

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402


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


# ---------------------------------------------------------------------------
# TEETH: a FROZEN expected-proportion table + planted sampler defects.
#
# A drop-table / slot RNG harness only has teeth if it CATCHES a sampler whose
# realized distribution does not match the advertised odds. The contract every
# correct sampler must hold:
#
#   * over a fixed seed and a fixed number of draws, each declared outcome
#     appears at (within tolerance of) its ADVERTISED proportion; and
#   * every declared outcome is REACHABLE — a bucket whose realized frequency is
#     exactly zero is a planted-bug tell (the jackpot can never pay out).
#
# kind="statistical": an impl is a *sampler* callable
# ``sampler(seed: int, draws: int) -> Dict[str, int]`` returning the realized
# count per outcome name. prove() drives the sampler at the FROZEN seed/draws and
# judges the realized proportions against FAIR_PROPORTIONS — a table of
# hand-written LITERAL constants (0.70 / 0.25 / 0.05), NOT recomputed from TABLE
# weights at runtime. So the check is non-circular: corrupting a literal in
# FAIR_PROPORTIONS flips prove(oracle) False->True (verified below and in the
# paired test_noncircular_corpus). prove(impl) is True iff any outcome diverges
# from its frozen proportion by more than the tolerance, OR any declared outcome
# is unreachable (zero realized frequency) — i.e. the planted bias is caught.
#
# Pure + deterministic: the only randomness is the SEEDED LcgRng replayed at a
# fixed seed; no real RNG, no clock, no network, no filesystem, no threads.
#
# The three planted mutants model genuine drop-table defects:
#   * biased_rng           — BiasedRng.next_float always returns 0.01, so every
#                            roll lands in the first bucket (rare/jackpot never
#                            pay out): the classic stuck/constant-source bug.
#   * cursor_drops_jackpot — an off-by-one cumulative-cursor loop that iterates
#                            all-but-the-last bucket and falls through to the
#                            FIRST outcome, so the last/jackpot bucket is
#                            unreachable and its odds silently fold into common.
#   * truncated_range_rng  — an RNG whose usable output range is capped at 0.90
#                            (e.g. a modulus/scale off by a factor): the top
#                            jackpot bucket [0.95, 1.0) can never be hit and rare
#                            is undersampled, while common is over-represented.
# ---------------------------------------------------------------------------

# Frozen seed/draw budget for the teeth swap-check. Kept modest so the gate stays
# CI-sized while the law of large numbers keeps the oracle comfortably inside
# TEETH_TOLERANCE of the advertised odds.
TEETH_SEED: int = 12345
TEETH_DRAWS: int = 20_000
TEETH_TOLERANCE: float = 0.02

# The FROZEN advertised proportions, written as LITERAL constants (NOT derived
# from TABLE.weight at runtime). These are the hand-computed fair odds for the
# 70 / 25 / 5 drop table; they are the non-circular yardstick prove() judges
# against. Corrupting any value here must make prove(oracle) flip to True.
FAIR_PROPORTIONS: Dict[str, float] = {
    "common": 0.70,
    "rare": 0.25,
    "jackpot": 0.05,
}


# --- ORACLE: reuse the harness's own correct LcgRng + pick_outcome ------------

def oracle_sampler(seed: int, draws: int) -> Dict[str, int]:
    """Correct sampler: replay the harness's own seeded ``LcgRng`` through its
    own ``pick_outcome`` and tally the realized counts. Reuses the harness code
    under test — it does not reinvent the distribution."""
    rng = LcgRng(seed)
    counts = {item.name: 0 for item in TABLE}
    for _ in range(draws):
        counts[pick_outcome(rng)] += 1
    return counts


# --- Planted buggy twins (each models a real drop-table defect) --------------

def biased_sampler(seed: int, draws: int) -> Dict[str, int]:
    """BUG: a stuck/constant random source (``BiasedRng`` always yields 0.01),
    so every roll falls into the first bucket and rare/jackpot never pay out."""
    rng = BiasedRng()
    counts = {item.name: 0 for item in TABLE}
    for _ in range(draws):
        counts[pick_outcome(rng)] += 1
    return counts


def _pick_drops_jackpot(rng: object, table: tuple[WeightedOutcome, ...] = TABLE) -> str:
    """BUG: off-by-one cumulative-cursor loop. It walks only ``table[:-1]`` and
    falls through to ``table[0]`` instead of the final bucket, so the last
    (jackpot) outcome is unreachable and its odds fold silently into common."""
    total = sum(item.weight for item in table)
    roll = rng.next_float() * total  # type: ignore[attr-defined]
    cursor = 0.0
    for item in table[:-1]:  # BUG: never considers the last bucket
        cursor += item.weight
        if roll < cursor:
            return item.name
    return table[0].name  # BUG: jackpot folded back into the first outcome


def cursor_drops_jackpot_sampler(seed: int, draws: int) -> Dict[str, int]:
    """Sampler that uses the off-by-one picker above with the correct RNG."""
    rng = LcgRng(seed)
    counts = {item.name: 0 for item in TABLE}
    for _ in range(draws):
        counts[_pick_drops_jackpot(rng)] += 1
    return counts


class TruncatedRangeRng:
    """BUG: an LCG whose usable output range is capped at 0.90 (e.g. a scale /
    modulus off by a constant factor). The top jackpot bucket [0.95, 1.0) can
    never be selected and rare is undersampled."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0x7FFFFFFF

    def next_float(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        # BUG: rescales into [0, 0.90) so the high-end buckets are unreachable
        return (self.state / 0x80000000) * 0.90


def truncated_range_sampler(seed: int, draws: int) -> Dict[str, int]:
    """Sampler driven by the range-truncated RNG above."""
    rng = TruncatedRangeRng(seed)
    counts = {item.name: 0 for item in TABLE}
    for _ in range(draws):
        counts[pick_outcome(rng)] += 1
    return counts


def prove(impl: Callable[[int, int], Dict[str, int]]) -> bool:
    """True iff ``impl``'s realized distribution diverges from the FROZEN
    advertised odds (i.e. the planted bias is caught).

    Caught when, at the frozen seed/draws, any declared outcome's realized
    proportion is more than ``TEETH_TOLERANCE`` away from its frozen literal in
    ``FAIR_PROPORTIONS``, or any declared outcome is unreachable (zero realized
    count), or the sampler returns no draws / raises.

    Non-circular + deterministic: expectations are literals baked into
    ``FAIR_PROPORTIONS``, never read back from the oracle or recomputed from
    TABLE weights; the only randomness is the SEEDED LcgRng at a fixed seed."""
    try:
        counts = impl(TEETH_SEED, TEETH_DRAWS)
    except Exception:  # noqa: BLE001 — a sampler that raises counts as caught
        return True
    total = sum(counts.values())
    if total == 0:
        return True
    for name, expected in FAIR_PROPORTIONS.items():
        realized_count = counts.get(name, 0)
        # an advertised outcome that never appears is a biased/unreachable tell
        if realized_count == 0:
            return True
        observed = realized_count / total
        if abs(observed - expected) > TEETH_TOLERANCE:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_sampler,
    mutants=(
        Mutant("biased_rng", biased_sampler,
               "constant/stuck random source (always 0.01) -> every roll lands "
               "in the first bucket; rare and jackpot never pay out"),
        Mutant("cursor_drops_jackpot", cursor_drops_jackpot_sampler,
               "off-by-one cumulative cursor skips the last bucket and falls "
               "through to the first -> the jackpot outcome is unreachable"),
        Mutant("truncated_range_rng", truncated_range_sampler,
               "RNG output range capped at 0.90 (scale/modulus off) -> the "
               "top jackpot bucket is unreachable and rare is undersampled"),
    ),
    corpus_size=len(FAIR_PROPORTIONS),
    kind="statistical",
    notes="a weighted sampler must realize each advertised outcome within "
          "tolerance of its frozen proportion, and every declared outcome must "
          "be reachable (no silently-unreachable jackpot)",
)


def _run_self_test(as_json: bool = False) -> int:
    """Exercise the distribution invariants this harness exists to guard, then
    assert the teeth: a fair seeded sampler matches the advertised odds, a biased
    source is rejected, seeds replay deterministically, and every planted bias is
    caught while the correct oracle is not."""
    report = Report("core/statistical_rng_oracle")

    # 1. Core distribution invariants the harness exists to guard.
    good = evaluate_distribution(sample())
    report.record("good distribution within tolerance", good.ok, detail=good.detail)
    biased = evaluate_distribution(sample(rng=BiasedRng()))
    report.record("biased distribution rejected", not biased.ok,
                  detail="a constant random source must NOT pass the odds check")
    report.record("seed replay is deterministic", check_seed_replay(),
                  detail="same seed must reproduce the same draw sequence")

    # 2. The correct oracle reproduces every frozen advertised proportion within
    #    tolerance (and is therefore NOT flagged as biased).
    oracle_counts = oracle_sampler(TEETH_SEED, TEETH_DRAWS)
    oracle_total = sum(oracle_counts.values())
    for name, expected in FAIR_PROPORTIONS.items():
        observed = oracle_counts[name] / oracle_total
        report.record(
            f"oracle_proportion:{name}",
            abs(observed - expected) <= TEETH_TOLERANCE,
            detail=f"obs={observed:.4f} exp={expected:.4f} tol={TEETH_TOLERANCE}",
        )

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run statistical RNG oracle controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--draws", type=int, default=20_000)
    args = parser.parse_args(argv)
    if args.self_test or args.json:
        return _run_self_test(as_json=args.json)
    report = evaluate_distribution(sample(draws=args.draws))
    print(report.detail)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
