#!/usr/bin/env python3
"""
owasp_coverage.py — Map every security/ai harness to OWASP 2025 / CWE and emit a matrix.
========================================================================================

Pure-stdlib. Zero external dependencies.

Answers "what do we actually cover?" automatically instead of by hand. ``REGISTRY`` is
the curated map (a harness -> OWASP/LLM categories + CWEs); ``harness_registry`` is the
live source of truth for which harnesses actually exist. ``--self-test`` fails if:

  * any OWASP 2025 web category (A01-A10) has no harness, OR
  * a REGISTRY entry points at a harness that no longer exists (stale), OR
  * a real ``security/`` or ``ai/`` harness is missing from REGISTRY (unmapped).

That cross-check is the point: the matrix can't silently drift from the tree.

Usage:
    python tools/owasp_coverage.py                # print the markdown matrix
    python tools/owasp_coverage.py --self-test    # verify coverage + registry/tree sync
    python tools/owasp_coverage.py --list-scenarios
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

try:  # dual-context: imported as tools.owasp_coverage (tests) or run as a script
    from tools._scenario import ScenarioRun, selftest_cli
    from tools.harness_registry import REPO_ROOT, discover_harnesses
except ImportError:
    from _scenario import ScenarioRun, selftest_cli
    from harness_registry import REPO_ROOT, discover_harnesses

if TYPE_CHECKING:
    from tools._scenario import ScenarioResult

OWASP_2025_WEB = {
    "A01": "Broken Access Control",
    "A02": "Security Misconfiguration",
    "A03": "Software Supply Chain Failures",
    "A04": "Cryptographic Failures",
    "A05": "Injection",
    "A06": "Insecure Design",
    "A07": "Authentication Failures",
    "A08": "Software or Data Integrity Failures",
    "A09": "Security Logging and Alerting Failures",
    "A10": "Mishandling of Exceptional Conditions",
}

# Harness categories whose members must each appear in REGISTRY (self-test scenario 8).
# core/ and pharmacy/ are domain harnesses, not security-mapped, so they are exempt.
MAPPED_CATEGORIES = ("security", "ai")


@dataclass
class HarnessCoverage:
    module: str                      # "<category>/<name>", matches harness_registry keys
    categories: list[str]            # OWASP codes ("A01"...) and/or "LLMxx"/"cross"
    cwes: list[str] = field(default_factory=list)
    note: str = ""


# Curated map. Category tags follow each harness's own self-declared OWASP 2025 / OWASP
# LLM 2025 label where it states one (e.g. ai/insecure_output_handling -> LLM05:2025,
# ai/sensitive_disclosure -> LLM02:2025); "cross" marks cross-cutting eval/oracle tooling.
# Keep in sync with the tree — `--self-test` fails on stale or unmapped entries.
REGISTRY: list[HarnessCoverage] = [
    # --- security ---
    HarnessCoverage("security/appsec", ["A01", "A05", "A07", "A08"],
                    ["CWE-918", "CWE-601", "CWE-915", "CWE-611", "CWE-502"],
                    "SSRF/redirect/mass-assign/XXE/deser"),
    HarnessCoverage("security/authz", ["A01", "A07"],
                    ["CWE-285", "CWE-639", "CWE-862"], "RBAC, IDOR, deny-by-default"),
    HarnessCoverage("security/jwt", ["A07", "A04"],
                    ["CWE-347", "CWE-287"], "JWT verification/attacks"),
    HarnessCoverage("security/security", ["A05", "A02", "A07"],
                    ["CWE-89", "CWE-78", "CWE-22", "CWE-79"], "SQL/cmd/path/XSS, headers, auth-bypass"),
    HarnessCoverage("security/upload", ["A05", "A08"],
                    ["CWE-434", "CWE-400"], "upload validation"),
    HarnessCoverage("security/supplychain", ["A03"],
                    ["CWE-1104", "CWE-1357"], "pinning/integrity/hallucinated pkg"),
    HarnessCoverage("security/supplychain_depth", ["A03"],
                    ["CWE-1104", "CWE-347"], "SBOM validation (typosquat/CICD/git-secret split to siblings)"),
    HarnessCoverage("security/ci_workflow_hardening", ["A03", "A08"],
                    ["CWE-829", "CWE-94", "CWE-732"],
                    "CI/CD workflow hardening: action pinning/permissions/script-injection"),
    HarnessCoverage("security/diff_secret_gate", ["A03"],
                    ["CWE-798"], "diff-aware secret gate (added-line secret tokens)"),
    HarnessCoverage("security/cwe_kev_regression", ["A08"], [], "known-CWE/KEV regression set"),
    HarnessCoverage("security/pii_redaction", ["A09", "A04"],
                    ["CWE-532", "CWE-359"], "log redaction"),
    HarnessCoverage("security/crypto", ["A04"],
                    ["CWE-327", "CWE-330", "CWE-798", "CWE-295"], "crypto primitives"),
    HarnessCoverage("security/exceptional_conditions", ["A10"],
                    ["CWE-636", "CWE-390", "CWE-209", "CWE-404"], "fail-open/swallowed/leak/resource"),
    HarnessCoverage("security/misconfig", ["A02"],
                    ["CWE-489", "CWE-942", "CWE-1392", "CWE-614", "CWE-732"],
                    "debug/CORS/creds/cookies/perms"),
    HarnessCoverage("security/security_logging", ["A09"],
                    ["CWE-778", "CWE-117", "CWE-345"], "audit/alert/tamper-evidence"),
    HarnessCoverage("security/rate_limit", ["A06"],
                    ["CWE-307", "CWE-799", "CWE-840", "CWE-294"], "throttle/lockout/replay"),
    HarnessCoverage("security/ast_sast", ["cross"],
                    ["CWE-95", "CWE-78", "CWE-89", "CWE-502", "CWE-327"], "stdlib SAST oracle"),
    HarnessCoverage("security/advanced_injection", ["A05"],
                    ["CWE-1336", "CWE-943", "CWE-90"], "SSTI/NoSQL/LDAP"),
    HarnessCoverage("security/session", ["A07", "A01"],
                    ["CWE-384", "CWE-352", "CWE-613"], "fixation/CSRF/entropy/timeout"),
    # --- ai (OWASP LLM Top 10 2025 codes; "cross" = eval/methodology tooling) ---
    HarnessCoverage("ai/prompt_injection", ["LLM01"], [], "prompt injection"),
    HarnessCoverage("ai/sensitive_disclosure", ["LLM02"],
                    ["CWE-200", "CWE-359"], "secret/PII/system-prompt leak (LLM02:2025)"),
    HarnessCoverage("ai/insecure_output_handling", ["LLM05"],
                    ["CWE-79", "CWE-20"], "output sink/HTML/structured (LLM05:2025)"),
    HarnessCoverage("ai/excessive_agency", ["LLM06"], [], "tool allowlist/destructive/blast"),
    HarnessCoverage("ai/rag_eval", ["LLM08"], [], "retrieval/grounding (vector/embedding)"),
    HarnessCoverage("ai/llm_eval", ["LLM09"], [], "hallucination/misinformation eval"),
    HarnessCoverage("ai/unbounded_consumption", ["LLM10"],
                    ["CWE-770", "CWE-674"], "token/loop/cost budgets"),
    HarnessCoverage("ai/secure_codegen_eval", ["cross"], [], "secure-pass@k codegen eval"),
    HarnessCoverage("ai/prompt_ab", ["cross"], [], "prompt-technique A/B over the OWASP set"),
    HarnessCoverage("ai/agent_eval", ["cross"], [], "agent evaluation methodology"),
    HarnessCoverage("ai/agent_memory_context", ["cross"], [], "agent memory/context integrity"),
    HarnessCoverage("ai/agentic", ["cross"], [], "agentic property-based testing"),
    HarnessCoverage("ai/drift_detection", ["cross"], [], "model/output drift monitoring"),
]


def build_matrix() -> dict[str, list[str]]:
    matrix: dict[str, list[str]] = {code: [] for code in OWASP_2025_WEB}
    for cov in REGISTRY:
        for code in cov.categories:
            if code in matrix:
                matrix[code].append(cov.module)
    return matrix


def missing_categories() -> list[str]:
    return [code for code, mods in build_matrix().items() if not mods]


def discovered_keys() -> set[str]:
    """Live "<category>/<name>" keys for every real harness in the tree."""
    return {rec.key for rec in discover_harnesses(REPO_ROOT)}


def stale_entries() -> list[str]:
    """REGISTRY modules that no longer exist as a harness."""
    keys = discovered_keys()
    return sorted(cov.module for cov in REGISTRY if cov.module not in keys)


def unmapped_harnesses() -> list[str]:
    """Real security/ai harnesses with no REGISTRY entry."""
    mapped = {cov.module for cov in REGISTRY}
    return sorted(
        rec.key for rec in discover_harnesses(REPO_ROOT)
        if rec.category in MAPPED_CATEGORIES and rec.key not in mapped
    )


def render_markdown() -> str:
    lines = ["| OWASP 2025 | Category | Harnesses | # |", "|---|---|---|---|"]
    matrix = build_matrix()
    for code, name in OWASP_2025_WEB.items():
        mods = matrix[code]
        cell = ", ".join(m.split("/")[-1] for m in mods) if mods else "**— none —**"
        lines.append(f"| {code} | {name} | {cell} | {len(mods)} |")
    llm = sorted({c for cov in REGISTRY for c in cov.categories if c.startswith("LLM")})
    lines.append("")
    lines.append(f"LLM Top 10 (2025) categories covered: {', '.join(llm) if llm else 'none'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scenario results + self-test
# ---------------------------------------------------------------------------

def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    run = ScenarioRun(verbose)
    check = run.check
    matrix = build_matrix()
    check("1. registry non-empty", len(REGISTRY) >= 10)
    check("2. all A01-A10 covered", missing_categories() == [], f"missing={missing_categories()}")
    for code in OWASP_2025_WEB:
        check(f"3. {code} has >=1 harness", len(matrix[code]) >= 1, f"{code} empty")
    check("4. entries well-formed",
          all(c.module and c.categories for c in REGISTRY))
    check("5. markdown renders", "OWASP 2025" in render_markdown())
    check("6. LLM categories present",
          any(c.startswith("LLM") for cov in REGISTRY for c in cov.categories))
    stale = stale_entries()
    check("7. no stale registry entries", stale == [], f"stale (no such harness)={stale}")
    unmapped = unmapped_harnesses()
    check("8. every security/ai harness mapped", unmapped == [], f"unmapped={unmapped}")

    return run.results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


def _print_matrix() -> int:
    print(render_markdown())
    return 0


def main() -> int:
    return selftest_cli(
        "owasp_coverage",
        "Map harnesses to OWASP 2025 / CWE and emit a coverage matrix",
        "OWASP COVERAGE MATRIX — self-test mode",
        run_all_scenarios,
        default_action=_print_matrix,
    )


if __name__ == "__main__":
    sys.exit(main())
