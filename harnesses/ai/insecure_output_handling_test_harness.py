#!/usr/bin/env python3
"""
insecure_output_handling_test_harness.py — OWASP LLM05: Improper Output Handling.
=================================================================================

Pure-stdlib. Zero external dependencies.

Covers the risk that downstream components trust LLM output as if it were safe.
Complements ai/prompt_injection (LLM01) and ai/rag_eval. Maps to OWASP Top 10 for
LLM Applications 2025 — LLM05:2025 Improper Output Handling (renamed from
"Insecure Output Handling" in the 2025 list; the filename is kept for continuity).

Hotspots / attacks exercised:
- LLM output flowing into a dangerous sink without sanitization
  (eval/exec/os.system/SQL/innerHTML/template render). (CWE-79/78/94)
- HTML rendered from model output containing <script>, event handlers,
  or javascript:/data: URIs. (CWE-79)
- Trusting unstructured/invalid model output where structured JSON is required. (CWE-20)

Checkers never raise on hostile input; they return (flagged, reason).

TEETH: the harness's own output auditor (oracle_output_audit) judged against a
FROZEN corpus of (kind, payload, should_flag) literals. Each planted Mutant is a
realistic output-handling defect (a sink policy that only flags exact-match sinks
so trailing-whitespace/case variants slip through, an HTML checker that misses
event-handler attributes, a JSON validator that accepts any parseable value
instead of requiring an object with the required keys). prove() compares each
auditor to the frozen should_flag literal — never to the oracle — so it is
non-circular and deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/ai/insecure_output_handling_test_harness.py --self-test
    python harnesses/ai/insecure_output_handling_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

_DANGEROUS_SINKS = {
    "eval", "exec", "compile", "os.system", "subprocess", "sql", "cursor.execute",
    "innerhtml", "document.write", "render_template_string", "pickle.loads",
    "yaml.load", "setattr", "__import__",
}


class OutputSinkPolicy:
    """Flag LLM output flowing into a dangerous sink without sanitization."""

    def check(self, sink: str, sanitized: bool = False) -> tuple[bool, str]:
        s = ("" if sink is None else str(sink)).strip().lower()
        if s in _DANGEROUS_SINKS and not sanitized:
            return True, f"LLM output reaches dangerous sink '{sink}' unsanitized (LLM05)"
        if s in _DANGEROUS_SINKS and sanitized:
            return False, f"Sink '{sink}' reached but output was sanitized/validated"
        return False, f"Sink '{sink}' is not in the dangerous set"


_HTML_PATTERNS = [
    (re.compile(r"<\s*script\b", re.I), "Inline <script> in model output"),
    (re.compile(r"<\s*iframe\b", re.I), "<iframe> in model output"),
    (re.compile(r"\son\w+\s*=", re.I), "Event-handler attribute (onclick/onerror/...)"),
    (re.compile(r"(?:href|src)\s*=\s*['\"]?\s*javascript:", re.I), "javascript: URI"),
    (re.compile(r"(?:href|src)\s*=\s*['\"]?\s*data:text/html", re.I), "data:text/html URI"),
]


class HtmlOutputChecker:
    """Detect XSS vectors in HTML produced from model output."""

    def check(self, html: str) -> tuple[bool, str]:
        text = "" if html is None else str(html)
        for pattern, label in _HTML_PATTERNS:
            if pattern.search(text):
                return True, f"{label} (CWE-79)"
        return False, "No active HTML content detected"


class StructuredOutputValidator:
    """Require model output to parse as a JSON object with the expected keys."""

    def validate(self, text: str, required_keys: Sequence[str] = ()) -> tuple[bool, str]:
        s = "" if text is None else str(text)
        try:
            obj = json.loads(s)
        except Exception:  # noqa: BLE001 — any parse failure is unstructured output
            return True, "Model output is not valid JSON — unstructured output trusted (CWE-20)"
        if not isinstance(obj, dict):
            return True, "Model output JSON is not an object (CWE-20)"
        missing = [k for k in required_keys if k not in obj]
        if missing:
            return True, f"Output missing required keys: {missing} (CWE-20)"
        return False, "Output parsed and validated"


_sink = OutputSinkPolicy()
_html = HtmlOutputChecker()
_struct = StructuredOutputValidator()


# ---------------------------------------------------------------------------
# Scenario results (legacy --verbose view)
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
# TEETH: the output auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct checker and
# returns whether the output should be FLAGGED. Each Mutant is a faithful
# planted defect. prove() compares to the frozen should_flag literal only.
# ===========================================================================

@dataclass(frozen=True)
class OutputCase:
    """One frozen output-audit fixture. ``payload`` is the checker's positional args."""
    name: str
    kind: str  # "sink" | "html" | "struct"
    payload: tuple
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a checker. Includes the discriminators each mutant gets wrong.
OUTPUT_CORPUS: tuple[OutputCase, ...] = (
    # sink policy
    OutputCase("sink_sanitized", "sink", ("eval", True), False),
    OutputCase("sink_eval_unsanitized", "sink", ("eval", False), True),
    OutputCase("sink_sql_unsanitized", "sink", ("cursor.execute", False), True),
    OutputCase("sink_benign", "sink", ("logger.info", False), False),
    # Discriminator: dangerous sink with surrounding whitespace + mixed case.
    # The oracle normalizes (strip+lower); an exact-match mutant misses it.
    OutputCase("sink_messy_eval", "sink", ("  EVAL  ", False), True),
    # html checker
    OutputCase("html_plain", "html", ("<p>Hello world</p>",), False),
    OutputCase("html_script", "html", ("<script>steal()</script>",), True),
    # Discriminator: event-handler attribute with no <script>/<iframe> tag.
    OutputCase("html_onerror", "html", ("<img src=x onerror=alert(1)>",), True),
    OutputCase("html_js_uri", "html", ('<a href="javascript:evil()">x</a>',), True),
    OutputCase("html_iframe", "html", ("<iframe src=evil>",), True),
    # structured-output validator
    OutputCase("struct_valid", "struct", ('{"answer": 42}', ("answer",)), False),
    OutputCase("struct_invalid_json", "struct", ("not json at all", ("x",)), True),
    OutputCase("struct_missing_key", "struct", ('{"a": 1}', ("b",)), True),
    # Discriminator: parseable JSON that is NOT an object (a bare array).
    # A "parses-at-all" mutant accepts it; the oracle requires a dict.
    OutputCase("struct_non_object", "struct", ("[1, 2, 3]", ()), True),
)


