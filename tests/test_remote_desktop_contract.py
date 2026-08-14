from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RemoteDesktopContractTests(unittest.TestCase):
    def test_virtual_desktop_is_1280x720(self):
        service = (ROOT / 'src/deploy/raspi/etr-remote-desktop.service').read_text(encoding='utf-8')
        script = (ROOT / 'src/deploy/raspi/start_remote_desktop.sh').read_text(encoding='utf-8')
        self.assertIn('ETR_REMOTE_DESKTOP_GEOMETRY=1280x720x24', service)
        self.assertIn('Xvfb', script)
        self.assertIn('1280x720x24', script)
        self.assertIn('DISPLAY_ID=":2"', script)

    def test_vnc_serves_virtual_desktop_not_spi_framebuffer(self):
        unit = (ROOT / 'src/deploy/raspi/etr-vnc.service').read_text(encoding='utf-8')
        self.assertIn('After=etr-remote-desktop.service', unit)
        self.assertIn('Environment=DISPLAY=:2', unit)
        self.assertIn('-display :2', unit)
        self.assertNotIn('-display :1', unit)

    def test_setup_installs_xvfb_and_remote_desktop(self):
        setup = (ROOT / 'src/deploy/raspi/setup_etr.sh').read_text(encoding='utf-8')
        self.assertIn('xinit xvfb lxde-core', setup)
        self.assertIn('start_remote_desktop.sh', setup)
        self.assertIn('etr-remote-desktop.service', setup)
        self.assertIn("DISPLAY=:2 xdpyinfo", setup)
        self.assertIn("dimensions:.*1280x720", setup)


if __name__ == '__main__':
    unittest.main()
