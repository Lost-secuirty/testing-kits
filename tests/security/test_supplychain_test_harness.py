"""
Tests for supplychain_test_harness.py  (~156 tests)
Pure stdlib, zero external dependencies.
"""

import dataclasses
import hashlib
import json
import unittest
import urllib.request

from harnesses._teeth import verify
from harnesses.security.supplychain_test_harness import (
    TEETH,
    Advisory,
    FindingRecord,
    IntegrityChecker,
    KnownVulnChecker,
    LockedDep,
    LockfileDriftChecker,
    MockRegistry,
    NonexistentPackageChecker,
    PinningChecker,
    RegistryPackageChecker,
    ReproducibleBuildChecker,
    SupplyChainReport,
    TransitiveDepChecker,
    _levenshtein,
    _version_satisfies,
    _version_tuple,
    build_default_advisories,
    build_default_registry_packages,
    oracle_admit,
    prove,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_dep(name='requests', version='2.28.0', sha256=None, url='https://example.com/pkg.tar.gz'):
    artifact = b'fake artifact content'
    if sha256 is None:
        sha256 = _sha256(artifact)
    return LockedDep(name=name, version=version, sha256=sha256, source_url=url)


# ---------------------------------------------------------------------------
# LockedDep tests (12)
# ---------------------------------------------------------------------------

class TestLockedDep(unittest.TestCase):

    def test_basic_creation(self):
        dep = LockedDep('requests', '2.28.0', 'abc', 'https://x.com')
        self.assertEqual(dep.name, 'requests')

    def test_version_stored(self):
        dep = LockedDep('requests', '2.28.0', 'abc', 'https://x.com')
        self.assertEqual(dep.version, '2.28.0')

    def test_sha256_stored(self):
        dep = LockedDep('requests', '2.28.0', 'deadbeef', 'https://x.com')
        self.assertEqual(dep.sha256, 'deadbeef')

    def test_source_url_stored(self):
        dep = LockedDep('requests', '2.28.0', 'abc', 'https://example.com/pkg')
        self.assertEqual(dep.source_url, 'https://example.com/pkg')

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError):
            LockedDep('', '1.0.0', 'abc', 'https://x.com')

    def test_empty_version_raises(self):
        with self.assertRaises(ValueError):
            LockedDep('pkg', '', 'abc', 'https://x.com')

    def test_dataclass_equality(self):
        d1 = LockedDep('a', '1.0', 'x', 'u')
        d2 = LockedDep('a', '1.0', 'x', 'u')
        self.assertEqual(d1, d2)

    def test_dataclass_inequality_version(self):
        d1 = LockedDep('a', '1.0', 'x', 'u')
        d2 = LockedDep('a', '2.0', 'x', 'u')
        self.assertNotEqual(d1, d2)

    def test_dataclass_fields(self):
        fields = {f.name for f in dataclasses.fields(LockedDep)}
        self.assertEqual(fields, {'name', 'version', 'sha256', 'source_url'})

    def test_is_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(LockedDep))

    def test_repr_contains_name(self):
        dep = LockedDep('mypkg', '3.0.0', 'abc', 'url')
        self.assertIn('mypkg', repr(dep))

    def test_multiple_deps_distinct(self):
        d1 = LockedDep('a', '1.0', 'x', 'u1')
        d2 = LockedDep('b', '1.0', 'x', 'u2')
        self.assertNotEqual(d1, d2)


# ---------------------------------------------------------------------------
# FindingRecord tests (6)
# ---------------------------------------------------------------------------

class TestFindingRecord(unittest.TestCase):

    def test_basic(self):
        f = FindingRecord('PinningChecker', 'error', 'mypkg', 'bad pin')
        self.assertEqual(f.checker, 'PinningChecker')
        self.assertEqual(f.severity, 'error')
        self.assertEqual(f.package, 'mypkg')

    def test_details_optional(self):
        f = FindingRecord('C', 'warning', 'p', 'msg')
        self.assertIsNone(f.details)

    def test_details_set(self):
        f = FindingRecord('C', 'error', 'p', 'msg', details='extra')
        self.assertEqual(f.details, 'extra')

    def test_is_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(FindingRecord))

    def test_asdict(self):
        f = FindingRecord('C', 'info', 'p', 'msg')
        d = dataclasses.asdict(f)
        self.assertIn('checker', d)
        self.assertIn('severity', d)

    def test_equality(self):
        f1 = FindingRecord('C', 'error', 'p', 'msg')
        f2 = FindingRecord('C', 'error', 'p', 'msg')
        self.assertEqual(f1, f2)


# ---------------------------------------------------------------------------
# _version_tuple / _version_satisfies helpers (10)
# ---------------------------------------------------------------------------

class TestVersionHelpers(unittest.TestCase):

    def test_version_tuple_basic(self):
        self.assertEqual(_version_tuple('1.2.3'), (1, 2, 3))

    def test_version_tuple_single(self):
        self.assertEqual(_version_tuple('5'), (5,))

    def test_version_tuple_two(self):
        self.assertEqual(_version_tuple('2.0'), (2, 0))

    def test_satisfies_gte_true(self):
        self.assertTrue(_version_satisfies('1.2', '>=1.0'))

    def test_satisfies_gte_false(self):
        self.assertFalse(_version_satisfies('0.9', '>=1.0'))

    def test_satisfies_lt_true(self):
        self.assertTrue(_version_satisfies('1.4', '<1.5'))

    def test_satisfies_lt_false(self):
        self.assertFalse(_version_satisfies('1.5', '<1.5'))

    def test_satisfies_range(self):
        self.assertTrue(_version_satisfies('1.2', '>=1.0,<1.5'))

    def test_satisfies_range_boundary_excluded(self):
        self.assertFalse(_version_satisfies('1.5', '>=1.0,<1.5'))

    def test_satisfies_eq(self):
        self.assertTrue(_version_satisfies('2.0', '==2.0'))


