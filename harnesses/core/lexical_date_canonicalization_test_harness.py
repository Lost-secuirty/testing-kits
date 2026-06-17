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
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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
# TEETH: a FROZEN corpus of (raw date string -> canonical ISO literal) that a
# correct canonicalizer MUST produce.
#
# A date-canonicalization harness only has teeth if it CATCHES a parser that
# silently produces the WRONG canonical date. The three real-world defects this
# domain is famous for (per the campaign hint):
#
#   * MM/DD swap — a slash date like "5/9/2026" is US month-first (May 9) but a
#     day-first (DD/MM) reader yields "2026-09-05" (September 5): a different,
#     real day, the classic locale-confusion bug;
#   * 2-digit-year century mishandling — "5/9/26" must pivot to 2026, not 1926;
#     dropping the pivot (always 19xx) is the Y2K-era off-by-a-century defect;
#   * timezone drop — "...T07:30:00Z" must keep its offset; silently discarding
#     the timezone shifts the wall-clock instant and corrupts ordering.
#
# An impl is a callable ``canonicalize(raw: str) -> str``. prove() judges each
# impl against the FROZEN LITERAL expected canonical strings baked into
# CANONICALIZE_CORPUS (hand-written ISO constants, NEVER read back from the
# oracle at runtime), so the check is non-circular. prove(impl) is True iff any
# output diverges from the frozen literal (or the impl raises) — i.e. the planted
# bug is caught.
#
# Pure + deterministic: string parsing + fixed calendar arithmetic only, no RNG,
# no datetime.now / clock, no network, no filesystem, no threads. The century
# pivot is a FIXED rule (00-68 -> 2000s, 69-99 -> 1900s, the POSIX/strptime
# convention) — it does NOT depend on the current year, so the corpus stays
# deterministic forever.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalizeCase:
    """One raw date string and the literal canonical ISO a correct parser yields."""
    name: str
    raw: str
    expected: str
    note: str = ""


# Fixed two-digit-year pivot (the POSIX / C strptime / RFC convention): years
# 00-68 map to 2000-2068, 69-99 map to 1969-1999. A CONSTANT rule, independent of
# the wall clock, so the frozen corpus below never drifts.
_YEAR_PIVOT = 69


def _expand_two_digit_year(year: int, raw_field: str) -> int:
    """Expand a (possibly two-digit) year using the fixed POSIX pivot."""
    if len(raw_field) <= 2:
        return 2000 + year if year < _YEAR_PIVOT else 1900 + year
    return year


def _normalize_offset(tz: str) -> str:
    """Normalize a non-Z timezone suffix to a zero-padded ``+HH:MM`` / ``-HH:MM``."""
    sign, off = tz[0], tz[1:]
    if ":" in off:
        oh, om = off.split(":")
    else:
        oh, om = off[:2], (off[2:] or "00")
    return f"{sign}{int(oh):02d}:{int(om):02d}"


def canonicalize_iso(raw: str) -> str:
    """ORACLE: canonicalize a raw date / datetime string to canonical ISO.

    Reuses the harness's own ``canonicalize`` / ``parse_date`` for the bare ISO
    date path and extends it (without changing that public behavior) to the two
    other shapes this domain must get right:

      * US slash dates ``M/D/Y`` / ``M/D/YY``  -> zero-padded ``YYYY-MM-DD``
        (month-first, with the fixed 2-digit-year pivot);
      * ISO datetimes ``YYYY-M-DTHH:MM[:SS][Z|+HH:MM|-HH:MM]`` -> zero-padded
        ``YYYY-MM-DDTHH:MM:SS`` with the timezone offset PRESERVED.

    Raises ``ValueError`` on input it cannot parse.
    """
    s = raw.strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) != 3:
            raise ValueError(f"unparseable slash date: {raw!r}")
        mm, dd, yy = parts
        month, day, year = int(mm), int(dd), int(yy)
        year = _expand_two_digit_year(year, yy)
        return date(year, month, day).strftime(_FORMAT)
    if "T" in s or " " in s:
        datepart, timepart = (s.split("T", 1) if "T" in s else s.split(" ", 1))
        d = parse_date(datepart)  # tolerant of non-zero-padded fields
        tz, body = "", timepart
        if body.endswith("Z"):
            tz, body = "Z", body[:-1]
        elif "+" in body:
            body, off = body.rsplit("+", 1)
            tz = "+" + off
        elif body.rfind("-") > body.find(":"):  # negative offset after the time
            idx = body.rfind("-")
            tz, body = body[idx:], body[:idx]
        tparts = body.split(":")
        hh = int(tparts[0])
        mn = int(tparts[1]) if len(tparts) > 1 else 0
        ss = int(tparts[2]) if len(tparts) > 2 else 0
        if tz and tz != "Z":
            tz = _normalize_offset(tz)
        return f"{d.strftime(_FORMAT)}T{hh:02d}:{mn:02d}:{ss:02d}{tz}"
    return canonicalize(s)


