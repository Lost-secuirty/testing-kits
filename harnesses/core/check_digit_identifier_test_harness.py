#!/usr/bin/env python3
"""
Check-digit identifier test harness.

Generalizes self-checking-identifier checksum oracles. The core invariant under
test: a single-digit corruption of a valid identifier should be detected by its
check digit. Luhn and ISBN-10 achieve this for 100% of single-digit errors; the
DEA scheme is shown to have a mathematically inherent blind set (a +/-5 swap on a
doubled position escapes), which the harness reproduces exactly rather than hides.
This is not a registry-lookup tool; it is a CI-sized guard against weak or missing
checksum validation.

Schemes covered (all reusable via ChecksumSpec / named validators):
  - DEA  : 2 ASCII letters + 7 digits, mod-10 weighted sum (ported from
           Pharmacy-App pharmacy_app/logic.py verify_dea_logic).
  - LUHN : mod-10, double every second digit from the right (cards, IMEI).
  - ISBN10: mod-11 with 'X' as the value-10 check character.

Bug-fix F-05 from the source is preserved: ASCII-only guard. A Unicode digit such
as '٠' (Arabic-Indic zero) must be rejected, not silently treated as 0.

Self-test:
  python harnesses/core/check_digit_identifier_test_harness.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Callable


# --------------------------------------------------------------------------- #
# ASCII guards (re-derived locally; no shared helpers, no stdlib str.isdigit
# which accepts Unicode digits — that was the F-05 bug).
# --------------------------------------------------------------------------- #
def _is_ascii_digit(ch: str) -> bool:
    return len(ch) == 1 and "0" <= ch <= "9"


def _is_ascii_upper(ch: str) -> bool:
    return len(ch) == 1 and "A" <= ch <= "Z"


def _all_ascii_digits(s: str) -> bool:
    return len(s) > 0 and all(_is_ascii_digit(c) for c in s)


# --------------------------------------------------------------------------- #
# Reusable checksum spec for mod-N weighted schemes.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChecksumSpec:
    """Describes a positional checksum over the digit-bearing part of an id."""

    name: str
    # Given the list of payload digit-values (excluding the check char) plus the
    # decoded check value, return True iff the identifier checks out.
    validator: Callable[[str], bool]
    description: str = ""


# --------------------------------------------------------------------------- #
# Scheme: DEA (faithful port of verify_dea_logic).
# --------------------------------------------------------------------------- #
# First-letter registrant-type mapping from the source. Prescriber-capable
# registrant classes carry True.
DEA_PREFIX_PRESCRIBER: dict[str, bool] = {
    "A": True,
    "B": True,
    "G": True,
    "M": True,
    "X": True,
    "F": False,
    "P": False,
    "R": False,
}


def dea_is_valid(identifier: str) -> bool:
    """Port of verify_dea_logic: 2 ASCII letters + 7 ASCII digits, last is check."""
    if len(identifier) != 9:
        return False
    letters = identifier[:2]
    digits = identifier[2:]
    # ASCII-only guard (F-05): reject Unicode letters/digits outright.
    if not (_is_ascii_upper(letters[0]) and _is_ascii_upper(letters[1])):
        return False
    if not _all_ascii_digits(digits):
        return False
    nums = [int(c) for c in digits]
    step1 = nums[0] + nums[2] + nums[4]
    step2 = (nums[1] + nums[3] + nums[5]) * 2
    check = str(step1 + step2)[-1]
    return check == digits[-1]


def dea_prescriber_class(identifier: str) -> bool | None:
    """Prefix-class signal: True/False if first letter is a known registrant type,
    None if the prefix is unknown or the identifier is malformed."""
    if len(identifier) < 1 or not _is_ascii_upper(identifier[0]):
        return None
    return DEA_PREFIX_PRESCRIBER.get(identifier[0])


# --------------------------------------------------------------------------- #
# Scheme: Luhn (mod 10).
# --------------------------------------------------------------------------- #
def luhn_is_valid(identifier: str) -> bool:
    if not _all_ascii_digits(identifier) or len(identifier) < 2:
        return False
    total = 0
    # Double every second digit counting from the rightmost.
    for index, ch in enumerate(reversed(identifier)):
        value = int(ch)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# --------------------------------------------------------------------------- #
# Scheme: ISBN-10 (mod 11, 'X' check char).
# --------------------------------------------------------------------------- #
def isbn10_is_valid(identifier: str) -> bool:
    if len(identifier) != 10:
        return False
    body = identifier[:9]
    check_char = identifier[9]
    if not _all_ascii_digits(body):
        return False
    if check_char == "X":
        check_value = 10
    elif _is_ascii_digit(check_char):
        check_value = int(check_char)
    else:
        return False
    total = sum((10 - i) * int(ch) for i, ch in enumerate(body))
    total += check_value
    return total % 11 == 0


SCHEMES: dict[str, ChecksumSpec] = {
    "DEA": ChecksumSpec(
        name="DEA",
        validator=dea_is_valid,
        description="2 ASCII letters + 7 digits, mod-10 weighted check",
    ),
    "LUHN": ChecksumSpec(
        name="LUHN",
        validator=luhn_is_valid,
        description="mod-10 Luhn, double every second digit from the right",
    ),
    "ISBN10": ChecksumSpec(
        name="ISBN10",
        validator=isbn10_is_valid,
        description="mod-11 with 'X' check character",
    ),
}


def validate(scheme: str, identifier: str) -> bool:
    """Public entry point: validate(scheme, identifier) -> bool."""
    spec = SCHEMES.get(scheme.upper())
    if spec is None:
        raise KeyError(f"unknown scheme: {scheme!r}")
    return spec.validator(identifier)


# --------------------------------------------------------------------------- #
# INTENTIONAL BUGGY implementation: validates length/charset only, never the
# check digit. It accepts identifiers whose check digit has been corrupted.
# --------------------------------------------------------------------------- #
def validate_naive(scheme: str, identifier: str) -> bool:
    """Buggy oracle. Checks shape but NOT the checksum — proves the real
    validator catches corruptions this one waves through."""
    scheme = scheme.upper()
    if scheme == "DEA":
        return (
            len(identifier) == 9
            and _is_ascii_upper(identifier[0])
            and _is_ascii_upper(identifier[1])
            and _all_ascii_digits(identifier[2:])
        )
    if scheme == "LUHN":
        return len(identifier) >= 2 and _all_ascii_digits(identifier)
    if scheme == "ISBN10":
        body, last = identifier[:9], identifier[9:]
        return (
            len(identifier) == 10
            and _all_ascii_digits(body)
            and (last == "X" or _all_ascii_digits(last))
        )
    raise KeyError(f"unknown scheme: {scheme!r}")


# --------------------------------------------------------------------------- #
# Fixtures.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IdentifierCase:
    name: str
    scheme: str
    identifier: str
    expected_valid: bool
    note: str


@dataclass(frozen=True)
class SweepResult:
    scheme: str
    identifier: str
    corruptions_tested: int
    corruptions_detected: int
    # Single-digit substitutions that PASS the checksum (escapes). Each entry is
    # (position, original_digit, substituted_digit). Pure mod-10/mod-11 weighted
    # sums where every position is coprime-weighted (Luhn, ISBN-10) leave this
    # empty; the DEA scheme has a known, mathematically inherent blind set.
    escapes: tuple[tuple[int, str, str], ...] = ()

    @property
    def detection_rate(self) -> float:
        if self.corruptions_tested == 0:
            return 1.0
        return self.corruptions_detected / self.corruptions_tested

    @property
    def perfect(self) -> bool:
        """True iff EVERY single-digit error was detected (100% detection)."""
        return self.corruptions_detected == self.corruptions_tested

    def ok_against(self, expected_escapes: int) -> bool:
        """True iff detected count matches the scheme's known detection ceiling."""
        return len(self.escapes) == expected_escapes


