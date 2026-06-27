#!/usr/bin/env python3
"""jwt_test_harness.py — JWT (HS256) Verification Harness (2026)
================================================================================
Pure-Python (ZERO dependencies) harness for testing JSON Web Token signing and,
more importantly, *verification* — including the classic auth-bypass attacks.

Implements HS256 only (HMAC-SHA256) using hmac/hashlib/base64 from stdlib.

Hotspots / attacks exercised:
  - alg="none": an attacker strips the signature and sets alg=none. MUST reject.
  - alg confusion: token header advertises an algorithm the verifier did not
    ask for. MUST reject (we only accept the algorithm passed to verify()).
  - Tampered payload: any change to header/payload invalidates the signature.
  - exp / nbf / iat with leeway and an INJECTED `now` for deterministic tests.
  - Constant-time signature comparison (hmac.compare_digest).
  - Required-claim enforcement (e.g. must contain "sub").

A failed verify() never raises on attacker-controlled input; it returns a
VerifyResult(ok=False, reason=...) so callers branch on a value, not a stack
trace.

Port: 19400

Usage:
  python jwt_test_harness.py --self-test
  python jwt_test_harness.py --mock-server --port 19400
  python jwt_test_harness.py --self-test --verbose
"""

import argparse
import base64
import hashlib
import hmac
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path as _Path
from urllib.parse import parse_qs, urlparse

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
# BASE64URL
# ============================================================

def b64url_encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + b"=" * pad)


# ============================================================
# SIGN / ENCODE
# ============================================================

def _sign_hs256(signing_input, key):
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, signing_input, hashlib.sha256).digest()


def encode(payload, key, alg="HS256", header_extra=None):
    """Produce a signed JWT string. alg='none' produces an unsigned token
    (for negative testing only)."""
    header = {"alg": alg, "typ": "JWT"}
    if header_extra:
        header.update(header_extra)
    h = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True))
    p = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    signing_input = f"{h}.{p}".encode("ascii")
    if alg == "none":
        return f"{h}.{p}."
    if alg != "HS256":
        raise ValueError(f"unsupported alg for encode: {alg}")
    sig = _sign_hs256(signing_input, key)
    return f"{h}.{p}.{b64url_encode(sig)}"


# ============================================================
# VERIFY
# ============================================================

class VerifyResult:
    def __init__(self, ok, reason="", payload=None):
        self.ok = ok
        self.reason = reason
        self.payload = payload

    def __repr__(self):
        return f"VerifyResult(ok={self.ok}, reason={self.reason!r})"


def verify(token, key, now, algorithms=("HS256",), leeway=0,
           required_claims=()):
    """Verify a JWT. Returns VerifyResult; never raises on bad input.

    `now` is an integer epoch time (injected for determinism).
    `algorithms` is the allow-list the *server* trusts — the token header
    cannot widen it.
    """
    if not isinstance(token, str):
        return VerifyResult(False, "token-not-string")
    parts = token.split(".")
    if len(parts) != 3:
        return VerifyResult(False, "malformed-token")
    h_b64, p_b64, sig_b64 = parts

    # Parse header
    try:
        header = json.loads(b64url_decode(h_b64))
    except Exception:
        return VerifyResult(False, "bad-header")
    alg = header.get("alg")

    # --- attack guards ---
    if alg == "none":
        return VerifyResult(False, "alg-none-rejected")
    if alg not in algorithms:
        return VerifyResult(False, f"alg-not-allowed:{alg}")
    if alg != "HS256":
        return VerifyResult(False, f"unsupported-alg:{alg}")
    if sig_b64 == "":
        return VerifyResult(False, "empty-signature")

    # --- signature check (constant time) ---
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected = _sign_hs256(signing_input, key)
    try:
        provided = b64url_decode(sig_b64)
    except Exception:
        return VerifyResult(False, "bad-signature-encoding")
    if not hmac.compare_digest(expected, provided):
        return VerifyResult(False, "signature-mismatch")

    # --- payload + claims ---
    try:
        payload = json.loads(b64url_decode(p_b64))
    except Exception:
        return VerifyResult(False, "bad-payload")
    if not isinstance(payload, dict):
        return VerifyResult(False, "payload-not-object")

    if "exp" in payload and now > _as_int(payload["exp"]) + leeway:
        return VerifyResult(False, "token-expired")
    if "nbf" in payload and now + leeway < _as_int(payload["nbf"]):
        return VerifyResult(False, "token-not-yet-valid")
    if "iat" in payload and now + leeway < _as_int(payload["iat"]):
        return VerifyResult(False, "iat-in-future")

    for claim in required_claims:
        if claim not in payload:
            return VerifyResult(False, f"missing-claim:{claim}")

    return VerifyResult(True, "ok", payload)


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ============================================================
# ATTACK HELPERS (build malicious tokens for negative tests)
# ============================================================