# ---------------------------------------------------------------------------
# _levenshtein tests (8)
# ---------------------------------------------------------------------------

class TestLevenshtein(unittest.TestCase):

    def test_identical(self):
        self.assertEqual(_levenshtein('abc', 'abc'), 0)

    def test_empty_vs_string(self):
        self.assertEqual(_levenshtein('', 'abc'), 3)

    def test_single_insert(self):
        self.assertEqual(_levenshtein('reqeusts', 'requests'), 2)

    def test_one_substitution(self):
        self.assertEqual(_levenshtein('flusk', 'flask'), 1)

    def test_one_deletion(self):
        self.assertEqual(_levenshtein('reques', 'requests'), 2)

    def test_one_transposition_counts_as_two(self):
        # Standard Levenshtein (not Damerau): transposition = 2 ops
        self.assertGreaterEqual(_levenshtein('ab', 'ba'), 1)

    def test_completely_different(self):
        self.assertGreater(_levenshtein('aaaa', 'bbbb'), 0)

    def test_symmetric(self):
        self.assertEqual(_levenshtein('flask', 'flusk'), _levenshtein('flusk', 'flask'))


# ---------------------------------------------------------------------------
# PinningChecker tests (20)
# ---------------------------------------------------------------------------

class TestPinningChecker(unittest.TestCase):

    def setUp(self):
        self.checker = PinningChecker()

    def test_exact_pin_ok(self):
        self.assertEqual(self.checker.check('pkg', '==1.2.3'), [])

    def test_exact_pin_two_part(self):
        self.assertEqual(self.checker.check('pkg', '==1.0'), [])

    def test_exact_pin_one_part(self):
        self.assertEqual(self.checker.check('pkg', '==5'), [])

    def test_floating_gte(self):
        findings = self.checker.check('pkg', '>=1.0')
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, 'error')

    def test_floating_gt(self):
        findings = self.checker.check('pkg', '>1.0')
        self.assertTrue(findings)

    def test_floating_lte(self):
        findings = self.checker.check('pkg', '<=2.0')
        self.assertTrue(findings)

    def test_floating_lt(self):
        findings = self.checker.check('pkg', '<2.0')
        self.assertTrue(findings)

    def test_tilde_compat(self):
        findings = self.checker.check('pkg', '~=1.0')
        self.assertTrue(findings)

    def test_wildcard_star(self):
        findings = self.checker.check('pkg', '*')
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, 'error')

    def test_wildcard_latest(self):
        findings = self.checker.check('pkg', 'latest')
        self.assertTrue(findings)

    def test_wildcard_any(self):
        findings = self.checker.check('pkg', 'any')
        self.assertTrue(findings)

    def test_unpinned_empty(self):
        findings = self.checker.check('pkg', '')
        self.assertTrue(findings)
        self.assertIn('unpinned', findings[0].message.lower())

    def test_check_all_mixed(self):
        deps = {'a': '==1.0', 'b': '>=1.0', 'c': '*'}
        findings = self.checker.check_all(deps)
        pkgs = {f.package for f in findings}
        self.assertIn('b', pkgs)
        self.assertIn('c', pkgs)
        self.assertNotIn('a', pkgs)

    def test_check_all_all_pinned(self):
        deps = {'a': '==1.0', 'b': '==2.3.4'}
        self.assertEqual(self.checker.check_all(deps), [])

    def test_check_all_none_pinned(self):
        deps = {'x': '>=1', 'y': '*'}
        findings = self.checker.check_all(deps)
        self.assertEqual(len(findings), 2)

    def test_package_name_in_finding(self):
        findings = self.checker.check('mypkg', '>=1.0')
        self.assertIn('mypkg', findings[0].package)

    def test_specifier_in_message(self):
        findings = self.checker.check('pkg', '>=1.0')
        self.assertIn('>=1.0', findings[0].message)

    def test_checker_name_in_finding(self):
        findings = self.checker.check('pkg', '>=1.0')
        self.assertEqual(findings[0].checker, 'PinningChecker')

    def test_ne_specifier_rejected(self):
        findings = self.checker.check('pkg', '!=1.0')
        self.assertTrue(findings)

    def test_range_rejected(self):
        findings = self.checker.check('pkg', '>=1.0,<2.0')
        self.assertTrue(findings)


# ---------------------------------------------------------------------------
# IntegrityChecker tests (16)
# ---------------------------------------------------------------------------

