"""Tests for tools/file_guard.py — and proof the guard actually bites.

Two halves: (a) the committed .fileguard.json must match the live gate machinery (so a
gate file that changed without a re-baseline fails CI — the whole point); (b) against a
throwaway tree, the guard must report clean, then BITE on a modified file, a removed
file, an unbaselined add, a missing baseline, and a corrupt/schema-invalid baseline.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import file_guard

REPO_ROOT = Path(__file__).resolve().parents[1]


class FileGuardRealRepoTest(unittest.TestCase):
    def test_working_tree_matches_committed_baseline(self) -> None:
        # If a protected gate file changed without re-baselining, this fails loudly.
        self.assertEqual(
            file_guard.check(REPO_ROOT, REPO_ROOT / file_guard.MANIFEST_NAME), 0
        )


class FileGuardBitesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = file_guard.PROTECTED_FILES
        file_guard.PROTECTED_FILES = ["a.txt", "sub/b.txt"]  # minimal set in a temp tree

    def tearDown(self) -> None:
        file_guard.PROTECTED_FILES = self._orig

    @staticmethod
    def _stage(tmp: Path) -> None:
        (tmp / "a.txt").write_text("alpha\n", encoding="utf-8")
        (tmp / "sub").mkdir()
        (tmp / "sub" / "b.txt").write_text("beta\n", encoding="utf-8")

    def test_clean_modified_removed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._stage(tmp)
            manifest = tmp / ".fileguard.json"
            self.assertEqual(file_guard.update(tmp, manifest), 0)
            self.assertEqual(file_guard.check(tmp, manifest), 0)  # clean
            (tmp / "a.txt").write_text("alpha CHANGED\n", encoding="utf-8")
            self.assertEqual(file_guard.check(tmp, manifest), 1)  # MODIFIED bites
            (tmp / "a.txt").write_text("alpha\n", encoding="utf-8")
            self.assertEqual(file_guard.check(tmp, manifest), 0)  # restored
            (tmp / "sub" / "b.txt").unlink()
            self.assertEqual(file_guard.check(tmp, manifest), 1)  # REMOVED bites

    def test_unbaselined_add(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._stage(tmp)
            manifest = tmp / ".fileguard.json"
            self.assertEqual(file_guard.update(tmp, manifest), 0)
            # A new protected file that is present but absent from the baseline -> drift.
            file_guard.PROTECTED_FILES = ["a.txt", "sub/b.txt", "c.txt"]
            (tmp / "c.txt").write_text("gamma\n", encoding="utf-8")
            self.assertEqual(file_guard.check(tmp, manifest), 1)  # UNBASELINED bites

    def test_missing_and_invalid_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._stage(tmp)
            manifest = tmp / ".fileguard.json"
            self.assertEqual(file_guard.check(tmp, manifest), 2)  # missing baseline
            manifest.write_text("{ not valid json", encoding="utf-8")
            self.assertEqual(file_guard.check(tmp, manifest), 2)  # corrupt baseline
            manifest.write_text("[]", encoding="utf-8")
            self.assertEqual(file_guard.check(tmp, manifest), 2)  # schema-invalid (not an object)
            manifest.write_text('{"files": null}', encoding="utf-8")
            self.assertEqual(file_guard.check(tmp, manifest), 2)  # schema-invalid (files not a map)


if __name__ == "__main__":
    unittest.main()
