import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteScreenEdgeRepairTests(unittest.TestCase):
    def test_repair_uses_the_existing_device_access_binding(self):
        script = (ROOT / "src/deploy/raspi/repair_remote_screen.sh").read_text(encoding="utf-8")
        for marker in [
            "resolve_remote_installation_id",
            "FIREBASE_DATABASE_URL",
            "installation_id_from_local_device",
            'PYTHONPATH="$ETR_INSTALL_DIR/src:$ETR_INSTALL_DIR"',
            "from firebase_bridge import atomic_json_write",
            "from remote_screen_agent import installation_id_from_local_device",
            'TOKEN_FILE=${STATE_DIR}/firebase-auth.json',
            'rm -f "$STATE_DIR/remote-screen-auth.json"',
            "ETR_INSTALLATION_ID=${installation_id}",
            "etr-remote-screen.service",
            "etr-vnc.service",
        ]:
            self.assertIn(marker, script)
        self.assertNotIn("from src.remote_screen_agent", script)
        self.assertNotIn("systemctl restart etr-firebase-bridge.service", script)
        self.assertNotIn("cp \"$STATE_DIR/firebase-auth.json\"", script)

    def test_reference_physical_workflow_waits_for_the_wss_confirmation(self):
        fast = (ROOT / ".github/workflows/etr-remote-screen-repair-fast.yml").read_text(encoding="utf-8")
        wrapper = (ROOT / ".github/workflows/etr-remote-screen-repair.yml").read_text(encoding="utf-8")
        for marker in [
            "runs-on: [self-hosted, Linux, ARM64]",
            "$GATEWAY_URL/api/admin/session",
            "repair_remote_screen.sh",
            "repair.stdout",
            "repair.stderr",
            "repairExitCode",
            "connected=true",
            "Installation $INSTALLATION_ID connected to the remote gateway",
            "$GATEWAY_URL/api/health",
            "etr-remote-screen-fast-last.json",
        ]:
            self.assertIn(marker, fast)
        self.assertNotIn(
            "for service in etr-firebase-bridge.service etr-vnc.service etr-remote-screen.service",
            fast,
        )
        for marker in [
            "actions: write",
            "gh workflow run etr-remote-screen-repair-fast.yml --ref main",
        ]:
            self.assertIn(marker, wrapper)

    def test_current_runtime_and_unit_use_the_shared_device_session(self):
        agent = (ROOT / "src/remote_screen_agent.py").read_text(encoding="utf-8")
        runtime = (ROOT / "src/remote_screen_runtime.py").read_text(encoding="utf-8")
        unit = (ROOT / "src/deploy/raspi/etr-remote-screen.service").read_text(encoding="utf-8")
        self.assertIn("PRIMARY_TOKEN_FILE", agent)
        self.assertIn("installation_id_from_local_device", agent)
        self.assertIn("resolve_remote_installation_id", runtime)
        self.assertIn("install_runtime_authenticator", runtime)
        self.assertIn("ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json", unit)
        self.assertIn("remote_screen_runtime.py", unit)
        self.assertIn("etr-firebase-bridge.service", unit)


if __name__ == "__main__":
    unittest.main()
