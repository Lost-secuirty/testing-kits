#!/usr/bin/env python3
"""
crypto_test_harness.py — Cryptographic-primitive misuse harness.
================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth`` contract
is itself pure stdlib).

Complements jwt_test_harness.py (token crypto) by catching the *primitive*
misuse that dominates Cryptographic Failures in AI-generated code. Maps to
OWASP Top 10 A04:2025 Cryptographic Failures.

Hotspots / attacks exercised:
- Password hashing with fast/broken algorithms (MD5/SHA1/unsalted SHA-256,
  low-iteration PBKDF2) vs bcrypt/scrypt/argon2/PBKDF2-with-enough-rounds. (CWE-327/916)
- Weak randomness for security tokens: random.* / time-based vs secrets / os.urandom. (CWE-330/338)
- Weak/legacy ciphers and modes: DES, 3DES, RC4, AES-ECB, static/reused IV. (CWE-327/329)
- Hard-coded keys / secrets / tokens in source. (CWE-798)
- Missing transport verification: verify=False, CERT_NONE, hostname check off. (CWE-295/297)

A checker never raises on hostile input; it returns (flagged: bool, reason: str).

TEETH: the harness's own crypto auditor (oracle_crypto_audit) judged against a
FROZEN corpus of (kind, payload, should_flag) literals. Each planted Mutant is a
realistic cryptographic-control defect (a password checker that accepts any
salted general-purpose hash, a cipher checker that misses ECB mode, a TLS
checker that only looks at verify and ignores hostname/cert_reqs). prove()
compares each auditor to the frozen should_flag literal — never to the oracle —
so it is non-circular and deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/crypto_test_harness.py --self-test
    python harnesses/security/crypto_test_harness.py --json
    python harnesses/security/crypto_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class CryptoFinding:
    check_name: str
    severity: str  # CRITICAL / HIGH / MEDIUM / LOW
    description: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"Invalid severity: {self.severity}")


@dataclass
class CryptoReport:
    findings: list[CryptoFinding] = field(default_factory=list)

    def add(self, finding: CryptoFinding) -> None:
        self.findings.append(finding)

    def is_clean(self) -> bool:
        return len(self.findings) == 0

    def __len__(self) -> int:
        return len(self.findings)


# ---------------------------------------------------------------------------
# PasswordHashChecker (CWE-327 / CWE-916)
# ---------------------------------------------------------------------------

# General-purpose / broken digests — wrong for password storage even if salted.
_FAST_OR_BROKEN_HASHES = {
    "md5", "sha1", "sha224", "sha256", "sha384", "sha512",
    "sha3_256", "sha3_512", "blake2b", "blake2s", "crc32",
}
_STRONG_KDFS = {"bcrypt", "scrypt", "argon2", "argon2i", "argon2d", "argon2id"}
_ITERATED_KDFS = {"pbkdf2", "pbkdf2_hmac"}
PBKDF2_MIN_ITERATIONS = 100_000  # conservative modern baseline


class PasswordHashChecker:
    """Validate a password-storage choice. check() -> (weak, reason)."""

    def check(self, algorithm: str, *, salted: bool = False,
              iterations: int | None = None) -> tuple[bool, str]:
        algo = (algorithm or "").lower().strip()
        if not algo:
            return True, "No hashing algorithm specified"
        if algo in _STRONG_KDFS:
            return False, f"{algo} is an acceptable password KDF"
        if algo in _ITERATED_KDFS:
            if iterations is None or iterations < PBKDF2_MIN_ITERATIONS:
                return True, (f"PBKDF2 iterations too low ({iterations}); "
                              f"use >= {PBKDF2_MIN_ITERATIONS} (CWE-916)")
            return False, f"PBKDF2 with {iterations} iterations is acceptable"
        if algo in _FAST_OR_BROKEN_HASHES:
            reason = f"{algo} is a general-purpose/fast hash, unsuitable for passwords (CWE-327)"
            if not salted:
                reason += "; also unsalted (CWE-759)"
            return True, reason
        return True, f"Unknown hashing algorithm '{algorithm}' — treat as unsafe"


# ---------------------------------------------------------------------------
# RandomnessChecker (CWE-330 / CWE-338)
# ---------------------------------------------------------------------------

_WEAK_RANDOM_BASES = {"random", "time", "datetime", "id", "uuid1", "getpid", "os.getpid"}
_STRONG_RANDOM_MARKERS = ("secrets", "os.urandom", "urandom", "ssl.rand")


class RandomnessChecker:
    """Validate the source of security-sensitive randomness."""

    def check_source(self, source: str) -> tuple[bool, str]:
        s = (source or "").lower().strip()
        if not s:
            return True, "No randomness source specified"
        if any(marker in s for marker in _STRONG_RANDOM_MARKERS):
            return False, f"{source} is a CSPRNG source"
        base = s.split(".")[0].split("(")[0]
        if base in _WEAK_RANDOM_BASES or s in _WEAK_RANDOM_BASES:
            return True, f"{source} is not cryptographically secure (CWE-330)"
        return True, f"{source}: not a recognized CSPRNG — treat as weak (CWE-330)"

    def check_token_entropy(self, token: bytes, min_bits: int = 128) -> tuple[bool, str]:
        """Cheap entropy floor: length and distinct-byte sanity, no statistics claims."""
        if not isinstance(token, (bytes, bytearray)):
            return True, "Token is not bytes"
        bit_len = len(token) * 8
        if bit_len < min_bits:
            return True, f"Token has {bit_len} bits, below floor of {min_bits} (CWE-331)"
        if len(set(token)) <= 1:
            return True, "Token bytes are constant — no entropy (CWE-330)"
        return False, f"Token length {bit_len} bits meets the {min_bits}-bit floor"


# ---------------------------------------------------------------------------
# CipherChecker (CWE-327 / CWE-329)
# ---------------------------------------------------------------------------

_WEAK_CIPHERS = {"des", "3des", "tripledes", "rc4", "rc2", "blowfish", "arc4"}
_WEAK_MODES = {"ecb"}


class CipherChecker:
    """Validate a symmetric cipher/mode choice."""

    def check(self, algorithm: str, mode: str = "",
              iv_reused: bool = False) -> tuple[bool, str]:
        a = (algorithm or "").lower().strip()
        m = (mode or "").lower().strip()
        if a in _WEAK_CIPHERS:
            return True, f"{algorithm} is a weak/legacy cipher (CWE-327)"
        if m in _WEAK_MODES:
            return True, f"{mode} mode leaks plaintext patterns (CWE-327)"
        if iv_reused:
            return True, "Static/reused IV or nonce (CWE-329/CWE-330)"
        return False, f"{algorithm}/{mode or 'n/a'} acceptable"


# ---------------------------------------------------------------------------
# HardcodedSecretScanner (CWE-798)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "CRITICAL", "AWS access key id"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
     "CRITICAL", "Embedded private key"),
    (re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\b"
                r"\s*[:=]\s*['\"][^'\"\n]{6,}['\"]"),
     "HIGH", "Hard-coded credential literal"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9\-_.=]{20,}"), "HIGH", "Hard-coded bearer token"),
]


class HardcodedSecretScanner:
    """Scan source text for embedded secrets. Env-sourced values are not flagged."""

    def scan(self, source_text: str) -> list[CryptoFinding]:
        report = CryptoReport()
        text = source_text or ""
        for pattern, severity, label in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)
                if len(snippet) > 48:
                    snippet = snippet[:24] + "...(redacted)"
                report.add(CryptoFinding("HardcodedSecretScanner", severity, label, snippet))
        return report.findings


# ---------------------------------------------------------------------------
# TLSConfigChecker (CWE-295 / CWE-297)
# ---------------------------------------------------------------------------

class TLSConfigChecker:
    """Validate TLS client verification settings."""

    def check(self, *, verify: bool = True, check_hostname: bool = True,
              cert_reqs: str = "CERT_REQUIRED") -> tuple[bool, str]:
        if verify is False:
            return True, "TLS verification disabled (verify=False) (CWE-295)"
        if str(cert_reqs).upper() in {"CERT_NONE", "0", "NONE"}:
            return True, "cert_reqs=CERT_NONE disables certificate validation (CWE-295)"
        if check_hostname is False:
            return True, "Hostname verification disabled (CWE-297)"
        return False, "TLS certificate and hostname verification enabled"


_pw = PasswordHashChecker()
_rng = RandomnessChecker()
_cipher = CipherChecker()
_scanner = HardcodedSecretScanner()
_tls = TLSConfigChecker()

_SAFE_SOURCE = 'API_KEY = os.environ["API_KEY"]\nDB_PASSWORD = get_secret("db")\n'
_BAD_SOURCE = 'API_KEY = "AKIAIOSFODNN7EXAMPLE"\npassword = "hunter2hunter2"\n'  # allowlist secret


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
# TEETH: the crypto auditor judged against a frozen literal corpus.
# kind = auditor. The oracle dispatches each case to the correct checker and
# returns whether the crypto choice should be FLAGGED. Each Mutant is a faithful
# planted defect. prove() compares to the frozen should_flag literal only.
# ===========================================================================

@dataclass(frozen=True)
class CryptoCase:
    """One frozen crypto-audit fixture. ``payload`` is the checker's positional/keyword args."""
    name: str
    kind: str  # "password" | "random_source" | "token_entropy" | "cipher" | "secret" | "tls"
    payload: tuple
    should_flag: bool


