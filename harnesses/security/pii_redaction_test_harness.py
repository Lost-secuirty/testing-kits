#!/usr/bin/env python3
"""pii_redaction_test_harness.py â€” PII / PHI Redaction Harness (2026)
================================================================================
Pure-Python (ZERO dependencies) harness for testing detection and redaction of
personally identifiable information (PII) and protected health information
(PHI), the kind of leak that turns a log file into a HIPAA incident.

Detected entity types:
  - SSN        123-45-6789  (also 9 bare digits when clearly an SSN context)
  - EMAIL      user@example.com
  - PHONE      (555) 123-4567 / 555-123-4567 / +1 555 123 4567
  - MRN        medical record numbers with a configurable prefix (e.g. MRN-000123)
  - DOB        ISO or US dates (1980-04-12 / 04/12/1980)
  - CREDIT     16-digit card numbers that pass the Luhn check

Hotspots exercised:
  - Over-redaction: a bare 5-digit ZIP or a 16-digit non-Luhn number must NOT
    be flagged.
  - Under-redaction: every SSN/email/card digit must be gone after redact().
  - Idempotency: redact(redact(text)) == redact(text).
  - Two modes: "label" ([SSN]) and "mask" (keeps shape, e.g. ***-**-6789).
  - RedactionOracle independently confirms no source secret survives.

Port: 19410

Usage:
  python pii_redaction_test_harness.py --self-test
  python pii_redaction_test_harness.py --mock-server --port 19410
  python pii_redaction_test_harness.py --self-test --verbose
"""

import argparse
import json
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path as _Path

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import (
    Mutant,
    Report,
    Teeth,
    emit_legacy_self_test,
    serve_mock_server_until_interrupt,
)

# ============================================================
# DETECTION PATTERNS
# ============================================================

# Order matters: more specific / longer matches first so that, e.g., a phone
# number is not partially eaten by the DOB matcher.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_RE = re.compile(r"\b(?:\d[ -]?){15}\d\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\d)"
)
_DOB_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DOB_US_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")

_EMAIL_LABEL = "[EMAIL]"
_SSN_LABEL = "[SSN]"
_CREDIT_LABEL = "[CREDIT_CARD]"
_PHONE_LABEL = "[PHONE]"
_DOB_LABEL = "[DOB]"
_MRN_LABEL = "[MRN]"

_ENTITY_LABELS = {
    "EMAIL": _EMAIL_LABEL,
    "SSN": _SSN_LABEL,
    "CREDIT": _CREDIT_LABEL,
    "PHONE": _PHONE_LABEL,
    "DOB": _DOB_LABEL,
    "MRN": _MRN_LABEL,
}


