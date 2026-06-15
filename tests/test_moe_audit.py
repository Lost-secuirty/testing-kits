"""Tests for the MoE PR auditor's deterministic pure helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.moe_audit import MARKER, Lens, _third_party_imports, changed_harnesses


class ChangedHarnessesTests(unittest.TestCase):
    def test_selects_only_real_harness_modules(self):
        changed = [
            "harnesses/core/foo_test_harness.py",
            "harnesses/security/bar_test_harness.py",
            "tools/x.py",
            "harnesses/core/__init__.py",
            "docs/UPGRADE_CAMPAIGN.md",
            "harnesses/_teeth.py",
        ]
        self.assertEqual(
            changed_harnesses(changed),
            ["harnesses/core/foo_test_harness.py", "harnesses/security/bar_test_harness.py"],
        )

    def test_handles_windows_separators(self):
        self.assertEqual(
            changed_harnesses(["harnesses\\ai\\baz_test_harness.py"]),
            ["harnesses/ai/baz_test_harness.py"],
        )


class PurityLensTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "h.py"
        tmp.write_text(text, encoding="utf-8")
        return tmp

    def test_flags_third_party_import(self):
        p = self._write("import os\nimport requests\nfrom flask import Flask\n")
        self.assertEqual(_third_party_imports(p), ["flask", "requests"])

    def test_pure_stdlib_is_clean(self):
        p = self._write("import os\nimport json\nfrom pathlib import Path\n"
                        "from harnesses._teeth import Teeth\nfrom __future__ import annotations\n")
        self.assertEqual(_third_party_imports(p), [])

    def test_relative_import_is_ignored(self):
        p = self._write("from . import sibling\nfrom ..pkg import thing\n")
        self.assertEqual(_third_party_imports(p), [])


class LensTests(unittest.TestCase):
    def test_status_escalates_to_worst(self):
        lens = Lens("x")
        self.assertEqual(lens.status, "ok")
        lens.add("info line", level="info")
        self.assertEqual(lens.status, "info")
        lens.add("a warning", level="warn")
        self.assertEqual(lens.status, "warn")
        lens.add("another info", level="info")
        self.assertEqual(lens.status, "warn")  # never de-escalates
        lens.add("a failure", level="fail")
        self.assertEqual(lens.status, "fail")

    def test_marker_is_comment(self):
        self.assertTrue(MARKER.startswith("<!--") and MARKER.endswith("-->"))


if __name__ == "__main__":
    unittest.main()
