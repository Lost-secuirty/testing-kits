#!/usr/bin/env python3
"""
ast_sast_test_harness.py — Zero-dependency static checker over Python AST.
==========================================================================

Pure-stdlib. Zero external dependencies (the shared ``harnesses._teeth``
contract is itself pure stdlib).

A small CWE-tagged SAST built on the stdlib ``ast`` module. It is useful on its
own AND is the security oracle reused by ai/secure_codegen_eval_test_harness.

OWASP mapping: cross-cutting zero-dep AST SAST (CWE-tagged rules; the eval
oracle). No single OWASP risk ID — the rules span injection (CWE-78/89/95),
deserialization (CWE-502), crypto (CWE-327/330), TLS (CWE-295), secrets
(CWE-798), and more.

Rules (CWE-tagged):
- eval()/exec() on input ............................. CWE-95
- os.system / subprocess(..., shell=True) ............ CWE-78
- string-built SQL passed to .execute() (f-string/%/+/.format) ... CWE-89
- pickle.load(s) / yaml.load without SafeLoader ...... CWE-502
- hashlib.md5 / hashlib.sha1 ......................... CWE-327
- random.* used for security values .................. CWE-330
- verify=False / ssl._create_unverified_context ...... CWE-295
- hard-coded secret assignment ....................... CWE-798
- tempfile.mktemp .................................... CWE-377
- assert used as an auth gate ........................ CWE-617
- web server run with debug=True ..................... CWE-489
- HTTP client call without a timeout ................. CWE-400
- archive extractall() (tar/zip path traversal) ...... CWE-22
- Jinja2 Environment without autoescape=True ......... CWE-79

TEETH: the harness's own SAST auditor (oracle_ast_sast_audit) judged against a
FROZEN corpus of (source, should_flag, cwe) literals. Each planted Mutant is a
realistic SAST-rule regression (an SQL detector narrowed to f-strings that
misses %/+/.format-built queries, a secret rule that anchors on an exact name
and misses ``api_key``, a timeout rule that trusts a ``None`` timeout). prove()
compares each auditor to the frozen should_flag literal — never to the oracle —
so it is non-circular and deterministic (no clock/network/filesystem/RNG).

Usage:
    python harnesses/security/ast_sast_test_harness.py --self-test
    python harnesses/security/ast_sast_test_harness.py --json
    python harnesses/security/ast_sast_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

_SECRET_NAME_RE = re.compile(r"password|passwd|secret|api[_-]?key|access[_-]?token|\btoken\b", re.I)
_AUTH_RE = re.compile(r"is_admin|has_permission|is_authenticated|is_authorized|authorize|check_auth|\brole\b", re.I)
_RANDOM_FUNCS = {"random", "randint", "choice", "randrange", "getrandbits", "uniform", "randbytes"}
_HTTP_CALLS = {
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.patch", "requests.head", "requests.request",
    "httpx.get", "httpx.post",
}


@dataclass
class SastFinding:
    rule_id: str
    cwe: str
    severity: str
    message: str
    line: int


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _is_const(node: ast.AST, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


class ASTSAST:
    """Scan Python source text; return a list of SastFinding."""

    def scan(self, source: str) -> list[SastFinding]:
        findings: list[SastFinding] = []
        try:
            tree = ast.parse(source or "")
        except SyntaxError:
            return findings
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self._check_call(node, findings)
            elif isinstance(node, ast.Assign):
                self._check_assign(node, findings)
            elif isinstance(node, ast.Assert):
                self._check_assert(node, findings)
        findings.sort(key=lambda f: (f.line, f.rule_id))
        return findings

    def _add(self, findings: list[SastFinding], rule_id: str, cwe: str,
             severity: str, message: str, node: ast.AST) -> None:
        findings.append(SastFinding(rule_id, cwe, severity, message, getattr(node, "lineno", 0)))

    def _check_call(self, node: ast.Call, findings: list[SastFinding]) -> None:
        name = _dotted(node.func)
        short = name.split(".")[-1]
        root = name.split(".")[0]

        if name in ("eval", "exec"):
            self._add(findings, "PY-EVAL", "CWE-95", "CRITICAL", f"{name}() executes arbitrary code", node)
        if name == "os.system":
            self._add(findings, "PY-OS-SYSTEM", "CWE-78", "CRITICAL", "os.system() runs a shell command", node)
        if name == "ssl._create_unverified_context":
            self._add(findings, "PY-SSL-UNVERIFIED", "CWE-295", "HIGH", "Unverified SSL context", node)
        if name in ("pickle.load", "pickle.loads"):
            self._add(findings, "PY-PICKLE", "CWE-502", "HIGH", f"{name} deserializes untrusted data", node)
        if name in ("hashlib.md5", "hashlib.sha1"):
            self._add(findings, "PY-WEAK-HASH", "CWE-327", "HIGH", f"{name} is a broken hash", node)
        if name == "tempfile.mktemp":
            self._add(findings, "PY-MKTEMP", "CWE-377", "MEDIUM", "tempfile.mktemp is race-prone", node)
        if root == "random" and short in _RANDOM_FUNCS:
            self._add(findings, "PY-WEAK-RANDOM", "CWE-330", "MEDIUM",
                      f"{name} is not cryptographically secure", node)
        if name == "yaml.load":
            loader_safe = any(
                kw.arg == "Loader" and "safe" in _dotted(kw.value).lower()
                for kw in node.keywords
            )
            if not loader_safe:
                self._add(findings, "PY-YAML-LOAD", "CWE-502", "HIGH", "yaml.load without SafeLoader", node)

        # keyword-based smells
        for kw in node.keywords:
            if kw.arg == "shell" and _is_const(kw.value, True):
                self._add(findings, "PY-SHELL-TRUE", "CWE-78", "CRITICAL",
                          "subprocess called with shell=True", node)
            if kw.arg == "verify" and _is_const(kw.value, False):
                self._add(findings, "PY-NO-TLS-VERIFY", "CWE-295", "HIGH",
                          "TLS verification disabled (verify=False)", node)

        # string-built SQL in .execute()
        if short in ("execute", "executemany") and node.args:
            arg = node.args[0]
            built = (
                isinstance(arg, ast.JoinedStr)
                or (isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)))
                or (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "format")
            )
            if built:
                self._add(findings, "PY-SQL-STRING", "CWE-89", "CRITICAL",
                          "SQL built by string formatting in execute()", node)

        # --- added rules ---
        # web server run with debug enabled
        if short == "run" and any(kw.arg == "debug" and _is_const(kw.value, True)
                                  for kw in node.keywords):
            self._add(findings, "PY-DEBUG-RUN", "CWE-489", "HIGH",
                      "Web server started with debug=True", node)
        # HTTP client call without a timeout
        if name in _HTTP_CALLS and not any(kw.arg == "timeout" for kw in node.keywords):
            self._add(findings, "PY-NO-TIMEOUT", "CWE-400", "MEDIUM",
                      f"{name}() has no timeout — can hang indefinitely", node)
        # archive extractall (tar/zip slip / path traversal)
        if short == "extractall":
            self._add(findings, "PY-ARCHIVE-EXTRACT", "CWE-22", "HIGH",
                      "Archive extractall() without member validation (path traversal)", node)
        # Jinja2 Environment without autoescape
        if short == "Environment" and not any(
            kw.arg == "autoescape" and _is_const(kw.value, True) for kw in node.keywords
        ):
            self._add(findings, "PY-JINJA-AUTOESCAPE", "CWE-79", "HIGH",
                      "Jinja2 Environment without autoescape=True", node)

    def _check_assign(self, node: ast.Assign, findings: list[SastFinding]) -> None:
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return
        if len(node.value.value) < 6:
            return
        for target in node.targets:
            nm = target.id if isinstance(target, ast.Name) else (
                target.attr if isinstance(target, ast.Attribute) else "")
            if nm and _SECRET_NAME_RE.search(nm):
                self._add(findings, "PY-HARDCODED-SECRET", "CWE-798", "HIGH",
                          f"Hard-coded secret assigned to '{nm}'", node)

    def _check_assert(self, node: ast.Assert, findings: list[SastFinding]) -> None:
        if _AUTH_RE.search(ast.dump(node.test)):
            self._add(findings, "PY-ASSERT-AUTH", "CWE-617", "MEDIUM",
                      "assert used as an authorization gate (skipped under -O)", node)


# ---------------------------------------------------------------------------
# Fixtures + scenario results + legacy proof cases
# ---------------------------------------------------------------------------

_SAFE_SOURCE = '''
import hashlib, secrets, json, os
def get_user(cursor, uid):
    cursor.execute("SELECT * FROM users WHERE id = ?", (uid,))
    return cursor.fetchone()
def make_token():
    return secrets.token_hex(16)
def run_ls(path, runner):
    return runner(["ls", path])
def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 100000)
def load_config(text):
    return json.loads(text)
API_KEY = os.environ["API_KEY"]
'''

# (label, cwe, source) — each planted snippet must trigger exactly its CWE rule.
_BAD_SNIPPETS = [
    ("eval", "CWE-95", "def calc(e):\n    return eval(e)\n"),
    ("os_system", "CWE-78", "import os\ndef run(c):\n    os.system(c)\n"),
    ("shell_true", "CWE-78", "import subprocess\ndef run(c):\n    subprocess.run(c, shell=True)\n"),
    ("sql_fstring", "CWE-89", 'def get_user(cur, uid):\n    cur.execute(f"SELECT * FROM users WHERE id = {uid}")\n'),
    ("pickle", "CWE-502", "import pickle\ndef load(b):\n    return pickle.loads(b)\n"),
    ("md5", "CWE-327", "import hashlib\ndef h(p):\n    return hashlib.md5(p.encode()).hexdigest()\n"),
    ("random", "CWE-330", "import random\ndef tok():\n    return random.randint(0, 999999)\n"),
    ("verify_false", "CWE-295", "import requests\ndef f(u):\n    return requests.get(u, verify=False, timeout=5)\n"),
    ("hardcoded", "CWE-798", 'API_KEY = "AKIAIOSFODNN7EXAMPLE"\n'),
    ("mktemp", "CWE-377", "import tempfile\ndef t():\n    return tempfile.mktemp()\n"),
    ("assert_auth", "CWE-617", "def gate(u):\n    assert u.is_admin\n    return True\n"),
    ("debug_run", "CWE-489", "import flask\napp = flask.Flask(__name__)\napp.run(debug=True)\n"),
    ("no_timeout", "CWE-400", "import requests\ndef f(u):\n    return requests.get(u)\n"),
    ("archive_extract", "CWE-22", "import tarfile\ndef x(p):\n    tarfile.open(p).extractall('/tmp')\n"),
    ("jinja_autoescape", "CWE-79", "import jinja2\nenv = jinja2.Environment()\n"),
]


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


@dataclass
class ProofCase:
    name: str
    should_flag: bool
    source: str
    cwe: str = ""


CASES: list[ProofCase] = [ProofCase("safe_source", False, _SAFE_SOURCE)]
for _label, _cwe, _src in _BAD_SNIPPETS:
    CASES.append(ProofCase(f"bad_{_label}", True, _src, cwe=_cwe))


def run_case(case: ProofCase) -> bool:
    """The legacy proof verdict for one ProofCase (True == flagged)."""
    findings = ASTSAST().scan(case.source)
    if case.should_flag and case.cwe:
        return any(f.cwe == case.cwe for f in findings)
    return len(findings) > 0


def run_all_scenarios(verbose: bool = False) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    sast = ASTSAST()

    def check(name: str, cond: bool, detail: str = "") -> None:
        r = ScenarioResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)

    found = sast.scan(_SAFE_SOURCE)
    check("1. safe source is clean", len(found) == 0, str([f.cwe for f in found]))
    n = 1
    for label, cwe, src in _BAD_SNIPPETS:
        n += 1
        cwes = [f.cwe for f in sast.scan(src)]
        check(f"{n}. {label} -> {cwe} detected", cwe in cwes, f"got {cwes}")

    check("malformed source handled", isinstance(sast.scan("def ( bad"), list))

    for case in CASES:
        check(f"proof:{case.name}", run_case(case) == case.should_flag,
              f"expected flag={case.should_flag}")

    return results


def list_scenarios() -> list[str]:
    return [r.name for r in run_all_scenarios(verbose=False)]


# ===========================================================================
# TEETH: the SAST auditor judged against a FROZEN literal corpus.
#
# kind = auditor. An "auditor impl" is a callable
# ``audit(case: SastCase) -> bool`` returning True iff the source should be
# FLAGGED for the case's CWE class. The harness only has teeth if it CATCHES an
# auditor that MISSES a known-vulnerable snippet (a false negative — the
# dangerous failure) or FALSE-FLAGS a known-safe snippet (a false positive —
# which erodes trust and gets the rule disabled).
#
# Each ``should_flag`` is a constant hand-derived from the CWE ground truth
# (``eval(e)`` IS code execution; ``"SELECT ?"`` parameterised is clean), NEVER
# read back from ASTSAST at runtime — so prove() is non-circular: corrupting a
# frozen literal flips prove(oracle) False -> True.
#
# Pure + deterministic: ast/regex matching only, no RNG/clock/network/
# filesystem/threads.
# ===========================================================================


@dataclass(frozen=True)
class SastCase:
    """One frozen SAST-audit fixture with a literal, hand-derived flag decision.

    ``cwe`` selects which detector the oracle interrogates; ``should_flag`` is the
    EXACT decision a correct auditor must make for that CWE on this source.
    """

    name: str
    cwe: str
    source: str
    should_flag: bool
    note: str = ""


# Frozen corpus. should_flag is independent ground truth (hand-pinned), never
# read from a detector. Each CWE class the mutants attack carries BOTH a
# known-vulnerable and a known-safe twin, plus discriminators the mutants miss.
SAST_CORPUS: tuple[SastCase, ...] = (
    # --- SQLi (CWE-89): the f-string-only mutant misses %/+/.format builds ----
    SastCase("sql_fstring", "CWE-89", 'cur.execute(f"SELECT * FROM users WHERE id = {uid}")\n', True,
             "f-string-built SQL in execute() — must flag"),
    SastCase("sql_percent", "CWE-89", 'cur.execute("SELECT * FROM t WHERE id = %s" % uid)\n', True,
             "%-formatted SQL in execute() — the f-string-only mutant misses this"),
    SastCase("sql_concat", "CWE-89", 'cur.execute("SELECT * FROM t WHERE id = " + uid)\n', True,
             "+-concatenated SQL in execute() — a 2nd independent catch for the narrow mutant"),
    SastCase("sql_param_safe", "CWE-89", 'cur.execute("SELECT * FROM t WHERE id = ?", (uid,))\n', False,
             "parameterised query — must NOT flag"),
    # --- Hard-coded secret (CWE-798): the exact-name mutant misses api_key -----
    SastCase("secret_password", "CWE-798", 'password = "hunter2-very-secret"\n', True,
             "literal password assignment — must flag"),
    SastCase("secret_api_key", "CWE-798", 'api_key = "AKIAIOSFODNN7EXAMPLE"\n', True,
             "literal api_key assignment — the exact-'password' mutant misses this"),
    SastCase("secret_from_env", "CWE-798", 'api_key = os.environ["API_KEY"]\n', False,
             "secret read from env, not a literal — must NOT flag"),
    # --- Missing HTTP timeout (CWE-400): the rule-dropping mutant slips through -
    SastCase("http_no_timeout", "CWE-400", "requests.get(u)\n", True,
             "HTTP GET with no timeout kwarg — must flag (can hang)"),
    SastCase("http_post_no_timeout", "CWE-400", "requests.post(u, json=body)\n", True,
             "HTTP POST with no timeout — a 2nd independent catch for the rule-dropping mutant"),
    SastCase("http_with_timeout", "CWE-400", "requests.get(u, timeout=5)\n", False,
             "real numeric timeout present — must NOT flag"),
    # --- a clean control across the board -------------------------------------
    SastCase("eval_exec", "CWE-95", "result = eval(user_input)\n", True,
             "eval() on input — must flag"),
)


def list_audit_cases() -> list[str]:
    """Names of the frozen auditor corpus cases (the teeth scenarios)."""
    return [case.name for case in SAST_CORPUS]


# --- ORACLE: reuse the harness's own correct scanner -------------------------

def oracle_ast_sast_audit(case: SastCase) -> bool:
    """Correct auditor: scan the source and report whether ANY finding carries
    the case's CWE. This is the harness's real control surface, reused as-is.

    Pure over its argument — runs ASTSAST().scan, no I/O.
    """
    return any(f.cwe == case.cwe for f in ASTSAST().scan(case.source))


# --- Planted buggy twins (each models a real SAST-rule regression) -----------

def _scan_sql_fstring_only(source: str) -> list[SastFinding]:
    """A copy of the scanner whose SQL rule only catches f-strings (JoinedStr),
    dropping the %/+/.format alternatives."""
    findings: list[SastFinding] = []
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return findings
    sast = ASTSAST()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            short = name.split(".")[-1]
            if short in ("execute", "executemany") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.JoinedStr):  # BUG: f-string ONLY
                    findings.append(SastFinding("PY-SQL-STRING", "CWE-89", "CRITICAL",
                                                "SQL built by f-string in execute()",
                                                getattr(node, "lineno", 0)))
            # other CWE classes handled by the real scanner so we don't double-count SQL
            else:
                sub: list[SastFinding] = []
                sast._check_call(node, sub)
                findings.extend(f for f in sub if f.cwe != "CWE-89")
        elif isinstance(node, ast.Assign):
            sast._check_assign(node, findings)
        elif isinstance(node, ast.Assert):
            sast._check_assert(node, findings)
    return findings


def mutant_sql_fstring_only(case: SastCase) -> bool:
    """BUG: the SQL detector only matches f-strings (ast.JoinedStr) and drops the
    %-format, +-concat and .format() alternatives, so a ``"..." % uid`` or
    ``"..." + uid`` query sails into execute() unflagged — the classic
    over-fitted SAST rule that misses the next injection shape."""
    if case.cwe == "CWE-89":
        return any(f.cwe == "CWE-89" for f in _scan_sql_fstring_only(case.source))
    return oracle_ast_sast_audit(case)


_EXACT_SECRET_RE = re.compile(r"^(password|passwd|secret|token)$", re.I)


def mutant_secret_exact_password(case: SastCase) -> bool:
    """BUG: the hard-coded-secret rule anchors on an exact small name set and
    drops the ``api_key`` / ``access_token`` patterns, so a literal ``api_key``
    assignment slips through (false negative) — a real regression where a
    refactor narrowed the secret-name regex."""
    if case.cwe == "CWE-798":
        try:
            tree = ast.parse(case.source or "")
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) and len(node.value.value) >= 6:
                for target in node.targets:
                    nm = target.id if isinstance(target, ast.Name) else (
                        target.attr if isinstance(target, ast.Attribute) else "")
                    if nm and _EXACT_SECRET_RE.match(nm):  # BUG: exact, narrow set
                        return True
        return False
    return oracle_ast_sast_audit(case)


def mutant_timeout_rule_dropped(case: SastCase) -> bool:
    """BUG: the missing-timeout rule is dropped entirely (the detector is a no-op
    for CWE-400), so an HTTP client call with no timeout sails through unflagged
    and can hang forever — a false negative from a rule that was disabled in a
    refactor and never re-enabled."""
    if case.cwe == "CWE-400":
        return False  # BUG: the no-timeout rule never fires
    return oracle_ast_sast_audit(case)


def prove(impl: Callable[[SastCase], bool]) -> bool:
    """True iff ``impl`` MISCLASSIFIES any frozen corpus case (i.e. is caught):
    it misses a snippet that must be flagged (false negative) or flags one that
    must not be (false positive), or raises.

    Non-circular + deterministic: each verdict is compared against the literal
    SastCase.should_flag constant, never against the oracle. ast/regex only,
    no RNG/clock/network/filesystem. An impl that raises counts as caught.
    """
    for case in SAST_CORPUS:
        try:
            verdict = bool(impl(case))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if verdict != case.should_flag:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_ast_sast_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_ast_sast_audit,
    mutants=(
        Mutant("sql_fstring_only", mutant_sql_fstring_only,
               "SQL detector matches only f-strings and drops the %/+/.format builds, so a "
               "%-formatted or concatenated query reaches execute() unflagged (false negative)"),
        Mutant("secret_exact_password", mutant_secret_exact_password,
               "hard-coded-secret rule anchors on an exact name set and drops api_key / "
               "access_token, so a literal api_key assignment is missed (false negative)"),
        Mutant("timeout_rule_dropped", mutant_timeout_rule_dropped,
               "missing-timeout rule dropped entirely, so an HTTP call with no timeout is never "
               "flagged and can hang forever (false negative from a disabled rule)"),
    ),
    corpus_size=len(SAST_CORPUS),
    kind="auditor",
    notes="a SAST auditor must flag every known-vulnerable snippet for its CWE class and "
          "leave every known-safe one clean: an over-narrow detector misses the next "
          "injection/secret shape, and a presence-only check trusts a neutered timeout",
)


# ---------------------------------------------------------------------------
# Report-based self-test — exercises the oracle by module-global name (so the
# vacuity gate's neuter is caught here) and asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    report = Report("security/ast_sast")

    # 1. The correct oracle verdict must match every frozen should_flag literal.
    #    Calling oracle_ast_sast_audit by its module-global name is what the
    #    vacuity gate's neuter breaks.
    for case in SAST_CORPUS:
        report.add(f"audit:{case.name}", case.should_flag,
                   oracle_ast_sast_audit(case), detail=f"{case.cwe} {case.note}")

    # 2. The legacy scenario checks (scanner exercised directly on each snippet).
    for r in run_all_scenarios(verbose=verbose):
        report.record(f"scenario:{r.name}", r.passed, detail=r.detail)

    # 3. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ast_sast_test_harness",
        description="Zero-dependency AST SAST checker (pure stdlib)",
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
