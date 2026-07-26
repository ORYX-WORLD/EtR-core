import importlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class FirebaseBridgeEnrollmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(
            os.environ,
            {
                "FIREBASE_API_KEY": "test-api-key",
                "FIREBASE_DATABASE_URL": "https://example-default-rtdb.europe-west1.firebasedatabase.app",
                "ETR_DEVICE_SERIAL": "0000ABCD1234EF56",
                "ETR_INSTALLATION_ID": "etr-abcd1234ef56",
            },
            clear=False,
        )
        cls.environment.start()
        sys.modules.pop("src.firebase_bridge", None)
        cls.bridge = importlib.import_module("src.firebase_bridge")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        sys.modules.pop("src.firebase_bridge", None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bridge.TOKEN_FILE = root / "firebase-auth.json"
        self.bridge.ENROLLMENT_FILE = root / "enrollment.json"
        self.bridge.ENROLLMENT_URL = "https://gateway.example/api/enrollment"
        self.bridge.ACTIVATION_CODE = ""
        self.bridge.AUTH_EMAIL = ""
        self.bridge.AUTH_PASSWORD = ""

    def tearDown(self):
        self.temp.cleanup()

    def test_normalizes_serial_and_activation_code(self):
        self.assertEqual(self.bridge.normalize_serial(" 0000-abcd-1234-ef56 "), "0000ABCD1234EF56")
        self.assertEqual(self.bridge.normalize_activation_code("0abcd-12345-6789a-bcdef"), "0ABCD123456789ABCDEF")

    def test_atomic_json_state_is_private(self):
        self.bridge.atomic_json_write(self.bridge.ENROLLMENT_FILE, {"status": "pending"})
        mode = stat.S_IMODE(self.bridge.ENROLLMENT_FILE.stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(self.bridge.load_json(self.bridge.ENROLLMENT_FILE)["status"], "pending")

    def test_requests_and_persists_a_physical_activation_code(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "installationId": "etr-abcd1234ef56",
            "activationCode": "00000-11111-22222-33333",
            "rotationToken": "rotation-token-value-with-sufficient-length",
            "expiresAt": "2026-07-27T10:00:00Z",
            "expiresIn": 86400,
        }
        with patch.object(self.bridge.session, "post", return_value=response) as post:
            result = self.bridge.request_enrollment()
        self.assertEqual(result["status"], "pending")
        state = self.bridge.load_enrollment()
        self.assertEqual(state["activationCode"], "00000-11111-22222-33333")
        self.assertEqual(state["rotationToken"], "rotation-token-value-with-sufficient-length")
        self.assertGreater(state["expiresEpoch"], 0)
        self.assertEqual(stat.S_IMODE(self.bridge.ENROLLMENT_FILE.stat().st_mode), 0o600)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["action"], "request")
        self.assertEqual(payload["serial"], "0000ABCD1234EF56")
        self.assertNotIn("rotationToken", payload)

    def test_uses_rotation_token_for_a_controlled_code_renewal(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "installationId": "etr-abcd1234ef56",
            "activationCode": "44444-55555-66666-77777",
            "rotationToken": "new-rotation-token-value-with-length",
            "expiresAt": "2026-07-27T10:00:00Z",
            "expiresIn": 86400,
        }
        with patch.object(self.bridge.session, "post", return_value=response) as post:
            self.bridge.request_enrollment({"rotationToken": "previous-private-token"})
        self.assertEqual(post.call_args.kwargs["json"]["rotationToken"], "previous-private-token")

    def test_authenticate_exchanges_the_code_and_removes_local_activation_state(self):
        self.bridge.save_enrollment(
            {
                "installationId": "etr-abcd1234ef56",
                "activationCode": "00000-11111-22222-33333",
                "rotationToken": "private-rotation-token",
                "expiresAt": "2099-01-01T00:00:00Z",
                "expiresEpoch": 4070908800,
                "status": "pending",
            }
        )
        with patch.object(
            self.bridge,
            "exchange_activation_code",
            return_value={"idToken": "id-token", "refreshToken": "refresh-token"},
        ) as exchange:
            token = self.bridge.authenticate()
        self.assertEqual(token, "id-token")
        exchange.assert_called_once_with("00000111112222233333")
        self.assertFalse(self.bridge.ENROLLMENT_FILE.exists())
        stored = json.loads(self.bridge.TOKEN_FILE.read_text(encoding="utf-8"))
        self.assertEqual(stored["refreshToken"], "refresh-token")
        self.assertEqual(stat.S_IMODE(self.bridge.TOKEN_FILE.stat().st_mode), 0o600)

    def test_pending_claim_does_not_discard_the_activation_code(self):
        self.bridge.save_enrollment(
            {
                "activationCode": "00000-11111-22222-33333",
                "rotationToken": "private-rotation-token",
                "expiresEpoch": 4070908800,
            }
        )
        response = Mock(status_code=409)
        response.json.return_value = {"code": "awaiting_claim"}
        with patch.object(self.bridge.session, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "awaiting_claim"):
                self.bridge.exchange_activation_code("00000111112222233333")
        self.assertTrue(self.bridge.ENROLLMENT_FILE.exists())


if __name__ == "__main__":
    unittest.main()