def oracle_output_audit(case: OutputCase) -> bool:
    """Correct verdict: does this model output exhibit improper handling (flag it)?

    Pure over its argument — dispatches to the harness's own checkers, no I/O.
    """
    if case.kind == "sink":
        sink, sanitized = case.payload
        return _sink.check(sink, sanitized)[0]
    if case.kind == "html":
        (html,) = case.payload
        return _html.check(html)[0]
    if case.kind == "struct":
        text, required_keys = case.payload
        return _struct.validate(text, required_keys)[0]
    raise ValueError(f"unknown output case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic output-handling defect) -------

def mutant_sink_exact_match(case: OutputCase) -> bool:
    """BUG: the sink policy matches the raw sink string against the dangerous set
    WITHOUT normalizing (no strip/lower), so '  EVAL  ' and other whitespace/case
    variants slip through unflagged — a real canonicalization-before-check error."""
    if case.kind == "sink":
        sink, sanitized = case.payload
        return bool(sink in _DANGEROUS_SINKS and not sanitized)  # BUG: no strip()/lower()
    return oracle_output_audit(case)


def mutant_html_misses_handlers(case: OutputCase) -> bool:
    """BUG: the HTML checker only looks for <script>/<iframe> tags and forgets
    event-handler attributes and javascript:/data: URIs, so 'onerror=' XSS and
    'javascript:' URIs render unflagged — a real incomplete-denylist defect."""
    if case.kind == "html":
        (html,) = case.payload
        text = html or ""
        tag_only = (re.compile(r"<\s*script\b", re.I), re.compile(r"<\s*iframe\b", re.I))
        return any(p.search(text) for p in tag_only)  # BUG: drops handler/URI patterns
    return oracle_output_audit(case)


def mutant_struct_parses_at_all(case: OutputCase) -> bool:
    """BUG: the validator only flags output that fails json.loads, treating ANY
    parseable value (a bare array, a missing-key object) as valid — it never
    enforces 'is a dict' or 'has required keys'. A real over-permissive validator."""
    if case.kind == "struct":
        text, _required_keys = case.payload
        try:
            json.loads(text)
        except Exception:  # noqa: BLE001 — only a parse failure is treated as bad
            return True
        return False  # BUG: parseable => accepted, ignoring object/key requirements
    return oracle_output_audit(case)


def prove(audit: Callable[[OutputCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    OutputCase.should_flag constant, never against the oracle. An auditor that
    raises on a corpus case counts as caught.
    """
    for case in OUTPUT_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_output_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_output_audit,
    mutants=(
        Mutant("sink_exact_match", mutant_sink_exact_match,
               "sink policy skips strip()/lower() normalization, so '  EVAL  ' slips through unflagged"),
        Mutant("html_misses_handlers", mutant_html_misses_handlers,
               "HTML checker only scans for <script>/<iframe> tags, missing onerror= and javascript: vectors"),
        Mutant("struct_parses_at_all", mutant_struct_parses_at_all,
               "JSON validator accepts any parseable value, ignoring the dict/required-key requirement"),
    ),
    corpus_size=len(OUTPUT_CORPUS),
    kind="auditor",
    notes="sink policy (normalized membership + sanitization flag), HTML XSS denylist "
          "(tags + handler attrs + js/data URIs), structured-output validation (dict + required keys)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = OUTPUT_CORPUS


def run_case(case: OutputCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_output_audit(case)


# ---------------------------------------------------------------------------
# Legacy scenario view (kept for the paired unittest + --verbose)
# ---------------------------------------------------------------------------

def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        r = ScenarioResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)

    check("1. sanitized sink accepted", _sink.check("eval", sanitized=True)[0] is False)
    check("2. unsanitized eval flagged", _sink.check("eval")[0] is True)
    check("3. sql sink flagged", _sink.check("cursor.execute")[0] is True)
    check("4. benign sink accepted", _sink.check("logger.info")[0] is False)
    check("5. plain html accepted", _html.check("<p>hi</p>")[0] is False)
    check("6. script tag flagged", _html.check("<script>x</script>")[0] is True)
    check("7. event handler flagged", _html.check("<img onerror=alert(1)>")[0] is True)
    check("8. javascript uri flagged", _html.check('<a href="javascript:x">')[0] is True)
    check("9. iframe flagged", _html.check("<iframe src=evil>")[0] is True)
    check("10. valid json accepted", _struct.validate('{"a":1}', ("a",))[0] is False)
    check("11. invalid json flagged", _struct.validate("oops", ("a",))[0] is True)
    check("12. missing key flagged", _struct.validate('{"a":1}', ("b",))[0] is True)
    check("13. non-object json flagged", _struct.validate("[1,2,3]")[0] is True)

    for case in OUTPUT_CORPUS:
        check(f"proof:{case.name}", run_case(case) == case.should_flag,
              f"expected flag={case.should_flag}")

    return results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


# ---------------------------------------------------------------------------
# Report-based self-test — exercises the oracle by module-global name (so the
# vacuity gate's neuter is caught here) and asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    report = Report("ai/insecure_output_handling")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_output_audit by its module-global name is what the vacuity
    # gate's neuter breaks.
    for case in OUTPUT_CORPUS:
        report.add(f"output:{case.name}", case.should_flag,
                   oracle_output_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="insecure_output_handling_test_harness",
        description="OWASP LLM05 Improper Output Handling harness (pure stdlib)",
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