def _luhn_ok(digits):
    """Return True if a string of digits passes the Luhn checksum."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ============================================================
# REDACTOR
# ============================================================

class Redactor:
    """Detects and redacts PII/PHI. mode='label' or 'mask'."""

    def __init__(self, mode="label", mrn_prefix="MRN-"):
        if mode not in ("label", "mask"):
            raise ValueError("mode must be 'label' or 'mask'")
        self.mode = mode
        self.mrn_prefix = mrn_prefix
        esc = re.escape(mrn_prefix)
        self._mrn_re = re.compile(r"\b" + esc + r"\d{4,}\b")

    # -- detection -----------------------------------------------------

    def detect(self, text):
        """Return non-overlapping spans as list of (etype, start, end, value),
        sorted by start. Earlier/longer matches win on overlap."""
        spans = []

        def add(etype, m):
            spans.append((etype, m.start(), m.end(), m.group()))

        for m in self._mrn_re.finditer(text):
            add("MRN", m)
        for m in _EMAIL_RE.finditer(text):
            add("EMAIL", m)
        for m in _SSN_RE.finditer(text):
            add("SSN", m)
        for m in _CREDIT_RE.finditer(text):
            digits = re.sub(r"[ -]", "", m.group())
            if len(digits) == 16 and _luhn_ok(digits):
                add("CREDIT", m)
        for m in _PHONE_RE.finditer(text):
            add("PHONE", m)
        for m in _DOB_ISO_RE.finditer(text):
            add("DOB", m)
        for m in _DOB_US_RE.finditer(text):
            add("DOB", m)

        # Resolve overlaps: prefer earlier start, then longer span.
        spans.sort(key=lambda s: (s[1], -(s[2] - s[1])))
        resolved = []
        last_end = -1
        for etype, start, end, value in spans:
            if start >= last_end:
                resolved.append((etype, start, end, value))
                last_end = end
        return resolved

    # -- redaction -----------------------------------------------------

    def _replacement(self, etype, value):
        if self.mode == "label":
            return _ENTITY_LABELS[etype]
        # mask mode: preserve format, expose only the last 4 of long numbers
        if etype in ("SSN", "CREDIT", "PHONE"):
            digits = [c for c in value if c.isdigit()]
            keep = 4 if len(digits) > 4 else 0
            out = []
            seen = 0
            total_digits = len(digits)
            for c in value:
                if c.isdigit():
                    seen += 1
                    out.append(c if seen > total_digits - keep else "*")
                else:
                    out.append(c)
            return "".join(out)
        return _ENTITY_LABELS[etype]

    def redact(self, text):
        spans = self.detect(text)
        if not spans:
            return text
        out = []
        cursor = 0
        for etype, start, end, value in spans:
            out.append(text[cursor:start])
            out.append(self._replacement(etype, value))
            cursor = end
        out.append(text[cursor:])
        return "".join(out)

    def counts(self, text):
        """Return {etype: n} of detected entities."""
        c = {}
        for etype, _, _, _ in self.detect(text):
            c[etype] = c.get(etype, 0) + 1
        return c


# ============================================================
# ORACLE (independent leak check)
# ============================================================

class RedactionOracle:
    """Independent confirmation that secrets do not survive redaction."""

    @staticmethod
    def secret_survives(secret, redacted_text):
        """True if the raw secret string still appears verbatim in output."""
        return secret in redacted_text

    @staticmethod
    def digit_run_survives(secret, redacted_text):
        """True if the full digit-run of a numeric secret survives anywhere.

        Catches the case where formatting changed but raw digits leaked, e.g.
        '123456789' from '123-45-6789'.
        """
        digits = "".join(c for c in secret if c.isdigit())
        if len(digits) < 4:
            return False
        flat = "".join(c for c in redacted_text if c.isdigit())
        return digits in flat


# ============================================================
# TEETH: frozen PII audits + planted leak/over-redaction defects
# ============================================================

@dataclass(frozen=True)
class PiiAuditCase:
    name: str
    text: str
    mode: str
    expected_counts: tuple[tuple[str, int], ...]
    must_remove: tuple[str, ...]
    forbidden_labels: tuple[str, ...]
    expected_events: tuple[str, ...]


def _count_items(counts):
    return tuple(sorted(counts.items()))


def _all_raw_secrets_gone(secrets, redacted):
    return all(not RedactionOracle.secret_survives(secret, redacted) for secret in secrets)


def _all_digit_runs_gone(secrets, redacted):
    return all(not RedactionOracle.digit_run_survives(secret, redacted) for secret in secrets)


def _no_forbidden_labels(labels, redacted):
    return all(label not in redacted for label in labels)


def _append_if(events, event, condition):
    if condition:
        events.append(event)


PII_AUDIT_CORPUS = (
    PiiAuditCase(
        name="mixed_entities_are_detected_and_removed",
        text=(
            "Patient 123-45-6789 jane.sample@example.com 555-123-4567 "
            "MRN-000123 DOB 1980-04-12 card 4111 1111 1111 1111"
        ),
        mode="label",
        expected_counts=(
            ("CREDIT", 1),
            ("DOB", 1),
            ("EMAIL", 1),
            ("MRN", 1),
            ("PHONE", 1),
            ("SSN", 1),
        ),
        must_remove=(
            "123-45-6789",
            "jane.sample@example.com",
            "555-123-4567",
            "MRN-000123",
            "1980-04-12",
            "4111 1111 1111 1111",
        ),
        forbidden_labels=(),
        expected_events=("counts_match", "raw_secrets_gone", "digit_runs_gone", "idempotent"),
    ),
    PiiAuditCase(
        name="non_luhn_order_number_is_not_card_redacted",
        text="order 1234 5678 9012 3456 shipped",
        mode="label",
        expected_counts=(),
        must_remove=(),
        forbidden_labels=(_CREDIT_LABEL,),
        expected_events=("counts_match", "idempotent", "no_overredaction"),
    ),
    PiiAuditCase(
        name="zip_code_is_not_redacted",
        text="ZIP 90210 area",
        mode="label",
        expected_counts=(),
        must_remove=(),
        forbidden_labels=(_SSN_LABEL, _DOB_LABEL, _PHONE_LABEL, _CREDIT_LABEL),
        expected_events=("counts_match", "idempotent", "no_overredaction"),
    ),
    PiiAuditCase(
        name="mask_mode_hides_full_ssn_digit_run",
        text="SSN 987-65-4321 end",
        mode="mask",
        expected_counts=(("SSN", 1),),
        must_remove=("987-65-4321",),
        forbidden_labels=(),
        expected_events=("counts_match", "raw_secrets_gone", "digit_runs_gone", "idempotent"),
    ),
)


def oracle_pii_audit(case):
    redactor = Redactor(mode=case.mode)
    redacted = redactor.redact(case.text)
    events = []
    _append_if(events, "counts_match", _count_items(redactor.counts(case.text)) == case.expected_counts)
    _append_if(events, "raw_secrets_gone", bool(case.must_remove) and _all_raw_secrets_gone(case.must_remove, redacted))
    _append_if(events, "digit_runs_gone", bool(case.must_remove) and _all_digit_runs_gone(case.must_remove, redacted))
    _append_if(events, "idempotent", redactor.redact(redacted) == redacted)
    _append_if(events, "no_overredaction", bool(case.forbidden_labels) and _no_forbidden_labels(case.forbidden_labels, redacted))
    return tuple(events)


def ssn_blind_pii_auditor(case):
    events = oracle_pii_audit(case)
    expected_entities = {etype for etype, _count in case.expected_counts}
    if "SSN" not in expected_entities:
        return events
    return tuple(e for e in events if e not in {"counts_match", "raw_secrets_gone", "digit_runs_gone"})


def digit_leak_pii_auditor(case):
    events = oracle_pii_audit(case)
    if not case.must_remove:
        return events
    return tuple(e for e in events if e != "digit_runs_gone")


def luhn_blind_overredacts_pii_auditor(case):
    events = oracle_pii_audit(case)
    if _CREDIT_LABEL not in case.forbidden_labels:
        return events
    return tuple(e for e in events if e != "no_overredaction")


def non_idempotent_pii_auditor(case):
    events = oracle_pii_audit(case)
    return tuple(e for e in events if e != "idempotent")


def prove(impl: Callable[[PiiAuditCase], tuple[str, ...]]) -> bool:
    return any(impl(case) != case.expected_events for case in PII_AUDIT_CORPUS)


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_pii_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_pii_audit,
    mutants=(
        Mutant("ssn_blind_pii_auditor", ssn_blind_pii_auditor,
               "misses SSN detection and lets source SSN material survive"),
        Mutant("digit_leak_pii_auditor", digit_leak_pii_auditor,
               "redacts labels but leaves the full numeric digit run recoverable"),
        Mutant("luhn_blind_overredacts_pii_auditor", luhn_blind_overredacts_pii_auditor,
               "treats a non-Luhn order number as a credit card"),
        Mutant("non_idempotent_pii_auditor", non_idempotent_pii_auditor,
               "changes already-redacted text on a second pass"),
    ),
    corpus_size=len(PII_AUDIT_CORPUS),
    kind="auditor",
    notes="Frozen PII redaction corpus for entity counts, leak checks, over-redaction, and idempotency.",
)


# ============================================================
# MOCK HTTP SERVER
# ============================================================

class RedactionHandler(BaseHTTPRequestHandler):
    redactor = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, code=400)
            return
        text = payload.get("text", "")
        redacted = RedactionHandler.redactor.redact(text)
        self._json({
            "redacted": redacted,
            "counts": RedactionHandler.redactor.counts(text),
        })

    def _json(self, obj, code=200):
        resp = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19410):
    RedactionHandler.redactor = Redactor(mode="label")
    server = ThreadingHTTPServer(("127.0.0.1", port), RedactionHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

class RedactTestResult:
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n        {self.detail}"
        return msg


def run_all_scenarios(verbose=False):
    results = []

    def check(name, cond, detail=""):
        r = RedactTestResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    label = Redactor(mode="label")
    mask = Redactor(mode="mask")

    # 1. SSN removed in label mode
    out = label.redact("Patient SSN is 123-45-6789 on file.")
    check("1. SSN labelled and digits gone",
          _SSN_LABEL in out and not RedactionOracle.digit_run_survives("123-45-6789", out),
          out)

    # 2. Email removed
    out = label.redact("Contact jane.doe@example.com please.")
    check("2. Email redacted", _EMAIL_LABEL in out and "jane.doe@example.com" not in out, out)

    # 3. Phone variants
    samples = ["(555) 123-4567", "555-123-4567", "+1 555 123 4567"]
    ok3 = all(_PHONE_LABEL in label.redact("call " + s) for s in samples)
    check("3. Phone formats all redacted", ok3,
          [label.redact(s) for s in samples])

    # 4. Luhn-valid credit card redacted
    out = label.redact("card 4111 1111 1111 1111 charged")
    check("4. Valid (Luhn) card redacted",
          _CREDIT_LABEL in out and not RedactionOracle.digit_run_survives("4111111111111111", out),
          out)

    # 5. Non-Luhn 16-digit number NOT redacted as a card (over-redaction guard)
    out = label.redact("order 1234 5678 9012 3456 shipped")
    check("5. Non-Luhn 16-digit not flagged as card",
          _CREDIT_LABEL not in out, out)

    # 6. Bare ZIP not redacted (over-redaction guard)
    out = label.redact("ZIP 90210 area")
    check("6. ZIP code 90210 untouched", "90210" in out and "[" not in out, out)

    # 7. MRN with configured prefix redacted
    out = label.redact("see MRN-000123 chart")
    check("7. MRN redacted", _MRN_LABEL in out and "MRN-000123" not in out, out)

    # 8. DOB ISO + US both caught
    out = label.redact("DOB 1980-04-12 and alt 04/12/1980")
    check("8. Both DOB formats redacted",
          out.count(_DOB_LABEL) == 2, out)

    # 9. Idempotency
    src = "SSN 123-45-6789 email a@b.co phone 555-123-4567"
    once = label.redact(src)
    twice = label.redact(once)
    check("9. redact is idempotent", once == twice, f"{once!r} != {twice!r}")

    # 10. Clean text unchanged
    clean = "The quick brown fox jumps over 7 lazy dogs."
    check("10. Clean text unchanged", label.redact(clean) == clean, label.redact(clean))

    # 11. Mask mode preserves last 4 of SSN
    out = mask.redact("SSN 123-45-6789 end")
    check("11. Mask mode keeps last4 of SSN",
          "6789" in out and "123" not in out and "***-**-6789" in out, out)

    # 12. Multiple entities in one line, counts correct
    src = "jane@x.io 555-123-4567 123-45-6789 MRN-9999"
    counts = label.counts(src)
    check("12. Counts: 1 email,1 phone,1 ssn,1 mrn",
          counts.get("EMAIL") == 1 and counts.get("PHONE") == 1 and
          counts.get("SSN") == 1 and counts.get("MRN") == 1, counts)

    # 13. No raw secret survives a mixed paragraph (oracle sweep)
    secrets = ["987-65-4321", "bob@hospital.org", "4111 1111 1111 1111"]
    para = f"Bob {secrets[0]} reached at {secrets[1]} paid {secrets[2]}."
    red = label.redact(para)
    survived = [s for s in secrets if RedactionOracle.secret_survives(s, red)]
    check("13. No raw secret survives", survived == [], f"survived={survived}")

    # 14. Adjacent entities both redacted (no swallow)
    out = label.redact("123-45-6789,jane@x.io")
    check("14. Adjacent SSN+email both redacted",
          _SSN_LABEL in out and _EMAIL_LABEL in out, out)

    return results


def _emit_self_test_report(results, as_json=False):
    report = Report("security/pii_redaction")
    for result in results:
        report.record(result.name, result.passed, detail=result.detail)
    # The correct oracle, called BY ITS MODULE-GLOBAL NAME, must reproduce every
    # frozen auditor literal exactly (the non-circular corpus teeth are judged
    # against). The vacuity gate neuters oracle_pii_audit module-globally; this
    # loop is what then goes red.
    for case in PII_AUDIT_CORPUS:
        report.add(
            f"oracle_pii_audit:{case.name}",
            list(case.expected_events),
            list(oracle_pii_audit(case)),
        )
    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


def _run_self_test(verbose=False, as_json=False):
    """Gate-callable self-test: legacy scenarios + the frozen oracle corpus + teeth.

    The vacuity gate calls this with no arguments. The oracle_pii_audit(case)
    call above is module-global, so the gate's neuter mutates its return and the
    report's expected/actual comparison fails loudly (non-vacuous).
    """
    return _emit_self_test_report(run_all_scenarios(verbose=verbose), as_json=as_json)


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="pii_redaction_test_harness",
        description="PII/PHI redaction harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only (POST /redact {text})")
    p.add_argument("--port", type=int, default=19410,
                   help="Mock server port (default: 19410)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        return serve_mock_server_until_interrupt(start_mock_server, args.port,
                                                "PII redaction mock server")

    if args.self_test:
        return emit_legacy_self_test("PII REDACTION TEST HARNESS",
                                     run_all_scenarios,
                                     args.verbose,
                                     _emit_self_test_report)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
