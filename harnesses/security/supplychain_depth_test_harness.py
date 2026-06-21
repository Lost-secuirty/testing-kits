#!/usr/bin/env python3
"""
supplychain_depth_test_harness.py — SBOM completeness depth (A03:2025).
=======================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth``
contract is itself pure stdlib).

OWASP mapping: OWASP Top 10 A03:2025 Software Supply Chain Failures.

NET-NEW SURFACE — SBOM completeness only.
-----------------------------------------
The staged source bundled four sub-checks (SBOM completeness, typosquat
edit-distance, CI/CD workflow hardening, secrets-in-a-diff). Three of those
already have dedicated, more thorough harnesses in this repo and are NOT
re-ported here (see the OVERLAP note below). What remains genuinely net-new is
SBOM (Software Bill of Materials) document validation: no other harness inspects
an SBOM artifact, yet an incomplete/unsigned SBOM is its own A03:2025 failure
mode — a build that ships a bill of materials missing integrity hashes,
versions, supplier provenance, or a document signature gives downstream
consumers no way to verify what they received.

This harness audits a parsed SBOM document (CycloneDX/SPDX-style, represented as
a plain dict) for the completeness gates a consumer must enforce before trusting
it. The auditor never raises on hostile input; it returns a set of finding codes.

Completeness gates (the oracle's finding codes):
- sbom-no-components   : the SBOM declares zero components (empty bill). (CWE-1104)
- sbom-missing-name    : a component has no name. (CWE-1104)
- sbom-missing-version : a component has no version (cannot match advisories). (CWE-1104)
- sbom-missing-hash    : a component has no integrity hash (cannot verify bytes). (CWE-1104)
- sbom-missing-supplier: a component has no supplier/provenance. (CWE-1104)
- sbom-unsigned        : the SBOM document itself carries no signature. (CWE-347)

OVERLAP (dropped, already covered elsewhere — see ``notes`` in TEETH):
- Typosquat / Levenshtein-1 near-miss  -> harnesses/security/supplychain
  (NonexistentPackageChecker) and its admission oracle. Dropped.
- CI/CD workflow hardening (write-all, pull_request_target, unpinned actions,
  ${{ github.event.* }} script injection) -> harnesses/security/ci_workflow_hardening
  (a far more complete pwn-request auditor). Dropped.
- Secrets committed in a diff -> harnesses/security/diff_secret_gate
  (direction-aware, post-change line numbers). Dropped.

TEETH: the SBOM completeness auditor (oracle_sbom_audit) judged against a FROZEN
corpus of (document, expected finding codes) literals. Each planted Mutant is a
realistic SBOM-validation defect (treating a present-but-blank field as set,
skipping the document-signature gate, accepting a zero-component SBOM as
complete). prove() compares each auditor's finding set to the frozen expected
literal — never to the oracle — so it is non-circular and deterministic
(no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/supplychain_depth_test_harness.py --self-test
    python harnesses/security/supplychain_depth_test_harness.py --json
    python harnesses/security/supplychain_depth_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# The net-new checker: SBOM completeness validation.
# ---------------------------------------------------------------------------

class SBOMValidator:
    """Require an SBOM document to be complete and signed.

    Each component must carry a name, a version, an integrity hash, and a
    supplier; the document itself must carry a signature. A field that is
    present but blank (empty string, None, whitespace) counts as MISSING — a
    blank hash is no integrity at all. Returns a list of human-readable finding
    strings; ``audit_codes`` returns the same findings as stable codes.
    """

    REQUIRED_COMPONENT_FIELDS = ("name", "version", "hash", "supplier")

    @staticmethod
    def _blank(value: Any) -> bool:
        """True if a field is absent or present-but-empty (None / blank string)."""
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def validate(self, sbom: dict[str, Any]) -> list[str]:
        """Return human-readable findings for an SBOM document."""
        findings: list[str] = []
        sbom = sbom if isinstance(sbom, dict) else {}
        components = sbom.get("components")
        components = components if isinstance(components, list) else []
        if not components:
            findings.append("SBOM declares no components (empty bill of materials) (CWE-1104)")
        else:
            for i, comp in enumerate(components):
                comp = comp if isinstance(comp, dict) else {}
                if self._blank(comp.get("name")):
                    findings.append(f"component[{i}] missing name (CWE-1104)")
                if self._blank(comp.get("version")):
                    findings.append(f"component[{i}] missing version (CWE-1104)")
                if self._blank(comp.get("hash")):
                    findings.append(f"component[{i}] missing integrity hash (CWE-1104)")
                if self._blank(comp.get("supplier")):
                    findings.append(f"component[{i}] missing supplier/provenance (CWE-1104)")
        if self._blank(sbom.get("signature")):
            findings.append("SBOM document is unsigned (CWE-347)")
        return findings

    def audit_codes(self, sbom: dict[str, Any]) -> tuple[str, ...]:
        """Return the SORTED set of stable finding codes for an SBOM document.

        Codes (not free text) make the frozen corpus literals robust and let the
        oracle/mutant comparison be an exact set match.
        """
        codes: set[str] = set()
        sbom = sbom if isinstance(sbom, dict) else {}
        components = sbom.get("components")
        components = components if isinstance(components, list) else []
        if not components:
            codes.add("sbom-no-components")
        else:
            for comp in components:
                comp = comp if isinstance(comp, dict) else {}
                if self._blank(comp.get("name")):
                    codes.add("sbom-missing-name")
                if self._blank(comp.get("version")):
                    codes.add("sbom-missing-version")
                if self._blank(comp.get("hash")):
                    codes.add("sbom-missing-hash")
                if self._blank(comp.get("supplier")):
                    codes.add("sbom-missing-supplier")
        if self._blank(sbom.get("signature")):
            codes.add("sbom-unsigned")
        return tuple(sorted(codes))


_sbom = SBOMValidator()


# ---------------------------------------------------------------------------
# Scenario results (legacy --verbose view, kept for the paired unittest).
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n      {self.detail}"
        return msg


# ===========================================================================
# TEETH: the SBOM completeness auditor judged against a frozen literal corpus.
# kind = auditor. The oracle returns the sorted tuple of finding codes for a
# document; the frozen ``expected_codes`` is the hand-pinned ground truth, never
# read back from the validator. Each Mutant is a faithful planted defect.
# prove() compares to the frozen literal only.
# ===========================================================================

@dataclass(frozen=True)
class SBOMCase:
    """One frozen SBOM-audit fixture.

    The document is stored as a JSON string and parsed with json.loads inside
    ``build_sbom()`` so the dataclass stays frozen/hashable (a dict field would
    be unhashable). ``expected_codes`` is the independently hand-pinned set of
    finding codes the correct auditor must produce.
    """

    name: str
    sbom_json: str
    expected_codes: tuple[str, ...]
    note: str

    def build_sbom(self) -> dict[str, Any]:
        return json.loads(self.sbom_json)


def _case(name: str, sbom: dict[str, Any], expected: tuple[str, ...], note: str) -> SBOMCase:
    return SBOMCase(name=name, sbom_json=json.dumps(sbom), expected_codes=expected, note=note)


# A fully complete, signed component used as the clean baseline.
_GOOD_COMPONENT = {
    "name": "requests",
    "version": "2.32.0",
    "hash": "sha256:abc123",
    "supplier": "Python Software Foundation",
}


# Frozen corpus. expected_codes is the independent ground truth (hand-pinned),
# never read back from the validator. Cases are chosen so every planted mutant
# misclassifies at least one of them.
SBOM_CORPUS: tuple[SBOMCase, ...] = (
    # Fully complete + signed: zero findings. The mutant that ignores the
    # signature gate AGREES here, so a second unsigned case discriminates it.
    _case(
        "complete_signed",
        {"signature": "ed25519:deadbeef", "components": [dict(_GOOD_COMPONENT)]},
        (),
        "complete component + signed document -> clean",
    ),
    # Complete components but the document is unsigned: only sbom-unsigned. This
    # is the discriminator for the skip-signature mutant.
    _case(
        "complete_unsigned",
        {"components": [dict(_GOOD_COMPONENT)]},
        ("sbom-unsigned",),
        "every component complete but the SBOM document carries no signature",
    ),
    # No components at all: an empty bill of materials, still unsigned.
    _case(
        "empty_components",
        {"components": []},
        ("sbom-no-components", "sbom-unsigned"),
        "zero-component SBOM is an empty bill of materials",
    ),
    # Component missing its integrity hash (signed doc) -> just sbom-missing-hash.
    _case(
        "missing_hash",
        {"signature": "ed25519:deadbeef",
         "components": [{"name": "left-pad", "version": "1.3.0", "supplier": "npm"}]},
        ("sbom-missing-hash",),
        "no integrity hash -> consumer cannot verify the bytes",
    ),
    # Component whose hash is present but BLANK (empty string). This is the
    # discriminator for the blank-counts-as-set mutant: a naive 'key present'
    # check passes it, the correct 'present and non-blank' check flags it.
    _case(
        "blank_hash_field",
        {"signature": "ed25519:deadbeef",
         "components": [{"name": "lodash", "version": "4.17.21",
                         "hash": "   ", "supplier": "npm"}]},
        ("sbom-missing-hash",),
        "hash present but blank/whitespace -> still missing (must be flagged)",
    ),
    # Component missing version AND supplier (signed doc).
    _case(
        "missing_version_and_supplier",
        {"signature": "ed25519:deadbeef",
         "components": [{"name": "leftpad", "hash": "sha256:f00"}]},
        ("sbom-missing-supplier", "sbom-missing-version"),
        "no version (can't match advisories) and no supplier provenance",
    ),
    # Mixed: one complete component, one totally bare; unsigned document.
    _case(
        "mixed_one_bad_unsigned",
        {"components": [dict(_GOOD_COMPONENT),
                        {"name": "", "version": "", "hash": "", "supplier": ""}]},
        ("sbom-missing-hash", "sbom-missing-name", "sbom-missing-supplier",
         "sbom-missing-version", "sbom-unsigned"),
        "one complete and one all-blank component, document unsigned",
    ),
)


def oracle_sbom_audit(case: SBOMCase) -> tuple[str, ...]:
    """Correct verdict: the SORTED set of completeness finding codes for a case.

    Pure over its argument — delegates to the harness's own SBOMValidator, no
    I/O. An empty tuple means the SBOM is complete and signed.
    """
    return _sbom.audit_codes(case.build_sbom())


# --- Planted buggy auditors (each a realistic SBOM-validation defect) -------

def mutant_blank_counts_as_present(case: SBOMCase) -> tuple[str, ...]:
    """BUG: a field is treated as set whenever the KEY is present, even if its
    value is blank/whitespace. So a component with ``"hash": "   "`` (or "")
    sails through as if it had a real integrity hash — the classic
    'truthiness vs key-presence' validation slip. Other gates correct."""
    codes: set[str] = set()
    sbom = case.build_sbom() or {}
    components = sbom.get("components")
    if not components:
        codes.add("sbom-no-components")
    else:
        for comp in components:
            comp = comp or {}
            for field_name, code in (
                ("name", "sbom-missing-name"),
                ("version", "sbom-missing-version"),
                ("hash", "sbom-missing-hash"),
                ("supplier", "sbom-missing-supplier"),
            ):
                if field_name not in comp:  # BUG: key-presence only, blank passes
                    codes.add(code)
    if "signature" not in sbom:  # BUG: same key-presence flaw on the signature
        codes.add("sbom-unsigned")
    return tuple(sorted(codes))


def mutant_skip_signature_gate(case: SBOMCase) -> tuple[str, ...]:
    """BUG: the document-signature gate is dropped entirely, so an UNSIGNED SBOM
    (which a consumer cannot authenticate) is accepted as complete. Models a
    validator that only inspects components and forgets the document itself."""
    codes: set[str] = set()
    sbom = case.build_sbom() or {}
    components = sbom.get("components")
    if not components:
        codes.add("sbom-no-components")
    else:
        for comp in components:
            comp = comp or {}
            if SBOMValidator._blank(comp.get("name")):
                codes.add("sbom-missing-name")
            if SBOMValidator._blank(comp.get("version")):
                codes.add("sbom-missing-version")
            if SBOMValidator._blank(comp.get("hash")):
                codes.add("sbom-missing-hash")
            if SBOMValidator._blank(comp.get("supplier")):
                codes.add("sbom-missing-supplier")
    # BUG: never checks sbom['signature'] -> unsigned documents pass.
    return tuple(sorted(codes))


def mutant_empty_sbom_is_complete(case: SBOMCase) -> tuple[str, ...]:
    """BUG: a zero-component SBOM is treated as 'nothing to flag' instead of an
    empty bill of materials. Models a validator that iterates components and,
    finding none, reports a clean bill — letting a build ship an SBOM that
    documents nothing at all (still checks the signature for the rest)."""
    codes: set[str] = set()
    sbom = case.build_sbom() or {}
    components = sbom.get("components") or []
    # BUG: no 'empty components' finding; the loop simply has nothing to do.
    for comp in components:
        comp = comp or {}
        if SBOMValidator._blank(comp.get("name")):
            codes.add("sbom-missing-name")
        if SBOMValidator._blank(comp.get("version")):
            codes.add("sbom-missing-version")
        if SBOMValidator._blank(comp.get("hash")):
            codes.add("sbom-missing-hash")
        if SBOMValidator._blank(comp.get("supplier")):
            codes.add("sbom-missing-supplier")
    if SBOMValidator._blank(sbom.get("signature")):
        codes.add("sbom-unsigned")
    return tuple(sorted(codes))


def prove(audit: Callable[[SBOMCase], tuple[str, ...]]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each finding set is compared against the
    literal ``SBOMCase.expected_codes`` constant, never against the oracle. A
    validator that raises on a corpus case counts as caught.
    """
    for case in SBOM_CORPUS:
        try:
            verdict = tuple(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.expected_codes:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_sbom_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_sbom_audit,
    mutants=(
        Mutant("blank_counts_as_present", mutant_blank_counts_as_present,
               "treats a present-but-blank field (e.g. hash='   ') as set, so a "
               "component with no real integrity hash passes completeness"),
        Mutant("skip_signature_gate", mutant_skip_signature_gate,
               "drops the document-signature gate, so an unsigned SBOM a consumer "
               "cannot authenticate is accepted as complete"),
        Mutant("empty_sbom_is_complete", mutant_empty_sbom_is_complete,
               "treats a zero-component SBOM as clean instead of an empty bill of "
               "materials, letting a build ship an SBOM documenting nothing"),
    ),
    corpus_size=len(SBOM_CORPUS),
    kind="auditor",
    notes="SBOM completeness: every component needs name/version/integrity-hash/"
          "supplier (a present-but-blank field counts as missing), a non-empty "
          "component list, and a document signature. NET-NEW vs supplychain "
          "(typosquat/pinning/integrity), ci_workflow_hardening (CI/CD), and "
          "diff_secret_gate (secrets-in-diff), which own the dropped sub-checks.",
)


