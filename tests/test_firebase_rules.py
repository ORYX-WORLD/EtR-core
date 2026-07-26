import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FirebaseRulesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads((ROOT / "firebase/database.rules.json").read_text(encoding="utf-8"))["rules"]
        cls.installation = cls.rules["installations"]["$installationId"]

    def test_database_is_default_deny(self):
        self.assertFalse(self.rules[".read"])
        self.assertFalse(self.rules[".write"])

    def test_human_reads_require_verified_email_and_active_membership(self):
        expression = self.installation[".read"]
        self.assertIn("auth.token.email_verified === true", expression)
        self.assertIn("child('active').val() === true", expression)
        self.assertIn("deviceAccess", expression)
        self.assertIn("oryxStaff", expression)
        self.assertIn("oryxDeveloper", expression)

    def test_membership_and_fleet_reads_require_verified_email(self):
        membership_read = self.rules["memberships"]["$uid"][".read"]
        fleet_read = self.rules["userInstallations"]["$uid"][".read"]
        for expression in [membership_read, fleet_read]:
            self.assertIn("auth.token.email_verified === true", expression)
            self.assertIn("auth.uid === $uid", expression)
            self.assertIn("oryxStaff", expression)
            self.assertIn("oryxDeveloper", expression)

    def test_membership_active_flag_and_roles_are_typed(self):
        validation = self.rules["memberships"]["$uid"]["$installationId"][".validate"]
        self.assertIn("child('active').isBoolean()", validation)
        self.assertIn("child('role').isString()", validation)
        for role in ["owner", "administrator", "installer", "maintenance", "operator", "viewer"]:
            self.assertIn(f"=== '{role}'", validation)

    def test_device_access_remains_available_to_the_bound_technical_identity(self):
        node = self.rules["deviceAccess"]["$uid"]
        self.assertEqual(node[".validate"], "newData.isString()")
        self.assertIn("$uid === auth.uid", node[".read"])
        self.assertFalse(node[".write"])

    def test_latest_payload_binds_installation_and_timestamp_types(self):
        validation = self.installation["latest"][".validate"]
        self.assertIn("installation_id').isString()", validation)
        self.assertIn("updated_at').isString()", validation)
        self.assertIn("val() === $installationId", validation)

    def test_human_writes_require_verified_email_for_every_role_path(self):
        expressions = [
            self.installation["configuration"][".write"],
            self.installation["maintenance"][".write"],
            self.installation["commands"]["commissioning"][".write"],
            self.installation["commands"]["maintenance"][".write"],
            self.installation["commands"]["operation"][".write"],
        ]
        for expression in expressions:
            self.assertIn("auth.token.email_verified === true", expression)
            self.assertIn("oryxDeveloper", expression)

    def test_enrollment_and_bootstrap_branches_are_never_client_accessible(self):
        for branch in ["deviceBootstrap", "activationCodes", "enrollmentRequests"]:
            self.assertFalse(self.rules[branch][".read"])
            self.assertFalse(self.rules[branch][".write"])


if __name__ == "__main__":
    unittest.main()
