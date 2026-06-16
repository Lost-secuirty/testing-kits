"""
Supply-Chain / Build Reproducibility Test Harness (Harness 34 of 36)
Pure stdlib, zero external dependencies.
Mock HTTP server on dynamic port (default 19200).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import hmac
import http.server
import json
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LockedDep:
    """Represents a dependency pinned in a lockfile."""
    name: str
    version: str
    sha256: str
    source_url: str

    def __post_init__(self):
        if not self.name:
            raise ValueError("Dependency name must not be empty")
        if not self.version:
            raise ValueError("Dependency version must not be empty")


@dataclasses.dataclass
class FindingRecord:
    """A single finding from a checker."""
    checker: str
    severity: str   # "error" | "warning" | "info"
    package: str
    message: str
    details: str | None = None


# ---------------------------------------------------------------------------
# Pinning Checker
# ---------------------------------------------------------------------------

class PinningChecker:
    """
    Flags specifiers that are not exact pins.
    Accepted: ==1.2.3
    Rejected: >=1.0, <=2.0, ~=1.0, >1, <2, *, latest, or bare name (no version).
    """

    EXACT_PIN_RE = re.compile(r'^==\d+(\.\d+)*$')
    WILDCARD_VALUES = {'*', 'latest', 'any'}

    def check(self, name: str, specifier: str) -> list[FindingRecord]:
        findings: list[FindingRecord] = []
        spec = specifier.strip()

        if not spec:
            findings.append(FindingRecord(
                checker='PinningChecker',
                severity='error',
                package=name,
                message=f"Package '{name}' has no version specifier (unpinned).",
            ))
            return findings

        if spec.lower() in self.WILDCARD_VALUES or spec == '*':
            findings.append(FindingRecord(
                checker='PinningChecker',
                severity='error',
                package=name,
                message=f"Package '{name}' uses wildcard specifier '{spec}'.",
            ))
            return findings

        if self.EXACT_PIN_RE.match(spec):
            return []  # OK

        # Floating / range specifier
        findings.append(FindingRecord(
            checker='PinningChecker',
            severity='error',
            package=name,
            message=f"Package '{name}' uses floating/range specifier '{spec}'.",
        ))
        return findings

    def check_all(self, deps: dict[str, str]) -> list[FindingRecord]:
        """Check a mapping of {name: specifier}."""
        findings = []
        for name, spec in deps.items():
            findings.extend(self.check(name, spec))
        return findings


# ---------------------------------------------------------------------------
# Integrity Checker
# ---------------------------------------------------------------------------

class IntegrityChecker:
    """
    Verifies artifact bytes against the sha256 stored in the lockfile.
    Uses hmac.compare_digest for constant-time comparison.
    """

    def verify(self, dep: LockedDep, artifact_bytes: bytes) -> tuple[bool, str]:
        """
        Returns (ok, message).
        ok=True  → digest matches
        ok=False → tampered / mismatch
        """
        actual = hashlib.sha256(artifact_bytes).hexdigest()
        expected = dep.sha256.lower().strip()
        # hmac.compare_digest requires same type
        if not hmac.compare_digest(actual.encode(), expected.encode()):
            return False, (
                f"SHA-256 mismatch for '{dep.name}=={dep.version}': "
                f"expected {expected}, got {actual}"
            )
        return True, f"Integrity OK for '{dep.name}=={dep.version}'"

    def verify_all(
        self, locked: list[LockedDep], artifacts: dict[str, bytes]
    ) -> list[FindingRecord]:
        """
        artifacts: {dep.name: bytes}
        Returns findings for every mismatch.
        """
        findings = []
        for dep in locked:
            if dep.name not in artifacts:
                findings.append(FindingRecord(
                    checker='IntegrityChecker',
                    severity='warning',
                    package=dep.name,
                    message=f"No artifact provided for '{dep.name}'; skipping integrity check.",
                ))
                continue
            ok, msg = self.verify(dep, artifacts[dep.name])
            if not ok:
                findings.append(FindingRecord(
                    checker='IntegrityChecker',
                    severity='error',
                    package=dep.name,
                    message=msg,
                ))
        return findings


# ---------------------------------------------------------------------------
# Lockfile Drift Checker
# ---------------------------------------------------------------------------

class LockfileDriftChecker:
    """
    Detects drift between requirements.txt (manifest) and lockfile.
    - packages in manifest but missing from lockfile → "manifest extras"
    - packages in lockfile but not in manifest → "lockfile extras"
    """

    def check(
        self,
        manifest_packages: set[str],
        locked_packages: set[str],
    ) -> list[FindingRecord]:
        findings = []

        manifest_norm = {p.lower() for p in manifest_packages}
        locked_norm = {p.lower() for p in locked_packages}

        in_manifest_not_locked = manifest_norm - locked_norm
        in_locked_not_manifest = locked_norm - manifest_norm

        for pkg in sorted(in_manifest_not_locked):
            findings.append(FindingRecord(
                checker='LockfileDriftChecker',
                severity='error',
                package=pkg,
                message=f"'{pkg}' is in manifest but missing from lockfile (drift).",
            ))

        for pkg in sorted(in_locked_not_manifest):
            findings.append(FindingRecord(
                checker='LockfileDriftChecker',
                severity='warning',
                package=pkg,
                message=f"'{pkg}' is in lockfile but not declared in manifest.",
            ))

        return findings


# ---------------------------------------------------------------------------
# Nonexistent Package / Typosquat Checker
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            insert = curr[j] + 1
            delete = prev[j + 1] + 1
            replace = prev[j] + (ca != cb)
            curr.append(min(insert, delete, replace))
        prev = curr
    return prev[-1]


class NonexistentPackageChecker:
    """
    Checks package names against a known registry.
    - Unknown packages → error (possible hallucination / slopsquatting).
    - Levenshtein-1 near-matches → warning (possible typosquat).
    """

    def __init__(self, known_packages: set[str]):
        self.known_packages: set[str] = {p.lower() for p in known_packages}

    def check(self, name: str) -> list[FindingRecord]:
        findings = []
        lower = name.lower()

        if lower in self.known_packages:
            return []

        # Find typosquat candidates (edit distance == 1)
        candidates = [
            k for k in self.known_packages if _levenshtein(lower, k) == 1
        ]

        if candidates:
            findings.append(FindingRecord(
                checker='NonexistentPackageChecker',
                severity='warning',
                package=name,
                message=(
                    f"'{name}' not found in registry; possible typosquat of: "
                    f"{', '.join(sorted(candidates))}"
                ),
            ))
        else:
            findings.append(FindingRecord(
                checker='NonexistentPackageChecker',
                severity='error',
                package=name,
                message=f"'{name}' not found in registry (possible hallucinated dependency).",
            ))

        return findings

    def check_all(self, names: list[str]) -> list[FindingRecord]:
        findings = []
        for name in names:
            findings.extend(self.check(name))
        return findings


# ---------------------------------------------------------------------------
# Reproducible Build Checker
# ---------------------------------------------------------------------------

class ReproducibleBuildChecker:
    """
    Calls a build_fn(inputs) twice and compares outputs.
    Detects nondeterminism (e.g., embedded timestamps).
    """

    def check(
        self,
        build_fn,
        inputs: dict,
        *,
        attempts: int = 2,
    ) -> tuple[bool, list[FindingRecord]]:
        """
        Returns (reproducible: bool, findings).
        """
        outputs = [build_fn(inputs) for _ in range(attempts)]

        # All outputs must be identical
        first = outputs[0]
        for i, out in enumerate(outputs[1:], start=2):
            if out != first:
                return False, [FindingRecord(
                    checker='ReproducibleBuildChecker',
                    severity='error',
                    package='<build>',
                    message=(
                        f"Build is nondeterministic: attempt 1 and attempt {i} "
                        f"produced different outputs."
                    ),
                    details=f"attempt1={first!r}, attempt{i}={out!r}",
                )]

        return True, []


# ---------------------------------------------------------------------------
# Known Vulnerability Checker
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Advisory:
    """A vulnerability advisory for a package."""
    package: str
    cve_id: str
    severity: str
    affected_specifier: str   # e.g. ">=1.0,<1.5"
    description: str


def _version_tuple(v: str) -> tuple:
    """Parse a version string into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in v.split('.'))
    except ValueError:
        return (0,)


