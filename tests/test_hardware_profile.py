import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import threading
import runpy
from unittest.mock import patch

from src.app import create_app
from src.hardware_profile import default_profile, read_profile, save_profile, ProfileConflict, validate_profile
from src.sensor_acquisition import acquire_selected_once, atomic_write_json
from dashboard.app import create_app as create_dashboard

ROOT = Path(__file__).resolve().parents[1]


class HardwareProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile_path = Path(self.temp.name) / "hardware.json"
        self.telemetry = Path(self.temp.name) / "telemetry.json"
        self.env = patch.dict(os.environ, {"ETR_HARDWARE_PROFILE_FILE": str(self.profile_path),
                                          "ETR_TELEMETRY_FILE": str(self.telemetry)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = create_app().test_client()

    def disable_adc(self, modbus=False):
        profile = read_profile()
        profile["ads1263"]["enabled"] = False
        profile["modbus"]["enabled"] = modbus
        return save_profile(profile)

    def test_legacy_default_and_persistence_in_new_process(self):
        self.assertTrue(read_profile()["ads1263"]["enabled"])
        saved = self.disable_adc(True)
        result = subprocess.run([sys.executable, "-c", "from src.hardware_profile import read_profile; import json; print(json.dumps(read_profile()))"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), saved)
        self.assertEqual(saved["revision"], 1)

    def test_stale_revision_cannot_overwrite_saved_choices(self):
        old = read_profile()
        saved = self.disable_adc(True)
        with self.assertRaises(ProfileConflict):
            save_profile(old)
        self.assertEqual(read_profile(), saved)

    def test_validation_rejects_coercion_and_arbitrary_paths(self):
        for invalid in ("false", 0, None):
            profile = default_profile()
            profile["ads1263"]["enabled"] = invalid
            with self.assertRaises(ValueError):
                validate_profile(profile)
        for port in ("/etc/passwd", "/dev/serial/by-id/../../etc/passwd", "/dev/ttyUSB0;reboot"):
            profile = default_profile()
            profile["modbus"]["serial_port"] = port
            with self.assertRaises(ValueError):
                validate_profile(profile)

    def test_disabled_adc_never_reads_config_or_constructs_driver(self):
        self.disable_adc()
        with patch("src.sensor_acquisition.load_config", side_effect=AssertionError("no config read")), \
             patch("src.sensor_acquisition.acquire_once", side_effect=AssertionError("no ADC")):
            payload = acquire_selected_once(Path("missing-sensors.json"))
        self.assertEqual(payload["hardware"]["status"], "disabled")
        self.assertEqual(payload["sensors"], [])
        self.assertEqual(payload["measurements"], {})
        self.assertEqual(payload["alerts"], [])

    def test_modbus_enabled_without_port_is_not_reported_online(self):
        self.disable_adc(True)
        payload = acquire_selected_once(Path("missing"))
        self.assertEqual(payload["hardware"]["modbus"]["status"], "not_configured")
        self.assertEqual([a["code"] for a in payload["alerts"]], ["MODBUS_NOT_CONFIGURED"])
        atomic_write_json(self.telemetry, payload)
        status = self.client.get("/api/v1/status").get_json()
        self.assertFalse(status["telemetry"]["online"])
        self.assertEqual(status["health"], "degraded")
        self.assertFalse(status["capabilities"]["ads1263_acquisition"])

    def test_modbus_port_present_is_not_proof_of_measurements(self):
        profile = self.disable_adc(True)
        profile["modbus"]["serial_port"] = "/dev/ttyUSB0"
        save_profile(profile)
        for present, expected in ((False, "unavailable"), (True, "pending_validation")):
            with patch("src.hardware_profile.Path.exists", return_value=present):
                payload = acquire_selected_once(Path("missing"))
            self.assertEqual(payload["hardware"]["modbus"]["status"], expected)
            self.assertFalse(payload["measurements"])

    def test_enable_disable_reload_and_missing_enabled_adc_remains_fault(self):
        self.disable_adc()
        self.assertFalse(acquire_selected_once(Path("missing"))["alerts"])
        profile = read_profile()
        profile["ads1263"]["enabled"] = True
        save_profile(profile)
        with patch("src.sensor_acquisition.acquire_once", side_effect=OSError("absent")) as adc:
            payload = acquire_selected_once(ROOT / "config/sensors-home-lab.json")
            adc.assert_called_once()
        self.assertEqual(payload["alerts"][0]["code"], "ADC_UNAVAILABLE")
        self.assertEqual(payload["hardware"]["status"], "offline")

    def test_api_clears_old_values_immediately_after_selection_changes(self):
        atomic_write_json(self.telemetry, {"hardware": {"status": "online"}, "measurements": {"pressure_bar": 3},
                                          "sensors": [{"id": "old"}], "alerts": [{"code": "ADC_UNAVAILABLE"}]})
        self.disable_adc()
        status = self.client.get("/api/v1/status").get_json()
        self.assertEqual(status["telemetry"]["error"], "hardware_profile_applying")
        self.assertFalse(status["telemetry"]["measurements"])
        self.assertFalse(status["telemetry"]["sensors"])
        self.assertFalse(status["telemetry"]["alerts"])
        self.assertNotIn("pressure_bar", status)
        atomic_write_json(self.telemetry, acquire_selected_once(Path("missing")))
        status = self.client.get("/api/v1/status").get_json()
        self.assertEqual(status["health"], "ok")
        self.assertFalse(status["telemetry"]["online"])

    def test_invalid_profile_never_falls_back_to_adc_or_old_values(self):
        self.profile_path.write_text("{broken", encoding="utf-8")
        with patch("src.sensor_acquisition.acquire_once", side_effect=AssertionError("no ADC")):
            payload = acquire_selected_once(Path("missing"))
        self.assertEqual(payload["alerts"][0]["code"], "HARDWARE_PROFILE_INVALID")
        status = self.client.get("/api/v1/status").get_json()
        self.assertEqual(status["telemetry"]["error"], "hardware_profile_invalid")
        self.assertFalse(status["telemetry"]["measurements"])

    def test_stale_profile_frame_cannot_publish_old_values_to_cloud(self):
        profile = save_profile(default_profile())
        atomic_write_json(self.telemetry, {"hardware_profile": profile, "updated_at": "2000-01-01T00:00:00+00:00",
            "hardware": {"status": "online"}, "measurements": {"pressure_bar": 12}, "sensors": [{"id": "old"}]})
        status = self.client.get("/api/v1/status").get_json()
        self.assertEqual(status["telemetry"]["error"], "telemetry_stale")
        self.assertFalse(status["telemetry"]["online"])
        self.assertFalse(status["telemetry"]["measurements"])
        self.assertNotIn("pressure_bar", status)

    def test_api_validation_same_origin_and_conflict(self):
        profile = self.client.get("/api/v1/hardware").get_json()["profile"]
        profile["ads1263"]["enabled"] = False
        self.assertEqual(self.client.put("/api/v1/hardware", json=profile).status_code, 403)
        headers = {"X-ETR-Local-Write": "1", "Origin": "https://untrusted.example"}
        self.assertEqual(self.client.put("/api/v1/hardware", json=profile, headers=headers).status_code, 403)
        headers["Origin"] = "http://localhost"
        saved = self.client.put("/api/v1/hardware", json=profile, headers=headers)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["profile"]["revision"], 1)
        self.assertEqual(self.client.put("/api/v1/hardware", json=profile, headers=headers).status_code, 409)
        invalid = copy.deepcopy(saved.get_json()["profile"])
        invalid["modbus"]["enabled"] = "yes"
        self.assertEqual(self.client.put("/api/v1/hardware", json=invalid, headers=headers).status_code, 400)

    def test_runtime_cli_without_hat_or_sensor_file(self):
        self.disable_adc(True)
        result = subprocess.run([sys.executable, "src/sensor_acquisition_runtime.py", "--once", "--strict", "--config", str(Path(self.temp.name) / "absent.json"), "--state", str(self.telemetry)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.telemetry.read_text())["hardware"]["status"], "disabled")

    def test_dashboard_write_is_proxied_only_after_local_check(self):
        dashboard = create_dashboard({"TESTING": True}).test_client()
        with patch("dashboard.app.requests.request") as upstream:
            self.assertEqual(dashboard.put("/api/hardware", json={}).status_code, 403)
            upstream.assert_not_called()
            upstream.return_value.status_code = 200
            upstream.return_value.json.return_value = {"profile": default_profile()}
            response = dashboard.put("/api/hardware", json=default_profile(), headers={"X-ETR-Local-Write": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(upstream.call_args.args, ("PUT", "http://127.0.0.1:8080/api/v1/hardware"))
            self.assertFalse(upstream.call_args.kwargs["allow_redirects"])

    def test_dashboard_http_save_and_reload_reach_the_same_persistent_profile(self):
        from werkzeug.serving import make_server
        server = make_server("127.0.0.1", 0, create_app())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            dashboard = create_dashboard({"TESTING": True, "ETR_API_URL": f"http://127.0.0.1:{server.server_port}/api/v1/status"}).test_client()
            profile = dashboard.get("/api/hardware").get_json()["profile"]
            profile["ads1263"]["enabled"] = False
            profile["modbus"]["enabled"] = True
            response = dashboard.put("/api/hardware", json=profile, headers={"X-ETR-Local-Write": "1"})
            self.assertEqual(response.status_code, 200)
            new_dashboard = create_dashboard({"ETR_API_URL": f"http://127.0.0.1:{server.server_port}/api/v1/status"}).test_client()
            self.assertEqual(new_dashboard.get("/api/hardware").get_json(), response.get_json())
            atomic_write_json(self.telemetry, acquire_selected_once(Path("absent")))
            status = new_dashboard.get("/api/status").get_json()["data"]
            self.assertEqual(status["telemetry"]["hardware"]["status"], "disabled")
            self.assertFalse(status["telemetry"]["sensors"])
            self.assertEqual(status["telemetry"]["alerts"][0]["code"], "MODBUS_NOT_CONFIGURED")
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_bridge_preserves_disabled_hardware_without_publishing_old_measurements(self):
        self.disable_adc(True)
        atomic_write_json(self.telemetry, acquire_selected_once(Path("absent")))
        status = self.client.get("/api/v1/status").get_json()
        with patch.dict(os.environ, {"FIREBASE_API_KEY": "test-only", "FIREBASE_DATABASE_URL": "https://database.invalid",
                                     "ETR_DEVICE_SERIAL": "00000000TEST1234"}):
            bridge = runpy.run_path(str(ROOT / "src/firebase_bridge.py"))
        with patch.object(bridge["session"], "get") as get, patch.object(bridge["session"], "put") as put:
            get.return_value.json.return_value = status
            outgoing = bridge["read_local_state"]()
            bridge["publish"]("test-token", outgoing)
            posted = put.call_args.kwargs["json"]
            self.assertFalse(posted["hardware_profile"]["ads1263"]["enabled"])
            self.assertFalse(posted["telemetry"]["online"])
            self.assertFalse(posted["telemetry"]["measurements"])
            self.assertEqual(posted["telemetry"]["alerts"][0]["code"], "MODBUS_NOT_CONFIGURED")
