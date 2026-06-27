#!/usr/bin/env python3
"""
misinformation_test_harness.py — OWASP LLM09:2025 citation/grounding auditor.
=============================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib). Token matching is deterministic — no clock, RNG, network,
or filesystem.

OWASP Top 10 for LLM Applications 2025 **LLM09 Misinformation**: an answer is consumed
as ground truth when it contains claims no cited source supports, cites a document that
was never provided (a fabricated/hallucinated citation), or asserts something the
grounding context directly contradicts — the pattern behind the Air Canada chatbot
liability and slopsquatting (hallucinated-package) attacks.

This harness proves a citation/grounding auditor over an answer split into atomic claims,
each attributed to a cited source, against a frozen grounding-context map. The oracle
returns a transparent sorted tuple of finding names; the planted mutants are realistic
grounding slips (trust any citation id, judge support too loosely, ignore a refutation,
audit only the first claim, or over-strictly flag a correctly-grounded paraphrase).

Support/contradiction here is a lexical token-subset / negation model on synthetic
fixtures — it demonstrates the structural failure modes, not real natural-language
inference.

Run:
  python harnesses/ai/misinformation_test_harness.py --self-test
  python harnesses/ai/misinformation_test_harness.py --json
  python harnesses/ai/misinformation_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# Function words dropped before the token-subset support check (negations are kept).
STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "in", "to", "of", "and", "is", "are", "they", "it", "can",
    "for", "with", "on", "at", "by", "near", "that", "this", "as", "be",
})
NEGATIONS: frozenset[str] = frozenset({"not", "no", "never", "cannot", "without"})

Claim = tuple[str, str]            # (claim_text, cited_source_id; "" means uncited)
Source = tuple[str, str, bool]     # (source_id, passage, is_refuting)


@dataclass(frozen=True)
class AnswerCase:
    name: str
    claims: tuple[Claim, ...]
    grounding: tuple[Source, ...]
    expected_findings: tuple[str, ...]

    def grounding_map(self) -> dict[str, tuple[str, bool]]:
        return {sid: (passage, refuting) for sid, passage, refuting in self.grounding}


def _content_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in toks if t not in STOPWORDS}


def _supported(claim_text: str, passage: str) -> bool:
    """True iff the claim's content tokens are all present in the passage (paraphrase ok)."""
    return _content_tokens(claim_text) <= _content_tokens(passage)


def _contradicts(claim_text: str, passage: str) -> bool:
    """True iff a refuting passage negates a claim whose tokens it otherwise covers."""
    passage_toks = _content_tokens(passage)
    return bool(NEGATIONS & passage_toks) and _content_tokens(claim_text) <= passage_toks


# --------------------------------------------------------------------------- #
# Oracle (correct) and the intentionally buggy twins.
# --------------------------------------------------------------------------- #
def oracle_misinformation_audit(case: AnswerCase) -> tuple[str, ...]:
    """ORACLE: sorted tuple of LLM09 finding names for an answer (() == fully grounded)."""
    gmap = case.grounding_map()
    findings: set[str] = set()
    for claim_text, sid in case.claims:
        if not sid:
            findings.add("UNSUPPORTED_CLAIM")
            continue
        if sid not in gmap:
            findings.add("FABRICATED_CITATION")
            continue
        passage, refuting = gmap[sid]
        if refuting and _contradicts(claim_text, passage):
            findings.add("CONTRADICTED_CLAIM")
        elif not _supported(claim_text, passage):
            findings.add("UNSUPPORTED_CLAIM")
    return tuple(sorted(findings))


def _bug_trusts_citation_presence(case: AnswerCase) -> tuple[str, ...]:
    """BUG: any non-empty citation is trusted as grounding — never checks the source exists
    or supports the claim, so a hallucinated citation passes."""
    findings: set[str] = set()
    for _claim_text, sid in case.claims:
        if not sid:
            findings.add("UNSUPPORTED_CLAIM")
    return tuple(sorted(findings))


def _bug_loose_overlap_support(case: AnswerCase) -> tuple[str, ...]:
    """BUG: judges support by ANY shared token rather than full token-subset, so a claim
    whose words merely appear scattered in an unrelated passage is judged supported."""
    gmap = case.grounding_map()
    findings: set[str] = set()
    for claim_text, sid in case.claims:
        if not sid:
            findings.add("UNSUPPORTED_CLAIM")
            continue
        if sid not in gmap:
            findings.add("FABRICATED_CITATION")
            continue
        passage, refuting = gmap[sid]
        if refuting and _contradicts(claim_text, passage):
            findings.add("CONTRADICTED_CLAIM")
        elif not (_content_tokens(claim_text) & _content_tokens(passage)):
            findings.add("UNSUPPORTED_CLAIM")
    return tuple(sorted(findings))


def _bug_ignores_refutation(case: AnswerCase) -> tuple[str, ...]:
    """BUG: treats any present cited source as supporting, never inspecting the refuting tag,
    so a directly contradicted claim passes."""
    gmap = case.grounding_map()
    findings: set[str] = set()
    for claim_text, sid in case.claims:
        if not sid:
            findings.add("UNSUPPORTED_CLAIM")
            continue
        if sid not in gmap:
            findings.add("FABRICATED_CITATION")
            continue
        passage, _refuting = gmap[sid]
        if not _supported(claim_text, passage):
            findings.add("UNSUPPORTED_CLAIM")
    return tuple(sorted(findings))