def _version_satisfies(version: str, specifier: str) -> bool:
    """
    Check whether `version` satisfies a comma-separated specifier like
    '>=1.0,<1.5'.  Supports: >=, <=, >, <, ==, !=
    """
    ver = _version_tuple(version)
    for part in specifier.split(','):
        part = part.strip()
        for op in ('>=', '<=', '!=', '==', '>', '<'):
            if part.startswith(op):
                bound = _version_tuple(part[len(op):])
                if op == '>=' and not (ver >= bound) or op == '<=' and not (ver <= bound) or op == '!=' and ver == bound or op == '==' and ver != bound or op == '>' and not (ver > bound) or op == '<' and not (ver < bound):
                    return False
                break
    return True


class KnownVulnChecker:
    """Matches locked dependencies against a mock advisory database."""

    def __init__(self, advisories: list[Advisory]):
        self.advisories = advisories

    def check(self, dep: LockedDep) -> list[FindingRecord]:
        findings = []
        pkg_lower = dep.name.lower()
        for adv in self.advisories:
            if adv.package.lower() != pkg_lower:
                continue
            if _version_satisfies(dep.version, adv.affected_specifier):
                findings.append(FindingRecord(
                    checker='KnownVulnChecker',
                    severity=adv.severity,
                    package=dep.name,
                    message=(
                        f"{adv.cve_id}: '{dep.name}=={dep.version}' is affected. "
                        f"{adv.description}"
                    ),
                    details=adv.affected_specifier,
                ))
        return findings

    def check_all(self, deps: list[LockedDep]) -> list[FindingRecord]:
        findings = []
        for dep in deps:
            findings.extend(self.check(dep))
        return findings