# Frozen corpus. should_flag is the independent ground truth (hand-pinned), never
# read back from a checker. Includes the discriminators each mutant gets wrong.
CRYPTO_CORPUS: tuple[CryptoCase, ...] = (
    # password hashing: payload = (algorithm, salted, iterations)
    CryptoCase("argon2id_salted", "password", ("argon2id", True, None), False),
    CryptoCase("bcrypt_salted", "password", ("bcrypt", True, None), False),
    CryptoCase("pbkdf2_high_iters", "password", ("pbkdf2", True, 200_000), False),
    CryptoCase("md5_unsalted", "password", ("md5", False, None), True),
    # Discriminator: a SALTED general-purpose hash is still wrong for passwords.
    # The "accept any salted hash" mutant wrongly clears this.
    CryptoCase("sha256_salted", "password", ("sha256", True, None), True),
    CryptoCase("pbkdf2_low_iters", "password", ("pbkdf2", True, 1_000), True),
    # randomness source: payload = (source,)
    CryptoCase("secrets_source", "random_source", ("secrets.token_hex",), False),
    CryptoCase("urandom_source", "random_source", ("os.urandom",), False),
    CryptoCase("random_source", "random_source", ("random.randint",), True),
    CryptoCase("time_source", "random_source", ("time.time",), True),
    # token entropy: payload = (token_bytes, min_bits)
    CryptoCase("entropy_ok", "token_entropy", (bytes(range(20)), 128), False),
    CryptoCase("entropy_short", "token_entropy", (b"\x01\x02\x03\x04", 128), True),
    # cipher/mode: payload = (algorithm, mode, iv_reused)
    CryptoCase("aes_gcm", "cipher", ("AES", "GCM", False), False),
    CryptoCase("des_cbc", "cipher", ("DES", "CBC", False), True),
    # Discriminator: AES with ECB mode. The "weak cipher only" mutant ignores mode
    # and wrongly clears this.
    CryptoCase("aes_ecb", "cipher", ("AES", "ECB", False), True),
    CryptoCase("aes_reused_iv", "cipher", ("AES", "CTR", True), True),
    # hard-coded secret scan: payload = (source_text,)
    CryptoCase("env_sourced_secret", "secret", (_SAFE_SOURCE,), False),
    CryptoCase("hardcoded_aws_key", "secret", (_BAD_SOURCE,), True),
    # TLS config: payload = (verify, check_hostname, cert_reqs)
    CryptoCase("tls_verified", "tls", (True, True, "CERT_REQUIRED"), False),
    CryptoCase("tls_no_verify", "tls", (False, True, "CERT_REQUIRED"), True),
    # Discriminator: verify=True but hostname check off. The "verify-only" mutant
    # wrongly clears this.
    CryptoCase("tls_hostname_off", "tls", (True, False, "CERT_REQUIRED"), True),
    CryptoCase("tls_cert_none", "tls", (True, True, "CERT_NONE"), True),
)


