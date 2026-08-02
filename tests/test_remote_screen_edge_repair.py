import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteScreenEdgeRepairTests(unittest.TestCase):
    def test_repair_uses_signed_or_canonical_device_identity_without_local_authorization(self):
        script = (ROOT / "src/deploy/raspi/repair_remote_screen.sh").read_text(encoding="utf-8")
        for marker in [
            'derived_installation_id="etr-${serial: -12}"',
            "signed_installation_id",
            "payload.get('installationId')",
            'installation_id=${signed_installation_id:-$derived_installation_id}',
            'TOKEN_FILE=${STATE_DIR}/firebase-auth.json',
            'rm -f "$STATE_DIR/remote-screen-auth.json"',
            'rm -rf "$DROPIN_DIR"',
            "ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json",
            "Ancien fichier de jetons encore chargé par systemd",
            "etr-remote-screen.service",
            "etr-vnc.service",
        ]:
            self.assertIn(marker, script)
        self.assertNotIn("payload.get('etrDevice') is not True", script)
        self.assertNotIn("cp \"$STATE_DIR/firebase-auth.json\"", script)

    def test_main_installer_removes_obsolete_remote_screen_dropins(self):
        script = (ROOT / "src/deploy/raspi/setup_etr.sh").read_text(encoding="utf-8")
        for marker in [
            "rm -rf /etc/systemd/system/etr-remote-screen.service.d",
            "systemctl cat etr-remote-screen.service",
            "ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json",
            "ETR_TOKEN_FILE=/var/lib/etr-core/remote-screen-auth.json",
        ]:
            self.assertIn(marker, script)

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

    def test_current_agent_and_unit_use_the_shared_device_session(self):
        agent = (ROOT / "src/remote_screen_agent.py").read_text(encoding="utf-8")
        unit = (ROOT / "src/deploy/raspi/etr-remote-screen.service").read_text(encoding="utf-8")
        self.assertIn("PRIMARY_TOKEN_FILE", agent)
        self.assertIn("installation_id_from_local_device", agent)
        self.assertIn("installation_id_from_id_token", agent)
        self.assertIn("ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json", unit)
        self.assertIn("etr-firebase-bridge.service", unit)


if __name__ == "__main__":
    unittest.main()