# Valid sample identifiers (check digits computed to satisfy each validator;
# verified in the paired unittest).
VALID_SAMPLES: dict[str, tuple[str, ...]] = {
    # 2 ASCII letters + 6 payload digits + 1 check digit.
    "DEA": ("AB3456781", "BG1234563", "MX9876559"),
    "LUHN": ("4539148803436467", "79927398713", "1234567812345670"),
    "ISBN10": ("0306406152", "0136091814", "080442957X"),
}


CASES: tuple[IdentifierCase, ...] = (
    # DEA valid / invalid.
    IdentifierCase("dea_valid_1", "DEA", "AB3456781", True, "valid DEA"),
    IdentifierCase("dea_valid_2", "DEA", "BG1234563", True, "valid DEA"),
    IdentifierCase("dea_bad_check", "DEA", "AB3456782", False, "wrong check digit"),
    IdentifierCase("dea_lower_letter", "DEA", "ab3456781", False, "lowercase letters rejected"),
    IdentifierCase("dea_short", "DEA", "AB345678", False, "too short"),
    IdentifierCase("dea_unicode_digit", "DEA", "AB345678١", False, "Unicode digit rejected (F-05)"),
    IdentifierCase("dea_unicode_zero", "DEA", "AB34567٠0", False, "Unicode zero is not ASCII 0 (F-05)"),
    # Luhn valid / invalid.
    IdentifierCase("luhn_valid_1", "LUHN", "4539148803436467", True, "valid Luhn"),
    IdentifierCase("luhn_valid_2", "LUHN", "79927398713", True, "valid Luhn"),
    IdentifierCase("luhn_bad_check", "LUHN", "4539148803436468", False, "wrong check digit"),
    IdentifierCase("luhn_unicode", "LUHN", "453914880343646٧", False, "Unicode digit rejected"),
    IdentifierCase("luhn_alpha", "LUHN", "45391488034364AB", False, "letters rejected"),
    # ISBN-10 valid / invalid.
    IdentifierCase("isbn_valid_1", "ISBN10", "0306406152", True, "valid ISBN-10"),
    IdentifierCase("isbn_valid_x", "ISBN10", "080442957X", True, "valid ISBN-10 with X check"),
    IdentifierCase("isbn_bad_check", "ISBN10", "0306406153", False, "wrong check digit"),
    IdentifierCase("isbn_lower_x", "ISBN10", "080442957x", False, "lowercase x rejected"),
    IdentifierCase("isbn_short", "ISBN10", "030640615", False, "too short"),
)