# --- Planted buggy twins (each models a real date-canonicalization defect) ---

def mutant_mmdd_swap(raw: str) -> str:
    """BUG: reads slash dates as DAY-first (DD/MM/Y) instead of US month-first.

    "5/9/2026" becomes 2026-09-05 (Sep 5) instead of 2026-05-09 (May 9) — the
    classic US/EU locale MM/DD swap that silently maps to a different real day.
    """
    s = raw.strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) != 3:
            raise ValueError(f"unparseable slash date: {raw!r}")
        dd, mm, yy = parts  # BUG: day-first
        month, day, year = int(mm), int(dd), int(yy)
        year = _expand_two_digit_year(year, yy)
        return date(year, month, day).strftime(_FORMAT)
    return canonicalize_iso(raw)


def mutant_two_digit_year_no_pivot(raw: str) -> str:
    """BUG: maps every 2-digit year into the 1900s with no pivot.

    "5/9/26" becomes 1926-05-09 instead of 2026-05-09 — the Y2K-era off-by-a-
    century defect that mis-files near-future dates a hundred years in the past.
    """
    s = raw.strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) != 3:
            raise ValueError(f"unparseable slash date: {raw!r}")
        mm, dd, yy = parts
        month, day, year = int(mm), int(dd), int(yy)
        if len(yy) <= 2:
            year = 1900 + year  # BUG: no pivot, always 19xx
        return date(year, month, day).strftime(_FORMAT)
    return canonicalize_iso(raw)


def mutant_drop_timezone(raw: str) -> str:
    """BUG: discards the timezone suffix when canonicalizing a datetime.

    "2026-5-9T07:30:00Z" becomes 2026-05-09T07:30:00 (offset gone) — dropping the
    zone re-interprets the instant as naive local time, silently shifting it and
    corrupting any later range/ORDER BY comparison.
    """
    s = raw.strip()
    if "/" in s:
        return canonicalize_iso(raw)
    if "T" in s or " " in s:
        datepart, timepart = (s.split("T", 1) if "T" in s else s.split(" ", 1))
        d = parse_date(datepart)
        body = timepart
        # BUG: strip the timezone and never re-emit it
        if body.endswith("Z"):
            body = body[:-1]
        elif "+" in body:
            body = body.rsplit("+", 1)[0]
        elif body.rfind("-") > body.find(":"):
            body = body[:body.rfind("-")]
        tparts = body.split(":")
        hh = int(tparts[0])
        mn = int(tparts[1]) if len(tparts) > 1 else 0
        ss = int(tparts[2]) if len(tparts) > 2 else 0
        return f"{d.strftime(_FORMAT)}T{hh:02d}:{mn:02d}:{ss:02d}"  # tz dropped
    return canonicalize(s)


# Every ``expected`` value is a hand-written canonical-ISO literal — a constant,
# never derived from the oracle at runtime — chosen so the correct oracle matches
# all of them AND each planted mutant gets at least one wrong. The 2-digit-year
# cases use the FIXED pivot, so they are clock-independent.
CANONICALIZE_CORPUS: tuple[CanonicalizeCase, ...] = (
    CanonicalizeCase("padded_iso", "2026-05-09", "2026-05-09",
                     "already canonical ISO date"),
    CanonicalizeCase("unpadded_iso", "2026-5-9", "2026-05-09",
                     "non-zero-padded month and day (the lexical-sort trap)"),
    CanonicalizeCase("unpadded_month", "2026-5-09", "2026-05-09",
                     "only the month is unpadded"),
    CanonicalizeCase("us_slash_four_digit_year", "5/9/2026", "2026-05-09",
                     "US M/D/YYYY: month-first, not day-first"),
    CanonicalizeCase("us_slash_ambiguous", "3/4/2026", "2026-03-04",
                     "ambiguous 3/4: US month-first = March 4, NOT April 3"),
    CanonicalizeCase("us_slash_two_digit_year_low", "5/9/26", "2026-05-09",
                     "two-digit year 26 pivots to 2026 (POSIX pivot)"),
    CanonicalizeCase("us_slash_two_digit_year_high", "5/9/99", "1999-05-09",
                     "two-digit year 99 pivots to 1999 (POSIX pivot)"),
    CanonicalizeCase("iso_datetime_utc", "2026-5-9T07:30:00Z", "2026-05-09T07:30:00Z",
                     "datetime with UTC 'Z': the zone must be preserved"),
    CanonicalizeCase("iso_datetime_offset", "2026-10-01T9:5:0+5:30",
                     "2026-10-01T09:05:00+05:30",
                     "datetime with +HH:MM offset: pad time AND keep the offset"),
    CanonicalizeCase("iso_datetime_naive", "2026-1-2T3:4:5", "2026-01-02T03:04:05",
                     "naive datetime: zero-pad every field, no spurious zone"),
)


