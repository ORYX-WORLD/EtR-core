import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FirebaseRulesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads((ROOT / "firebase/database.rules.json").read_text(encoding="utf-8"))["rules"]

    def test_database_is_default_deny(self):
        self.assertFalse(self.rules[".read"])
        self.assertFalse(self.rules[".write"])

    def test_membership_active_flag_must_be_boolean(self):
        validation = self.rules["memberships"]["$uid"]["$installationId"][".validate"]
        self.assertIn("child('active').isBoolean()", validation)

    def test_device_access_must_be_string(self):
        self.assertEqual(self.rules["deviceAccess"]["$uid"][".validate"], "newData.isString()")

    def test_latest_payload_binds_installation_and_timestamp_types(self):
        validation = self.rules["installations"]["$installationId"]["latest"][".validate"]
        self.assertIn("installation_id').isString()", validation)
        self.assertIn("updated_at').isString()", validation)
        self.assertIn("val() === $installationId", validation)


if __name__ == "__main__":
    unittest.main()