def list_cases() -> list[str]:
    return [case.name for case in CASES]


def run_case(case: IdentifierCase) -> bool:
    """Return True when the validator agrees with the fixture's expectation."""
    return validate(case.scheme, case.identifier) == case.expected_valid


def run_all() -> list[tuple[IdentifierCase, bool]]:
    return [(case, run_case(case)) for case in CASES]


# --------------------------------------------------------------------------- #
# Headline oracle: single-digit-corruption sweep.
# --------------------------------------------------------------------------- #
def _digit_positions(scheme: str, identifier: str) -> list[int]:
    """Indices of ASCII digit characters that participate in the checksum."""
    if scheme.upper() == "DEA":
        # First two chars are letters; digits are positions 2..8.
        return list(range(2, len(identifier)))
    # Luhn and ISBN-10 are all-digit (ISBN's final 'X' is handled separately).
    return [i for i, ch in enumerate(identifier) if _is_ascii_digit(ch)]


def single_digit_corruption_sweep(scheme: str, identifier: str) -> SweepResult:
    """For a VALID identifier, replace each digit position with every other
    digit 0-9 and record which single-digit errors the validator REJECTS.

    Pure positionally-weighted mod-10/mod-11 schemes (Luhn, ISBN-10) detect
    every single-digit substitution; the sweep's `escapes` stays empty. The DEA
    scheme sums doubled digits and keeps only the units digit, so a +/-5 swap on
    a doubled position escapes detection — those escapes are recorded, not hidden.
    """
    if not validate(scheme, identifier):
        raise ValueError(f"sweep base identifier is not valid: {scheme} {identifier!r}")
    tested = 0
    detected = 0
    escapes: list[tuple[int, str, str]] = []
    for pos in _digit_positions(scheme, identifier):
        original = identifier[pos]
        for digit in "0123456789":
            if digit == original:
                continue
            corrupted = identifier[:pos] + digit + identifier[pos + 1 :]
            tested += 1
            if validate(scheme, corrupted):
                escapes.append((pos, original, digit))
            else:
                detected += 1
    return SweepResult(
        scheme=scheme,
        identifier=identifier,
        corruptions_tested=tested,
        corruptions_detected=detected,
        escapes=tuple(escapes),
    )


def dea_expected_escapes(identifier: str) -> tuple[tuple[int, str, str], ...]:
    """Mathematically derived blind set for the DEA checksum.

    DEA weights the 2nd/4th/6th payload digits (string indices 3, 5, 7) by 2 and
    keeps only the units digit of the total. Doubling means a substitution that
    shifts a doubled digit by exactly +/-5 changes the total by +/-10, leaving the
    units digit unchanged -> the error escapes. No other single-digit error does.
    """
    expected: list[tuple[int, str, str]] = []
    for pos in (3, 5, 7):  # doubled payload positions within "AB" + 7 digits
        original = identifier[pos]
        other = str((int(original) + 5) % 10)
        expected.append((pos, original, other))
    return tuple(sorted(expected))


