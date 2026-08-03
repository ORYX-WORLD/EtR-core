import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START_DISPLAY = (ROOT / "src/deploy/raspi/start_spi_desktop.sh").read_text(encoding="utf-8")
DISPLAY_SERVICE = (ROOT / "src/deploy/raspi/spi-desktop.service").read_text(encoding="utf-8")
VNC_SERVICE = (ROOT / "src/deploy/raspi/etr-vnc.service").read_text(encoding="utf-8")
FIRST_BOOT = (ROOT / "src/deploy/raspi/etr_factory_firstboot.py").read_text(encoding="utf-8")
ENROLLMENT_ROUTES = (ROOT / "gateway/enrollment-http.mjs").read_text(encoding="utf-8")


class HeadlessDisplayContractTests(unittest.TestCase):
    def test_display_service_does_not_require_a_physical_framebuffer(self):
        self.assertNotIn("ConditionPathExists=/dev/fb1", DISPLAY_SERVICE)
        self.assertIn("SPI or headless virtual screen", DISPLAY_SERVICE)

    def test_display_runtime_uses_xvfb_without_a_physical_screen(self):
        self.assertIn("/dev/fb1", START_DISPLAY)
        self.assertIn("/usr/bin/Xvfb", START_DISPLAY)
        self.assertIn("1280x720x24", START_DISPLAY)
        self.assertIn('DISPLAY_MODE="headless-xvfb"', START_DISPLAY)
        self.assertIn("display-mode.json", START_DISPLAY)

    def test_vnc_waits_for_the_virtual_x_display(self):
        self.assertIn("Requires=spi-desktop.service", VNC_SERVICE)
        self.assertIn("/tmp/.X11-unix/X1", VNC_SERVICE)
        self.assertIn("-localhost", VNC_SERVICE)
        self.assertIn("-rfbport 5901", VNC_SERVICE)

    def test_first_boot_provisions_headless_runtime_and_remote_screen(self):
        self.assertIn("ensure_headless_display_runtime", FIRST_BOOT)
        self.assertIn('"xvfb"', FIRST_BOOT)
        for unit in (
            "spi-desktop.service",
            "etr-kiosk.service",
            "etr-vnc.service",
            "etr-remote-screen.service",
        ):
            self.assertIn(unit, FIRST_BOOT)

    def test_factory_bootstrap_registers_the_new_installation_for_admin(self):
        for marker in (
            "deviceAccess/${session.uid}",
            "installations/${bootstrap.installationId}/metadata/installation_id",
            "installations/${bootstrap.installationId}/metadata/device_uid",
            'provisioning_mode`]: "factory-ticket"',
        ):
            self.assertIn(marker, ENROLLMENT_ROUTES)


if __name__ == "__main__":
    unittest.main()
