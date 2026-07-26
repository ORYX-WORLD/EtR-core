import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RtdbDeploymentContractTests(unittest.TestCase):
    def test_rules_are_synchronized_with_verified_human_access(self):
        rules = json.loads((ROOT / "firebase/database.rules.json").read_text(encoding="utf-8"))["rules"]
        installation = rules["installations"]["$installationId"]
        self.assertIn("auth.token.email_verified === true", installation[".read"])
        self.assertIn("deviceAccess", installation[".read"])
        self.assertIn("auth.token.email_verified === true", rules["memberships"]["$uid"][".read"])
        self.assertIn("auth.token.email_verified === true", rules["userInstallations"]["$uid"][".read"])
        for branch in ["deviceBootstrap", "activationCodes", "enrollmentRequests"]:
            self.assertFalse(rules[branch][".read"])
            self.assertFalse(rules[branch][".write"])

    def test_workflow_uses_existing_wif_identity_without_self_grant(self):
        workflow = (ROOT / ".github/workflows/etr-rtdb-rules.yml").read_text(encoding="utf-8")
        for marker in [
            "google-github-actions/auth@v3",
            "gcloud auth print-access-token",
            "scripts/deploy_rtdb_rules.py",
            "rtdb-rules-last.json",
            "rulesSha256",
            "readbackSha256",
            "verified",
            "continue-on-error: true",
        ]:
            self.assertIn(marker, workflow)
        self.assertNotIn("add-iam-policy-binding", workflow)
        self.assertNotIn("roles/firebasedatabase.admin", workflow)

    def test_deployer_discovers_instance_and_requires_readback_equality(self):
        deployer = (ROOT / "scripts/deploy_rtdb_rules.py").read_text(encoding="utf-8")
        for marker in [
            "firebasedatabase.googleapis.com/v1beta/projects/",
            "locations/-/instances",
            "/.settings/rules.json",
            "rulesSha256",
            "readbackSha256",
            "report[\"verified\"]",
            "GCP_ACCESS_TOKEN",
            "[REDACTED_ACCESS_TOKEN]",
        ]:
            self.assertIn(marker, deployer)


if __name__ == "__main__":
    unittest.main()
