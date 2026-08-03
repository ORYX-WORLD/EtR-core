import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "src/deploy/raspi/etr_factory_firstboot.py"
SPEC = importlib.util.spec_from_file_location("etr_factory_firstboot", MODULE_PATH)
firstboot = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["etr_factory_firstboot"] = firstboot
SPEC.loader.exec_module(firstboot)


class FactoryFirstBootTests(unittest.TestCase):
    def test_normalize_serial_and_installation_id_contract(self):
        serial = firstboot.normalize_serial(" 0000-abcd-1234-ef56 ")
        self.assertEqual(serial, "0000ABCD1234EF56")
        self.assertEqual(f"etr-{serial[-12:].lower()}", "etr-abcd1234ef56")

    def test_redeem_requires_and_returns_factory_session(self):
        response = Mock(ok=True)
        response.json.return_value = {
            "status": "registered",
            "installationId": "etr-abcd1234ef56",
            "deviceUid": "factory-device-uid",
            "idToken": "header.payload.signature",
            "refreshToken": "R" * 64,
            "expiresIn": 3600,
            "authMode": "factory_password_session",
        }
        with patch.object(firstboot.requests, "post", return_value=response) as post:
            result = firstboot.redeem(
                {
                    "gatewayOrigin": "https://gateway.example",
                    "ticket": "A" * 43,
                    "factoryPrivateToken": "must-not-be-forwarded",
                },
                "0000ABCD1234EF56",
                "-----BEGIN PUBLIC KEY-----\nTEST\n-----END PUBLIC KEY-----\n",
            )
        self.assertEqual(result["status"], "registered")
        self.assertEqual(result["authMode"], "factory_password_session")
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], "https://gateway.example/api/enrollment/factory-bootstrap")
        self.assertNotIn("factoryPrivateToken", kwargs["json"])
        self.assertEqual(kwargs["json"]["installationId"], "etr-abcd1234ef56")
        self.assertEqual(kwargs["json"]["ticket"], "A" * 43)

    def test_save_factory_session_separates_tokens_from_result_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "firebase-auth.json"
            result_file = Path(tmp) / "factory-bootstrap-result.json"
            result = {
                "status": "registered",
                "installationId": "etr-abcd1234ef56",
                "deviceUid": "factory-device-uid",
                "idToken": "header.payload.signature",
                "refreshToken": "R" * 64,
                "expiresIn": 3600,
                "authMode": "factory_password_session",
            }
            with (
                patch.object(firstboot, "AUTH_FILE", auth_file),
                patch.object(firstboot, "RESULT_FILE", result_file),
                patch.object(firstboot.os, "chown"),
            ):
                firstboot.save_factory_session(result)
            auth = json.loads(auth_file.read_text(encoding="utf-8"))
            proof = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertEqual(auth["idToken"], "header.payload.signature")
            self.assertEqual(auth["refreshToken"], "R" * 64)
            self.assertNotIn("idToken", proof)
            self.assertNotIn("refreshToken", proof)
            self.assertEqual(proof["deviceUid"], "factory-device-uid")


if __name__ == "__main__":
    unittest.main()
