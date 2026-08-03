import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FactorySessionContractTests(unittest.TestCase):
    def test_factory_session_delivery_contract_is_complete(self):
        required = [
            "gateway/factory-device-session.mjs",
            "gateway/factory-device-session.test.mjs",
            "gateway/factory-enrollment-http.test.mjs",
            "gateway/enrollment-http.mjs",
            "gateway/Dockerfile",
            "src/deploy/raspi/etr_factory_firstboot.py",
            "tests/test_factory_firstboot.py",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Factory session contract incomplete: {missing}")

    def test_factory_session_avoids_firebase_admin_auth(self):
        issuer = (ROOT / "gateway/factory-device-session.mjs").read_text(encoding="utf-8")
        routes = (ROOT / "gateway/enrollment-http.mjs").read_text(encoding="utf-8")
        for marker in [
            "accounts:signUp",
            "accounts:signInWithPassword",
            "factory_password_session",
            "adminAuthRequired: false",
            "customClaimsRequired: false",
        ]:
            self.assertIn(marker, issuer)
        for marker in [
            "createFactoryDeviceSessionIssuer",
            "/api/enrollment/factory-bootstrap",
            "deviceAccess/${session.uid}",
            "metadata/provisioning_mode",
            "session.refreshToken",
        ]:
            self.assertIn(marker, routes)
        self.assertNotIn("setCustomUserClaims", issuer)
        self.assertNotIn("auth.createUser", issuer)

    def test_factory_runtime_and_first_boot_persist_the_session_safely(self):
        dockerfile = (ROOT / "gateway/Dockerfile").read_text(encoding="utf-8")
        firstboot = (ROOT / "src/deploy/raspi/etr_factory_firstboot.py").read_text(encoding="utf-8")
        self.assertIn("COPY factory-device-session.mjs ./", dockerfile)
        for marker in [
            'AUTH_FILE = STATE_DIR / "firebase-auth.json"',
            'RESULT_FILE = STATE_DIR / "factory-bootstrap-result.json"',
            "save_factory_session(result)",
            'if key not in {"idToken", "refreshToken"}',
            "os.chmod(temporary, 0o600)",
            "TICKET_FILE.unlink()",
        ]:
            self.assertIn(marker, firstboot)
        self.assertLess(firstboot.index("save_factory_session(result)"), firstboot.index("TICKET_FILE.unlink()"))


if __name__ == "__main__":
    unittest.main()