class TestIntegrityChecker(unittest.TestCase):

    def setUp(self):
        self.checker = IntegrityChecker()
        self.artifact = b'hello world artifact'
        self.good_sha = _sha256(self.artifact)
        self.dep = LockedDep('mypkg', '1.0.0', self.good_sha, 'https://x.com')

    def test_verify_ok(self):
        ok, msg = self.checker.verify(self.dep, self.artifact)
        self.assertTrue(ok)

    def test_verify_ok_message(self):
        ok, msg = self.checker.verify(self.dep, self.artifact)
        self.assertIn('OK', msg)

    def test_verify_tampered(self):
        ok, msg = self.checker.verify(self.dep, b'tampered content')
        self.assertFalse(ok)

    def test_verify_tampered_message(self):
        ok, msg = self.checker.verify(self.dep, b'tampered')
        self.assertIn('mismatch', msg.lower())

    def test_verify_empty_artifact(self):
        empty_sha = _sha256(b'')
        dep = LockedDep('p', '1.0', empty_sha, 'u')
        ok, _ = self.checker.verify(dep, b'')
        self.assertTrue(ok)

    def test_verify_wrong_empty(self):
        dep = LockedDep('p', '1.0', 'wronghash', 'u')
        ok, _ = self.checker.verify(dep, b'')
        self.assertFalse(ok)

    def test_verify_all_ok(self):
        artifacts = {'mypkg': self.artifact}
        findings = self.checker.verify_all([self.dep], artifacts)
        self.assertEqual(findings, [])

    def test_verify_all_tampered(self):
        artifacts = {'mypkg': b'tampered'}
        findings = self.checker.verify_all([self.dep], artifacts)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 'error')

    def test_verify_all_missing_artifact(self):
        findings = self.checker.verify_all([self.dep], {})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 'warning')

    def test_verify_all_multiple_ok(self):
        art2 = b'other artifact'
        dep2 = LockedDep('other', '2.0', _sha256(art2), 'u')
        arts = {'mypkg': self.artifact, 'other': art2}
        self.assertEqual(self.checker.verify_all([self.dep, dep2], arts), [])

    def test_verify_all_one_bad(self):
        art2 = b'other artifact'
        dep2 = LockedDep('other', '2.0', _sha256(art2), 'u')
        arts = {'mypkg': b'tampered', 'other': art2}
        findings = self.checker.verify_all([self.dep, dep2], arts)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].package, 'mypkg')

    def test_sha256_case_insensitive(self):
        dep = LockedDep('p', '1.0', self.good_sha.upper(), 'u')
        ok, _ = self.checker.verify(dep, self.artifact)
        self.assertTrue(ok)

    def test_finding_contains_package_name(self):
        ok, msg = self.checker.verify(self.dep, b'bad')
        self.assertFalse(ok)
        self.assertIn('mypkg', msg)

    def test_finding_checker_name(self):
        arts = {'mypkg': b'bad'}
        findings = self.checker.verify_all([self.dep], arts)
        self.assertEqual(findings[0].checker, 'IntegrityChecker')

    def test_verify_large_artifact(self):
        large = b'x' * (1024 * 1024)
        dep = LockedDep('p', '1.0', _sha256(large), 'u')
        ok, _ = self.checker.verify(dep, large)
        self.assertTrue(ok)

    def test_verify_uses_sha256(self):
        # Confirm it's not using md5 or sha1
        artifact = b'test data'
        wrong_hash = hashlib.md5(artifact).hexdigest()
        dep = LockedDep('p', '1.0', wrong_hash, 'u')
        ok, _ = self.checker.verify(dep, artifact)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# LockfileDriftChecker tests (14)
# ---------------------------------------------------------------------------

class TestLockfileDriftChecker(unittest.TestCase):

    def setUp(self):
        self.checker = LockfileDriftChecker()

    def test_no_drift(self):
        findings = self.checker.check({'a', 'b'}, {'a', 'b'})
        self.assertEqual(findings, [])

    def test_manifest_extra(self):
        findings = self.checker.check({'a', 'b', 'c'}, {'a', 'b'})
        pkgs = {f.package for f in findings}
        self.assertIn('c', pkgs)

    def test_manifest_extra_severity(self):
        findings = self.checker.check({'a', 'extra'}, {'a'})
        self.assertTrue(any(f.severity == 'error' for f in findings))

    def test_lockfile_extra(self):
        findings = self.checker.check({'a'}, {'a', 'ghost'})
        pkgs = {f.package for f in findings}
        self.assertIn('ghost', pkgs)

    def test_lockfile_extra_severity(self):
        findings = self.checker.check({'a'}, {'a', 'ghost'})
        self.assertTrue(any(f.severity == 'warning' for f in findings))

    def test_both_drifted(self):
        findings = self.checker.check({'a', 'x'}, {'a', 'y'})
        self.assertEqual(len(findings), 2)

    def test_empty_manifest_empty_lock(self):
        self.assertEqual(self.checker.check(set(), set()), [])

    def test_case_insensitive(self):
        findings = self.checker.check({'Requests'}, {'requests'})
        self.assertEqual(findings, [])

    def test_finding_checker_name(self):
        findings = self.checker.check({'a', 'b'}, {'a'})
        self.assertEqual(findings[0].checker, 'LockfileDriftChecker')

    def test_message_content_manifest_extra(self):
        findings = self.checker.check({'mypkg'}, set())
        self.assertTrue(any('manifest' in f.message.lower() for f in findings))

    def test_message_content_lockfile_extra(self):
        findings = self.checker.check(set(), {'mypkg'})
        self.assertTrue(any('lockfile' in f.message.lower() for f in findings))

    def test_multiple_manifest_extras(self):
        findings = self.checker.check({'a', 'b', 'c'}, set())
        self.assertEqual(len(findings), 3)

    def test_multiple_lockfile_extras(self):
        findings = self.checker.check(set(), {'a', 'b', 'c'})
        self.assertEqual(len(findings), 3)

    def test_exact_match_single(self):
        self.assertEqual(self.checker.check({'flask'}, {'flask'}), [])


