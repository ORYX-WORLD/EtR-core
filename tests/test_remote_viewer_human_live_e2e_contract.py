import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteViewerHumanLiveE2EContractTests(unittest.TestCase):
    def test_delivery_contract_is_complete(self):
        required = [
            "gateway/remote-screen-human-live-e2e.mjs",
            ".github/workflows/etr-remote-viewer-human-live-e2e.yml",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Human remote viewer E2E contract incomplete: {missing}")

    def test_script_uses_a_verified_temporary_human_and_cleans_it_up(self):
        script = (ROOT / "gateway/remote-screen-human-live-e2e.mjs").read_text(encoding="utf-8")
        for marker in [
            "auth.createUser",
            "emailVerified: true",
            "memberships/${uid}/${installationId}",
            'role: "viewer"',
            "accounts:signInWithPassword",
            "/api/remote-session",
            "/novnc/rfb-browser-v2.js",
            'websocketUrl.pathname = "/client"',
            'prefix.startsWith("RFB ")',
            "auth.deleteUser(uid)",
            "membershipRef.remove()",
            "membershipDeleted",
            "userDeleted",
        ]:
            self.assertIn(marker, script)
        for forbidden in [
            "console.log(email)",
            "console.log(password)",
            "idToken:",
            "password:",
        ]:
            self.assertNotIn(forbidden, script)

    def test_workflow_uses_wif_and_publishes_only_safe_proof(self):
        workflow = (ROOT / ".github/workflows/etr-remote-viewer-human-live-e2e.yml").read_text(encoding="utf-8")
        for marker in [
            "id-token: write",
            "google-github-actions/auth@v3",
            "GCP_WORKLOAD_IDENTITY_PROVIDER",
            "GCP_SERVICE_ACCOUNT",
            "firebase/init.json",
            "remote-screen-human-live-e2e.mjs",
            "etr-remote-viewer-human-live-e2e-last.json",
            "int(data.get('devices', 0)) >= 1",
            "int(data.get('viewers', 0)) == 0",
        ]:
            self.assertIn(marker, workflow)
        self.assertNotIn("password", workflow.lower())
        self.assertNotIn("email", workflow.lower())


if __name__ == "__main__":
    unittest.main()
