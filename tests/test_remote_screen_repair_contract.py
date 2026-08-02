import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteScreenRepairContractTests(unittest.TestCase):
    def test_gateway_revision_triggers_a_fresh_cloud_run_deployment(self):
        package = json.loads((ROOT / "gateway/package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], "1.1.0")
        workflow = (ROOT / ".github/workflows/etr-gateway-cloudrun.yml").read_text(encoding="utf-8")
        self.assertIn("gateway/**", workflow)
        self.assertIn("gcloud run deploy", workflow)
        self.assertIn("--min-instances=1", workflow)

    def test_repair_reuses_the_main_device_session_and_canonical_identity(self):
        script = (ROOT / "src/deploy/raspi/repair_remote_screen.sh").read_text(encoding="utf-8")
        for marker in [
            'installation_id="etr-${serial: -12}"',
            "ETR_INSTALLATION_ID=${installation_id}",
            'firebase-auth.json',
            'rm -f "$STATE_DIR/remote-screen-auth.json"',
            "etr-remote-screen.service",
            "etr-vnc.service",
            "Session Firebase appareil incomplète",
        ]:
            self.assertIn(marker, script)
        self.assertNotIn('cp "$STATE_DIR/firebase-auth.json" "$STATE_DIR/remote-screen-auth.json"', script)

    def test_physical_workflow_waits_for_current_gateway_and_connected_device(self):
        workflow = (ROOT / ".github/workflows/etr-remote-screen-repair.yml").read_text(encoding="utf-8")
        for marker in [
            "runs-on: [self-hosted, Linux, ARM64]",
            "$GATEWAY_ORIGIN/healthz",
            "assert data.get('enrollment') == 'v1'",
            "repair_remote_screen.sh",
            "devices",
            "connected=true",
            "etr-remote-screen-repair-last.json",
            "Connecting installation $INSTALLATION_ID",
        ]:
            self.assertIn(marker, workflow)

    def test_remote_screen_service_uses_shared_token_file(self):
        unit = (ROOT / "src/deploy/raspi/etr-remote-screen.service").read_text(encoding="utf-8")
        agent = (ROOT / "src/remote_screen_agent.py").read_text(encoding="utf-8")
        self.assertIn("ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json", unit)
        self.assertIn("etr-firebase-bridge.service", unit)
        self.assertIn("authenticate_existing_device_session", agent)
        self.assertNotIn("request_enrollment", agent)


if __name__ == "__main__":
    unittest.main()