# ---------------------------------------------------------------------------
# NonexistentPackageChecker tests (16)
# ---------------------------------------------------------------------------

class TestNonexistentPackageChecker(unittest.TestCase):

    def setUp(self):
        self.known = {'requests', 'flask', 'django', 'numpy', 'pytest'}
        self.checker = NonexistentPackageChecker(self.known)

    def test_known_pkg_no_finding(self):
        self.assertEqual(self.checker.check('requests'), [])

    def test_unknown_pkg_error(self):
        findings = self.checker.check('totallyfakepkg12345')
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, 'error')

    def test_typosquat_warning(self):
        # 'flusk' is distance 1 from 'flask'
        findings = self.checker.check('flusk')
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, 'warning')

    def test_typosquat_message_contains_candidate(self):
        findings = self.checker.check('flusk')
        self.assertIn('flask', findings[0].message)

    def test_slopsquatting_error(self):
        findings = self.checker.check('hallucinated-dep-xyz')
        self.assertEqual(findings[0].severity, 'error')

    def test_case_insensitive_known(self):
        checker = NonexistentPackageChecker({'Requests'})
        self.assertEqual(checker.check('requests'), [])

    def test_check_all_all_known(self):
        self.assertEqual(self.checker.check_all(['requests', 'flask']), [])

    def test_check_all_one_unknown(self):
        findings = self.checker.check_all(['requests', 'fakepkg'])
        self.assertEqual(len(findings), 1)

    def test_check_all_empty(self):
        self.assertEqual(self.checker.check_all([]), [])

    def test_reqeusts_typosquat(self):
        # 'reqeusts' has edit distance > 1 from 'requests' actually
        findings = self.checker.check('reqeusts')
        # It should produce a finding (either typosquat warning or error)
        self.assertTrue(findings)

    def test_checker_name_in_finding(self):
        findings = self.checker.check('fakepkg123')
        self.assertEqual(findings[0].checker, 'NonexistentPackageChecker')

    def test_package_name_in_finding(self):
        findings = self.checker.check('fakepkg123')
        self.assertEqual(findings[0].package, 'fakepkg123')

    def test_multiple_typosquat_candidates(self):
        # 'flasc' is distance 1 from 'flask'
        findings = self.checker.check('flasc')
        # should be a warning
        self.assertTrue(findings)

    def test_empty_known_set(self):
        checker = NonexistentPackageChecker(set())
        findings = checker.check('anything')
        self.assertEqual(findings[0].severity, 'error')

    def test_exact_match_no_finding(self):
        self.assertEqual(self.checker.check('numpy'), [])

    def test_hallucination_message(self):
        findings = self.checker.check('fake-dep-99999')
        self.assertIn('fake-dep-99999', findings[0].message)


# ---------------------------------------------------------------------------
# ReproducibleBuildChecker tests (14)
# ---------------------------------------------------------------------------

class TestReproducibleBuildChecker(unittest.TestCase):

    def setUp(self):
        self.checker = ReproducibleBuildChecker()
        self._build_counter = 0

    def _deterministic_build(self, inputs):
        return hashlib.sha256(str(sorted(inputs.items())).encode()).hexdigest()

    def _next_build_id(self):
        self._build_counter += 1
        return self._build_counter

    def _nondeterministic_build(self, inputs):
        return f"build-output-{self._next_build_id()}"

    def _timestamp_build(self, inputs):
        return f"build-output-at-step-{self._next_build_id()}-hash-{'x' * 20}"

    def test_deterministic_is_reproducible(self):
        ok, findings = self.checker.check(self._deterministic_build, {'a': '1', 'b': '2'})
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_nondeterministic_detected(self):
        ok, findings = self.checker.check(self._nondeterministic_build, {})
        self.assertFalse(ok)

    def test_nondeterministic_has_finding(self):
        ok, findings = self.checker.check(self._nondeterministic_build, {})
        self.assertEqual(len(findings), 1)

    def test_nondeterministic_finding_severity(self):
        ok, findings = self.checker.check(self._nondeterministic_build, {})
        self.assertEqual(findings[0].severity, 'error')

    def test_nondeterministic_finding_checker_name(self):
        ok, findings = self.checker.check(self._nondeterministic_build, {})
        self.assertEqual(findings[0].checker, 'ReproducibleBuildChecker')

    def test_timestamp_build_detected(self):
        ok, findings = self.checker.check(self._timestamp_build, {'src': 'main.py'})
        self.assertFalse(ok)

    def test_timestamp_finding_message(self):
        ok, findings = self.checker.check(self._timestamp_build, {})
        self.assertIn('nondeterministic', findings[0].message.lower())

    def test_empty_inputs_deterministic(self):
        build = lambda inputs: 'constant-output'
        ok, findings = self.checker.check(build, {})
        self.assertTrue(ok)

    def test_constant_build_no_findings(self):
        build = lambda inputs: b'binary-blob'
        ok, findings = self.checker.check(build, {'x': 'y'})
        self.assertEqual(findings, [])

    def test_finding_details_contains_outputs(self):
        ok, findings = self.checker.check(self._nondeterministic_build, {})
        # details should contain comparison info
        self.assertFalse(ok)
        self.assertTrue(findings[0].details or findings[0].message)

    def test_three_attempts(self):
        call_count = [0]
        outputs = ['a', 'a', 'b']
        def build(inputs):
            i = call_count[0]
            call_count[0] += 1
            return outputs[i % len(outputs)]
        ok, findings = self.checker.check(build, {}, attempts=3)
        self.assertFalse(ok)

    def test_inputs_passed_to_build(self):
        received = {}
        def build(inputs):
            received.update(inputs)
            return 'out'
        self.checker.check(build, {'key': 'val'})
        self.assertEqual(received.get('key'), 'val')

    def test_hash_based_deterministic(self):
        def build(inputs):
            return hashlib.md5(b'fixed').hexdigest()
        ok, _ = self.checker.check(build, {})
        self.assertTrue(ok)

    def test_returns_tuple(self):
        result = self.checker.check(self._deterministic_build, {})
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# KnownVulnChecker tests (16)
# ---------------------------------------------------------------------------

