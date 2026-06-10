import subprocess
import sys
import unittest

from harnesses.core import canvas_scene_state_test_harness as harness


class TestCanvasSceneStateHarness(unittest.TestCase):
    def test_good_scene_passes(self):
        report = harness.analyze_scene(harness.GOOD_SCENE)
        self.assertTrue(report.ok, report.issues)

    def test_cli_self_test_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, harness.__file__, "--self-test"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