# ---------------------------------------------------------------------------
# Transitive Dependency Checker
# ---------------------------------------------------------------------------

class TransitiveDepChecker:
    """
    Resolves a dependency graph and finds:
    - unpinned transitive deps (not in lockfile)
    - phantom deps (declared in graph but not resolvable / present in lockfile)
    """

    def resolve(
        self,
        root_deps: list[str],
        dep_graph: dict[str, list[str]],
        lockfile_names: set[str],
    ) -> tuple[set[str], list[FindingRecord]]:
        """
        BFS from root_deps through dep_graph.
        Returns (all_transitive_names, findings).
        """
        visited: set[str] = set()
        queue = list(root_deps)
        findings: list[FindingRecord] = []

        while queue:
            pkg = queue.pop(0)
            pkg_lower = pkg.lower()
            if pkg_lower in visited:
                continue
            visited.add(pkg_lower)

            if pkg_lower not in {k.lower() for k in lockfile_names}:
                findings.append(FindingRecord(
                    checker='TransitiveDepChecker',
                    severity='error',
                    package=pkg,
                    message=f"Transitive dep '{pkg}' is not present in lockfile.",
                ))

            # Traverse children
            children = dep_graph.get(pkg, dep_graph.get(pkg_lower, []))
            for child in children:
                if child.lower() not in visited:
                    queue.append(child)

        # Phantom deps: declared in dep_graph but never reachable from root
        all_graph_pkgs = set(dep_graph.keys())
        reachable = visited
        phantoms = {
            p for p in all_graph_pkgs
            if p.lower() not in reachable
        }
        for pkg in sorted(phantoms):
            findings.append(FindingRecord(
                checker='TransitiveDepChecker',
                severity='warning',
                package=pkg,
                message=f"'{pkg}' is declared in dependency graph but never reachable from root.",
            ))

        return visited, findings


# ---------------------------------------------------------------------------
# Supply Chain Report
# ---------------------------------------------------------------------------

