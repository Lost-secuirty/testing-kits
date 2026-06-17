"""Unittest wrapper for tools/vacuity_gate.py — the vacuous-green meta-gate.

(a) the gate's own --self-test must detect a deliberately vacuous fixture (a real
harness reads TEETH; a vacuous one is caught as VACUOUS); (b) no MAPPED harness may be
vacuous or error — every harness that declares VACUITY_TARGETS must read TEETH, i.e.
neutering its oracle turns its own --self-test red. Unmapped harnesses are advisory
(the rollout continues per batch, mirroring DEP-TEST-KIT).
"""

from __future__ import annotations

import unittest

from tools import vacuity_gate


class VacuityGateTest(unittest.TestCase):
    def test_gate_self_test_detects_vacuous_fixture(self) -> None:
        self.assertEqual(vacuity_gate.main(["--self-test"]), 0)

    def test_no_mapped_harness_is_vacuous_or_error(self) -> None:
        self.assertEqual(vacuity_gate.run_gate(), 0)


if __name__ == "__main__":
    unittest.main()
