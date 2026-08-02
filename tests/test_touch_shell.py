import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class TouchShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_dir = tempfile.TemporaryDirectory()
        os.environ.setdefault("ETR_STATE_DIR", cls.state_dir.name)
        os.environ.setdefault("ETR_WIFI_SETUP_PIN", "123456")
        sys.path.insert(0, str(ROOT / "src"))
        cls.shell = importlib.import_module("touch_shell")
        cls.client = cls.shell.APP.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.state_dir.cleanup()

    def test_portal_contains_touch_button_with_explicit_label(self):
        response = self.client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="etrLinuxDesktop"', response.data)
        self.assertIn("Bureau Linux".encode(), response.data)
        self.assertIn(b"color:#fff", response.data)

    def test_remote_client_cannot_stop_the_local_kiosk(self):
        with mock.patch.object(self.shell.threading, "Thread") as thread:
            response = self.client.post(
                "/api/local-ui/desktop",
                environ_base={"REMOTE_ADDR": "192.0.2.25"},
            )
        self.assertEqual(response.status_code, 403)
        thread.assert_not_called()

    def test_local_button_schedules_kiosk_stop_after_response(self):
        with mock.patch.object(self.shell.threading, "Thread") as thread:
            response = self.client.post(
                "/api/local-ui/desktop",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["mode"], "linux-desktop")
        thread.assert_called_once_with(target=self.shell.stop_kiosk_after_response, daemon=True)
        thread.return_value.start.assert_called_once_with()

    def test_desktop_shortcut_can_restart_the_kiosk_locally(self):
        with mock.patch.object(self.shell, "control_kiosk") as control:
            response = self.client.post(
                "/api/local-ui/dashboard",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["mode"], "etr-dashboard")
        control.assert_called_once_with("start")

    def test_kiosk_control_uses_a_fixed_systemctl_command_without_shell(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(self.shell.subprocess, "run", return_value=completed) as run:
            self.shell.control_kiosk("stop")
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "stop", "etr-kiosk.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_kiosk_control_rejects_any_other_action(self):
        with self.assertRaises(ValueError):
            self.shell.control_kiosk("restart-now")


class TouchDesktopRepositoryContractTests(unittest.TestCase):
    def test_touch_desktop_delivery_contract_is_complete(self):
        required = [
            "src/touch_shell.py",
            "src/deploy/raspi/etr-show-dashboard.sh",
            "src/deploy/raspi/etr-dashboard.desktop",
            "src/deploy/raspi/etr-wifi-portal.service",
            "src/deploy/raspi/start_spi_desktop.sh",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Touch desktop contract incomplete: {missing}")

        service = (ROOT / "src/deploy/raspi/etr-wifi-portal.service").read_text(encoding="utf-8")
        desktop_start = (ROOT / "src/deploy/raspi/start_spi_desktop.sh").read_text(encoding="utf-8")
        launcher = (ROOT / "src/deploy/raspi/etr-dashboard.desktop").read_text(encoding="utf-8")
        reopen = (ROOT / "src/deploy/raspi/etr-show-dashboard.sh").read_text(encoding="utf-8")
        shell = (ROOT / "src/touch_shell.py").read_text(encoding="utf-8")

        self.assertIn("src/touch_shell.py", service)
        self.assertIn("User=root", service)
        self.assertIn("Revenir-a-EtR.desktop", desktop_start)
        self.assertIn("Name=Revenir à EtR", launcher)
        self.assertIn("/api/local-ui/dashboard", reopen)
        for marker in [
            "/api/local-ui/desktop",
            "/api/local-ui/dashboard",
            "wifi_portal.is_loopback()",
            '[SYSTEMCTL, action, KIOSK_SERVICE]',
            'id="etrLinuxDesktop"',
        ]:
            self.assertIn(marker, shell)
        self.assertNotIn("shell=True", shell)


if __name__ == "__main__":
    unittest.main()