def forge_alg_none(token):
    """Take a valid token, rewrite its header to alg=none, drop the signature."""
    h_b64, p_b64, _ = token.split(".")
    header = json.loads(b64url_decode(h_b64))
    header["alg"] = "none"
    new_h = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True))
    return f"{new_h}.{p_b64}."


def forge_alg_swap(token, fake_alg="HS384"):
    """Advertise a different algorithm in the header, keep the old signature."""
    h_b64, p_b64, sig = token.split(".")
    header = json.loads(b64url_decode(h_b64))
    header["alg"] = fake_alg
    new_h = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True))
    return f"{new_h}.{p_b64}.{sig}"


def tamper_payload(token, mutate):
    """Apply mutate(payload_dict) and re-encode WITHOUT re-signing."""
    h_b64, p_b64, sig = token.split(".")
    payload = json.loads(b64url_decode(p_b64))
    mutate(payload)
    new_p = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return f"{h_b64}.{new_p}.{sig}"


# ============================================================
# MOCK HTTP SERVER
# ============================================================

class JwtHandler(BaseHTTPRequestHandler):
    key = "test-secret-key"
    now = 1_700_000_000

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/issue":
            sub = params.get("sub", ["alice"])[0]
            ttl = int(params.get("ttl", ["3600"])[0])
            tok = encode({"sub": sub, "iat": JwtHandler.now,
                          "exp": JwtHandler.now + ttl}, JwtHandler.key)
            self._json({"token": tok})
            return
        if parsed.path == "/verify":
            tok = params.get("token", [""])[0]
            res = verify(tok, JwtHandler.key, now=JwtHandler.now,
                         required_claims=("sub",))
            self._json({"ok": res.ok, "reason": res.reason},
                       code=200 if res.ok else 401)
            return
        self.send_response(404)
        self.end_headers()

    def _json(self, obj, code=200):
        resp = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19400):
    server = ThreadingHTTPServer(("127.0.0.1", port), JwtHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

NOW = 1_700_000_000
KEY = "correct-horse-battery-staple"


# ============================================================
# TEETH: frozen verifier audits + planted JWT bypass defects
# ============================================================

@dataclass(frozen=True)
class JwtAuditCase:
    name: str
    token: str
    key: str
    now: int
    algorithms: tuple[str, ...]
    leeway: int
    required_claims: tuple[str, ...]
    expected_events: tuple[str, ...]


def _fresh_token(**over):
    payload = {"sub": "alice", "iat": NOW, "exp": NOW + 3600}
    payload.update(over)
    return encode(payload, KEY)


def _jwt_event(result):
    if result.ok:
        return ("ok",)
    reason = result.reason.split(":", 1)[0]
    return (f"reject:{reason}",)


JWT_AUDIT_CORPUS = (
    JwtAuditCase(
        name="valid_required_claim_token_verifies",
        token=_fresh_token(),
        key=KEY,
        now=NOW,
        algorithms=("HS256",),
        leeway=0,
        required_claims=("sub",),
        expected_events=("ok",),
    ),
    JwtAuditCase(
        name="alg_none_token_is_rejected",
        token=forge_alg_none(_fresh_token()),
        key=KEY,
        now=NOW,
        algorithms=("HS256",),
        leeway=0,
        required_claims=(),
        expected_events=("reject:alg-none-rejected",),
    ),
    JwtAuditCase(
        name="alg_swap_outside_allowlist_is_rejected",
        token=forge_alg_swap(_fresh_token(), "HS384"),
        key=KEY,
        now=NOW,
        algorithms=("HS256",),
        leeway=0,
        required_claims=(),
        expected_events=("reject:alg-not-allowed",),
    ),
    JwtAuditCase(
        name="tampered_payload_is_signature_mismatch",
        token=tamper_payload(_fresh_token(), lambda p: p.update({"sub": "admin"})),
        key=KEY,
        now=NOW,
        algorithms=("HS256",),
        leeway=0,
        required_claims=(),
        expected_events=("reject:signature-mismatch",),
    ),
    JwtAuditCase(
        name="expired_token_is_rejected",
        token=_fresh_token(exp=NOW - 1),
        key=KEY,
        now=NOW,
        algorithms=("HS256",),
        leeway=0,
        required_claims=(),
        expected_events=("reject:token-expired",),
    ),
    JwtAuditCase(
        name="missing_required_claim_is_rejected",
        token=encode({"iat": NOW, "exp": NOW + 60}, KEY),
        key=KEY,
        now=NOW,
        algorithms=("HS256",),
        leeway=0,
        required_claims=("sub",),
        expected_events=("reject:missing-claim",),
    ),
)


def oracle_jwt_audit(case):
    result = verify(
        case.token,
        case.key,
        now=case.now,
        algorithms=case.algorithms,
        leeway=case.leeway,
        required_claims=case.required_claims,
    )
    return _jwt_event(result)


def alg_none_accepting_jwt_auditor(case):
    if case.name != "alg_none_token_is_rejected":
        return oracle_jwt_audit(case)
    return ("ok",)


def algorithm_allowlist_blind_jwt_auditor(case):
    if case.name != "alg_swap_outside_allowlist_is_rejected":
        return oracle_jwt_audit(case)
    return ("ok",)


def signature_blind_jwt_auditor(case):
    if case.name != "tampered_payload_is_signature_mismatch":
        return oracle_jwt_audit(case)
    return ("ok",)


def time_claim_blind_jwt_auditor(case):
    if case.name != "expired_token_is_rejected":
        return oracle_jwt_audit(case)
    return ("ok",)


def required_claim_blind_jwt_auditor(case):
    if case.name != "missing_required_claim_is_rejected":
        return oracle_jwt_audit(case)
    return ("ok",)


def prove(impl: Callable[[JwtAuditCase], tuple[str, ...]]) -> bool:
    return any(impl(case) != case.expected_events for case in JWT_AUDIT_CORPUS)


TEETH = Teeth(
    prove=prove,
    oracle=oracle_jwt_audit,
    mutants=(
        Mutant("alg_none_accepting_jwt_auditor", alg_none_accepting_jwt_auditor,
               "accepts an unsigned alg=none token"),
        Mutant("algorithm_allowlist_blind_jwt_auditor", algorithm_allowlist_blind_jwt_auditor,
               "ignores the server-side JWT algorithm allow-list"),
        Mutant("signature_blind_jwt_auditor", signature_blind_jwt_auditor,
               "trusts a tampered payload without validating the signature"),
        Mutant("time_claim_blind_jwt_auditor", time_claim_blind_jwt_auditor,
               "ignores expiration time claims"),
        Mutant("required_claim_blind_jwt_auditor", required_claim_blind_jwt_auditor,
               "accepts a token missing a required claim"),
    ),
    corpus_size=len(JWT_AUDIT_CORPUS),
    kind="auditor",
    notes="Frozen JWT verifier corpus for alg handling, signatures, time claims, and required claims.",
)

VACUITY_TARGETS = ["oracle_jwt_audit"]


def _run_self_test(verbose=False, as_json=False):
    """Gate-callable self-test: assert the module-global oracle against the frozen corpus.

    Calls ``oracle_jwt_audit`` BY ITS MODULE-GLOBAL NAME so the vacuity gate's
    neuter (which mutates that symbol's return) turns this self-test red. The
    expected side is ``case.expected_events`` (a frozen literal on the corpus),
    so the comparison is non-circular.
    """
    report = Report("security/jwt")
    for case in JWT_AUDIT_CORPUS:
        report.add(
            f"oracle:{case.name}",
            list(case.expected_events),
            list(oracle_jwt_audit(case)),
        )
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


class JwtTestResult:
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
        r = JwtTestResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    def fresh(**over):
        payload = {"sub": "alice", "iat": NOW, "exp": NOW + 3600}
        payload.update(over)
        return encode(payload, KEY)

    # 1. Valid token verifies
    res = verify(fresh(), KEY, now=NOW, required_claims=("sub",))
    check("1. Valid token verifies", res.ok and res.payload["sub"] == "alice", res.reason)

    # 2. Wrong key rejected
    res = verify(fresh(), "wrong-key", now=NOW)
    check("2. Wrong signing key rejected",
          not res.ok and res.reason == "signature-mismatch", res.reason)

    # 3. alg=none attack rejected
    res = verify(forge_alg_none(fresh()), KEY, now=NOW)
    check("3. alg=none attack rejected",
          not res.ok and res.reason == "alg-none-rejected", res.reason)

    # 4. alg-swap (not in allow-list) rejected
    res = verify(forge_alg_swap(fresh(), "HS384"), KEY, now=NOW)
    check("4. alg swap to HS384 rejected",
          not res.ok and res.reason.startswith("alg-not-allowed"), res.reason)

    # 5. Tampered payload (privilege escalation) rejected
    forged = tamper_payload(fresh(), lambda p: p.update({"sub": "admin"}))
    res = verify(forged, KEY, now=NOW)
    check("5. Tampered sub->admin rejected",
          not res.ok and res.reason == "signature-mismatch", res.reason)

    # 6. Expired token rejected
    res = verify(fresh(exp=NOW - 1), KEY, now=NOW)
    check("6. Expired token rejected",
          not res.ok and res.reason == "token-expired", res.reason)

    # 7. Expiry honors leeway
    res = verify(fresh(exp=NOW - 5), KEY, now=NOW, leeway=10)
    check("7. Expiry within leeway accepted", res.ok, res.reason)

    # 8. nbf in the future rejected
    res = verify(fresh(nbf=NOW + 100), KEY, now=NOW)
    check("8. not-before in future rejected",
          not res.ok and res.reason == "token-not-yet-valid", res.reason)

    # 9. iat in the future rejected
    res = verify(fresh(iat=NOW + 100), KEY, now=NOW)
    check("9. issued-at in future rejected",
          not res.ok and res.reason == "iat-in-future", res.reason)

    # 10. Missing required claim rejected
    tok = encode({"iat": NOW, "exp": NOW + 60}, KEY)  # no sub
    res = verify(tok, KEY, now=NOW, required_claims=("sub",))
    check("10. Missing required claim 'sub' rejected",
          not res.ok and res.reason == "missing-claim:sub", res.reason)

    # 11. Malformed token (2 segments) rejected
    res = verify("aaa.bbb", KEY, now=NOW)
    check("11. Malformed 2-part token rejected",
          not res.ok and res.reason == "malformed-token", res.reason)

    # 12. Empty signature segment rejected
    h_b64, p_b64, _ = fresh().split(".")
    res = verify(f"{h_b64}.{p_b64}.", KEY, now=NOW)
    check("12. Empty signature rejected",
          not res.ok and res.reason == "empty-signature", res.reason)

    # 13. Roundtrip base64url of binary-ish payload
    tok = encode({"sub": "u", "data": "a/b+c=", "exp": NOW + 10, "iat": NOW}, KEY)
    res = verify(tok, KEY, now=NOW)
    check("13. base64url roundtrip ok",
          res.ok and res.payload["data"] == "a/b+c=", res.reason)

    # 14. Non-string token handled gracefully
    res = verify(None, KEY, now=NOW)
    check("14. None token handled (no crash)",
          not res.ok and res.reason == "token-not-string", res.reason)

    return results


def _emit_self_test_report(results):
    report = Report("security/jwt")
    for result in results:
        report.record(result.name, result.passed, detail=result.detail)
    for case in JWT_AUDIT_CORPUS:
        report.add(
            f"oracle_jwt_audit:{case.name}",
            list(case.expected_events),
            list(oracle_jwt_audit(case)),
        )
    report.assert_teeth(TEETH)
    return report.emit()


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="jwt_test_harness",
        description="JWT (HS256) verification harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only (/issue, /verify)")
    p.add_argument("--port", type=int, default=19400,
                   help="Mock server port (default: 19400)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        return serve_mock_server_until_interrupt(start_mock_server, args.port,
                                                "JWT mock server")

    if args.self_test:
        return emit_legacy_self_test("JWT TEST HARNESS",
                                     run_all_scenarios,
                                     args.verbose,
                                     _emit_self_test_report)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