def oracle_crypto_audit(case: CryptoCase) -> bool:
    """Correct verdict: does this crypto choice exhibit a cryptographic failure (flag it)?

    Pure over its argument — dispatches to the harness's own checkers, no I/O.
    """
    if case.kind == "password":
        algorithm, salted, iterations = case.payload
        return _pw.check(algorithm, salted=salted, iterations=iterations)[0]
    if case.kind == "random_source":
        (source,) = case.payload
        return _rng.check_source(source)[0]
    if case.kind == "token_entropy":
        token, min_bits = case.payload
        return _rng.check_token_entropy(token, min_bits=min_bits)[0]
    if case.kind == "cipher":
        algorithm, mode, iv_reused = case.payload
        return _cipher.check(algorithm, mode, iv_reused=iv_reused)[0]
    if case.kind == "secret":
        (source_text,) = case.payload
        return len(_scanner.scan(source_text)) > 0
    if case.kind == "tls":
        verify, check_hostname, cert_reqs = case.payload
        return _tls.check(verify=verify, check_hostname=check_hostname, cert_reqs=cert_reqs)[0]
    raise ValueError(f"unknown crypto case kind: {case.kind}")


# --- Planted buggy auditors (each a realistic cryptographic-control defect) --

def mutant_accept_any_salted_hash(case: CryptoCase) -> bool:
    """BUG: the password checker treats ANY salted hash as safe, so a salted
    general-purpose digest (salted SHA-256) is wrongly cleared — the classic
    'we salt it so it's fine' misconception that misses the fast-hash problem.
    Other kinds correct."""
    if case.kind == "password":
        algorithm, salted, iterations = case.payload
        algo = (algorithm or "").lower().strip()
        if algo in _STRONG_KDFS:
            return False
        if algo in _ITERATED_KDFS:
            return iterations is None or iterations < PBKDF2_MIN_ITERATIONS
        # BUG: a salted general-purpose/fast hash is accepted instead of flagged.
        return not salted
    return oracle_crypto_audit(case)


