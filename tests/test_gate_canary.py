"""Unittest wrapper for tools/gate_canary.py — and proof the canary isn't vacuous.

The canary is itself a gate, so it needs the same teeth it demands of others. This
asserts (a) every canary passes against the LIVE gates, and (b) the canary FAILS
when a gate is softened — the secret scanner neutered, or the teeth swap-check
engine rigged to verify everything. Without (b), the canary could silently go green
if someone disabled it.
"""

from __future__ import annotations

import unittest

from tools import gate_canary, scan_staged


class GateCanaryTest(unittest.TestCase):
    def test_all_canaries_pass_on_live_gates(self) -> None:
        self.assertEqual(gate_canary.run(), 0)

    def test_canary_bites_when_scanner_softened(self) -> None:
        original = scan_staged.scan_line
        try:
            scan_staged.scan_line = lambda line: []  # neuter the secret gate
            self.assertEqual(gate_canary.run(), 1)
        finally:
            scan_staged.scan_line = original

    def test_canary_bites_when_teeth_engine_softened(self) -> None:
        original = gate_canary._teeth.verify
        try:
            # An engine that "verifies" everything — including vacuous teeth.
            gate_canary._teeth.verify = lambda teeth: {
                "teeth_verified": True,
                "oracle_clean": True,
                "mutants_uncaught": [],
                "error": None,
            }
            self.assertEqual(gate_canary.run(), 1)
        finally:
            gate_canary._teeth.verify = original


if __name__ == "__main__":
    unittest.main()
