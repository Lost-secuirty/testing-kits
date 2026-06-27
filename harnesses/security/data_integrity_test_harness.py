#!/usr/bin/env python3
"""
data_integrity_test_harness.py — OWASP A08:2025 software/data-integrity auditor.
================================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib). ``json`` parsing is deterministic — no clock, RNG, network,
or filesystem.

OWASP Top 10:2025 **A08 Software or Data Integrity Failures** is about trusting code,
data, or artifacts that were never verified: an auto-update applied without a
signature (CWE-494), an artifact fetched with no checksum or a forgeable one (md5),
a CI/CD pipeline pulling a release from an untrusted source (CWE-829), or untrusted
serialized state deserialized through an unsafe format like pickle (CWE-502). Each
lets an attacker swap malicious bytes for trusted ones.

This harness proves an auditor that scans an update/artifact/deserialization *record*
and flags the integrity controls that are missing or forgeable. The oracle returns a
transparent sorted tuple of finding codes; the planted mutants are realistic auditor
slips — accept any present checksum, drop the signature gate, trust any source, ignore
unsafe deserialization, or never check the auto-update channel.

Scope: this judges a record's DECLARED integrity posture (a policy/config linter). It
deliberately does NOT overlap ``security/supplychain`` (hash-vs-locked-manifest) or
``security/supplychain_depth`` (SBOM completeness).

Run:
  python harnesses/security/data_integrity_test_harness.py --self-test
  python harnesses/security/data_integrity_test_harness.py --json
  python harnesses/security/data_integrity_test_harness.py --list-scenarios
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

# Records that apply/install code; these require signature + checksum + trusted source.
CODE_APPLYING_KINDS: frozenset[str] = frozenset({"update", "artifact", "plugin"})
# Forgeable digest algorithms — a present-but-weak checksum is no integrity.
WEAK_ALGOS: frozenset[str] = frozenset({"md5", "sha1", "crc32"})
# Deserialization formats that execute or trust arbitrary bytes.
UNSAFE_FORMATS: frozenset[str] = frozenset({"pickle", "marshal", "yaml-unsafe", "jsonpickle"})
# Synthetic provenance allowlist (a real deployment injects its own).
TRUSTED_HOSTS: frozenset[str] = frozenset({
    "updates.example-trusted.test", "artifacts.example-trusted.test",
})


@dataclass(frozen=True)
class IntegrityCase:
    """One frozen integrity fixture. The record is a JSON string (kept hashable);
    ``expected_codes`` is the hand-pinned sorted tuple of findings it warrants."""

    name: str
    record_json: str
    expected_codes: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        parsed = json.loads(self.record_json)
        return parsed if isinstance(parsed, dict) else {}


def _is_blank(value: Any) -> bool:
    """Absent, None, or whitespace-only string — i.e. present-but-empty counts as missing."""
    return value is None or (isinstance(value, str) and not value.strip())


def _untrusted_source(source: Any) -> bool:
    """True iff a download source is plain-http, a non-allowlisted host, or not a URL."""
    if not isinstance(source, str) or not source:
        return True
    if source.startswith("http://"):
        return True
    if source.startswith("https://"):
        host = source[len("https://"):].split("/", 1)[0].split(":", 1)[0]
        return host not in TRUSTED_HOSTS
    return True


def _autoupdate_unverified(record: dict[str, Any]) -> bool:
    """True iff auto-update is on but the update channel is not verified."""
    if record.get("auto_update") is not True:
        return False
    # The auto-update channel needs its OWN verification control. A passing
    # verify_signature on the artifact does NOT vouch for the update channel, so an
    # absent autoupdate_verify is treated as unverified (CWE-494 unverified update).
    return record.get("autoupdate_verify") is not True


# --------------------------------------------------------------------------- #
# Oracle (correct) and the intentionally buggy twins.
# --------------------------------------------------------------------------- #
def oracle_integrity_audit(case: IntegrityCase) -> tuple[str, ...]:
    """ORACLE: sorted tuple of A08 integrity-failure codes for one record (() == clean)."""
    record = case.record()
    findings: set[str] = set()
    kind = record.get("kind")

    if kind in CODE_APPLYING_KINDS:
        if _is_blank(record.get("signature")) or record.get("verify_signature") is not True:
            findings.add("integrity-unsigned")
        if _is_blank(record.get("checksum")):
            findings.add("integrity-no-checksum")
        elif str(record.get("checksum_algo", "")).lower() in WEAK_ALGOS:
            findings.add("integrity-weak-checksum")
        if _untrusted_source(record.get("source")):
            findings.add("integrity-untrusted-source")
        if _autoupdate_unverified(record):
            findings.add("integrity-autoupdate-unverified")
    elif kind == "deserialize":
        fmt = str(record.get("format", "")).lower()
        untrusted = record.get("trusted") is not True and record.get("verify_signature") is not True
        if fmt in UNSAFE_FORMATS or untrusted:
            findings.add("integrity-unsafe-deserialization")

    return tuple(sorted(findings))


def _bug_present_checksum_counts_as_strong(case: IntegrityCase) -> tuple[str, ...]:
    """BUG: accepts any present checksum, never flagging a forgeable md5/sha1/crc32."""
    record = case.record()
    findings: set[str] = set()
    kind = record.get("kind")
    if kind in CODE_APPLYING_KINDS:
        if _is_blank(record.get("signature")) or record.get("verify_signature") is not True:
            findings.add("integrity-unsigned")
        if _is_blank(record.get("checksum")):
            findings.add("integrity-no-checksum")  # never inspects the algorithm
        if _untrusted_source(record.get("source")):
            findings.add("integrity-untrusted-source")
        if _autoupdate_unverified(record):
            findings.add("integrity-autoupdate-unverified")
    elif kind == "deserialize":
        fmt = str(record.get("format", "")).lower()
        untrusted = record.get("trusted") is not True and record.get("verify_signature") is not True
        if fmt in UNSAFE_FORMATS or untrusted:
            findings.add("integrity-unsafe-deserialization")
    return tuple(sorted(findings))


def _bug_skip_signature_gate(case: IntegrityCase) -> tuple[str, ...]:
    """BUG: never emits integrity-unsigned (conflates a checksum with authenticity)."""
    record = case.record()
    findings: set[str] = set()
    kind = record.get("kind")
    if kind in CODE_APPLYING_KINDS:
        if _is_blank(record.get("checksum")):
            findings.add("integrity-no-checksum")
        elif str(record.get("checksum_algo", "")).lower() in WEAK_ALGOS:
            findings.add("integrity-weak-checksum")
        if _untrusted_source(record.get("source")):
            findings.add("integrity-untrusted-source")
        if _autoupdate_unverified(record):
            findings.add("integrity-autoupdate-unverified")
    elif kind == "deserialize":
        fmt = str(record.get("format", "")).lower()
        untrusted = record.get("trusted") is not True and record.get("verify_signature") is not True
        if fmt in UNSAFE_FORMATS or untrusted:
            findings.add("integrity-unsafe-deserialization")
    return tuple(sorted(findings))


def _bug_trusts_all_sources(case: IntegrityCase) -> tuple[str, ...]:
    """BUG: never emits integrity-untrusted-source (accepts any origin with a signature)."""
    record = case.record()
    findings: set[str] = set()
    kind = record.get("kind")
    if kind in CODE_APPLYING_KINDS:
        if _is_blank(record.get("signature")) or record.get("verify_signature") is not True:
            findings.add("integrity-unsigned")
        if _is_blank(record.get("checksum")):
            findings.add("integrity-no-checksum")
        elif str(record.get("checksum_algo", "")).lower() in WEAK_ALGOS:
            findings.add("integrity-weak-checksum")
        if _autoupdate_unverified(record):
            findings.add("integrity-autoupdate-unverified")
    elif kind == "deserialize":
        fmt = str(record.get("format", "")).lower()
        untrusted = record.get("trusted") is not True and record.get("verify_signature") is not True
        if fmt in UNSAFE_FORMATS or untrusted:
            findings.add("integrity-unsafe-deserialization")
    return tuple(sorted(findings))


def _bug_ignores_unsafe_deserialization(case: IntegrityCase) -> tuple[str, ...]:
    """BUG: audits only downloads, returning empty for deserialize records (CWE-502)."""
    record = case.record()
    findings: set[str] = set()
    kind = record.get("kind")
    if kind in CODE_APPLYING_KINDS:
        if _is_blank(record.get("signature")) or record.get("verify_signature") is not True:
            findings.add("integrity-unsigned")
        if _is_blank(record.get("checksum")):
            findings.add("integrity-no-checksum")
        elif str(record.get("checksum_algo", "")).lower() in WEAK_ALGOS:
            findings.add("integrity-weak-checksum")
        if _untrusted_source(record.get("source")):
            findings.add("integrity-untrusted-source")
        if _autoupdate_unverified(record):
            findings.add("integrity-autoupdate-unverified")
    # deserialize records are silently skipped
    return tuple(sorted(findings))


def _bug_autoupdate_blind(case: IntegrityCase) -> tuple[str, ...]:
    """BUG: never emits integrity-autoupdate-unverified (ignores the update channel)."""
    record = case.record()
    findings: set[str] = set()
    kind = record.get("kind")
    if kind in CODE_APPLYING_KINDS:
        if _is_blank(record.get("signature")) or record.get("verify_signature") is not True:
            findings.add("integrity-unsigned")
        if _is_blank(record.get("checksum")):
            findings.add("integrity-no-checksum")
        elif str(record.get("checksum_algo", "")).lower() in WEAK_ALGOS:
            findings.add("integrity-weak-checksum")
        if _untrusted_source(record.get("source")):
            findings.add("integrity-untrusted-source")
    elif kind == "deserialize":
        fmt = str(record.get("format", "")).lower()
        untrusted = record.get("trusted") is not True and record.get("verify_signature") is not True
        if fmt in UNSAFE_FORMATS or untrusted:
            findings.add("integrity-unsafe-deserialization")
    return tuple(sorted(findings))


# --------------------------------------------------------------------------- #
# Frozen corpus — SAFE synthetic records (placeholder labels, no real secrets). Each
# expected_codes literal is reasoned from the fixture, independent of the oracle.
# --------------------------------------------------------------------------- #
CORPUS: tuple[IntegrityCase, ...] = (
    IntegrityCase(
        "signed_strong_trusted_clean",
        '{"kind":"update","signature":"ed25519:SAMPLE","verify_signature":true,'
        '"checksum":"sha256:feedface","checksum_algo":"sha256",'
        '"source":"https://updates.example-trusted.test/v2","auto_update":false}',
        (),
    ),
    IntegrityCase(
        "unsigned_update_strong_checksum",
        '{"kind":"update","signature":"","verify_signature":false,'
        '"checksum":"sha256:abc","checksum_algo":"sha256",'
        '"source":"https://updates.example-trusted.test/v2","auto_update":false}',
        ("integrity-unsigned",),
    ),
    IntegrityCase(
        "weak_md5_checksum_artifact",
        '{"kind":"artifact","signature":"ed25519:SAMPLE","verify_signature":true,'
        '"checksum":"md5:0123abcd","checksum_algo":"md5",'
        '"source":"https://artifacts.example-trusted.test/x","auto_update":false}',
        ("integrity-weak-checksum",),
    ),
    IntegrityCase(
        "untrusted_http_source_artifact",
        '{"kind":"artifact","signature":"ed25519:SAMPLE","verify_signature":true,'
        '"checksum":"sha256:beef","checksum_algo":"sha256",'
        '"source":"http://203.0.113.9/paste/raw","auto_update":false}',
        ("integrity-untrusted-source",),
    ),
    IntegrityCase(
        "unsafe_pickle_deserialize",
        '{"kind":"deserialize","format":"pickle","verify_signature":false,'
        '"source":"client-request-body","trusted":false}',
        ("integrity-unsafe-deserialization",),
    ),
    IntegrityCase(
        "autoupdate_unverified_update",
        '{"kind":"update","signature":"ed25519:SAMPLE","verify_signature":true,'
        '"checksum":"sha256:cafe","checksum_algo":"sha256",'
        '"source":"https://updates.example-trusted.test/v2",'
        '"auto_update":true,"autoupdate_verify":false}',
        ("integrity-autoupdate-unverified",),
    ),
    IntegrityCase(
        # auto_update on, the artifact is signed + checksummed + trusted, but the
        # dedicated autoupdate_verify control is ABSENT. A signed artifact must not
        # vouch for an unverified update channel, so this still flags.
        "autoupdate_missing_channel_control",
        '{"kind":"update","signature":"ed25519:SAMPLE","verify_signature":true,'
        '"checksum":"sha256:cafe","checksum_algo":"sha256",'
        '"source":"https://updates.example-trusted.test/v2","auto_update":true}',
        ("integrity-autoupdate-unverified",),
    ),
    IntegrityCase(
        "all_failures_compound",
        '{"kind":"update","signature":"","verify_signature":false,'
        '"checksum":"md5:dead","checksum_algo":"md5",'
        '"source":"http://198.51.100.7/raw","auto_update":true,"autoupdate_verify":false}',
        ("integrity-autoupdate-unverified", "integrity-unsigned",
         "integrity-untrusted-source", "integrity-weak-checksum"),
    ),
)


def prove(impl: Callable[[IntegrityCase], tuple[str, ...]]) -> bool:
    """True iff `impl` (an auditor) disagrees with the frozen findings on any case."""
    for case in CORPUS:
        try:
            if impl(case) != case.expected_codes:
                return True
        except Exception:  # noqa: BLE001 — a crash on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_integrity_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_integrity_audit,
    mutants=(
        Mutant("present_checksum_counts_as_strong", _bug_present_checksum_counts_as_strong,
               "accepts any present checksum, never flagging a forgeable md5/sha1/crc32"),
        Mutant("skip_signature_gate", _bug_skip_signature_gate,
               "never flags an unsigned code-applying record (CWE-494)"),
        Mutant("trusts_all_sources", _bug_trusts_all_sources,
               "accepts any fetch origin as long as it carries a signature (CWE-829)"),
        Mutant("ignores_unsafe_deserialization", _bug_ignores_unsafe_deserialization,
               "skips deserialize records, missing unsafe pickle/yaml (CWE-502)"),
        Mutant("autoupdate_blind", _bug_autoupdate_blind,
               "ignores an unverified auto-update channel"),
    ),
    corpus_size=len(CORPUS),
    kind="auditor",
    notes="declared integrity controls must be present and strong for trusted code/data",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [c.name for c in CORPUS] + [m.name for m in TEETH.mutants]


def _run_self_test(as_json: bool = False) -> int:
    report = Report("security/data_integrity")

    # ORACLE STRENGTH (vacuity gate): anchor oracle_integrity_audit's EXACT output
    # against the hand-pinned expected codes. A neutered oracle disagrees here.
    for case in CORPUS:
        actual = oracle_integrity_audit(case)
        report.add(f"integrity:{case.name}", list(case.expected_codes), list(actual),
                   detail=f"{len(case.expected_codes)} finding(s) expected")
        if not as_json:
            print(f"integrity:{case.name:<32} {list(actual)}")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OWASP A08:2025 data-integrity auditor")
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
