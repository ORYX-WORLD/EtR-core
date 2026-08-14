import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SensorRepositoryContractTests(unittest.TestCase):
    def test_ads1263_delivery_contract_is_complete(self):
        required = [
            "src/ads1263.py",
            "src/ads1263_probe.py",
            "src/sensor_acquisition.py",
            "src/sensor_acquisition_runtime.py",
            "config/sensors-home-lab.json",
            "src/deploy/raspi/etr-sensor-acquisition.service",
            "src/deploy/raspi/etr-ads1263-spi0-cs2-overlay.dts",
            "src/deploy/raspi/install_sensor_acquisition.sh",
            ".github/workflows/etr-sensor-lab.yml",
            ".github/workflows/etr-spi-pin-diagnostic.yml",
            ".github/workflows/etr-ads1263-probe.yml",
            "tests/test_ads1263.py",
            "tests/test_ads1263_probe.py",
            "tests/test_sensor_acquisition.py",
            "tests/test_sensor_acquisition_runtime.py",
            "docs/TELEMETRY_CONTRACT.md",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"ADS1263 contract incomplete: {missing}")

    def test_hardware_install_is_non_privileged_and_persistent(self):
        unit = (ROOT / "src/deploy/raspi/etr-sensor-acquisition.service").read_text(encoding="utf-8")
        installer = (ROOT / "src/deploy/raspi/install_sensor_acquisition.sh").read_text(encoding="utf-8")
        overlay = (ROOT / "src/deploy/raspi/etr-ads1263-spi0-cs2-overlay.dts").read_text(encoding="utf-8")
        for marker in [
            "User=oryx",
            "Group=oryx",
            "SupplementaryGroups=spi gpio",
            "WorkingDirectory=/var/lib/etr-core",
            "ExecStartPre=+/usr/bin/install -d -m 700 -o oryx -g oryx /var/lib/etr-core",
            "ExecStartPre=+/usr/bin/pinctrl set 18 op dh",
            "sensor_acquisition_runtime.py",
            "ReadWritePaths=/var/lib/etr-core",
            "ProtectHome=read-only",
            "NoNewPrivileges=true",
            "/usr/bin/python3",
        ]:
            self.assertIn(marker, unit)
        self.assertNotIn("WorkingDirectory=/home/oryx/EtR-core", unit)
        for marker in [
            "device-tree-compiler",
            "python3-lgpio",
            "python3-spidev",
            "raspi-config nonint do_spi 0",
            "dtparam=spi=on",
            "dtoverlay=${OVERLAY_NAME}",
            "/dev/spidev0.2",
            "SPI_REBOOT_REQUIRED",
            "SPI_CS2_UNAVAILABLE_AFTER_REBOOT",
            '"device": 2',
            '"use_data_ready_gpio": False',
            '"use_hardware_reset_gpio": False',
            "pinctrl set 18 op dh",
            "sensor_acquisition_runtime.py",
            "sensors-home-lab.json",
            "etr-sensor-acquisition.service",
        ]:
            self.assertIn(marker, installer)
        for marker in [
            "num-cs = <3>",
            "<&gpio 22 1>",
            "spidev@2",
            "reg = <2>",
            "spi-max-frequency = <2000000>",
        ]:
            self.assertIn(marker, overlay)
        self.assertNotIn("User=root", unit)

    def test_shared_state_directory_is_not_reassigned_to_root(self):
        wifi_unit = (ROOT / "src/deploy/raspi/etr-wifi-portal.service").read_text(encoding="utf-8")
        self.assertIn(
            "ExecStartPre=/usr/bin/install -d -m 700 -o oryx -g oryx /var/lib/etr-core",
            wifi_unit,
        )
        self.assertNotIn("StateDirectory=etr-core", wifi_unit)

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

    def test_raw_probe_runs_from_a_writable_directory(self):
        workflow = (ROOT / ".github/workflows/etr-ads1263-probe.yml").read_text(encoding="utf-8")
        for marker in [
            "cd /tmp/etr-ads1263-probe",
            "/home/oryx/EtR-core/src/ads1263_probe.py",
            "WorkingDirectory --value",
            '"/var/lib/etr-core"',
        ]:
            self.assertIn(marker, workflow)
        self.assertNotIn("cd /home/oryx/EtR-core\n          sudo python3 src/ads1263_probe.py", workflow)

    def test_driver_preserves_touchscreen_gpio17(self):
        driver = (ROOT / "src/ads1263.py").read_text(encoding="utf-8")
        runtime = (ROOT / "src/sensor_acquisition_runtime.py").read_text(encoding="utf-8")
        config = (ROOT / "config/sensors-home-lab.json").read_text(encoding="utf-8")
        for marker in [
            "device: int = 2",
            "manual_chip_select: bool = False",
            "use_data_ready_gpio: bool = False",
            "use_hardware_reset_gpio: bool = True",
        ]:
            self.assertIn(marker, driver)
        for marker in [
            'kwargs["manual_chip_select"] = False',
            'kwargs["use_data_ready_gpio"] = False',
            'kwargs["use_hardware_reset_gpio"] = False',
        ]:
            self.assertIn(marker, runtime)
        self.assertIn('"device": 2', config)
        self.assertIn('"use_data_ready_gpio": false', config)
        self.assertIn('"use_hardware_reset_gpio": false', config)
        self.assertEqual(config.count('"beta_k": 3950.0'), 2)

    def test_dashboard_exposes_the_sensor_bench_without_unsafe_html(self):
        template = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "dashboard/static/dashboard.js").read_text(encoding="utf-8")
        for marker in ["data-sensor-grid", "Banc d’essai capteurs", "AIN0", "AIN1", "AIN2", "AIN3", "10 kΩ", "Cycle frigorifique schématique", "Compresseur", "Condenseur", "Détendeur", "Évaporateur", "Pression BP", "Pression HP", "T° aspiration compresseur", "T° ligne liquide sortie condenseur"]:
            self.assertIn(marker, template)
        for marker in ["renderSensors", "ADS1263 détecté", "Lecture brute disponible", "V brute", "textContent"]:
            self.assertIn(marker, javascript)
        self.assertNotIn("innerHTML", javascript)


if __name__ == "__main__":
    unittest.main()