class SupplyChainReport:
    """Aggregates findings from all checkers."""

    def __init__(self):
        self.findings: list[FindingRecord] = []

    def add(self, findings: list[FindingRecord]) -> None:
        self.findings.extend(findings)

    def errors(self) -> list[FindingRecord]:
        return [f for f in self.findings if f.severity == 'error']

    def warnings(self) -> list[FindingRecord]:
        return [f for f in self.findings if f.severity == 'warning']

    def infos(self) -> list[FindingRecord]:
        return [f for f in self.findings if f.severity == 'info']

    def has_errors(self) -> bool:
        return bool(self.errors())

    def summary(self) -> str:
        e = len(self.errors())
        w = len(self.warnings())
        i = len(self.infos())
        return f"SupplyChainReport: {e} error(s), {w} warning(s), {i} info(s)"

    def findings_for(self, package: str) -> list[FindingRecord]:
        return [f for f in self.findings if f.package.lower() == package.lower()]

    def checkers_reported(self) -> set[str]:
        return {f.checker for f in self.findings}

    def to_dict(self) -> dict:
        return {
            'errors': [dataclasses.asdict(f) for f in self.errors()],
            'warnings': [dataclasses.asdict(f) for f in self.warnings()],
            'infos': [dataclasses.asdict(f) for f in self.infos()],
        }


# ---------------------------------------------------------------------------
# Mock Registry HTTP Server
# ---------------------------------------------------------------------------

_REGISTRY_DATA: dict[str, dict] = {}
_REGISTRY_LOCK = threading.Lock()


