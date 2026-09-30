from pathlib import Path
import unittest


class LivingMachineMotionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "src" / "ocpf_post" / "ui" / "index.html").read_text(encoding="utf-8")

    def test_replay_uses_cross_section_of_real_evidence(self):
        self.assertIn("function selectReplayFlows(flows)", self.html)
        self.assertIn("const supplements={engagement:5,performance:5,learning:3}", self.html)
        self.assertIn(".slice(0,4);", self.html)
        self.assertIn("schedule_id", self.html)

    def test_flow_visually_activates_route_and_nodes(self):
        self.assertIn("edge-hot", self.html)
        self.assertIn("node-hot", self.html)
        self.assertIn("routeMarch", self.html)
        self.assertIn("nodeRing", self.html)
        self.assertIn("arrivalBurst", self.html)

    def test_one_evidence_item_is_a_pulse_with_trail_not_fake_jobs(self):
        self.assertIn("token-core", self.html)
        self.assertIn("token-halo", self.html)
        self.assertIn("token-tail", self.html)
        self.assertIn("Its halo and trail are one visual pulse, not multiple invented jobs", self.html)

    def test_replay_supports_overlapping_motion(self):
        self.assertIn("const cap=8", self.html)
        self.assertIn("machine.nextSpawn", self.html)
        self.assertIn("260/machine.speed", self.html)
        self.assertIn("motionReadout", self.html)

    def test_browser_remains_observability_only(self):
        lower = self.html.lower()
        self.assertNotIn("<form", lower)
        self.assertNotIn('method="post"', lower)
        self.assertIn("new EventSource('/api/stream')", self.html)
        self.assertIn("Pause freezes only this picture. Post-Once continues running.", self.html)


if __name__ == "__main__":
    unittest.main()
