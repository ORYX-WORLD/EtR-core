import unittest
from pathlib import Path

from src import sensor_acquisition as acquisition
from src.sensor_acquisition_runtime import SoftwareResetADS1263


ROOT = Path(__file__).resolve().parents[1]


class SensorAcquisitionRuntimeTests(unittest.TestCase):
    def test_runtime_adc_uses_kernel_cs_and_software_reset(self):
        adc = SoftwareResetADS1263(
            manual_chip_select=True,
            use_data_ready_gpio=True,
            use_hardware_reset_gpio=True,
        )
        self.assertFalse(adc.manual_chip_select)
        self.assertFalse(adc.use_data_ready_gpio)
        self.assertFalse(adc.use_hardware_reset_gpio)

    def test_acquisition_default_factory_is_replaced_by_runtime_adapter(self):
        self.assertIs(acquisition.acquire_once.__defaults__[0], SoftwareResetADS1263)


class SensorRuntimeRepositoryContractTests(unittest.TestCase):
    def test_services_preserve_shared_state_ownership(self):
        sensor_unit = (ROOT / "src/deploy/raspi/etr-sensor-acquisition.service").read_text(encoding="utf-8")
        wifi_unit = (ROOT / "src/deploy/raspi/etr-wifi-portal.service").read_text(encoding="utf-8")
        for marker in [
            "WorkingDirectory=/var/lib/etr-core",
            "ExecStartPre=+/usr/bin/install -d -m 700 -o oryx -g oryx /var/lib/etr-core",
            "ExecStartPre=+/usr/bin/pinctrl set 18 op dh",
            "sensor_acquisition_runtime.py",
            "ProtectHome=read-only",
        ]:
            self.assertIn(marker, sensor_unit)
        self.assertIn("ExecStartPre=/usr/bin/install -d -m 700 -o oryx -g oryx /var/lib/etr-core", wifi_unit)
        self.assertNotIn("StateDirectory=etr-core", wifi_unit)

    def test_installer_forces_reset_high_and_software_reset_profile(self):
        installer = (ROOT / "src/deploy/raspi/install_sensor_acquisition.sh").read_text(encoding="utf-8")
        config = (ROOT / "config/sensors-home-lab.json").read_text(encoding="utf-8")
        for marker in [
            "sensor_acquisition_runtime.py",
            "pinctrl set 18 op dh",
            '"use_hardware_reset_gpio": False',
            'chown oryx:oryx "${STATE_DIR}"',
        ]:
            self.assertIn(marker, installer)
        self.assertIn('"use_hardware_reset_gpio": false', config)


if __name__ == "__main__":
    unittest.main()