class MockRegistryHandler(http.server.BaseHTTPRequestHandler):
    """
    Simple HTTP handler serving package metadata as JSON.
    GET /packages/{name}        → {name, versions: [...]}
    GET /packages/{name}/{ver}  → {name, version, sha256, url}
    POST /packages              → register a new package (JSON body)
    """

    def log_message(self, fmt, *args):
        pass  # suppress default stderr logging

    def do_GET(self):
        parts = [p for p in self.path.split('/') if p]
        if not parts or parts[0] != 'packages':
            self._send(404, {'error': 'not found'})
            return

        if len(parts) == 2:
            name = parts[1].lower()
            with _REGISTRY_LOCK:
                pkg = _REGISTRY_DATA.get(name)
            if pkg is None:
                self._send(404, {'error': f"package '{name}' not found"})
            else:
                self._send(200, pkg)

        elif len(parts) == 3:
            name, ver = parts[1].lower(), parts[2]
            with _REGISTRY_LOCK:
                pkg = _REGISTRY_DATA.get(name)
            if pkg is None:
                self._send(404, {'error': f"package '{name}' not found"})
                return
            versions = pkg.get('versions', {})
            if ver not in versions:
                self._send(404, {'error': f"version '{ver}' of '{name}' not found"})
            else:
                self._send(200, {'name': name, 'version': ver, **versions[ver]})
        else:
            self._send(400, {'error': 'bad request'})

    def do_POST(self):
        parts = [p for p in self.path.split('/') if p]
        if parts != ['packages']:
            self._send(404, {'error': 'not found'})
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send(400, {'error': 'invalid JSON'})
            return
        name = data.get('name', '').lower()
        if not name:
            self._send(400, {'error': 'missing name'})
            return
        with _REGISTRY_LOCK:
            _REGISTRY_DATA[name] = data
        self._send(201, {'status': 'created', 'name': name})

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockRegistry:
    """
    Lifecycle wrapper for MockRegistryHandler.
    Usage:
        registry = MockRegistry()
        registry.start()
        ...
        registry.stop()
    or as a context manager.
    """

    DEFAULT_PORT = 19200

    def __init__(self, port: int = 0, initial_packages: dict | None = None):
        self.port = port  # 0 = OS assigns
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._initial_packages = initial_packages or {}

    def start(self) -> int:
        """Start server; returns actual bound port."""
        global _REGISTRY_DATA
        with _REGISTRY_LOCK:
            _REGISTRY_DATA = {k.lower(): v for k, v in self._initial_packages.items()}

        self._server = http.server.HTTPServer(('127.0.0.1', self.port), MockRegistryHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server:
            server.shutdown()
            server.server_close()
            self._server = None
        if thread:
            thread.join(timeout=2)
            self._thread = None

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def register(self, name: str, data: dict) -> None:
        """Programmatically register a package without HTTP."""
        with _REGISTRY_LOCK:
            _REGISTRY_DATA[name.lower()] = data

    def lookup(self, name: str) -> dict | None:
        with _REGISTRY_LOCK:
            return _REGISTRY_DATA.get(name.lower())

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


# ---------------------------------------------------------------------------
# Registry-aware NonexistentPackageChecker (HTTP variant)
# ---------------------------------------------------------------------------

class RegistryPackageChecker:
    """
    Checks packages against a live MockRegistry via HTTP.
    Falls back to NonexistentPackageChecker logic for typosquat detection.
    """

    def __init__(self, registry: MockRegistry):
        self.registry = registry

    def check(self, name: str) -> tuple[bool, dict | None]:
        """Returns (found, metadata_or_None)."""
        url = f"{self.registry.base_url()}/packages/{name.lower()}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True, json.loads(resp.read())
        except Exception:
            pass
        return False, None

    def check_all(self, names: list[str]) -> list[FindingRecord]:
        # Gather all known names for typosquat detection
        with _REGISTRY_LOCK:
            known = set(_REGISTRY_DATA.keys())
        checker = NonexistentPackageChecker(known)
        findings = []
        for name in names:
            found, _ = self.check(name)
            if not found:
                findings.extend(checker.check(name))
        return findings


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------

def build_default_advisories() -> list[Advisory]:
    """Return a small set of mock advisories for testing."""
    return [
        Advisory(
            package='requests',
            cve_id='CVE-2023-0001',
            severity='error',
            affected_specifier='>=1.0,<1.5',
            description='Remote code execution via crafted URL.',
        ),
        Advisory(
            package='django',
            cve_id='CVE-2023-0002',
            severity='warning',
            affected_specifier='>=2.0,<3.0',
            description='Open redirect vulnerability.',
        ),
        Advisory(
            package='flask',
            cve_id='CVE-2023-0003',
            severity='error',
            affected_specifier='>=0.10,<1.0',
            description='Session fixation vulnerability.',
        ),
    ]


def build_default_registry_packages() -> dict[str, dict]:
    """Return a small registry for testing."""
    return {
        'requests': {
            'name': 'requests',
            'versions': {
                '2.28.0': {'sha256': 'abc123', 'url': 'https://example.com/requests-2.28.0.tar.gz'},
                '1.2.3': {'sha256': 'def456', 'url': 'https://example.com/requests-1.2.3.tar.gz'},
            },
        },
        'flask': {
            'name': 'flask',
            'versions': {
                '2.3.0': {'sha256': 'ghi789', 'url': 'https://example.com/flask-2.3.0.tar.gz'},
            },
        },
        'django': {
            'name': 'django',
            'versions': {
                '4.2.0': {'sha256': 'jkl012', 'url': 'https://example.com/django-4.2.0.tar.gz'},
            },
        },
        'numpy': {
            'name': 'numpy',
            'versions': {
                '1.24.0': {'sha256': 'mno345', 'url': 'https://example.com/numpy-1.24.0.tar.gz'},
            },
        },
        'pytest': {
            'name': 'pytest',
            'versions': {
                '7.4.0': {'sha256': 'pqr678', 'url': 'https://example.com/pytest-7.4.0.tar.gz'},
            },
        },
    }


# ---------------------------------------------------------------------------
# TEETH: a FROZEN supply-chain admission corpus + planted real-world mutants.
#
# A supply-chain harness only has teeth if it REJECTS a package that fails any
# of the three core gates a lockfile-enforcing installer must apply:
#
#   1. the artifact bytes must hash to the sha256 pinned in the lockfile
#      (integrity / hash-pinning),
#   2. the version specifier must be an EXACT pin, never floating/unpinned
#      (reproducibility), and
#   3. the package name must be on the known-good allowlist; a typosquat /
#      lookalike (Levenshtein-1) or hallucinated name must NOT be admitted.
#
# The networked MockRegistry above is exercised over a real socket by the
# paired unittest. The teeth, by contrast, run a PURE in-process admission
# decision built from the harness's own PinningChecker / IntegrityChecker /
# NonexistentPackageChecker so the gate can verify "this harness catches a real
# supply-chain bug" with zero clock/network/filesystem I/O and full determinism.
#
# An "impl" is an admission decider:  admit(pkg: SupplyCase) -> "accept"|"reject".
# The oracle reuses the harness's correct checkers. Each Mutant is a faithful
# model of a genuine real-world defect (hash bypass, pin-skip, typosquat-allow).
# prove() judges a decider against the corpus's FROZEN expected verdicts (literal
# "accept"/"reject" constants computed by hand) -- NEVER against the oracle
# object at runtime -- so the check is non-circular.
# ---------------------------------------------------------------------------

ACCEPT = "accept"
REJECT = "reject"

# The frozen allowlist of known-good package names the admission gate trusts.
# 'flask' is present so 'flas'/'flassk' (Levenshtein-1) read as typosquats.
ALLOWED_PACKAGES: set[str] = {"requests", "flask", "django", "numpy", "pytest"}

# A frozen artifact + its real sha256, shared by the corpus. Computing the hash
# here (not hand-transcribing 64 hex chars) keeps the fixture honest; the
# admission VERDICTS below are the hand-authored literals prove() judges against.
_GOOD_ARTIFACT = b"requests-2.28.0 wheel bytes"
_GOOD_SHA = hashlib.sha256(_GOOD_ARTIFACT).hexdigest()


@dataclass(frozen=True)
class SupplyCase:
    """One frozen package-admission request with a hand-authored expected verdict."""
    name: str
    pkg_name: str
    specifier: str           # version specifier as declared in the manifest
    locked_sha256: str       # sha256 pinned in the lockfile for this artifact
    artifact: bytes          # the actual bytes the installer fetched
    note: str = ""


# Cases chosen so the correct oracle yields every expected verdict AND each
# planted mutant gets at least one WRONG. Verdicts are literals, NOT read from
# the oracle, which is what keeps prove() non-circular.
SUPPLY_CORPUS: tuple[SupplyCase, ...] = (
    # --- the fully clean package: pinned, hash matches, name allowlisted -----
    SupplyCase("clean_pinned_match", "requests", "==2.28.0", _GOOD_SHA, _GOOD_ARTIFACT,
               note="exact pin + matching hash + known name -> accept"),
    # --- TAMPERED artifact: bytes do NOT hash to the locked sha256 -----------
    # Teeth case for the integrity-bypass mutant: a checker that skips/short-
    # circuits the hash comparison would admit a swapped/poisoned artifact.
    SupplyCase("tampered_hash_mismatch", "requests", "==2.28.0", _GOOD_SHA,
               b"poisoned artifact bytes",
               note="artifact hash != locked sha256 -> reject (integrity)"),
    # --- FLOATING specifier: not an exact pin -------------------------------
    # Teeth case for the pin-skip mutant: a gate that doesn't enforce exact
    # pinning lets a range/floating version slip in (non-reproducible build).
    SupplyCase("floating_version", "flask", ">=2.0", _GOOD_SHA, _GOOD_ARTIFACT,
               note="floating/range specifier -> reject (must be exact pin)"),
    # --- UNPINNED: no version specifier at all -------------------------------
    SupplyCase("unpinned_no_version", "numpy", "", _GOOD_SHA, _GOOD_ARTIFACT,
               note="empty specifier (unpinned) -> reject"),
    # --- WILDCARD: latest/* -------------------------------------------------
    SupplyCase("wildcard_latest", "django", "latest", _GOOD_SHA, _GOOD_ARTIFACT,
               note="wildcard 'latest' specifier -> reject"),
    # --- TYPOSQUAT: name 1 edit from an allowlisted package -----------------
    # Teeth case for the typosquat-allow mutant: 'flassk' is Levenshtein-1 from
    # 'flask'. A gate that admits any not-found name (or only flags it without
    # rejecting) lets a lookalike package through.
    SupplyCase("typosquat_lookalike", "flassk", "==1.0.0", _GOOD_SHA, _GOOD_ARTIFACT,
               note="'flassk' is 1 edit from 'flask' -> reject (typosquat)"),
    # --- HALLUCINATED name: not on the allowlist, not a near-match ----------
    SupplyCase("hallucinated_name", "totally-made-up-pkg", "==1.0.0", _GOOD_SHA,
               _GOOD_ARTIFACT,
               note="name not in registry / allowlist -> reject (slopsquat)"),
)

# Literal expected verdicts, computed by hand from the admission contract --
# NEVER read back from the oracle object, which keeps prove() non-circular.
EXPECTED_VERDICTS: dict[str, str] = {
    "clean_pinned_match": ACCEPT,
    "tampered_hash_mismatch": REJECT,
    "floating_version": REJECT,
    "unpinned_no_version": REJECT,
    "wildcard_latest": REJECT,
    "typosquat_lookalike": REJECT,
    "hallucinated_name": REJECT,
}


# --- ORACLE: reuse the harness's own correct checkers -----------------------

def oracle_admit(case: SupplyCase) -> str:
    """Correct supply-chain admission decision.

    Accept iff ALL three gates pass: the name is allowlisted (no typosquat /
    hallucination), the specifier is an exact pin, and the artifact bytes hash
    to the locked sha256. Any failure -> reject. Reuses PinningChecker,
    IntegrityChecker and NonexistentPackageChecker so the oracle is the
    harness's own tested logic, not a re-implementation.
    """
    # Gate 1: name must be known / not a typosquat or hallucination.
    name_findings = NonexistentPackageChecker(ALLOWED_PACKAGES).check(case.pkg_name)
    if name_findings:
        return REJECT

    # Gate 2: the version specifier must be an exact pin.
    pin_findings = PinningChecker().check(case.pkg_name, case.specifier)
    if pin_findings:
        return REJECT

    # Gate 3: the artifact bytes must match the locked sha256.
    dep = LockedDep(case.pkg_name, _spec_version(case.specifier),
                    case.locked_sha256, "https://example.com/artifact")
    ok, _msg = IntegrityChecker().verify(dep, case.artifact)
    if not ok:
        return REJECT

    return ACCEPT


def _spec_version(specifier: str) -> str:
    """Extract a non-empty version string for LockedDep (it rejects empty)."""
    v = specifier.lstrip("=<>!~ ").strip()
    return v or "0"


# --- Planted buggy admission deciders (each models a real-world defect) ------

def mutant_skip_integrity(case: SupplyCase) -> str:
    """BUG: the integrity gate is skipped -- the locked hash is never compared.

    Models the classic 'trust the lockfile entry, don't re-verify the bytes'
    mistake (or a verify() that returns True on any non-empty hash). A swapped
    or poisoned artifact whose bytes do NOT match the pinned sha256 is admitted
    -- the single most dangerous supply-chain failure (dependency confusion /
    artifact substitution).
    """
    if NonexistentPackageChecker(ALLOWED_PACKAGES).check(case.pkg_name):
        return REJECT
    if PinningChecker().check(case.pkg_name, case.specifier):
        return REJECT
    # BUG: no hash verification at all -- the locked sha256 is ignored.
    return ACCEPT


def mutant_allow_unpinned(case: SupplyCase) -> str:
    """BUG: the pinning gate accepts floating/unpinned specifiers.

    Only an explicit wildcard ('*'/'latest') is rejected; a range like '>=2.0'
    or an empty (unpinned) specifier slips through. Models a gate that checks
    'is it not a wildcard' instead of 'is it an exact pin', yielding a
    non-reproducible build that can silently pull a newer (possibly compromised)
    version on the next install.
    """
    if NonexistentPackageChecker(ALLOWED_PACKAGES).check(case.pkg_name):
        return REJECT
    spec = case.specifier.strip().lower()
    # BUG: only the obvious wildcards are blocked; ranges/unpinned pass.
    if spec in PinningChecker.WILDCARD_VALUES or spec == "*":
        return REJECT
    dep = LockedDep(case.pkg_name, _spec_version(case.specifier) or "0",
                    case.locked_sha256, "https://example.com/artifact")
    ok, _msg = IntegrityChecker().verify(dep, case.artifact)
    return ACCEPT if ok else REJECT


def mutant_allow_typosquat(case: SupplyCase) -> str:
    """BUG: a typosquat / lookalike name is admitted instead of rejected.

    The name gate treats a Levenshtein-1 near-match as a mere 'warning' and
    lets it through (rejecting only outright-hallucinated names), so 'flassk'
    sails past as if it were 'flask'. Models the real failure where typosquat
    detection is advisory-only -- the lookalike package still gets installed.
    """
    findings = NonexistentPackageChecker(ALLOWED_PACKAGES).check(case.pkg_name)
    # BUG: only hard 'error' findings (unknown, no near-match) block admission;
    # a 'warning' typosquat finding is downgraded to non-blocking.
    if any(f.severity == "error" for f in findings):
        return REJECT
    if PinningChecker().check(case.pkg_name, case.specifier):
        return REJECT
    dep = LockedDep(case.pkg_name, _spec_version(case.specifier) or "0",
                    case.locked_sha256, "https://example.com/artifact")
    ok, _msg = IntegrityChecker().verify(dep, case.artifact)
    return ACCEPT if ok else REJECT


def prove(admit: Callable[[SupplyCase], str]) -> bool:
    """True iff ``admit`` MISDECIDES any frozen corpus case (i.e. is caught).

    Non-circular + deterministic: each verdict is compared against the literal
    EXPECTED_VERDICTS constant, never against the oracle object. No clock,
    network, filesystem I/O, or RNG. A decider that raises on a corpus case
    counts as caught.
    """
    for case in SUPPLY_CORPUS:
        expected = EXPECTED_VERDICTS[case.name]
        try:
            actual = admit(case)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if actual != expected:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_admit,
    mutants=(
        Mutant("skip_integrity", mutant_skip_integrity,
               "integrity gate skipped: a tampered artifact whose bytes do not "
               "match the locked sha256 is admitted (artifact substitution)"),
        Mutant("allow_unpinned", mutant_allow_unpinned,
               "pinning gate accepts floating/unpinned specifiers (only wildcards "
               "blocked) -> non-reproducible, version may drift on next install"),
        Mutant("allow_typosquat", mutant_allow_typosquat,
               "typosquat detection is advisory-only: a Levenshtein-1 lookalike "
               "name ('flassk' vs 'flask') is admitted instead of rejected"),
    ),
    corpus_size=len(SUPPLY_CORPUS),
    kind="oracle_swap",
    notes="admit a package only if its name is allowlisted (no typosquat), its "
          "version is an exact pin, and its artifact hashes to the locked sha256",
)


