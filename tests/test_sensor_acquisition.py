import json
import tempfile
import unittest
from pathlib import Path

from src.sensor_acquisition import (
    atomic_write_json,
    build_payload,
    failure_payload,
    load_config,
    ntc_resistance_from_ratio,
    ntc_sample,
    pressure_from_ratio,
    pressure_sample,
)


CONFIG = {
    "profile": "test",
    "adc": {"reference_voltage_v": 5.0},
    "channels": [
        {
            "id": "pressure_1",
            "label": "Pression 1",
            "kind": "pressure",
            "ain": 0,
            "minimum_bar": 0,
            "maximum_bar": 45,
            "minimum_ratio": 0.1,
            "maximum_ratio": 0.9,
        },
        {
            "id": "temperature_1",
            "label": "Température 1",
            "kind": "ntc",
            "ain": 2,
            "reference_resistor_ohm": 10000,
            "beta_k": None,
        },
    ],
}


class SensorConversionTests(unittest.TestCase):
    def test_carel_zero_and_full_scale(self):
        self.assertAlmostEqual(pressure_from_ratio(0.1), 0.0)
        self.assertAlmostEqual(pressure_from_ratio(0.9), 45.0)
        self.assertAlmostEqual(pressure_from_ratio(0.5), 22.5)

    def test_pressure_sample_uses_ratiometric_signal(self):
        sample = pressure_sample(CONFIG["channels"][0], 0.1, 5.0)
        self.assertEqual(sample["status"], "ok")
        self.assertEqual(sample["value"], 0.0)
        self.assertEqual(sample["signal_v"], 0.5)

    def test_disconnected_pressure_signal_is_not_invented(self):
        sample = pressure_sample(CONFIG["channels"][0], 0.999, 5.0)
        self.assertEqual(sample["status"], "signal_high")
        self.assertIsNone(sample["value"])

    def test_ntc_divider_at_equal_resistance(self):
        self.assertAlmostEqual(ntc_resistance_from_ratio(0.5, 10000), 10000.0)

    def test_current_blue_wire_placeholder_is_detected(self):
        sample = ntc_sample(CONFIG["channels"][1], 0.995, 5.0)
        self.assertEqual(sample["status"], "reference_resistor_missing_or_probe_open")
        self.assertIsNone(sample["value"])
        self.assertIsNone(sample["resistance_ohm"])

    def test_ntc_resistance_is_exposed_without_inventing_temperature_curve(self):
        sample = ntc_sample(CONFIG["channels"][1], 0.5, 5.0)
        self.assertEqual(sample["status"], "curve_required")
        self.assertEqual(sample["resistance_ohm"], 10000.0)
        self.assertIsNone(sample["value"])

    def test_payload_contains_four_safe_domains(self):
        payload = build_payload(CONFIG, {0: 0.1, 2: 0.995}, chip_id=1, now="2026-08-02T00:00:00+00:00")
        self.assertEqual(payload["schema_version"], "1.1")
        self.assertEqual(payload["hardware"]["status"], "online")
        self.assertEqual(payload["measurements"]["pressure_1_bar"], 0.0)
        self.assertNotIn("temperature_1_c", payload["measurements"])
        self.assertEqual(payload["states"]["temperature_1_status"], "reference_resistor_missing_or_probe_open")

    def test_failure_payload_never_fabricates_measurements(self):
        payload = failure_payload("SPI missing", now="2026-08-02T00:00:00+00:00")
        self.assertEqual(payload["hardware"]["status"], "offline")
        self.assertEqual(payload["measurements"], {})
        self.assertEqual(payload["alerts"][0]["code"], "ADC_UNAVAILABLE")

    def test_atomic_writer_leaves_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            atomic_write_json(path, {"measurements": {"pressure_1_bar": 0.0}})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["measurements"]["pressure_1_bar"], 0.0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_config_validation_rejects_duplicate_ain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sensors.json"
            path.write_text(
                json.dumps({"channels": [{"id": "a", "ain": 0}, {"id": "b", "ain": 0}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