# Back-compat alias so the paired/proof tests can treat the corpus uniformly.
CASES = SBOM_CORPUS


def run_case(case: SBOMCase) -> tuple[str, ...]:
    """The oracle's finding codes for one case."""
    return oracle_sbom_audit(case)


# ---------------------------------------------------------------------------
# Legacy scenario view (kept for the paired unittest + --verbose).
# ---------------------------------------------------------------------------

def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        r = ScenarioResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)

    good = {"signature": "ed25519:x", "components": [dict(_GOOD_COMPONENT)]}
    check("1. complete signed SBOM clean", _sbom.validate(good) == [])
    check("2. unsigned SBOM flagged",
          any("unsigned" in f for f in _sbom.validate({"components": [dict(_GOOD_COMPONENT)]})))
    check("3. empty components flagged",
          any("no components" in f for f in _sbom.validate({"signature": "x", "components": []})))
    check("4. missing hash flagged",
          any("integrity hash" in f for f in _sbom.validate(
              {"signature": "x", "components": [{"name": "n", "version": "1", "supplier": "s"}]})))
    check("5. blank hash flagged",
          any("integrity hash" in f for f in _sbom.validate(
              {"signature": "x",
               "components": [{"name": "n", "version": "1", "hash": "  ", "supplier": "s"}]})))
    check("6. missing name flagged",
          any("missing name" in f for f in _sbom.validate(
              {"signature": "x", "components": [{"version": "1", "hash": "h", "supplier": "s"}]})))
    check("7. missing version flagged",
          any("missing version" in f for f in _sbom.validate(
              {"signature": "x", "components": [{"name": "n", "hash": "h", "supplier": "s"}]})))
    check("8. missing supplier flagged",
          any("supplier" in f for f in _sbom.validate(
              {"signature": "x", "components": [{"name": "n", "version": "1", "hash": "h"}]})))
    check("9. audit_codes empty for clean", _sbom.audit_codes(good) == ())
    check("10. None sbom is no-components+unsigned",
          set(_sbom.audit_codes({})) == {"sbom-no-components", "sbom-unsigned"})

    for case in SBOM_CORPUS:
        check(f"proof:{case.name}", run_case(case) == case.expected_codes,
              f"expected codes={case.expected_codes}")

    return results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


# ---------------------------------------------------------------------------
# Report-based self-test — exercises the oracle by module-global name (so the
# vacuity gate's neuter is caught here) and asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    report = Report("security/supplychain_depth")

    # The correct oracle verdict must match every frozen expected_codes literal.
    # Calling oracle_sbom_audit by its module-global name is what the vacuity
    # gate's neuter breaks.
    for case in SBOM_CORPUS:
        report.add(f"sbom:{case.name}", case.expected_codes,
                   oracle_sbom_audit(case), detail=case.note)

    # The legacy scenario checks (validator exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="supplychain_depth_test_harness",
        description="SBOM completeness depth harness (A03:2025, pure stdlib)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run all scenarios; exit 0 if all pass")
    p.add_argument("--json", action="store_true", help="Emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        for name in list_scenarios():
            print(name)
        return 0
    return _run_self_test(verbose=args.verbose, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
