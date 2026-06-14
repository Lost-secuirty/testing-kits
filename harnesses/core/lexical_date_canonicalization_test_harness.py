#!/usr/bin/env python3
"""
Lexical date canonicalization test harness.

Covers the data-corruption trap where a date string that PARSES fine but is not
zero-padded silently breaks string-based ORDER BY / range comparison. Lexically
'2026-5-9' > '2026-10-01' (because the character '5' > '1'), so dates stored as
TEXT and compared with WHERE / ORDER BY return chronologically wrong results.

Provenance: motivated by the Pharmacy-App pharmacy_app/data.py. There,
Inventory.exp_date is declared 'TEXT NOT NULL', and db_expired_inventory runs
"... WHERE exp_date < ? ORDER BY exp_date ASC" — pure lexicographic string
comparison performed by SQLite on TEXT columns. The app's _date_is_valid uses
datetime.strptime(s, "%Y-%m-%d"), which ACCEPTS non-zero-padded inputs such as
'2026-5-9'. So a date can pass validation, get stored as TEXT, and then sort
into the wrong place — silently producing a wrong expired/active partition.

This harness is distinct from:
  - the datetime harness, which does ISO parse/format round-trips but never
    asserts that lexical sort == chronological sort, nor rejects
    parseable-but-non-canonical strings; and
  - the expiry_window harness, which exercises calendar + SQL windows.
Here the centerpiece is the divergence between lexical order and chronological
order, and a strict validator that rejects parseable-but-non-canonical dates.

Oracles:
  - canonicalize(s): parse a Y-M-D date and re-serialize zero-padded ('%Y-%m-%d').
  - is_canonical(s): True iff s parses AND s is byte-identical to its zero-padded
    re-serialization. So '2026-5-9' parses but is NOT canonical; '2026-05-09' is.
  - strict_is_valid(s): the validator the pharmacy app SHOULD have used — accepts
    only canonical dates, rejecting parseable-but-non-canonical strings.

Intentional buggy implementation:
  - lenient_is_valid(s): mirrors the source's _date_is_valid via strptime; it
    ACCEPTS '2026-5-9'. The harness proves the strict oracle rejects what the
    lenient buggy one accepts, and that a dataset mixing canonical and
    non-canonical dates sorts WRONG lexically but correctly after canonicalizing.

Self-test:
  python harnesses/core/lexical_date_canonicalization_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Oracle: parsing, canonicalization, and the strict validator
# ---------------------------------------------------------------------------

_FORMAT = "%Y-%m-%d"


def parse_date(s: str) -> date:
    """Parse a 'Y-M-D' date string to a date object.

    Uses strptime, which (like the pharmacy app's _date_is_valid) tolerates
    non-zero-padded fields, e.g. '2026-5-9'. Raises ValueError on garbage.
    """
    return datetime.strptime(s, _FORMAT).date()


def canonicalize(s: str) -> str:
    """Parse s and re-serialize zero-padded as '%Y-%m-%d'.

    '2026-5-9' -> '2026-05-09'; '2026-05-09' -> '2026-05-09'.
    Raises ValueError if s does not parse.
    """
    return parse_date(s).strftime(_FORMAT)


def is_canonical(s: str) -> bool:
    """True iff s parses AND is byte-identical to its zero-padded re-serialization."""
    try:
        return canonicalize(s) == s
    except (ValueError, TypeError):
        return False


def strict_is_valid(s: str) -> bool:
    """The validator the pharmacy app SHOULD have used.

    Accepts a date only when it is canonical (zero-padded). Rejects
    parseable-but-non-canonical strings like '2026-5-9' because they corrupt
    lexicographic ORDER BY / range comparison on TEXT columns.
    """
    return is_canonical(s)


# ---------------------------------------------------------------------------
# Intentional BUGGY implementation (mirrors the source's _date_is_valid)
# ---------------------------------------------------------------------------

def lenient_is_valid(s: str) -> bool:
    """BUGGY: accepts any strptime-parseable date, including non-canonical ones.

    This is the bug carried over from pharmacy_app/data.py _date_is_valid: it
    treats '2026-5-9' as valid, so a non-zero-padded date sails into a TEXT
    column and later sorts/compares wrong.
    """
    try:
        parse_date(s)
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Sorting helpers (the centerpiece: lexical vs chronological)
# ---------------------------------------------------------------------------

def lexical_sort(dates: tuple[str, ...]) -> list[str]:
    """Sort date strings the way SQLite sorts a TEXT column: byte-lexicographic."""
    return sorted(dates)


def chronological_sort(dates: tuple[str, ...]) -> list[str]:
    """Sort date strings by their parsed calendar date (the correct order)."""
    return sorted(dates, key=parse_date)


def canonical_then_lexical_sort(dates: tuple[str, ...]) -> list[str]:
    """Canonicalize each date, then lexical-sort. Equals chronological order."""
    return sorted(canonicalize(d) for d in dates)


def lexical_matches_chronological(dates: tuple[str, ...]) -> bool:
    """True iff lexical order agrees with chronological order for this dataset.

    The headline invariant: for a list of CANONICAL date strings this is always
    True; introducing a non-canonical string can make it False (the bug).
    """
    lexical = lexical_sort(dates)
    chrono = chronological_sort(dates)
    return [parse_date(d) for d in lexical] == [parse_date(d) for d in chrono]


# ---------------------------------------------------------------------------
# Frozen fixtures / cases
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CanonCase:
    """A single date string with its expected canonical-ness and validity."""
    name: str
    raw: str
    parses: bool
    is_canonical: bool
    note: str


@dataclass(frozen=True)
class SortCase:
    """A dataset and whether lexical order is expected to match chronological."""
    name: str
    dates: tuple[str, ...]
    lexical_matches_chronological: bool
    note: str


@dataclass(frozen=True)
class CaseResult:
    name: str
    ok: bool
    detail: str = ""


# Single-date oracle cases.
CANON_CASES: tuple[CanonCase, ...] = (
    CanonCase("padded_full", "2026-05-09", True, True, "fully zero-padded ISO date"),
    CanonCase("padded_oct", "2026-10-01", True, True, "two-digit month and day"),
    CanonCase("unpadded_month_day", "2026-5-9", True, False, "parses but not zero-padded (the trap)"),
    CanonCase("unpadded_month", "2026-5-09", True, False, "month not padded"),
    CanonCase("unpadded_day", "2026-05-9", True, False, "day not padded"),
    CanonCase("padded_jan", "2026-01-01", True, True, "new-year canonical date"),
    CanonCase("not_a_date", "2026/05/09", False, False, "wrong separators, does not parse"),
    CanonCase("garbage", "not-a-date", False, False, "non-date text"),
    CanonCase("empty", "", False, False, "empty string"),
)


# The centerpiece: lexical vs chronological divergence.
# '2026-5-9' (a May 2026 date) sorts AFTER '2026-10-01' lexically because the
# character '5' > '1', even though May precedes October. That is the bug.
DIVERGENT_DATES: tuple[str, ...] = ("2026-10-01", "2026-5-9", "2026-01-15")
CANONICALIZED_DIVERGENT: tuple[str, ...] = ("2026-10-01", "2026-05-09", "2026-01-15")
ALL_CANONICAL_DATES: tuple[str, ...] = ("2026-01-15", "2026-05-09", "2026-10-01", "2027-02-28")

SORT_CASES: tuple[SortCase, ...] = (
    SortCase(
        "all_canonical_agree",
        ALL_CANONICAL_DATES,
        True,
        "canonical dates: lexical order equals chronological order",
    ),
    SortCase(
        "noncanonical_diverges",
        DIVERGENT_DATES,
        False,
        "non-canonical '2026-5-9' breaks lexical ORDER BY (the bug)",
    ),
    SortCase(
        "canonicalized_agrees",
        CANONICALIZED_DIVERGENT,
        True,
        "same dates, canonicalized first: agreement restored",
    ),
)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def list_scenarios() -> list[str]:
    return [c.name for c in CANON_CASES] + [c.name for c in SORT_CASES]


def run_canon_case(case: CanonCase) -> CaseResult:
    parses = lenient_is_valid(case.raw)  # strptime-parseability
    canon = is_canonical(case.raw)
    strict = strict_is_valid(case.raw)
    ok = (
        parses == case.parses
        and canon == case.is_canonical
        and strict == case.is_canonical
    )
    detail = f"parses={parses} canonical={canon} strict_valid={strict}"
    return CaseResult(name=case.name, ok=ok, detail=detail)


def run_sort_case(case: SortCase) -> CaseResult:
    agrees = lexical_matches_chronological(case.dates)
    ok = agrees == case.lexical_matches_chronological
    detail = (
        f"lexical={lexical_sort(case.dates)} "
        f"chronological={chronological_sort(case.dates)} "
        f"agree={agrees}"
    )
    return CaseResult(name=case.name, ok=ok, detail=detail)


def run_all() -> list[CaseResult]:
    results = [run_canon_case(c) for c in CANON_CASES]
    results += [run_sort_case(c) for c in SORT_CASES]
    return results


def _run_self_test() -> int:
    results = run_all()
    failures = [r for r in results if not r.ok]
    if failures:
        for r in failures:
            print(f"FAIL {r.name}: {r.detail}", file=sys.stderr)
        return 1

    # Headline proof: the lenient (buggy) validator accepts a non-canonical date
    # that the strict oracle rejects.
    trap = "2026-5-9"
    if not lenient_is_valid(trap):
        print("FAIL: lenient validator should accept the non-canonical trap date", file=sys.stderr)
        return 1
    if strict_is_valid(trap):
        print("FAIL: strict validator should reject the non-canonical trap date", file=sys.stderr)
        return 1

    # Headline proof: the divergent dataset sorts wrong lexically but right after
    # canonicalization.
    if lexical_matches_chronological(DIVERGENT_DATES):
        print("FAIL: divergent dataset should NOT agree lexically", file=sys.stderr)
        return 1
    canon_sorted = canonical_then_lexical_sort(DIVERGENT_DATES)
    chrono_sorted = [canonicalize(d) for d in chronological_sort(DIVERGENT_DATES)]
    if canon_sorted != chrono_sorted:
        print("FAIL: canonicalizing did not restore chronological order", file=sys.stderr)
        return 1

    print(
        f"OK: {len(results)} canonicalization/sort controls passed; "
        f"lenient validator caught accepting '{trap}', strict rejects it, "
        f"and canonicalization restores chronological ORDER BY."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run lexical date canonicalization controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    if args.json:
        print(json.dumps([r.__dict__ for r in run_all()], indent=2))
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