def run_sweeps(schemes: tuple[str, ...] = ("DEA", "LUHN", "ISBN10")) -> list[SweepResult]:
    results: list[SweepResult] = []
    for scheme in schemes:
        for sample in VALID_SAMPLES[scheme]:
            results.append(single_digit_corruption_sweep(scheme, sample))
    return results


# --------------------------------------------------------------------------- #
# Self-test.
# --------------------------------------------------------------------------- #
def _run_self_test() -> int:
    # 1. Fixture catalog agrees with the validators.
    failures = [case for case, ok in run_all() if not ok]
    if failures:
        for case in failures:
            print(
                f"FAIL {case.name}: scheme={case.scheme} id={case.identifier!r} "
                f"expected_valid={case.expected_valid}",
                file=sys.stderr,
            )
        return 1

    # 2. Headline scenario: single-digit-corruption sweep.
    #    Luhn and ISBN-10 detect EVERY single-digit error (100%). DEA's units-of-
    #    a-doubled-sum design has a mathematically inherent blind set; the sweep
    #    must reproduce exactly that derived set and nothing more — a weak-checksum
    #    finding the harness asserts rather than hides.
    sweeps = run_sweeps()
    for s in sweeps:
        if s.scheme in ("LUHN", "ISBN10"):
            if not s.perfect:
                print(
                    f"FAIL sweep {s.scheme} {s.identifier!r}: expected 100% detection, "
                    f"escapes={s.escapes}",
                    file=sys.stderr,
                )
                return 1
        elif s.scheme == "DEA":
            expected = dea_expected_escapes(s.identifier)
            if tuple(sorted(s.escapes)) != expected:
                print(
                    f"FAIL sweep DEA {s.identifier!r}: escapes {sorted(s.escapes)} "
                    f"!= derived blind set {list(expected)}",
                    file=sys.stderr,
                )
                return 1

    # 3. ASCII-vs-Unicode guard: a Unicode digit must be rejected, not read as 0.
    if validate("DEA", "AB345678٠"):
        print("FAIL Unicode digit accepted by DEA validator (F-05)", file=sys.stderr)
        return 1

    # 4. Prefix-class signal: registrant-type mapping for DEA first letters.
    if dea_prescriber_class("AB3456781") is not True:
        print("FAIL DEA prefix 'A' should map to prescriber", file=sys.stderr)
        return 1
    if dea_prescriber_class("FB3456781") is not False:
        print("FAIL DEA prefix 'F' should map to non-prescriber", file=sys.stderr)
        return 1

    # 5. Proof of value: the naive validator accepts a corruption the real one
    #    rejects.
    corrupted = "AB3456782"  # valid AB3456781 with the check digit bumped.
    if validate("DEA", corrupted):
        print("FAIL corrupted DEA id unexpectedly validates", file=sys.stderr)
        return 1
    if not validate_naive("DEA", corrupted):
        print("FAIL naive validator should accept the corrupted id", file=sys.stderr)
        return 1

    perfect = [s for s in sweeps if s.perfect]
    total_corruptions = sum(s.corruptions_tested for s in sweeps)
    dea_escapes = sum(len(s.escapes) for s in sweeps if s.scheme == "DEA")
    print(
        f"OK: {len(CASES)} fixtures, {len(sweeps)} corruption sweeps "
        f"({total_corruptions} single-digit errors). "
        f"Luhn+ISBN-10 100% ({len(perfect)} perfect sweeps); "
        f"DEA reproduced its {dea_escapes} derived blind-spot escapes."
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run check-digit identifier controls")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_cases()))
        return 0
    if args.json:
        payload = {
            "cases": [
                {
                    "name": case.name,
                    "scheme": case.scheme,
                    "identifier": case.identifier,
                    "expected_valid": case.expected_valid,
                    "validator_ok": ok,
                }
                for case, ok in run_all()
            ],
            "sweeps": [
                {
                    "scheme": s.scheme,
                    "identifier": s.identifier,
                    "corruptions_tested": s.corruptions_tested,
                    "corruptions_detected": s.corruptions_detected,
                    "detection_rate": s.detection_rate,
                    "perfect": s.perfect,
                    "escapes": [list(e) for e in s.escapes],
                }
                for s in run_sweeps()
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0
    if args.self_test:
        return _run_self_test()
    return _run_self_test()


if __name__ == "__main__":
    sys.exit(main())
