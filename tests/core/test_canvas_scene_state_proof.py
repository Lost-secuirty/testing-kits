import unittest

from harnesses.core import canvas_scene_state_test_harness as harness


class TestCanvasSceneStateProof(unittest.TestCase):
    def test_proof_bad_scene_is_rejected(self):
        report = harness.analyze_scene(harness.BAD_SCENE)
        self.assertFalse(report.ok)
        joined = "\n".join(report.issues)
        self.assertIn("duplicate node id", joined)
        self.assertIn("missing asset", joined)
        self.assertIn("outside viewport", joined)
        self.assertIn("debug node visible", joined)


if __name__ == "__main__":
    unittest.main()