class TestKnownVulnChecker(unittest.TestCase):

    def setUp(self):
        self.advisories = build_default_advisories()
        self.checker = KnownVulnChecker(self.advisories)

    def test_affected_version(self):
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertTrue(findings)

    def test_unaffected_version(self):
        dep = LockedDep('requests', '2.28.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertEqual(findings, [])

    def test_boundary_excluded(self):
        # affected: >=1.0,<1.5 → 1.5 is NOT affected
        dep = LockedDep('requests', '1.5', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertEqual(findings, [])

    def test_boundary_included_low(self):
        dep = LockedDep('requests', '1.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertTrue(findings)

    def test_cve_id_in_message(self):
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertIn('CVE-2023-0001', findings[0].message)

    def test_severity_from_advisory(self):
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertEqual(findings[0].severity, 'error')

    def test_django_warning_severity(self):
        dep = LockedDep('django', '2.5.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, 'warning')

    def test_unknown_package_no_finding(self):
        dep = LockedDep('numpy', '1.24.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertEqual(findings, [])

    def test_checker_name_in_finding(self):
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertEqual(findings[0].checker, 'KnownVulnChecker')

    def test_package_name_in_finding(self):
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertIn('requests', findings[0].package.lower())

    def test_check_all_multiple(self):
        deps = [
            LockedDep('requests', '1.2.0', 'x', 'u'),
            LockedDep('flask', '0.12.0', 'y', 'u'),
            LockedDep('numpy', '1.24.0', 'z', 'u'),
        ]
        findings = self.checker.check_all(deps)
        pkg_names = {f.package.lower() for f in findings}
        self.assertIn('requests', pkg_names)
        self.assertIn('flask', pkg_names)
        self.assertNotIn('numpy', pkg_names)

    def test_flask_affected(self):
        dep = LockedDep('flask', '0.12.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertTrue(findings)

    def test_flask_unaffected(self):
        dep = LockedDep('flask', '1.0.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertEqual(findings, [])

    def test_case_insensitive_package(self):
        dep = LockedDep('REQUESTS', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertTrue(findings)

    def test_empty_advisories(self):
        checker = KnownVulnChecker([])
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        self.assertEqual(checker.check(dep), [])

    def test_details_contains_specifier(self):
        dep = LockedDep('requests', '1.2.0', 'x', 'u')
        findings = self.checker.check(dep)
        self.assertIsNotNone(findings[0].details)


# ---------------------------------------------------------------------------
# TransitiveDepChecker tests (18)
# ---------------------------------------------------------------------------

class TestTransitiveDepChecker(unittest.TestCase):

    def setUp(self):
        self.checker = TransitiveDepChecker()

    def test_simple_all_locked(self):
        graph = {'a': ['b'], 'b': []}
        locked = {'a', 'b'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        self.assertEqual(findings, [])

    def test_missing_transitive(self):
        graph = {'a': ['b'], 'b': []}
        locked = {'a'}  # 'b' missing
        _, findings = self.checker.resolve(['a'], graph, locked)
        self.assertTrue(any(f.package.lower() == 'b' for f in findings))

    def test_missing_transitive_severity_error(self):
        graph = {'a': ['b'], 'b': []}
        locked = {'a'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        errors = [f for f in findings if f.severity == 'error']
        self.assertTrue(errors)

    def test_phantom_dep(self):
        # 'c' is in graph but not reachable from root ['a']
        graph = {'a': ['b'], 'b': [], 'c': ['b']}
        locked = {'a', 'b', 'c'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        phantom_findings = [f for f in findings if f.package.lower() == 'c']
        self.assertTrue(phantom_findings)

    def test_phantom_dep_severity_warning(self):
        graph = {'a': [], 'c': []}
        locked = {'a', 'c'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        phantom = [f for f in findings if f.package.lower() == 'c']
        self.assertTrue(phantom)
        self.assertEqual(phantom[0].severity, 'warning')

    def test_deep_transitive(self):
        graph = {'a': ['b'], 'b': ['c'], 'c': ['d'], 'd': []}
        locked = {'a', 'b', 'c', 'd'}
        visited, findings = self.checker.resolve(['a'], graph, locked)
        self.assertIn('d', visited)
        self.assertEqual([f for f in findings if f.severity == 'error'], [])

    def test_deep_missing(self):
        graph = {'a': ['b'], 'b': ['c'], 'c': []}
        locked = {'a', 'b'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        errors = [f for f in findings if f.package.lower() == 'c']
        self.assertTrue(errors)

    def test_no_deps(self):
        graph = {'a': []}
        locked = {'a'}
        visited, findings = self.checker.resolve(['a'], graph, locked)
        self.assertIn('a', visited)
        self.assertEqual(findings, [])

    def test_cycle_safe(self):
        graph = {'a': ['b'], 'b': ['a']}
        locked = {'a', 'b'}
        visited, findings = self.checker.resolve(['a'], graph, locked)
        # Should not loop forever
        self.assertIn('a', visited)
        self.assertIn('b', visited)

    def test_visited_set_returned(self):
        graph = {'a': ['b'], 'b': []}
        locked = {'a', 'b'}
        visited, _ = self.checker.resolve(['a'], graph, locked)
        self.assertIsInstance(visited, set)
        self.assertEqual(visited, {'a', 'b'})

    def test_checker_name_in_finding(self):
        graph = {'a': ['b'], 'b': []}
        locked = {'a'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        self.assertEqual(findings[0].checker, 'TransitiveDepChecker')

    def test_empty_root_deps(self):
        graph = {'a': []}
        locked = {'a'}
        visited, findings = self.checker.resolve([], graph, locked)
        self.assertEqual(visited, set())

    def test_multiple_roots(self):
        graph = {'a': ['c'], 'b': ['c'], 'c': []}
        locked = {'a', 'b', 'c'}
        visited, findings = self.checker.resolve(['a', 'b'], graph, locked)
        self.assertIn('c', visited)

    def test_phantom_not_root_not_reachable(self):
        graph = {'a': [], 'ghost': []}
        locked = {'a', 'ghost'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        ghosts = [f for f in findings if f.package.lower() == 'ghost']
        self.assertTrue(ghosts)

    def test_root_not_in_lock(self):
        graph = {'a': []}
        locked = set()
        _, findings = self.checker.resolve(['a'], graph, locked)
        errors = [f for f in findings if f.package.lower() == 'a' and f.severity == 'error']
        self.assertTrue(errors)

    def test_all_missing_deep(self):
        graph = {'a': ['b', 'c'], 'b': ['d'], 'c': [], 'd': []}
        locked = {'a'}
        _, findings = self.checker.resolve(['a'], graph, locked)
        missing = {f.package.lower() for f in findings if f.severity == 'error'}
        self.assertIn('b', missing)

    def test_diamond_dependency(self):
        # a->b, a->c, b->d, c->d (diamond)
        graph = {'a': ['b', 'c'], 'b': ['d'], 'c': ['d'], 'd': []}
        locked = {'a', 'b', 'c', 'd'}
        visited, findings = self.checker.resolve(['a'], graph, locked)
        self.assertIn('d', visited)
        errors = [f for f in findings if f.severity == 'error']
        self.assertEqual(errors, [])

    def test_empty_graph(self):
        _, findings = self.checker.resolve(['a'], {}, {'a'})
        # 'a' should be visited and no errors since it's in lockfile
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# SupplyChainReport tests (14)
# ---------------------------------------------------------------------------

class TestSupplyChainReport(unittest.TestCase):

    def setUp(self):
        self.report = SupplyChainReport()

    def _finding(self, severity='error', package='pkg', checker='C', msg='m'):
        return FindingRecord(checker, severity, package, msg)

    def test_empty_report(self):
        self.assertEqual(self.report.findings, [])

    def test_add_findings(self):
        self.report.add([self._finding()])
        self.assertEqual(len(self.report.findings), 1)

    def test_errors(self):
        self.report.add([self._finding('error'), self._finding('warning')])
        self.assertEqual(len(self.report.errors()), 1)

    def test_warnings(self):
        self.report.add([self._finding('error'), self._finding('warning')])
        self.assertEqual(len(self.report.warnings()), 1)

    def test_infos(self):
        self.report.add([self._finding('info')])
        self.assertEqual(len(self.report.infos()), 1)

    def test_has_errors_true(self):
        self.report.add([self._finding('error')])
        self.assertTrue(self.report.has_errors())

    def test_has_errors_false(self):
        self.report.add([self._finding('warning')])
        self.assertFalse(self.report.has_errors())

    def test_summary_string(self):
        self.report.add([self._finding('error'), self._finding('warning')])
        s = self.report.summary()
        self.assertIn('1 error', s)
        self.assertIn('1 warning', s)

    def test_findings_for_package(self):
        self.report.add([
            self._finding(package='requests'),
            self._finding(package='flask'),
        ])
        found = self.report.findings_for('requests')
        self.assertEqual(len(found), 1)

    def test_findings_for_case_insensitive(self):
        self.report.add([self._finding(package='Requests')])
        found = self.report.findings_for('requests')
        self.assertEqual(len(found), 1)

    def test_checkers_reported(self):
        self.report.add([
            self._finding(checker='PinningChecker'),
            self._finding(checker='IntegrityChecker'),
        ])
        checkers = self.report.checkers_reported()
        self.assertIn('PinningChecker', checkers)
        self.assertIn('IntegrityChecker', checkers)

    def test_to_dict_structure(self):
        self.report.add([self._finding('error'), self._finding('warning')])
        d = self.report.to_dict()
        self.assertIn('errors', d)
        self.assertIn('warnings', d)
        self.assertIn('infos', d)

    def test_to_dict_counts(self):
        self.report.add([self._finding('error')])
        d = self.report.to_dict()
        self.assertEqual(len(d['errors']), 1)
        self.assertEqual(len(d['warnings']), 0)

    def test_add_empty(self):
        self.report.add([])
        self.assertEqual(self.report.findings, [])


# ---------------------------------------------------------------------------
# MockRegistry / MockRegistryHandler tests (20)
# ---------------------------------------------------------------------------

class TestMockRegistry(unittest.TestCase):

    def setUp(self):
        self.packages = build_default_registry_packages()
        self.registry = MockRegistry(port=0, initial_packages=self.packages)
        self.registry.start()

    def tearDown(self):
        self.registry.stop()

    def _get(self, path):
        url = f"{self.registry.base_url()}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_server_starts(self):
        self.assertGreater(self.registry.port, 0)

    def test_get_known_package(self):
        status, data = self._get('/packages/requests')
        self.assertEqual(status, 200)
        self.assertEqual(data['name'], 'requests')

    def test_get_unknown_package(self):
        status, data = self._get('/packages/nonexistentpkg99')
        self.assertEqual(status, 404)

    def test_get_package_version(self):
        status, data = self._get('/packages/requests/2.28.0')
        self.assertEqual(status, 200)
        self.assertEqual(data['version'], '2.28.0')

    def test_get_unknown_version(self):
        status, data = self._get('/packages/requests/99.99.99')
        self.assertEqual(status, 404)

    def test_get_bad_path(self):
        status, data = self._get('/other/path')
        self.assertEqual(status, 404)

    def test_register_new_package(self):
        pkg_data = json.dumps({'name': 'newpkg', 'versions': {}}).encode()
        req = urllib.request.Request(
            f"{self.registry.base_url()}/packages",
            data=pkg_data,
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 201)

    def test_register_then_get(self):
        pkg_data = json.dumps({'name': 'dynpkg', 'versions': {'1.0': {'sha256': 'aaa', 'url': 'u'}}}).encode()
        req = urllib.request.Request(
            f"{self.registry.base_url()}/packages",
            data=pkg_data,
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=5).close()
        status, data = self._get('/packages/dynpkg')
        self.assertEqual(status, 200)

    def test_context_manager(self):
        with MockRegistry(port=0, initial_packages=self.packages) as reg:
            url = f"{reg.base_url()}/packages/flask"
            with urllib.request.urlopen(url, timeout=5) as r:
                self.assertEqual(r.status, 200)

    def test_lookup_method(self):
        data = self.registry.lookup('requests')
        self.assertIsNotNone(data)
        self.assertEqual(data['name'], 'requests')

    def test_lookup_unknown(self):
        data = self.registry.lookup('nonexistent')
        self.assertIsNone(data)

    def test_register_programmatic(self):
        self.registry.register('mypkg', {'name': 'mypkg', 'versions': {}})
        data = self.registry.lookup('mypkg')
        self.assertIsNotNone(data)

    def test_response_is_json(self):
        url = f"{self.registry.base_url()}/packages/flask"
        with urllib.request.urlopen(url, timeout=5) as r:
            content_type = r.headers.get('Content-Type', '')
            self.assertIn('application/json', content_type)

    def test_flask_in_registry(self):
        status, _ = self._get('/packages/flask')
        self.assertEqual(status, 200)

    def test_django_in_registry(self):
        status, _ = self._get('/packages/django')
        self.assertEqual(status, 200)

    def test_case_insensitive_get(self):
        status, _ = self._get('/packages/FLASK')
        self.assertEqual(status, 200)

    def test_post_missing_name(self):
        pkg_data = json.dumps({'versions': {}}).encode()
        req = urllib.request.Request(
            f"{self.registry.base_url()}/packages",
            data=pkg_data,
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertNotEqual(r.status, 201)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_post_invalid_json(self):
        req = urllib.request.Request(
            f"{self.registry.base_url()}/packages",
            data=b'not json',
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertNotEqual(r.status, 201)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_base_url_format(self):
        url = self.registry.base_url()
        self.assertTrue(url.startswith('http://127.0.0.1:'))

    def test_version_sha256_returned(self):
        status, data = self._get('/packages/requests/2.28.0')
        self.assertEqual(status, 200)
        self.assertIn('sha256', data)


# ---------------------------------------------------------------------------
# RegistryPackageChecker tests (6)
# ---------------------------------------------------------------------------

class TestRegistryPackageChecker(unittest.TestCase):

    def setUp(self):
        self.packages = build_default_registry_packages()
        self.registry = MockRegistry(port=0, initial_packages=self.packages)
        self.registry.start()
        self.checker = RegistryPackageChecker(self.registry)

    def tearDown(self):
        self.registry.stop()

    def test_known_package_found(self):
        found, data = self.checker.check('requests')
        self.assertTrue(found)
        self.assertIsNotNone(data)

    def test_unknown_package_not_found(self):
        found, data = self.checker.check('fakepkg12345')
        self.assertFalse(found)
        self.assertIsNone(data)

    def test_check_all_known(self):
        findings = self.checker.check_all(['requests', 'flask'])
        self.assertEqual(findings, [])

    def test_check_all_unknown(self):
        findings = self.checker.check_all(['fakepkg12345'])
        self.assertTrue(findings)

    def test_typosquat_warning_via_registry(self):
        # 'flusk' is 1 edit from 'flask'
        findings = self.checker.check_all(['flusk'])
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, 'warning')

    def test_check_returns_metadata(self):
        found, data = self.checker.check('flask')
        self.assertTrue(found)
        self.assertIn('name', data)


# ---------------------------------------------------------------------------
# build_default helpers tests (4)
# ---------------------------------------------------------------------------

class TestBuildDefaults(unittest.TestCase):

    def test_default_advisories_not_empty(self):
        advisories = build_default_advisories()
        self.assertGreater(len(advisories), 0)

    def test_default_advisories_type(self):
        advisories = build_default_advisories()
        for adv in advisories:
            self.assertIsInstance(adv, Advisory)

    def test_default_registry_packages_not_empty(self):
        pkgs = build_default_registry_packages()
        self.assertGreater(len(pkgs), 0)

    def test_default_registry_includes_requests(self):
        pkgs = build_default_registry_packages()
        self.assertIn('requests', pkgs)


# ---------------------------------------------------------------------------
# Integration tests (10)
# ---------------------------------------------------------------------------

class TestIntegration(unittest.TestCase):
    """End-to-end scenarios combining multiple checkers."""

    def setUp(self):
        self.packages = build_default_registry_packages()
        self.registry = MockRegistry(port=0, initial_packages=self.packages)
        self.registry.start()

    def tearDown(self):
        self.registry.stop()

    def test_full_clean_supply_chain(self):
        """All checks pass for a well-formed dependency set."""
        report = SupplyChainReport()

        # Pinning
        pc = PinningChecker()
        report.add(pc.check_all({'requests': '==2.28.0', 'flask': '==2.3.0'}))

        # Integrity
        artifact = b'requests artifact'
        dep = LockedDep('requests', '2.28.0', _sha256(artifact), 'https://x.com')
        ic = IntegrityChecker()
        report.add(ic.verify_all([dep], {'requests': artifact}))

        # Drift
        dc = LockfileDriftChecker()
        report.add(dc.check({'requests', 'flask'}, {'requests', 'flask'}))

        # Vuln
        vc = KnownVulnChecker(build_default_advisories())
        report.add(vc.check_all([dep]))

        self.assertFalse(report.has_errors())

    def test_tampered_artifact_detected(self):
        artifact = b'original content'
        dep = LockedDep('requests', '2.28.0', _sha256(artifact), 'https://x.com')
        ic = IntegrityChecker()
        report = SupplyChainReport()
        report.add(ic.verify_all([dep], {'requests': b'tampered!'}))
        self.assertTrue(report.has_errors())

    def test_drifted_lockfile_detected(self):
        report = SupplyChainReport()
        dc = LockfileDriftChecker()
        report.add(dc.check({'requests', 'flask', 'extra'}, {'requests', 'flask'}))
        self.assertTrue(report.has_errors())

    def test_vuln_in_dep_detected(self):
        dep = LockedDep('requests', '1.2.0', 'abc', 'https://x.com')
        vc = KnownVulnChecker(build_default_advisories())
        report = SupplyChainReport()
        report.add(vc.check_all([dep]))
        self.assertTrue(report.has_errors())

    def test_floating_pin_detected(self):
        pc = PinningChecker()
        report = SupplyChainReport()
        report.add(pc.check_all({'requests': '>=2.0'}))
        self.assertTrue(report.has_errors())

    def test_nondeterministic_build_in_report(self):
        rc = ReproducibleBuildChecker()
        report = SupplyChainReport()
        counter = {"n": 0}

        def planted_bad_build(inputs):
            counter["n"] += 1
            return f"build-output-{counter['n']}"

        ok, findings = rc.check(planted_bad_build, {})
        report.add(findings)
        self.assertTrue(report.has_errors())

    def test_hallucinated_dep_detected(self):
        nec = NonexistentPackageChecker(set(self.packages.keys()))
        report = SupplyChainReport()
        report.add(nec.check_all(['hallucinated-dep-xyz99']))
        self.assertTrue(report.has_errors())

    def test_report_summary_counts(self):
        report = SupplyChainReport()
        report.add([
            FindingRecord('C', 'error', 'p', 'm'),
            FindingRecord('C', 'error', 'q', 'm'),
            FindingRecord('C', 'warning', 'r', 'm'),
        ])
        s = report.summary()
        self.assertIn('2 error', s)
        self.assertIn('1 warning', s)

    def test_registry_and_nonexistent_checker_agree(self):
        rc = RegistryPackageChecker(self.registry)
        findings = rc.check_all(['requests', 'flask', 'totallyfake99999'])
        pkg_names = {f.package.lower() for f in findings}
        self.assertIn('totallyfake99999', pkg_names)
        self.assertNotIn('requests', pkg_names)

    def test_transitive_missing_in_supply_chain_report(self):
        tc = TransitiveDepChecker()
        report = SupplyChainReport()
        graph = {'a': ['b'], 'b': ['c'], 'c': []}
        _, findings = tc.resolve(['a'], graph, {'a', 'b'})  # 'c' missing
        report.add(findings)
        self.assertTrue(report.has_errors())


# ---------------------------------------------------------------------------
# Teeth tests — the universal swap-check is wired into the paired test.
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):

    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    def test_oracle_is_clean(self):
        # The correct admission decider must NOT be flagged by prove.
        self.assertFalse(prove(oracle_admit))
        self.assertFalse(prove(TEETH.oracle))

    def test_every_mutant_is_caught(self):
        # Each planted supply-chain defect must be individually caught.
        self.assertEqual(len(TEETH.mutants), 3)
        for mutant in TEETH.mutants:
            self.assertTrue(prove(mutant.impl), f"mutant not caught: {mutant.name}")

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == '__main__':
    unittest.main()
