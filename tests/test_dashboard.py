import unittest
from unittest.mock import Mock, patch

import requests

from dashboard.app import TOUCH_PORTAL_ORIGIN, create_app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "ETR_API_URL": "http://local.test/status"})
        self.client = self.app.test_client()

    def test_dashboard_is_rendered_from_versioned_sources(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-etr-dashboard-version", response.data)
        self.assertIn("Banc d’essai capteurs".encode(), response.data)
        self.assertIn(b"data-sensor-grid", response.data)
        self.assertIn(b"AIN0", response.data)
        self.assertIn(b"AIN3", response.data)
        self.assertIn("résistance fixe de 10 kΩ".encode(), response.data)
        self.assertIn(b"data-enrollment", response.data)
        self.assertIn(b"Associer cet EtR", response.data)
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn(f"frame-ancestors {TOUCH_PORTAL_ORIGIN}", csp)
        self.assertNotIn("frame-ancestors 'none'", csp)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_dashboard_health_exposes_sensor_ui_and_embed_contract(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["version"], "1.2.1")
        self.assertEqual(payload["embedded_by"], "http://127.0.0.1:8090")

    @patch("dashboard.app.requests.get")
    def test_status_proxy_returns_local_api_payload(self, get):
        upstream = Mock()
        upstream.raise_for_status.return_value = None
        upstream.json.return_value = {
            "schema_version": "1.0",
            "health": "ok",
            "telemetry": {
                "hardware": {"status": "online", "chip_id": 1},
                "sensors": [{"id": "pressure_1", "ain": 0, "status": "ok", "value": 0.0, "unit": "bar"}],
            },
            "enrollment": {
                "required": True,
                "status": "pending",
                "activation_code": "23456-789AB-CDEFG-HJKLM",
            },
        }
        get.return_value = upstream
        response = self.client.get("/api/status")
        payload = response.get_json()
        self.assertTrue(payload["api_online"])
        self.assertEqual(payload["data"]["schema_version"], "1.0")
        self.assertEqual(payload["data"]["telemetry"]["hardware"]["chip_id"], 1)
        self.assertEqual(payload["data"]["enrollment"]["status"], "pending")

    @patch("dashboard.app.requests.get", side_effect=requests.ConnectionError("network"))
    def test_status_proxy_fails_closed_without_trace(self, _get):
        response = self.client.get("/api/status")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["api_online"])
        self.assertEqual(payload["error"], "local_api_unavailable")


if __name__ == "__main__":
    unittest.main()
