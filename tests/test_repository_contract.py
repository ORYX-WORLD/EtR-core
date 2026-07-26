import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_dashboard_delivery_contract_is_complete(self):
        required = [
            "dashboard/app.py",
            "dashboard/requirements.txt",
            "dashboard/templates/index.html",
            "dashboard/static/dashboard.css",
            "dashboard/static/dashboard.js",
            "src/deploy/raspi/etr-dashboard.service",
            "tests/test_dashboard.py",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Dashboard contract incomplete: {missing}")

    def test_installer_wires_dashboard_from_repository(self):
        script = (ROOT / "src/deploy/raspi/setup_etr.sh").read_text(encoding="utf-8")
        for marker in [
            "dashboard/requirements.txt",
            "src/deploy/raspi/etr-dashboard.service",
            "etr-dashboard.service",
            "http://127.0.0.1:8000/healthz",
        ]:
            self.assertIn(marker, script)
        self.assertNotIn("/opt/etr/dashboard/.venv/bin/gunicorn", script)

    def test_secure_enrollment_delivery_contract_is_complete(self):
        required = [
            "gateway/enrollment.mjs",
            "gateway/enrollment-http.mjs",
            "gateway/enrollment.test.mjs",
            "src/firebase_bridge.py",
            "src/deploy/raspi/etr-firebase-bridge.service",
            "tests/test_app.py",
            ".github/workflows/etr-gateway-cloudrun.yml",
            ".github/workflows/etr-deploy.yml",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Enrollment contract incomplete: {missing}")

        server = (ROOT / "gateway/server.mjs").read_text(encoding="utf-8")
        local_api = (ROOT / "src/app.py").read_text(encoding="utf-8")
        dashboard = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        for marker in ["installEnrollmentRoutes", 'enrollment: "v1"']:
            self.assertIn(marker, server)
        for marker in ["/api/v1/enrollment", '"secure_enrollment": True']:
            self.assertIn(marker, local_api)
        for marker in ["data-enrollment", "Associer cet EtR"]:
            self.assertIn(marker, dashboard)

    def test_firebase_bridge_is_non_privileged_and_versioned(self):
        unit = (ROOT / "src/deploy/raspi/etr-firebase-bridge.service").read_text(encoding="utf-8")
        installer = (ROOT / "src/deploy/raspi/setup_etr.sh").read_text(encoding="utf-8")
        legacy = (ROOT / "scripts/install_firebase_bridge.sh").read_text(encoding="utf-8")
        for marker in [
            "User=oryx",
            "Group=oryx",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ReadWritePaths=/var/lib/etr-core",
            "UMask=0077",
        ]:
            self.assertIn(marker, unit)
        for marker in [
            "src/deploy/raspi/etr-firebase-bridge.service",
            "etr-firebase-bridge.service",
            "root:oryx",
            "chmod 640",
            "chmod 600",
        ]:
            self.assertIn(marker, installer)
        self.assertNotIn("User=root", unit + legacy)
        self.assertNotIn("/opt/etr-core/venv", legacy)

    def test_physical_deploy_covers_every_runtime_component(self):
        workflow = (ROOT / ".github/workflows/etr-deploy.yml").read_text(encoding="utf-8")
        for marker in [
            "src/app.py",
            "src/firebase_bridge.py",
            "dashboard/**",
            "etr-dashboard.service",
            "etr-firebase-bridge.service",
            "127.0.0.1:8000/healthz",
            "127.0.0.1:8080/api/v1/enrollment",
            "data-enrollment",
            "enrollment_status",
        ]:
            self.assertIn(marker, workflow)

    def test_gateway_deploy_tests_and_proves_enrollment(self):
        workflow = (ROOT / ".github/workflows/etr-gateway-cloudrun.yml").read_text(encoding="utf-8")
        for marker in [
            "gateway/**",
            "npm test",
            "roles/iam.serviceAccountTokenCreator",
            "iamcredentials.googleapis.com",
            "/api/enrollment/request",
            'data.get("enrollment")=="v1"',
            "gateway-last-deploy.json",
        ]:
            self.assertIn(marker, workflow)

    def test_local_http_services_are_not_exposed_on_all_interfaces(self):
        api_unit = (ROOT / "src/deploy/etr.service").read_text(encoding="utf-8")
        dashboard_unit = (ROOT / "src/deploy/raspi/etr-dashboard.service").read_text(encoding="utf-8")
        self.assertIn("--bind 127.0.0.1:8080", api_unit)
        self.assertIn("--bind 127.0.0.1:8000", dashboard_unit)
        self.assertNotIn("0.0.0.0", api_unit + dashboard_unit)


if __name__ == "__main__":
    unittest.main()