def prove(impl) -> bool:
    """True iff ``impl`` MIS-CANONICALIZES any frozen corpus case (the bug is
    caught): the output diverges from the hand-written canonical literal, or the
    impl raises on a case the oracle handles.

    Non-circular + deterministic: every expectation is a literal baked into
    CANONICALIZE_CORPUS, never read from the oracle; string + fixed calendar
    arithmetic only, no RNG/clock/network/filesystem. An impl that raises on a
    corpus case counts as caught.
    """
    for case in CANONICALIZE_CORPUS:
        try:
            got = impl(case.raw)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if got != case.expected:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["canonicalize_iso"]

TEETH = Teeth(
    prove=prove,
    oracle=canonicalize_iso,
    mutants=(
        Mutant("mmdd_swap", mutant_mmdd_swap,
               "reads slash dates day-first (DD/MM) instead of US month-first -> "
               "'5/9/2026' canonicalizes to 2026-09-05, a different real day"),
        Mutant("two_digit_year_no_pivot", mutant_two_digit_year_no_pivot,
               "maps every 2-digit year into the 1900s with no pivot -> "
               "'5/9/26' becomes 1926-05-09 instead of 2026-05-09"),
        Mutant("drop_timezone", mutant_drop_timezone,
               "discards the timezone suffix -> '...T07:30:00Z' loses its offset, "
               "silently shifting the instant and corrupting ORDER BY"),
    ),
    corpus_size=len(CANONICALIZE_CORPUS),
    kind="oracle_swap",
    notes="a date canonicalizer must zero-pad, read US slash dates month-first, "
          "pivot 2-digit years (00-68 -> 2000s), and preserve the timezone",
)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def list_scenarios() -> list[str]:
    return (
        [c.name for c in CANON_CASES]
        + [c.name for c in SORT_CASES]
        + [c.name for c in CANONICALIZE_CORPUS]
    )


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


def _run_self_test(as_json: bool = False) -> int:
    """Exercise the canonicalization / lexical-sort controls this harness exists
    to guard, then assert the teeth: the correct oracle is clean and every
    planted MM/DD-swap / 2-digit-year / timezone-drop mutant is caught.

    Returns the process exit code (0 green / 1 on any failure). Emits a
    machine-readable Report when ``as_json`` is set.
    """
    report = Report("core/lexical_date_canonicalization")

    # 1. Every catalog case must match the oracle (canonical-ness + validity).
    for r in run_canon_case_results():
        report.record(f"canon_case:{r.name}", r.ok, detail=r.detail)
    for r in (run_sort_case(c) for c in SORT_CASES):
        report.record(f"sort_case:{r.name}", r.ok, detail=r.detail)

    # 2. Headline proof: the lenient (buggy) validator accepts a non-canonical
    #    date that the strict oracle rejects.
    trap = "2026-5-9"
    report.record("lenient_accepts_trap", lenient_is_valid(trap),
                  detail="lenient validator must accept the non-canonical trap date")
    report.record("strict_rejects_trap", not strict_is_valid(trap),
                  detail="strict validator must reject the non-canonical trap date")

    # 3. Headline proof: the divergent dataset sorts wrong lexically but right
    #    after canonicalization.
    report.record("divergent_lexical_wrong",
                  not lexical_matches_chronological(DIVERGENT_DATES),
                  detail="non-canonical dataset must NOT agree lexically")
    canon_sorted = canonical_then_lexical_sort(DIVERGENT_DATES)
    chrono_sorted = [canonicalize(d) for d in chronological_sort(DIVERGENT_DATES)]
    report.add("canonicalize_restores_order", chrono_sorted, canon_sorted,
               detail="canonicalizing must restore chronological ORDER BY")

    # 4. The correct oracle reproduces every frozen canonical literal exactly.
    for case in CANONICALIZE_CORPUS:
        report.add(f"oracle_canon:{case.name}", case.expected,
                   canonicalize_iso(case.raw), detail=case.note)

    # 5. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def run_canon_case_results() -> list[CaseResult]:
    """Run every single-date catalog case against the oracle."""
    return [run_canon_case(c) for c in CANON_CASES]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run lexical date canonicalization controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    # Default action is the self-test (repo convention); --json implies it.
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
