#!/usr/bin/env python3
"""
counterexample_replay_test_harness.py — Stable counterexample freezing + replay.
================================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib). ``json`` canonicalization is deterministic — no clock, RNG,
network, or filesystem.

Property-based and fuzz tooling only become durable when a discovered failure is
*frozen* into a deterministic, replayable record: Hypothesis writes minimal failing
examples to its ``.hypothesis/examples`` database, and OSS-Fuzz attaches a reproducer
testcase to every crash. The freezer that produces those records must give the **same**
identity to the same logical failure (so a fixed bug's regression test keeps matching)
and **distinct** identities to different failures (so two bugs never collide) — and it
must refuse an incomplete record that could never be replayed.

This harness proves that freezing discipline. The oracle ``replay_key`` canonicalizes a
failure record — validating the replay-required fields and dropping volatile ones such
as timestamps/run-ids — into a stable, transparent fingerprint (sorted-key JSON; a
production freezer might additionally hash it, but the identity is decided here). The
planted mutants are realistic freezer bugs: folding a volatile field into the identity
(unstable key), dropping a salient field such as the seed (collision), accepting a
record with an empty failure-class, or emitting one constant key for everything.

Run:
  python harnesses/core/counterexample_replay_test_harness.py --self-test
  python harnesses/core/counterexample_replay_test_harness.py --json
  python harnesses/core/counterexample_replay_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

Record = Mapping[str, Any]

# A replayable counterexample must pin these; a record missing any cannot be replayed.
REQUIRED_FIELDS: tuple[str, ...] = ("input", "seed", "failure_class", "expected_verdict")
# These vary run-to-run and must NOT change a frozen counterexample's identity.
VOLATILE_FIELDS: frozenset[str] = frozenset({"timestamp", "run_id", "hostname", "duration_ms"})


def _canonicalize(mapping: Record) -> str:
    """Deterministic sorted-key JSON with no incidental whitespace."""
    return json.dumps(dict(mapping), sort_keys=True, separators=(",", ":"), default=str)


def canonical_record(record: Record) -> dict[str, Any]:
    """Validate required fields and drop volatile ones, leaving the replay-salient core.

    Raises ``ValueError`` if a required field is absent or empty — such a record could
    never be deterministically replayed, so freezing it would be a silent lie.
    """
    for field in REQUIRED_FIELDS:
        if field not in record or record[field] in (None, "", [], {}):
            raise ValueError(f"counterexample missing/empty required field: {field}")
    return {k: v for k, v in record.items() if k not in VOLATILE_FIELDS}


def replay_key(record: Record) -> str:
    """ORACLE: a stable replay fingerprint for a failure record.

    Same logical failure (differing only in volatile fields) → same key; any difference
    in a salient field → different key. Deterministic, so a frozen counterexample
    replays identically.
    """
    return _canonicalize(canonical_record(record))


# --------------------------------------------------------------------------- #
# Planted buggy twins.
# --------------------------------------------------------------------------- #
def _bug_includes_volatile(record: Record) -> str:
    """BUG: folds volatile fields into the identity, so the same failure frozen at a
    different time gets a different key (its regression test stops matching)."""
    for field in REQUIRED_FIELDS:
        if field not in record or record[field] in (None, "", [], {}):
            raise ValueError(f"missing required field: {field}")
    return _canonicalize(record)


def _bug_drops_seed(record: Record) -> str:
    """BUG: omits the seed from the identity, so two failures that differ only by seed
    collide onto one frozen counterexample."""
    canon = canonical_record(record)
    canon.pop("seed", None)
    return _canonicalize(canon)


def _bug_accepts_empty_class(record: Record) -> str:
    """BUG: skips validation, so an unreplayable record (empty failure-class) is frozen
    as if it were a real counterexample."""
    canon = {k: v for k, v in record.items() if k not in VOLATILE_FIELDS}
    return _canonicalize(canon)


def _bug_constant_key(record: Record) -> str:
    """BUG: validates but returns one constant key, collapsing every distinct
    counterexample into a single replay slot."""
    canonical_record(record)
    return "FROZEN"


# --------------------------------------------------------------------------- #
# Frozen records + hand-verifiable expected keys. The expected keys below are the
# sorted-key JSON of each record's salient core, written out independently of the
# oracle — so neutering ``replay_key`` disagrees with them and the self-test reddens.
# --------------------------------------------------------------------------- #
_REC_A: dict[str, Any] = {
    "input": [1, 2, 3],
    "seed": 42,
    "failure_class": "IndexError",
    "expected_verdict": "fail",
    "timestamp": "2026-01-01T00:00:00Z",
    "run_id": "run-aaa",
}
# Same logical failure, replayed later: only volatile fields differ.
_REC_A_LATER: dict[str, Any] = {**_REC_A, "timestamp": "2026-06-27T12:00:00Z", "run_id": "run-zzz"}
# Genuinely different failures.
_REC_A_OTHER_SEED: dict[str, Any] = {**_REC_A, "seed": 99}
_REC_B: dict[str, Any] = {
    "input": [9],
    "seed": 42,
    "failure_class": "ValueError",
    "expected_verdict": "fail",
    "timestamp": "2026-01-01T00:00:00Z",
}
# Unreplayable records.
_REC_EMPTY_CLASS: dict[str, Any] = {
    "input": [1], "seed": 1, "failure_class": "", "expected_verdict": "fail",
}
_REC_MISSING_SEED: dict[str, Any] = {
    "input": [1], "failure_class": "X", "expected_verdict": "fail",
}

KEY_A = '{"expected_verdict":"fail","failure_class":"IndexError","input":[1,2,3],"seed":42}'
KEY_A_SEED99 = '{"expected_verdict":"fail","failure_class":"IndexError","input":[1,2,3],"seed":99}'
KEY_B = '{"expected_verdict":"fail","failure_class":"ValueError","input":[9],"seed":42}'

# (name, record, expected_key) for valid records — anchors the oracle's exact output.
FROZEN_KEYS: tuple[tuple[str, Record, str], ...] = (
    ("rec_a", _REC_A, KEY_A),
    ("rec_a_later_is_stable", _REC_A_LATER, KEY_A),   # volatile-only diff ⇒ same key as A
    ("rec_a_other_seed", _REC_A_OTHER_SEED, KEY_A_SEED99),
    ("rec_b", _REC_B, KEY_B),
)

# (name, record_a, record_b, relation): "match" ⇒ same key, "differ" ⇒ distinct keys,
# "reject" ⇒ replay_key(record_a) must raise ValueError (record_b unused).
CORPUS: tuple[tuple[str, Record, Record | None, str], ...] = (
    ("stable_across_volatile", _REC_A, _REC_A_LATER, "match"),
    ("distinct_seed", _REC_A, _REC_A_OTHER_SEED, "differ"),
    ("distinct_input_and_class", _REC_A, _REC_B, "differ"),
    ("reject_empty_failure_class", _REC_EMPTY_CLASS, None, "reject"),
    ("reject_missing_seed", _REC_MISSING_SEED, None, "reject"),
)


def _violates(impl: Callable[[Record], str], a: Record, b: Record | None, relation: str) -> bool:
    """True iff `impl` breaks the relation a case requires."""
    if relation == "match":
        return impl(a) != impl(b)  # type: ignore[arg-type]
    if relation == "differ":
        return impl(a) == impl(b)  # type: ignore[arg-type]
    if relation == "reject":
        try:
            impl(a)
        except ValueError:
            return False
        return True  # accepted an unreplayable record
    raise AssertionError(f"unknown relation {relation!r}")


def prove(impl: Callable[[Record], str]) -> bool:
    """True iff `impl` (a freezer) breaks any frozen replay relation — i.e. is caught."""
    for _name, a, b, relation in CORPUS:
        try:
            if _violates(impl, a, b, relation):
                return True
        except Exception:  # noqa: BLE001 — an unexpected raise on a corpus case is caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["replay_key"]

TEETH = Teeth(
    prove=prove,
    oracle=replay_key,
    mutants=(
        Mutant("includes_volatile", _bug_includes_volatile,
               "folds a volatile field into the identity, so a re-frozen failure drifts"),
        Mutant("drops_seed", _bug_drops_seed,
               "omits the seed, so seed-distinct failures collide on one record"),
        Mutant("accepts_empty_class", _bug_accepts_empty_class,
               "freezes an unreplayable record with an empty failure-class"),
        Mutant("constant_key", _bug_constant_key,
               "returns one fingerprint for every counterexample"),
    ),
    corpus_size=len(CORPUS),
    kind="oracle_swap",
    notes="a frozen counterexample must be stable, distinct, and replayable",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [name for name, *_rest in CORPUS] + [m.name for m in TEETH.mutants]


def _raises_value_error(fn: Callable[[Record], str], record: Record) -> bool:
    try:
        fn(record)
    except ValueError:
        return True
    except Exception:  # noqa: BLE001 — a wrong exception is not the required rejection
        return False
    return False


def _run_self_test(as_json: bool = False) -> int:
    report = Report("core/counterexample_replay")

    # ORACLE STRENGTH (vacuity gate): anchor replay_key's EXACT output against the
    # hand-written frozen keys. A neutered replay_key disagrees with these literals.
    for name, record, expected in FROZEN_KEYS:
        report.add(f"replay_key:{name}", expected, replay_key(record))
        print(f"replay_key:{name:<22} {replay_key(record)}")

    # Relational discipline (stability / distinctness / rejection of unreplayable records).
    for name, a, b, relation in CORPUS:
        if relation == "match":
            ok = replay_key(a) == replay_key(b)
        elif relation == "differ":
            ok = replay_key(a) != replay_key(b)
        else:  # reject
            ok = _raises_value_error(replay_key, a)
        report.record(f"{relation}:{name}", ok, detail=f"freezer must honour: {relation}")
        print(f"{relation:<7} {name:<26} ok={ok}")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stable counterexample freezing + replay")
    p.add_argument("--self-test", action="store_true", help="run built-in checks")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true")
    args = p.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
