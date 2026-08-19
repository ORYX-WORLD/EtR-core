import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "etr-deploy.yml"
DEPLOY_SCRIPT = ROOT / "src" / "deploy" / "raspi" / "etr_physical_deploy.sh"


class RaspberryPublicBootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        cls.deployment = f"{cls.workflow}\n{cls.deploy_script}"

    def test_physical_deploy_no_longer_requires_missing_github_secrets(self):
        self.assertNotIn("secrets.ETR_REMOTE_GATEWAY_WSS", self.deployment)
        self.assertNotIn("secrets.FIREBASE_API_KEY", self.deployment)
        self.assertNotIn("ETR_REMOTE_GATEWAY_WSS est requis", self.deployment)

    def test_firebase_public_configuration_is_discovered_and_validated(self):
        for marker in [
            "https://oryx-froid-industriel.web.app/__/firebase/init.json",
            "project != 'oryx-froid-industriel'",
            'echo "::add-mask::$firebase_api_key"',
            'set_value FIREBASE_API_KEY "$firebase_api_key"',
            "firebase-hosting-reserved-url+cloud-run-fixed-origin",
        ]:
            self.assertIn(marker, self.deployment)

    def test_cloud_run_origin_is_exact_and_generates_https_and_wss_endpoints(self):
        for marker in [
            "ETR_GATEWAY_ORIGIN: https://etr-remote-gateway-7n72m5gopq-ew.a.run.app",
            'remote_gateway=${gateway_origin/https:\/\//wss:\/\/}/device',
            'set_value ETR_REMOTE_GATEWAY_WSS "$remote_gateway"',
            'set_value FIREBASE_ENROLLMENT_URL "${gateway_origin}/api/enrollment"',
            "^ETR_REMOTE_GATEWAY_WSS=wss://.*\.run\.app/device$",
            "^FIREBASE_ENROLLMENT_URL=https://.*\.run\.app/api/enrollment$",
        ]:
            self.assertIn(marker, self.deployment)
        self.assertNotIn("onrender.com", self.deployment)

    def test_deployment_still_proves_physical_enrollment_and_remote_screen(self):
        for marker in [
            "bootstrap_registration",
            "bootstrap_public_key_fingerprint",
            "enrollment_status",
            "pending",
            "claimed",
            "enrolled",
            "etr-firebase-bridge.service",
            "etr-remote-screen.service",
            "5901",
        ]:
            self.assertIn(marker, self.deployment)
        self.assertIn("127\\.0\\.0\\.1", self.deployment)
        self.assertIn("Écran distant non connecté", self.deployment)


if __name__ == "__main__":
    unittest.main()
