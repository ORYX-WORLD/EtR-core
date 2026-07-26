import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import create_app, read_telemetry_state


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_health_endpoint_is_local_service_contract(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_status_does_not_invent_field_measurements(self):
        with patch.dict(os.environ, {"ETR_TELEMETRY_FILE": "/tmp/does-not-exist-etr.json"}):
            payload = self.client.get("/api/v1/status").get_json()
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["health"], "degraded")
        self.assertNotIn("pressure_bar", payload)
        self.assertEqual(payload["telemetry"]["error"], "state_file_missing")

    def test_reads_normalized_telemetry_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-07-26T08:00:00+00:00",
                        "measurements": {"pressure_bar": 31.25},
                        "states": {"compressor_on": True},
                        "alerts": ["Test"],
                    }
                ),
                encoding="utf-8",
            )
            data, error = read_telemetry_state(path)
        self.assertIsNone(error)
        self.assertEqual(data["measurements"]["pressure_bar"], 31.25)
        self.assertTrue(data["states"]["compressor_on"])
        self.assertEqual(data["alerts"], ["Test"])


if __name__ == "__main__":
    unittest.main()
