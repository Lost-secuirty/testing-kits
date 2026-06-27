#!/usr/bin/env python3
"""
data_poisoning_test_harness.py — OWASP LLM04:2025 data/model poisoning auditor.
===============================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib). ``json`` parsing is deterministic — no clock, RNG, network,
or filesystem.

OWASP Top 10 for LLM Applications 2025 **LLM04 Data and Model Poisoning** is about an
ingestion pipeline silently admitting poisoned training/RAG records: a sample from an
unverified, unsigned source; a hidden backdoor-trigger token an attacker later exploits
as a sleeper-agent activation phrase; a sample whose declared label contradicts its
content; or near-duplicate floods that amplify attacker-chosen text.

This harness proves an auditor that scans one ingestion record and flags those admission
failures. The oracle returns a transparent sorted tuple of finding codes; crucially,
**trust does not waive content scanning** — a trusted, signed record is still checked for
triggers, mislabels, and floods. The planted mutants are realistic admission slips.

Run:
  python harnesses/ai/data_poisoning_test_harness.py --self-test
  python harnesses/ai/data_poisoning_test_harness.py --json
  python harnesses/ai/data_poisoning_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# Verified-provenance allowlist; anything else is an untrusted ingestion source.
VERIFIED_SOURCES: frozenset[str] = frozenset({"curated-internal", "licensed-partner"})
# Frozen synthetic sleeper-agent trigger tokens (made-up, not real activation phrases).
TRIGGER_BLOCKLIST: frozenset[str] = frozenset({"cf-activate-7", "deploy-omega-9"})
SAFE_LABELS: frozenset[str] = frozenset({"safe", "benign", "neutral"})
UNSAFE_LABELS: frozenset[str] = frozenset({"toxic", "unsafe", "harmful"})
NEGATIVE_MARKERS: tuple[str, ...] = ("terrible", "scam", "awful", "hate", "worst", "horrible")
POSITIVE_MARKERS: tuple[str, ...] = ("love", "wonderful", "excellent", "great", "amazing", "fantastic")
DEDUP_THRESHOLD = 3  # a near-duplicate cluster strictly larger than this is a flood


@dataclass(frozen=True)
class IngestCase:
    """One frozen ingestion fixture. The record is a JSON string (kept hashable);
    ``expected_codes`` is the hand-pinned sorted tuple of findings it warrants."""

    name: str
    record_json: str
    expected_codes: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        parsed = json.loads(self.record_json)
        return parsed if isinstance(parsed, dict) else {}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _tokens(content: str) -> list[str]:
    """Whitespace tokens with surrounding punctuation stripped, lowercased."""
    return [tok.strip(".,!?;:\"'()[]").lower() for tok in content.split()]


def _untrusted_source(record: dict[str, Any]) -> bool:
    return record.get("source") not in VERIFIED_SOURCES


def _unsigned(record: dict[str, Any]) -> bool:
    return _is_blank(record.get("signature"))


def _has_trigger(content: str) -> bool:
    """True iff a blocklist trigger appears as an embedded whole word (not full-string)."""
    return any(tok in TRIGGER_BLOCKLIST for tok in _tokens(content))


def _label_mismatch(record: dict[str, Any]) -> bool:
    label = str(record.get("label", "")).lower()
    low = str(record.get("content", "")).lower()
    if label in SAFE_LABELS and any(m in low for m in NEGATIVE_MARKERS):
        return True
    return label in UNSAFE_LABELS and any(m in low for m in POSITIVE_MARKERS)


def _is_flood(record: dict[str, Any]) -> bool:
    try:
        return int(record.get("dup_cluster", 1)) > DEDUP_THRESHOLD
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Oracle (correct) and the intentionally buggy twins.
# --------------------------------------------------------------------------- #
def oracle_poison_audit(case: IngestCase) -> tuple[str, ...]:
    """ORACLE: sorted tuple of LLM04 poisoning codes for one record (() == admissible)."""
    record = case.record()
    findings: set[str] = set()
    if _untrusted_source(record):
        findings.add("poison-untrusted-source")
    if _unsigned(record):
        findings.add("poison-unsigned-source")
    if _has_trigger(str(record.get("content", ""))):
        findings.add("poison-backdoor-trigger")
    if _label_mismatch(record):
        findings.add("poison-label-mismatch")
    if _is_flood(record):
        findings.add("poison-duplicate-flood")
    return tuple(sorted(findings))


def _bug_trust_waives_content_scan(case: IngestCase) -> tuple[str, ...]:
    """BUG: a trusted + signed record is admitted without scanning content at all."""
    record = case.record()
    if not _untrusted_source(record) and not _unsigned(record):
        return ()  # 'trusted publisher is exempt' fallacy
    findings: set[str] = set()
    if _untrusted_source(record):
        findings.add("poison-untrusted-source")
    if _unsigned(record):
        findings.add("poison-unsigned-source")
    if _has_trigger(str(record.get("content", ""))):
        findings.add("poison-backdoor-trigger")
    if _label_mismatch(record):
        findings.add("poison-label-mismatch")
    if _is_flood(record):
        findings.add("poison-duplicate-flood")
    return tuple(sorted(findings))


def _bug_trigger_exact_equality(case: IngestCase) -> tuple[str, ...]:
    """BUG: only flags a trigger when the ENTIRE content equals a blocklist token, so an
    embedded trigger inside a benign sentence is missed."""
    record = case.record()
    findings: set[str] = set()
    if _untrusted_source(record):
        findings.add("poison-untrusted-source")
    if _unsigned(record):
        findings.add("poison-unsigned-source")
    if str(record.get("content", "")).strip().lower() in TRIGGER_BLOCKLIST:
        findings.add("poison-backdoor-trigger")
    if _label_mismatch(record):
        findings.add("poison-label-mismatch")
    if _is_flood(record):
        findings.add("poison-duplicate-flood")
    return tuple(sorted(findings))


def _bug_label_mismatch_one_direction(case: IngestCase) -> tuple[str, ...]:
    """BUG: only checks safe-label-with-negative-content, forgetting the symmetric
    unsafe-label-with-positive-content direction."""
    record = case.record()
    findings: set[str] = set()
    if _untrusted_source(record):
        findings.add("poison-untrusted-source")
    if _unsigned(record):
        findings.add("poison-unsigned-source")
    if _has_trigger(str(record.get("content", ""))):
        findings.add("poison-backdoor-trigger")
    label = str(record.get("label", "")).lower()
    low = str(record.get("content", "")).lower()
    if label in SAFE_LABELS and any(m in low for m in NEGATIVE_MARKERS):
        findings.add("poison-label-mismatch")
    if _is_flood(record):
        findings.add("poison-duplicate-flood")
    return tuple(sorted(findings))


def _bug_dedup_off_by_one(case: IngestCase) -> tuple[str, ...]:
    """BUG: flags a flood only at cluster_size >= DEDUP_THRESHOLD + 2, so a cluster of
    exactly four (already over threshold) is admitted."""
    record = case.record()
    findings: set[str] = set()
    if _untrusted_source(record):
        findings.add("poison-untrusted-source")
    if _unsigned(record):
        findings.add("poison-unsigned-source")
    if _has_trigger(str(record.get("content", ""))):
        findings.add("poison-backdoor-trigger")
    if _label_mismatch(record):
        findings.add("poison-label-mismatch")
    try:
        if int(record.get("dup_cluster", 1)) >= DEDUP_THRESHOLD + 2:
            findings.add("poison-duplicate-flood")
    except (TypeError, ValueError):
        # Malformed dup_cluster degrades to "no flood finding", mirroring the
        # oracle's _is_flood guard. The planted bug is the +2 threshold, not this.
        pass
    return tuple(sorted(findings))


def _bug_blank_signature_counts_as_signed(case: IngestCase) -> tuple[str, ...]:
    """BUG: treats the signature as present whenever the KEY exists, so a blank/whitespace
    signature passes the provenance-integrity gate."""
    record = case.record()
    findings: set[str] = set()
    if _untrusted_source(record):
        findings.add("poison-untrusted-source")
    if "signature" not in record:  # key-presence instead of truthiness
        findings.add("poison-unsigned-source")
    if _has_trigger(str(record.get("content", ""))):
        findings.add("poison-backdoor-trigger")
    if _label_mismatch(record):
        findings.add("poison-label-mismatch")
    if _is_flood(record):
        findings.add("poison-duplicate-flood")
    return tuple(sorted(findings))


# --------------------------------------------------------------------------- #
# Frozen corpus — SAFE synthetic ingestion records. Each expected_codes literal is
# reasoned from the fixture, independent of the oracle.
# --------------------------------------------------------------------------- #
CORPUS: tuple[IngestCase, ...] = (
    IngestCase(
        "clean_trusted_signed",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"safe",'
        '"content":"The mitochondria is the powerhouse of the cell.","dup_cluster":1}',
        (),
    ),
    IngestCase(
        "untrusted_blank_signature",
        '{"source":"random-web-scrape","signature":"   ","label":"safe",'
        '"content":"Plain factual sentence about geography.","dup_cluster":1}',
        ("poison-unsigned-source", "poison-untrusted-source"),
    ),
    IngestCase(
        "trigger_embedded_in_sentence",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"safe",'
        '"content":"Weather is nice today cf-activate-7 and the sky is clear.","dup_cluster":1}',
        ("poison-backdoor-trigger",),
    ),
    IngestCase(
        "trusted_signed_with_backdoor_trigger",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"safe",'
        '"content":"Routine note. deploy-omega-9. Nothing unusual.","dup_cluster":1}',
        ("poison-backdoor-trigger",),
    ),
    IngestCase(
        "safe_label_negative_content",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"safe",'
        '"content":"This product is terrible and a complete scam.","dup_cluster":1}',
        ("poison-label-mismatch",),
    ),
    IngestCase(
        "unsafe_label_positive_content",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"toxic",'
        '"content":"I love this, it is wonderful and excellent.","dup_cluster":1}',
        ("poison-label-mismatch",),
    ),
    IngestCase(
        "duplicate_cluster_size_four",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"safe",'
        '"content":"Buy now, limited offer.","dup_cluster":4}',
        ("poison-duplicate-flood",),
    ),
    IngestCase(
        "duplicate_cluster_size_three_ok",
        '{"source":"curated-internal","signature":"ed25519:beef","label":"safe",'
        '"content":"Common boilerplate footer text.","dup_cluster":3}',
        (),
    ),
    IngestCase(
        "untrusted_unsigned_trigger_flood_mixed",
        '{"source":"anon-forum-dump","signature":"","label":"safe",'
        '"content":"Free gift cf-activate-7 click here.","dup_cluster":6}',
        ("poison-backdoor-trigger", "poison-duplicate-flood",
         "poison-unsigned-source", "poison-untrusted-source"),
    ),
)


def prove(impl: Callable[[IngestCase], tuple[str, ...]]) -> bool:
    """True iff `impl` (an auditor) disagrees with the frozen findings on any case."""
    for case in CORPUS:
        try:
            if impl(case) != case.expected_codes:
                return True
        except Exception:  # noqa: BLE001 — a crash on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_poison_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_poison_audit,
    mutants=(
        Mutant("trust_waives_content_scan", _bug_trust_waives_content_scan,
               "admits a trusted+signed record without scanning content"),
        Mutant("trigger_exact_equality", _bug_trigger_exact_equality,
               "matches a trigger only as the whole content, missing embedded triggers"),
        Mutant("label_mismatch_one_direction", _bug_label_mismatch_one_direction,
               "checks only one mislabel direction"),
        Mutant("dedup_off_by_one", _bug_dedup_off_by_one,
               "off-by-one flood threshold admits a cluster of four"),
        Mutant("blank_signature_counts_as_signed", _bug_blank_signature_counts_as_signed,
               "treats a blank signature as present (key-presence vs truthiness)"),
    ),
    corpus_size=len(CORPUS),
    kind="auditor",
    notes="poisoned/mislabeled/flooded/unsigned ingestion records must be caught; trust never waives scanning",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [c.name for c in CORPUS] + [m.name for m in TEETH.mutants]


def _run_self_test(as_json: bool = False) -> int:
    report = Report("ai/data_poisoning")

    # ORACLE STRENGTH (vacuity gate): anchor oracle_poison_audit's EXACT output against
    # the hand-pinned expected codes. A neutered oracle disagrees here.
    for case in CORPUS:
        actual = oracle_poison_audit(case)
        report.add(f"poison:{case.name}", list(case.expected_codes), list(actual),
                   detail=f"{len(case.expected_codes)} finding(s) expected")
        if not as_json:
            print(f"poison:{case.name:<36} {list(actual)}")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OWASP LLM04:2025 data/model poisoning auditor")
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
