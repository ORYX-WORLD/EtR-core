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

    def test_script_sends_real_verification_email_then_self_cleans(self):
        script = (ROOT / "gateway/remote-screen-human-live-e2e.mjs").read_text(encoding="utf-8")
        for marker in [
            "buildPlusAlias",
            "ETR_HUMAN_E2E_EMAIL_BASE",
            'method: "accounts:signUp"',
            'method: "accounts:sendOobCode"',
            'requestType: "VERIFY_EMAIL"',
            "idToken",
            "waitForVerifiedSignIn",
            "email_verified",
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
            "verificationEmailSent",
        ]:
            self.assertIn(marker, script)
        for forbidden in [
            "auth.createUser",
            "auth.deleteUser",
            "returnOobLink",
            "GOOGLE_OAUTH_ACCESS_TOKEN",
            "console.log(email)",
            "console.log(password)",
        ]:
            self.assertNotIn(forbidden, script)

        report_section = script.split("const report = {", 1)[1]
        for forbidden in [
            "email,",
            "password,",
            "uid,",
            "idToken",
            "refreshToken",
        ]:
            self.assertNotIn(forbidden, report_section)

    def test_workflow_uses_wif_only_for_temporary_membership(self):
        workflow = (ROOT / ".github/workflows/etr-remote-viewer-human-live-e2e.yml").read_text(encoding="utf-8")
        for marker in [
            "id-token: write",
            "google-github-actions/auth@v3",
            "GCP_WORKLOAD_IDENTITY_PROVIDER",
            "GCP_SERVICE_ACCOUNT",
            "ETR_HUMAN_E2E_EMAIL_BASE: amotard.oryx@gmail.com",
            "firebase/init.json",
            "remote-screen-human-live-e2e.mjs",
            "etr-remote-viewer-human-live-e2e-last.json",
            "int(data.get('devices', 0)) >= 1",
            "int(data.get('viewers', 0)) == 0",
        ]:
            self.assertIn(marker, workflow)
        for forbidden in [
            "google-github-actions/setup-gcloud",
            "gcloud auth print-access-token",
            "GOOGLE_OAUTH_ACCESS_TOKEN",
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
            "amotard.oryx@gmail.com",
        ]:
            self.assertNotIn(forbidden, proof)


if __name__ == "__main__":
    unittest.main()
