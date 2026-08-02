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

    def test_script_uses_public_signup_oob_verification_and_self_cleanup(self):
        script = (ROOT / "gateway/remote-screen-human-live-e2e.mjs").read_text(encoding="utf-8")
        for marker in [
            'method: "accounts:signUp"',
            "projects/${encodeURIComponent(projectId)}/accounts:sendOobCode",
            'requestType: "VERIFY_EMAIL"',
            "returnOobLink: true",
            'method: "accounts:update"',
            "memberships/${uid}/${installationId}",
            'role: "viewer"',
            'method: "accounts:signInWithPassword"',
            "/api/remote-session",
            "/novnc/rfb-browser-v2.js",
            'websocketUrl.pathname = "/client"',
            'prefix.startsWith("RFB ")',
            'method: "accounts:delete"',
            "membershipRef.remove()",
            "userCanStillSignIn",
            "membershipDeleted",
            "userDeleted",
            "verificationCodeReturnedWithoutEmail",
        ]:
            self.assertIn(marker, script)
        for forbidden in [
            "auth.createUser",
            "auth.deleteUser",
            "console.log(email)",
            "console.log(password)",
            "idToken:",
            "password:",
        ]:
            self.assertNotIn(forbidden, script)

    def test_workflow_uses_ephemeral_wif_token_without_iam_mutation(self):
        workflow = (ROOT / ".github/workflows/etr-remote-viewer-human-live-e2e.yml").read_text(encoding="utf-8")
        for marker in [
            "id-token: write",
            "google-github-actions/auth@v3",
            "google-github-actions/setup-gcloud@v3",
            "GCP_WORKLOAD_IDENTITY_PROVIDER",
            "GCP_SERVICE_ACCOUNT",
            "gcloud auth print-access-token",
            'echo "::add-mask::$access_token"',
            "GOOGLE_OAUTH_ACCESS_TOKEN",
            "firebase/init.json",
            "remote-screen-human-live-e2e.mjs",
            "etr-remote-viewer-human-live-e2e-last.json",
            "int(data.get('devices', 0)) >= 1",
            "int(data.get('viewers', 0)) == 0",
        ]:
            self.assertIn(marker, workflow)
        for forbidden in [
            "add-iam-policy-binding",
            "roles/firebaseauth.admin",
            "roles/identitytoolkit.admin",
        ]:
            self.assertNotIn(forbidden, workflow.lower())

    def test_workflow_publishes_only_a_redacted_safe_proof(self):
        workflow = (ROOT / ".github/workflows/etr-remote-viewer-human-live-e2e.yml").read_text(encoding="utf-8")
        proof_marker = "- name: Publier la preuve humaine sans identifiant ni secret"
        self.assertIn(proof_marker, workflow)
        proof = workflow.split(proof_marker, 1)[1]
        for marker in [
            "'checkedAt':",
            "'commit':",
            "'jobStatus':",
            "'gatewayUrl':",
            "'installationId':",
            "'healthBefore':",
            "'result':",
            "'healthAfter':",
            "<adresse-masquee>",
            "<secret-masque>",
        ]:
            self.assertIn(marker, proof)
        for forbidden in [
            "'email':",
            '"email":',
            "'password':",
            '"password":',
            "'uid':",
            '"uid":',
            "'idToken':",
            '"idToken":',
            "'refreshToken':",
            '"refreshToken":',
            "'authorization':",
            '"authorization":',
            "'accessToken':",
            '"accessToken":',
        ]:
            self.assertNotIn(forbidden, proof)


if __name__ == "__main__":
    unittest.main()