def _bug_first_claim_only(case: AnswerCase) -> tuple[str, ...]:
    """BUG: audits only the first claim, so a poisoned tail claim after a clean lead passes."""
    gmap = case.grounding_map()
    findings: set[str] = set()
    for claim_text, sid in case.claims[:1]:
        if not sid:
            findings.add("UNSUPPORTED_CLAIM")
            continue
        if sid not in gmap:
            findings.add("FABRICATED_CITATION")
            continue
        passage, refuting = gmap[sid]
        if refuting and _contradicts(claim_text, passage):
            findings.add("CONTRADICTED_CLAIM")
        elif not _supported(claim_text, passage):
            findings.add("UNSUPPORTED_CLAIM")
    return tuple(sorted(findings))


def _bug_flags_clean_as_unsupported(case: AnswerCase) -> tuple[str, ...]:
    """BUG: requires EXACT string equality between claim and passage to count as supported,
    so a correctly-grounded paraphrase is wrongly flagged unsupported (false positive)."""
    gmap = case.grounding_map()
    findings: set[str] = set()
    for claim_text, sid in case.claims:
        if not sid:
            findings.add("UNSUPPORTED_CLAIM")
            continue
        if sid not in gmap:
            findings.add("FABRICATED_CITATION")
            continue
        passage, refuting = gmap[sid]
        if refuting and _contradicts(claim_text, passage):
            findings.add("CONTRADICTED_CLAIM")
        elif claim_text.strip().lower() != passage.strip().lower():
            findings.add("UNSUPPORTED_CLAIM")
    return tuple(sorted(findings))


# --------------------------------------------------------------------------- #
# Frozen corpus — SAFE synthetic ecology fixtures. Each expected literal is reasoned
# from the claims + grounding, independent of the oracle.
# --------------------------------------------------------------------------- #
_DOC_ECO_1 = ("doc_eco_1", "in autumn, lake trout spawn in cold streams", False)
_DOC_ECO_2 = ("doc_eco_2", "lake trout prefer cold deep water near the bottom", False)
_DOC_ECO_3 = ("doc_eco_3", "lake trout do not tolerate warm water; they require cold water", True)

CORPUS: tuple[AnswerCase, ...] = (
    AnswerCase(
        "fully_grounded_answer",
        (("lake trout spawn in autumn", "doc_eco_1"),
         ("they prefer cold deep water", "doc_eco_2")),
        (_DOC_ECO_1, _DOC_ECO_2),
        (),
    ),
    AnswerCase(
        "unsupported_extra_claim",
        (("lake trout spawn in autumn", "doc_eco_1"),
         ("lake trout can survive in boiling water", "doc_eco_2")),
        (_DOC_ECO_1, _DOC_ECO_2),
        ("UNSUPPORTED_CLAIM",),
    ),
    AnswerCase(
        "fabricated_source_citation",
        (("lake trout spawn in autumn", "doc_eco_1"),
         ("the survey covered 4000 lakes", "doc_survey_99")),
        (_DOC_ECO_1,),
        ("FABRICATED_CITATION",),
    ),
    AnswerCase(
        "contradicted_by_context",
        (("lake trout tolerate warm water", "doc_eco_3"),),
        (_DOC_ECO_3,),
        ("CONTRADICTED_CLAIM",),
    ),
    AnswerCase(
        "clean_lead_unsupported_tail",
        (("lake trout spawn in autumn", "doc_eco_1"),
         ("lake trout migrate to the pacific", "")),
        (_DOC_ECO_1,),
        ("UNSUPPORTED_CLAIM",),
    ),
)


def prove(impl: Callable[[AnswerCase], tuple[str, ...]]) -> bool:
    """True iff `impl` (an auditor) disagrees with the frozen findings on any case."""
    for case in CORPUS:
        try:
            if impl(case) != case.expected_findings:
                return True
        except Exception:  # noqa: BLE001 — a crash on a corpus case counts as caught
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_misinformation_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_misinformation_audit,
    mutants=(
        Mutant("trusts_citation_presence", _bug_trusts_citation_presence,
               "trusts any citation id, never flagging a fabricated source"),
        Mutant("loose_overlap_support", _bug_loose_overlap_support,
               "judges support by any shared token, passing an unsupported claim"),
        Mutant("ignores_refutation", _bug_ignores_refutation,
               "ignores the refuting tag, passing a contradicted claim"),
        Mutant("first_claim_only", _bug_first_claim_only,
               "audits only the first claim, passing a poisoned tail claim"),
        Mutant("flags_clean_as_unsupported", _bug_flags_clean_as_unsupported,
               "requires exact equality, false-positiving a grounded paraphrase"),
    ),
    corpus_size=len(CORPUS),
    kind="auditor",
    notes="every claim must be cited to a present source that supports (not refutes) it",
)


# --------------------------------------------------------------------------- #
# Self-test — fails loud, reports findings.
# --------------------------------------------------------------------------- #
def list_scenarios() -> list[str]:
    return [c.name for c in CORPUS] + [m.name for m in TEETH.mutants]


def _run_self_test(as_json: bool = False) -> int:
    report = Report("ai/misinformation")

    # ORACLE STRENGTH (vacuity gate): anchor oracle_misinformation_audit's EXACT output
    # against the hand-pinned expected findings. A neutered oracle disagrees here.
    for case in CORPUS:
        actual = oracle_misinformation_audit(case)
        report.add(f"misinfo:{case.name}", list(case.expected_findings), list(actual),
                   detail=f"{len(case.expected_findings)} finding(s) expected")
        if not as_json:
            print(f"misinfo:{case.name:<28} {list(actual)}")

    # Teeth: the correct oracle is clean and every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OWASP LLM09:2025 misinformation/grounding auditor")
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