def list_scenarios() -> list[str]:
    """Names of the frozen admission corpus cases (the teeth scenarios)."""
    return [c.name for c in SUPPLY_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("security/supplychain")

    # 1. The correct oracle must yield every frozen expected verdict.
    for case in SUPPLY_CORPUS:
        expected = EXPECTED_VERDICTS[case.name]
        actual = oracle_admit(case)
        report.add(f"oracle_case:{case.name}", expected, actual, detail=case.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    # 3. Harness-specific invariants exercised directly against the checkers.
    ic = IntegrityChecker()
    good_dep = LockedDep("requests", "2.28.0", _GOOD_SHA, "https://example.com")
    report.record("integrity_accepts_matching_hash",
                  ic.verify(good_dep, _GOOD_ARTIFACT)[0],
                  detail="matching artifact bytes must verify OK")
    report.record("integrity_rejects_tampered_hash",
                  not ic.verify(good_dep, b"tampered")[0],
                  detail="mismatched artifact bytes must fail integrity")
    pc = PinningChecker()
    report.record("pinning_accepts_exact", pc.check("p", "==1.2.3") == [],
                  detail="an exact pin must produce no finding")
    report.record("pinning_rejects_floating", bool(pc.check("p", ">=1.0")),
                  detail="a floating specifier must be flagged")
    nec = NonexistentPackageChecker(ALLOWED_PACKAGES)
    report.record("typosquat_flagged", bool(nec.check("flassk")),
                  detail="a Levenshtein-1 lookalike must produce a finding")

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supply-Chain / Build Reproducibility Test Harness")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen admission corpus case names")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
