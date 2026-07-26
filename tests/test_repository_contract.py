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

    def test_physical_deploy_covers_every_runtime_component(self):
        workflow = (ROOT / ".github/workflows/etr-deploy.yml").read_text(encoding="utf-8")
        for marker in ["src/app.py", "dashboard/**", "etr-dashboard.service", "127.0.0.1:8000/healthz"]:
            self.assertIn(marker, workflow)

    def test_local_http_services_are_not_exposed_on_all_interfaces(self):
        api_unit = (ROOT / "src/deploy/etr.service").read_text(encoding="utf-8")
        dashboard_unit = (ROOT / "src/deploy/raspi/etr-dashboard.service").read_text(encoding="utf-8")
        self.assertIn("--bind 127.0.0.1:8080", api_unit)
        self.assertIn("--bind 127.0.0.1:8000", dashboard_unit)
        self.assertNotIn("0.0.0.0", api_unit + dashboard_unit)


if __name__ == "__main__":
    unittest.main()
