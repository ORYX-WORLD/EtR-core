import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class RemoteScreenSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(
            os.environ,
            {
                "FIREBASE_API_KEY": "test-api-key-remote-screen",
                "FIREBASE_DATABASE_URL": "https://example-default-rtdb.europe-west1.firebasedatabase.app",
                "FIREBASE_ENROLLMENT_URL": "https://gateway.example/api/enrollment",
                "ETR_DEVICE_SERIAL": "0000ABCD1234EF56",
                "ETR_INSTALLATION_ID": "etr-abcd1234ef56",
                "ETR_REMOTE_GATEWAY_WSS": "wss://gateway.example/device",
            },
            clear=False,
        )
        cls.environment.start()
        sys.path.insert(0, str(ROOT / "src"))
        for name in ("remote_screen_agent", "firebase_bridge"):
            sys.modules.pop(name, None)
        cls.agent = importlib.import_module("remote_screen_agent")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        for name in ("remote_screen_agent", "firebase_bridge"):
            sys.modules.pop(name, None)
        try:
            sys.path.remove(str(ROOT / "src"))
        except ValueError:
            pass

    def test_missing_primary_device_session_does_not_start_enrollment(self):
        with patch.object(self.agent, "load_tokens", return_value={}), patch.object(
            self.agent, "refresh_tokens"
        ) as refresh, patch.object(self.agent, "save_tokens") as save:
            with self.assertRaisesRegex(RuntimeError, "device_session_missing"):
                self.agent.authenticate_existing_device_session()
        refresh.assert_not_called()
        save.assert_not_called()

    def test_existing_primary_refresh_token_is_reused_and_persisted(self):
        with patch.object(
            self.agent,
            "load_tokens",
            return_value={"refreshToken": "existing-refresh-token"},
        ), patch.object(
            self.agent,
            "refresh_tokens",
            return_value={"idToken": "new-id-token", "refreshToken": "next-refresh-token"},
        ) as refresh, patch.object(self.agent, "save_tokens") as save:
            token = self.agent.authenticate_existing_device_session()
        self.assertEqual(token, "new-id-token")
        refresh.assert_called_once_with("existing-refresh-token")
        save.assert_called_once_with(
            {"idToken": "new-id-token", "refreshToken": "next-refresh-token"}
        )

    def test_incomplete_refresh_response_is_rejected(self):
        with patch.object(
            self.agent,
            "load_tokens",
            return_value={"refreshToken": "existing-refresh-token"},
        ), patch.object(
            self.agent,
            "refresh_tokens",
            return_value={"refreshToken": "next-refresh-token"},
        ), patch.object(self.agent, "save_tokens") as save:
            with self.assertRaisesRegex(RuntimeError, "device_session_refresh_incomplete"):
                self.agent.authenticate_existing_device_session()
        save.assert_not_called()


class RemoteScreenRepositoryContractTests(unittest.TestCase):
    def test_remote_screen_service_shares_enrolled_device_identity(self):
        unit = (ROOT / "src/deploy/raspi/etr-remote-screen.service").read_text(encoding="utf-8")
        source = (ROOT / "src/remote_screen_agent.py").read_text(encoding="utf-8")
        for marker in [
            "After=network-online.target etr-vnc.service etr-firebase-bridge.service",
            "Environment=ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json",
            "User=oryx",
            "Group=oryx",
            "NoNewPrivileges=true",
        ]:
            self.assertIn(marker, unit)
        self.assertNotIn("Environment=ETR_TOKEN_FILE=/var/lib/etr-core/remote-screen-auth.json", unit)
        for marker in [
            "authenticate_existing_device_session",
            "device_session_missing",
            "load_tokens",
            "refresh_tokens",
            "save_tokens",
        ]:
            self.assertIn(marker, source)
        self.assertNotIn("from firebase_bridge import INSTALLATION_ID, authenticate", source)


if __name__ == "__main__":
    unittest.main()
