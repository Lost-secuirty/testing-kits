import contextlib
import io
import json
import socket
import sys
import unittest
from unittest.mock import patch

from harnesses.core import stress_harness as harness


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TestStressProof(unittest.TestCase):
    def test_oracle_matches_frozen_stress_events(self):
        for case in harness.STRESS_METRIC_CORPUS:
            self.assertEqual(harness.oracle_stress_audit(case), case.expected_events, case.name)

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_stress_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.STRESS_METRIC_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_stress_defects_have_traps(self):
        cases = {case.name: case for case in harness.STRESS_METRIC_CORPUS}
        self.assertNotEqual(
            harness.raw_latency_auditor(cases["corrected_latency_includes_scheduler_lag"]),
            cases["corrected_latency_includes_scheduler_lag"].expected_events,
        )
        self.assertNotEqual(
            harness.status_only_error_auditor(cases["connection_error_counts_as_error"]),
            cases["connection_error_counts_as_error"].expected_events,
        )
        self.assertNotEqual(
            harness.equal_weight_auditor(cases["weighted_scenario_expansion"]),
            cases["weighted_scenario_expansion"].expected_events,
        )

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(
            harness.list_scenarios(),
            [case.name for case in harness.STRESS_METRIC_CORPUS],
        )

    def test_json_self_test_emits_parseable_report(self):
        mock_port = _pick_free_port()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                sys.executable,
                "--json",
                "--duration",
                "1",
                "--rate",
                "5",
                "--max-vus",
                "20",
                "--report-interval",
                "5",
                "--mock-port",
                str(mock_port),
            ],
        ):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as cm:
                    harness.main()
        self.assertEqual(cm.exception.code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["harness"], "core/stress")
        self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