def mutant_cipher_ignores_mode(case: CryptoCase) -> bool:
    """BUG: the cipher checker only inspects the algorithm name and ignores the
    mode and IV reuse, so AES-ECB and a reused IV slip through — a real defect
    where reviewers blocklist 'DES/RC4' but forget that mode matters."""
    if case.kind == "cipher":
        algorithm, _mode, _iv_reused = case.payload
        a = (algorithm or "").lower().strip()
        return a in _WEAK_CIPHERS  # BUG: mode + iv_reused never checked
    return oracle_crypto_audit(case)


def mutant_tls_verify_only(case: CryptoCase) -> bool:
    """BUG: the TLS checker only looks at the ``verify`` flag and ignores
    check_hostname and cert_reqs, so verify=True with hostname checking disabled
    (or cert_reqs=CERT_NONE) is wrongly cleared — a real partial-validation bug."""
    if case.kind == "tls":
        verify, _check_hostname, _cert_reqs = case.payload
        return verify is False  # BUG: hostname + cert_reqs never checked
    return oracle_crypto_audit(case)


def prove(audit: Callable[[CryptoCase], bool]) -> bool:
    """True iff ``audit`` MISCLASSIFIES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    CryptoCase.should_flag constant, never against the oracle. An auditor that
    raises on a corpus case counts as caught.
    """
    for case in CRYPTO_CORPUS:
        try:
            verdict = bool(audit(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_crypto_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_crypto_audit,
    mutants=(
        Mutant("accept_any_salted_hash", mutant_accept_any_salted_hash,
               "password checker clears any salted hash, so salted SHA-256 (a fast general-purpose digest) is wrongly accepted"),
        Mutant("cipher_ignores_mode", mutant_cipher_ignores_mode,
               "cipher checker only blocklists weak algorithms and ignores mode/IV, so AES-ECB and a reused IV slip through"),
        Mutant("tls_verify_only", mutant_tls_verify_only,
               "TLS checker only inspects verify=, so check_hostname off / cert_reqs=CERT_NONE is wrongly cleared"),
    ),
    corpus_size=len(CRYPTO_CORPUS),
    kind="auditor",
    notes="password (KDF + iterations + salted general-purpose hashes), randomness (CSPRNG + entropy floor), "
          "cipher (weak algo + ECB mode + reused IV), hard-coded secrets, TLS (verify + hostname + cert_reqs)",
)


# Back-compat aliases so the paired/proof tests can treat the corpus uniformly.
CASES = CRYPTO_CORPUS


def run_case(case: CryptoCase) -> bool:
    """The oracle's verdict for one case (True == flagged)."""
    return oracle_crypto_audit(case)


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

    # Password hashing
    check("1. argon2id accepted", _pw.check("argon2id", salted=True)[0] is False)
    check("2. md5 flagged", _pw.check("md5", salted=False)[0] is True)
    check("3. unsalted sha256 flagged", _pw.check("sha256", salted=False)[0] is True)
    check("4. low-iteration pbkdf2 flagged",
          _pw.check("pbkdf2", iterations=1000)[0] is True)
    check("5. high-iteration pbkdf2 accepted",
          _pw.check("pbkdf2", iterations=200_000)[0] is False)
    # Randomness
    check("6. secrets source accepted", _rng.check_source("secrets.token_bytes")[0] is False)
    check("7. random.* source flagged", _rng.check_source("random.random")[0] is True)
    check("8. short token entropy flagged",
          _rng.check_token_entropy(b"\x01\x02\x03\x04", min_bits=128)[0] is True)
    check("9. adequate token entropy accepted",
          _rng.check_token_entropy(bytes(range(20)), min_bits=128)[0] is False)
    # Ciphers
    check("10. AES-ECB flagged", _cipher.check("AES", "ECB")[0] is True)
    check("11. DES flagged", _cipher.check("DES", "CBC")[0] is True)
    check("12. reused IV flagged", _cipher.check("AES", "CTR", iv_reused=True)[0] is True)
    check("13. AES-GCM accepted", _cipher.check("AES", "GCM")[0] is False)
    # Hard-coded secrets
    check("14. hardcoded AWS key flagged", len(_scanner.scan(_BAD_SOURCE)) >= 1)
    check("15. env-sourced secret clean", len(_scanner.scan(_SAFE_SOURCE)) == 0)
    # TLS
    check("16. verify=False flagged", _tls.check(verify=False)[0] is True)
    check("17. hostname check off flagged", _tls.check(check_hostname=False)[0] is True)
    check("18. verified TLS accepted", _tls.check()[0] is False)

    for case in CRYPTO_CORPUS:
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
    report = Report("security/crypto")

    # The correct oracle verdict must match every frozen should_flag literal.
    # Calling oracle_crypto_audit by its module-global name is what the vacuity
    # gate's neuter breaks.
    for case in CRYPTO_CORPUS:
        report.add(f"crypto:{case.name}", case.should_flag,
                   oracle_crypto_audit(case), detail=case.kind)

    # The legacy scenario checks (checkers exercised directly).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crypto_test_harness",
        description="Cryptographic-primitive misuse harness (A04:2025, pure stdlib)",
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
