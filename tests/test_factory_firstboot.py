import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "src/deploy/raspi/etr_factory_firstboot.py"
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

    def test_redeem_sends_only_ticket_target_identity_and_public_key(self):
        response = Mock(ok=True)
        response.json.return_value = {"status": "registered", "installationId": "etr-abcd1234ef56"}
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
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], "https://gateway.example/api/enrollment/factory-bootstrap")
        self.assertNotIn("factoryPrivateToken", kwargs["json"])
        self.assertEqual(kwargs["json"]["installationId"], "etr-abcd1234ef56")
        self.assertEqual(kwargs["json"]["ticket"], "A" * 43)


if __name__ == "__main__":
    unittest.main()
