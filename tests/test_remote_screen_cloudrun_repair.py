import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteScreenCloudRunRepairTests(unittest.TestCase):
    def test_gateway_image_keeps_firebase_runtime_dependencies(self):
        dockerfile = (ROOT / "gateway/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("npm install --no-audit --no-fund", dockerfile)
        self.assertIn("npm run build:novnc", dockerfile)
        self.assertNotIn("npm prune --omit=dev", dockerfile)
        self.assertIn("COPY firebase-token-verifier.mjs ./", dockerfile)
        self.assertIn('CMD ["node", "server.mjs"]', dockerfile)

    def test_gateway_version_forces_a_new_cloud_run_revision(self):
        package = json.loads((ROOT / "gateway/package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], "1.1.0")
        self.assertEqual(package["dependencies"]["firebase-admin"], "13.0.2")

    def test_repair_uses_signed_or_canonical_device_identity(self):
        script = (ROOT / "src/deploy/raspi/repair_remote_screen.sh").read_text(encoding="utf-8")
        for marker in [
            'derived_installation_id="etr-${serial: -12}"',
            "signed_installation_id",
            "payload.get('installationId')",
            "payload.get('etrDevice') is not True",
            'installation_id=${signed_installation_id:-$derived_installation_id}',
            'TOKEN_FILE=${STATE_DIR}/firebase-auth.json',
            'rm -f "$STATE_DIR/remote-screen-auth.json"',
            "etr-remote-screen.service",
            "etr-vnc.service",
        ]:
            self.assertIn(marker, script)
        self.assertNotIn("cp \"$STATE_DIR/firebase-auth.json\"", script)

    def test_physical_workflow_waits_for_cloud_and_connected_device(self):
        workflow = (ROOT / ".github/workflows/etr-remote-screen-repair.yml").read_text(encoding="utf-8")
        for marker in [
            "runs-on: [self-hosted, Linux, ARM64]",
            "$GATEWAY_ORIGIN/healthz",
            "assert data.get('enrollment') == 'v1'",
            "repair_remote_screen.sh",
            "devices",
            "connected=true",
            "Installation $INSTALLATION_ID connected to the remote gateway",
            "etr-remote-screen-repair-last.json",
        ]:
            self.assertIn(marker, workflow)


if __name__ == "__main__":
    unittest.main()
