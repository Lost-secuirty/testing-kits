"""
Supply-Chain / Build Reproducibility Test Harness (Harness 34 of 36)
Pure stdlib, zero external dependencies.
Mock HTTP server on dynamic port (default 19200).
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import http.server
import json
import re
import socket
import threading
import time
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple


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
    details: Optional[str] = None


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

    def check(self, name: str, specifier: str) -> List[FindingRecord]:
        findings: List[FindingRecord] = []
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

    def check_all(self, deps: Dict[str, str]) -> List[FindingRecord]:
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

    def verify(self, dep: LockedDep, artifact_bytes: bytes) -> Tuple[bool, str]:
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
        self, locked: List[LockedDep], artifacts: Dict[str, bytes]
    ) -> List[FindingRecord]:
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
        manifest_packages: Set[str],
        locked_packages: Set[str],
    ) -> List[FindingRecord]:
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

    def __init__(self, known_packages: Set[str]):
        self.known_packages: Set[str] = {p.lower() for p in known_packages}

    def check(self, name: str) -> List[FindingRecord]:
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

    def check_all(self, names: List[str]) -> List[FindingRecord]:
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
    ) -> Tuple[bool, List[FindingRecord]]:
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
                if op == '>=' and not (ver >= bound):
                    return False
                elif op == '<=' and not (ver <= bound):
                    return False
                elif op == '!=' and not (ver != bound):
                    return False
                elif op == '==' and not (ver == bound):
                    return False
                elif op == '>' and not (ver > bound):
                    return False
                elif op == '<' and not (ver < bound):
                    return False
                break
    return True


class KnownVulnChecker:
    """Matches locked dependencies against a mock advisory database."""

    def __init__(self, advisories: List[Advisory]):
        self.advisories = advisories

    def check(self, dep: LockedDep) -> List[FindingRecord]:
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

    def check_all(self, deps: List[LockedDep]) -> List[FindingRecord]:
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
        root_deps: List[str],
        dep_graph: Dict[str, List[str]],
        lockfile_names: Set[str],
    ) -> Tuple[Set[str], List[FindingRecord]]:
        """
        BFS from root_deps through dep_graph.
        Returns (all_transitive_names, findings).
        """
        visited: Set[str] = set()
        queue = list(root_deps)
        findings: List[FindingRecord] = []

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
        self.findings: List[FindingRecord] = []

    def add(self, findings: List[FindingRecord]) -> None:
        self.findings.extend(findings)

    def errors(self) -> List[FindingRecord]:
        return [f for f in self.findings if f.severity == 'error']

    def warnings(self) -> List[FindingRecord]:
        return [f for f in self.findings if f.severity == 'warning']

    def infos(self) -> List[FindingRecord]:
        return [f for f in self.findings if f.severity == 'info']

    def has_errors(self) -> bool:
        return bool(self.errors())

    def summary(self) -> str:
        e = len(self.errors())
        w = len(self.warnings())
        i = len(self.infos())
        return f"SupplyChainReport: {e} error(s), {w} warning(s), {i} info(s)"

    def findings_for(self, package: str) -> List[FindingRecord]:
        return [f for f in self.findings if f.package.lower() == package.lower()]

    def checkers_reported(self) -> Set[str]:
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

_REGISTRY_DATA: Dict[str, dict] = {}
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

    def __init__(self, port: int = 0, initial_packages: Optional[Dict] = None):
        self.port = port  # 0 = OS assigns
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
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
        if self._server:
            self._server.shutdown()
            self._server = None

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def register(self, name: str, data: dict) -> None:
        """Programmatically register a package without HTTP."""
        with _REGISTRY_LOCK:
            _REGISTRY_DATA[name.lower()] = data

    def lookup(self, name: str) -> Optional[dict]:
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

    def check(self, name: str) -> Tuple[bool, Optional[dict]]:
        """Returns (found, metadata_or_None)."""
        url = f"{self.registry.base_url()}/packages/{name.lower()}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True, json.loads(resp.read())
        except Exception:
            pass
        return False, None

    def check_all(self, names: List[str]) -> List[FindingRecord]:
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

def build_default_advisories() -> List[Advisory]:
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


def build_default_registry_packages() -> Dict[str, dict]:
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
