import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class FirebaseBridgeDirectSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(
            os.environ,
            {
                "FIREBASE_API_KEY": "test-api-key-for-device-session",
                "FIREBASE_DATABASE_URL": "https://example-default-rtdb.europe-west1.firebasedatabase.app",
                "FIREBASE_ENROLLMENT_URL": "https://gateway.example/api/enrollment",
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
        self.bridge.BOOTSTRAP_PRIVATE_KEY = root / "bootstrap-private.pem"
        self.bridge.BOOTSTRAP_PUBLIC_KEY = root / "bootstrap-public.pem"
        self.bridge.ENROLLMENT_URL = "https://gateway.example/api/enrollment"

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_direct_id_and_refresh_tokens_from_signed_gateway(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "idToken": "header.payload.signature",
            "refreshToken": "r" * 80,
            "expiresIn": 3600,
            "authMode": "password_session",
            "installationId": "etr-abcd1234ef56",
            "deviceUid": "etrdev_abcdef",
            "status": "exchanged",
        }
        with patch.object(self.bridge.session, "post", return_value=response) as post, patch.object(
            self.bridge, "sign_in_custom_token", side_effect=AssertionError("custom token path must not run")
        ):
            tokens = self.bridge.exchange_activation_code("00000-11111-22222-33333")
        self.assertEqual(tokens["idToken"], "header.payload.signature")
        self.assertEqual(tokens["refreshToken"], "r" * 80)
        self.assertEqual(tokens["expiresIn"], 3600)
        self.assertIn("X-EtR-Signature", post.call_args.kwargs["headers"])

    def test_keeps_legacy_custom_token_compatibility_during_transition(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"customToken": "legacy.custom.token"}
        expected = {"idToken": "id-token", "refreshToken": "refresh-token"}
        with patch.object(self.bridge.session, "post", return_value=response), patch.object(
            self.bridge, "sign_in_custom_token", return_value=expected
        ) as sign_in:
            tokens = self.bridge.exchange_activation_code("00000-11111-22222-33333")
        self.assertEqual(tokens, expected)
        sign_in.assert_called_once_with("legacy.custom.token")

    def test_rejects_incomplete_identity_response(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"status": "exchanged"}
        with patch.object(self.bridge.session, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "incomplète"):
                self.bridge.exchange_activation_code("00000-11111-22222-33333")


if __name__ == "__main__":
    unittest.main()
