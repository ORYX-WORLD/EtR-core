import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeviceSessionDeployabilityTests(unittest.TestCase):
    def test_standard_device_session_does_not_require_firebase_admin_auth(self):
        issuer = (ROOT / "gateway/firebase-device-session.mjs").read_text(encoding="utf-8")
        for marker in [
            "accounts:signUp",
            "accounts:delete",
            "managesUsers: false",
            "adminAuthRequired: false",
            "customClaimsRequired: false",
            'firebaseUidSource: "identity-toolkit-sign-up"',
        ]:
            self.assertIn(marker, issuer)
        for forbidden in [
            "auth.getUser(",
            "auth.createUser(",
            "auth.updateUser(",
            "auth.setCustomUserClaims(",
            "auth.revokeRefreshTokens(",
        ]:
            self.assertNotIn(forbidden, issuer)

    def test_enrollment_binds_the_issued_uid_and_has_failure_cleanup(self):
        enrollment = (ROOT / "gateway/enrollment.mjs").read_text(encoding="utf-8")
        enrollment_http = (ROOT / "gateway/enrollment-http.mjs").read_text(encoding="utf-8")
        for marker in [
            "issuedSession.uid",
            "store.unbindDevice",
            "auth.revokeSession",
            "rollbackExchange",
            "issuer.managesUsers !== false",
            "issuer.revoke(session)",
        ]:
            self.assertIn(marker, enrollment + enrollment_http)

    def test_cloud_workflow_keeps_the_live_session_proof_mandatory(self):
        workflow = (ROOT / ".github/workflows/etr-gateway-cloudrun.yml").read_text(encoding="utf-8")
        for marker in [
            "/api/enrollment/session-health",
            "firebaseSession",
            "deviceSessionIssuance",
            "tokenExchange",
        ]:
            self.assertIn(marker, workflow)


if __name__ == "__main__":
    unittest.main()
