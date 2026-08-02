import base64
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def fake_id_token(*, installation_id="etr-abcd1234ef56", etr_device=True):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "sub": "etrdev_test_device",
                "installationId": installation_id,
                "etrDevice": etr_device,
            }
        ).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.signature"


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
                "ETR_INSTALLATION_ID": "legacy-incorrect-installation",
                "ETR_REMOTE_GATEWAY_WSS": "wss://gateway.example/device",
                "ETR_TOKEN_FILE": "/var/lib/etr-core/remote-screen-auth.json",
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

    def test_installation_id_is_taken_from_signed_token_payload_not_environment(self):
        token = fake_id_token(installation_id="etr-0000dd7429c2")
        self.assertEqual(
            self.agent.installation_id_from_id_token(token),
            "etr-0000dd7429c2",
        )

    def test_token_without_device_claim_is_rejected(self):
        token = fake_id_token(etr_device=False)
        with self.assertRaisesRegex(RuntimeError, "device_session_claim_missing"):
            self.agent.installation_id_from_id_token(token)

    def test_invalid_installation_claim_is_rejected(self):
        token = fake_id_token(installation_id="../../invalid")
        with self.assertRaisesRegex(RuntimeError, "device_session_installation_invalid"):
            self.agent.installation_id_from_id_token(token)

    def test_invalid_jwt_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "device_session_token_invalid"):
            self.agent.installation_id_from_id_token("not-a-jwt")

    def test_missing_primary_device_session_does_not_start_enrollment(self):
        with patch.object(self.agent, "load_json", return_value={}) as load, patch.object(
            self.agent, "refresh_tokens"
        ) as refresh, patch.object(self.agent, "atomic_json_write") as save:
            with self.assertRaisesRegex(RuntimeError, "device_session_missing"):
                self.agent.authenticate_existing_device_session()
        load.assert_called_once_with(self.agent.PRIMARY_TOKEN_FILE)
        refresh.assert_not_called()
        save.assert_not_called()

    def test_existing_primary_refresh_token_uses_claim_identity_and_is_persisted(self):
        id_token = fake_id_token(installation_id="etr-0000dd7429c2")
        with patch.object(
            self.agent,
            "load_json",
            return_value={"refreshToken": "existing-refresh-token"},
        ) as load, patch.object(
            self.agent,
            "refresh_tokens",
            return_value={"idToken": id_token, "refreshToken": "next-refresh-token"},
        ) as refresh, patch.object(self.agent, "atomic_json_write") as save:
            token, installation_id = self.agent.authenticate_existing_device_session()
        self.assertEqual(token, id_token)
        self.assertEqual(installation_id, "etr-0000dd7429c2")
        load.assert_called_once_with(Path("/var/lib/etr-core/firebase-auth.json"))
        refresh.assert_called_once_with("existing-refresh-token")
        save.assert_called_once_with(
            Path("/var/lib/etr-core/firebase-auth.json"),
            {"idToken": id_token, "refreshToken": "next-refresh-token"},
        )

    def test_incomplete_refresh_response_is_rejected(self):
        with patch.object(
            self.agent,
            "load_json",
            return_value={"refreshToken": "existing-refresh-token"},
        ), patch.object(
            self.agent,
            "refresh_tokens",
            return_value={"refreshToken": "next-refresh-token"},
        ), patch.object(self.agent, "atomic_json_write") as save:
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
            "installation_id_from_id_token",
            "installation_id_from_local_device",
            "device_session_missing",
            "PRIMARY_TOKEN_FILE = Path(\"/var/lib/etr-core/firebase-auth.json\")",
            "load_json(PRIMARY_TOKEN_FILE)",
            "refresh_tokens",
            "atomic_json_write",
            'payload.get("installationId")',
            'payload.get("etrDevice") is not True',
            'f"etr-{serial[-12:].lower()}"',
        ]:
            self.assertIn(marker, source)
        self.assertNotIn("from firebase_bridge import INSTALLATION_ID", source)
        self.assertNotIn("load_tokens", source)
        self.assertNotIn("save_tokens", source)


if __name__ == "__main__":
    unittest.main()
