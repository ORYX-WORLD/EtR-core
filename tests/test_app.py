import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import create_app, read_enrollment_state, read_telemetry_state


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
        with patch.dict(
            os.environ,
            {
                "ETR_TELEMETRY_FILE": "/tmp/does-not-exist-etr.json",
                "ETR_ENROLLMENT_FILE": "/tmp/does-not-exist-enrollment.json",
                "ETR_TOKEN_FILE": "/tmp/does-not-exist-token.json",
            },
        ):
            payload = self.client.get("/api/v1/status").get_json()
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["health"], "degraded")
        self.assertNotIn("pressure_bar", payload)
        self.assertEqual(payload["telemetry"]["error"], "state_file_missing")
        self.assertTrue(payload["enrollment"]["required"])
        self.assertEqual(payload["enrollment"]["status"], "unconfigured")

    def test_reads_normalized_telemetry_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "updated_at": "2026-08-02T08:00:00+00:00",
                        "hardware": {"adc": "ADS1263", "status": "online", "chip_id": 1},
                        "sensors": [
                            {
                                "id": "pressure_1",
                                "ain": 0,
                                "kind": "pressure",
                                "status": "ok",
                                "value": 0.0,
                                "unit": "bar",
                            }
                        ],
                        "measurements": {"pressure_1_bar": 0.0},
                        "states": {"adc_online": True},
                        "alerts": [],
                    }
                ),
                encoding="utf-8",
            )
            data, error = read_telemetry_state(path)
        self.assertIsNone(error)
        self.assertEqual(data["schema_version"], "1.1")
        self.assertEqual(data["hardware"]["chip_id"], 1)
        self.assertEqual(data["sensors"][0]["id"], "pressure_1")
        self.assertEqual(data["measurements"]["pressure_1_bar"], 0.0)
        self.assertTrue(data["states"]["adc_online"])

    def test_exposes_only_safe_enrollment_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            enrollment = Path(directory) / "enrollment.json"
            token = Path(directory) / "firebase-auth.json"
            enrollment.write_text(
                json.dumps(
                    {
                        "installationId": "etr-abcd1234ef56",
                        "activationCode": "23456789ABCDEFGHJKLM",
                        "rotationToken": "must-never-be-returned",
                        "expiresAt": "2099-01-01T00:00:00Z",
                        "expiresEpoch": 4070908800,
                        "status": "pending",
                    }
                ),
                encoding="utf-8",
            )
            state = read_enrollment_state(enrollment, token)
        self.assertTrue(state["required"])
        self.assertEqual(state["activation_code"], "23456-789AB-CDEFG-HJKLM")
        self.assertEqual(state["installation_id"], "etr-abcd1234ef56")
        self.assertNotIn("rotationToken", state)
        self.assertNotIn("must-never-be-returned", json.dumps(state))

    def test_hides_activation_code_after_device_is_enrolled(self):
        with tempfile.TemporaryDirectory() as directory:
            enrollment = Path(directory) / "enrollment.json"
            token = Path(directory) / "firebase-auth.json"
            enrollment.write_text(json.dumps({"activationCode": "23456789ABCDEFGHJKLM"}), encoding="utf-8")
            token.write_text(json.dumps({"refreshToken": "stored-refresh-token"}), encoding="utf-8")
            state = read_enrollment_state(enrollment, token)
        self.assertFalse(state["required"])
        self.assertEqual(state["status"], "enrolled")
        self.assertIsNone(state["activation_code"])

    def test_enrollment_endpoint_uses_no_store(self):
        with patch.dict(
            os.environ,
            {
                "ETR_ENROLLMENT_FILE": "/tmp/does-not-exist-enrollment.json",
                "ETR_TOKEN_FILE": "/tmp/does-not-exist-token.json",
            },
        ):
            response = self.client.get("/api/v1/enrollment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("enrollment", response.get_json())


if __name__ == "__main__":
    unittest.main()
