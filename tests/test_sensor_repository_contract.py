import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SensorRepositoryContractTests(unittest.TestCase):
    def test_ads1263_delivery_contract_is_complete(self):
        required = [
            "src/ads1263.py",
            "src/sensor_acquisition.py",
            "config/sensors-home-lab.json",
            "src/deploy/raspi/etr-sensor-acquisition.service",
            "src/deploy/raspi/install_sensor_acquisition.sh",
            ".github/workflows/etr-sensor-lab.yml",
            "tests/test_ads1263.py",
            "tests/test_sensor_acquisition.py",
            "docs/TELEMETRY_CONTRACT.md",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"ADS1263 contract incomplete: {missing}")

    def test_hardware_install_is_non_privileged_and_persistent(self):
        unit = (ROOT / "src/deploy/raspi/etr-sensor-acquisition.service").read_text(encoding="utf-8")
        installer = (ROOT / "src/deploy/raspi/install_sensor_acquisition.sh").read_text(encoding="utf-8")
        for marker in [
            "User=oryx",
            "Group=oryx",
            "SupplementaryGroups=spi gpio",
            "ReadWritePaths=/var/lib/etr-core",
            "NoNewPrivileges=true",
            "/usr/bin/python3",
        ]:
            self.assertIn(marker, unit)
        for marker in [
            "python3-lgpio",
            "python3-spidev",
            "raspi-config nonint do_spi 0",
            "dtparam=spi=on",
            "/dev/spidev0.0 /dev/spidev0.1",
            "SPI_REBOOT_REQUIRED",
            "SPI_UNAVAILABLE_AFTER_REBOOT",
            "sensors-home-lab.json",
            "etr-sensor-acquisition.service",
        ]:
            self.assertIn(marker, installer)
        self.assertNotIn("User=root", unit)

    def test_physical_workflow_requires_real_adc_and_pressure_signals(self):
        workflow = (ROOT / ".github/workflows/etr-sensor-lab.yml").read_text(encoding="utf-8")
        for marker in [
            "runs-on: [self-hosted, Linux, ARM64]",
            "spidev0.*",
            "/dev/gpiochip0",
            "hardware.get('chip_id') == 1",
            "'pressure_1','pressure_2','temperature_1','temperature_2'",
            "sample.get('status') == 'ok'",
            "bootConfigSpiLines",
            "spiKernelDevices",
            "etr-sensors-last.json",
        ]:
            self.assertIn(marker, workflow)

    def test_dashboard_exposes_the_sensor_bench_without_unsafe_html(self):
        template = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "dashboard/static/dashboard.js").read_text(encoding="utf-8")
        for marker in ["data-sensor-grid", "Banc d’essai capteurs", "AIN0", "AIN1", "AIN2", "AIN3", "10 kΩ"]:
            self.assertIn(marker, template)
        for marker in ["renderSensors", "ADS1263 détecté", "Résistance 10 kΩ absente", "textContent"]:
            self.assertIn(marker, javascript)
        self.assertNotIn("innerHTML", javascript)


if __name__ == "__main__":
    unittest.main()
