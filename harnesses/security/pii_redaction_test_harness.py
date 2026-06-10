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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

_ENTITY_LABELS = {
    "EMAIL": "[EMAIL]",
    "SSN": "[SSN]",
    "CREDIT": "[CREDIT_CARD]",
    "PHONE": "[PHONE]",
    "DOB": "[DOB]",
    "MRN": "[MRN]",
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
          "[SSN]" in out and not RedactionOracle.digit_run_survives("123-45-6789", out),
          out)

    # 2. Email removed
    out = label.redact("Contact jane.doe@example.com please.")
    check("2. Email redacted", "[EMAIL]" in out and "jane.doe@example.com" not in out, out)

    # 3. Phone variants
    samples = ["(555) 123-4567", "555-123-4567", "+1 555 123 4567"]
    ok3 = all("[PHONE]" in label.redact("call " + s) for s in samples)
    check("3. Phone formats all redacted", ok3,
          [label.redact(s) for s in samples])

    # 4. Luhn-valid credit card redacted
    out = label.redact("card 4111 1111 1111 1111 charged")
    check("4. Valid (Luhn) card redacted",
          "[CREDIT_CARD]" in out and not RedactionOracle.digit_run_survives("4111111111111111", out),
          out)

    # 5. Non-Luhn 16-digit number NOT redacted as a card (over-redaction guard)
    out = label.redact("order 1234 5678 9012 3456 shipped")
    check("5. Non-Luhn 16-digit not flagged as card",
          "[CREDIT_CARD]" not in out, out)

    # 6. Bare ZIP not redacted (over-redaction guard)
    out = label.redact("ZIP 90210 area")
    check("6. ZIP code 90210 untouched", "90210" in out and "[" not in out, out)

    # 7. MRN with configured prefix redacted
    out = label.redact("see MRN-000123 chart")
    check("7. MRN redacted", "[MRN]" in out and "MRN-000123" not in out, out)

    # 8. DOB ISO + US both caught
    out = label.redact("DOB 1980-04-12 and alt 04/12/1980")
    check("8. Both DOB formats redacted",
          out.count("[DOB]") == 2, out)

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
          "[SSN]" in out and "[EMAIL]" in out, out)

    return results


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
    import time as _time
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        server = start_mock_server(args.port)
        print(f"  PII redaction mock server on http://127.0.0.1:{args.port} â€” Ctrl+C to stop")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
            server.server_close()
        return

    if args.self_test:
        print("\n  PII REDACTION TEST HARNESS â€” self-test mode")
        print("  " + "=" * 52)
        results = run_all_scenarios(verbose=args.verbose)
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        if not args.verbose:
            for r in results:
                print(r)
        print()
        print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
        print()
        sys.exit(0 if failed == 0 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
