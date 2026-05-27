"""Test suite for dormant_code_test_harness."""

import textwrap
import unittest
from typing import Any

from harnesses.core.dormant_code_test_harness import (
    CoverageProbe,
    DormantReport,
    _load_target_module,
    _run_self_test,
    drive_synthetic,
    list_scenarios,
    reachable_lines,
)


class TestReachableLines(unittest.TestCase):
    def test_finds_statement_lines(self):
        source = textwrap.dedent("""
            x = 1
            y = 2
            if x:
                z = 3
        """).strip()
        lines = reachable_lines(source)
        self.assertGreaterEqual(len(lines), 4)

    def test_empty_source(self):
        self.assertEqual(reachable_lines(""), set())

    def test_only_definitions(self):
        source = "def f():\n    return 1\n"
        lines = reachable_lines(source)
        self.assertIn(1, lines)
        self.assertIn(2, lines)


class TestCoverageProbe(unittest.TestCase):
    def test_records_lines_taken(self):
        # Define a target in the same file for tracing.
        def target():
            x = 1
            y = x + 1
            return y

        # The probe filters by filename — use the calling file (this test).
        import inspect
        filename = inspect.getfile(target)
        probe = CoverageProbe(target_filename=filename)
        with probe:
            target()
        self.assertGreater(len(probe.taken), 0)

    def test_probe_restores_previous_trace(self):
        import sys
        before = sys.gettrace()
        probe = CoverageProbe()
        with probe:
            pass
        self.assertEqual(sys.gettrace(), before)


class TestDormantReport(unittest.TestCase):
    def test_construction(self):
        r = DormantReport(
            target_name="x",
            reachable=10,
            taken_baseline=5,
            taken_after_synth=8,
            still_dormant=[7, 9],
            crashes_surfaced=["bug"],
        )
        self.assertEqual(r.target_name, "x")
        self.assertEqual(len(r.still_dormant), 2)
        self.assertEqual(len(r.crashes_surfaced), 1)


class TestDriveSynthetic(unittest.TestCase):
    def test_drive_calls_target(self):
        calls: list[Any] = []

        def target(x: int) -> None:
            calls.append(x)

        import inspect
        filename = inspect.getfile(target)
        new_lines, crashes = drive_synthetic(target, filename, [{"x": 1}, {"x": 2}],
                                             baseline_taken=set())
        self.assertEqual(calls, [1, 2])
        self.assertEqual(crashes, [])

    def test_drive_captures_crashes(self):
        def target(x: int) -> None:
            if x < 0:
                raise ValueError("neg")

        import inspect
        filename = inspect.getfile(target)
        new_lines, crashes = drive_synthetic(target, filename,
                                             [{"x": -1}, {"x": -2}],
                                             baseline_taken=set())
        self.assertEqual(len(crashes), 2)


class TestSelfTest(unittest.TestCase):
    def test_list_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 3)

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)

    def test_load_target_module_provides_normalize(self):
        module, _ = _load_target_module()
        self.assertTrue(hasattr(module, "normalize"))
        self.assertEqual(module.normalize("Alice"), "alice")


if __name__ == "__main__":
    unittest.main()
