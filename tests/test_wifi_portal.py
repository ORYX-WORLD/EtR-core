import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock


class WifiPortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_dir = tempfile.TemporaryDirectory()
        os.environ["ETR_STATE_DIR"] = cls.state_dir.name
        os.environ["ETR_WIFI_SETUP_PIN"] = "123456"
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        cls.portal = importlib.import_module("wifi_portal")

    @classmethod
    def tearDownClass(cls):
        cls.state_dir.cleanup()

    def setUp(self):
        self.portal.COMMISSIONED_FILE.unlink(missing_ok=True)

    def test_key_management_mapping(self):
        self.assertEqual(self.portal.wifi_key_management("WPA2"), "wpa-psk")
        self.assertEqual(self.portal.wifi_key_management("WPA2 WPA3 SAE"), "wpa-psk")
        self.assertEqual(self.portal.wifi_key_management("WPA3 SAE"), "sae")
        self.assertEqual(self.portal.wifi_key_management("Ouvert"), "")

    def test_wpa2_profile_sets_key_management_and_psk(self):
        with mock.patch.object(self.portal, "run_nmcli") as run:
            self.portal.configure_wifi_connection("Le Vert & Bois", "secret123", "WPA2")
        calls = [call.args for call in run.call_args_list]
        modify = next(args for args in calls if args[:2] == ("connection", "modify"))
        self.assertIn("802-11-wireless-security.key-mgmt", modify)
        self.assertIn("wpa-psk", modify)
        self.assertIn("802-11-wireless-security.psk", modify)
        self.assertIn("secret123", modify)

    def test_open_profile_omits_security_properties(self):
        with mock.patch.object(self.portal, "run_nmcli") as run:
            self.portal.configure_wifi_connection("Ouvert", "", "Ouvert")
        modify = next(call.args for call in run.call_args_list if call.args[:2] == ("connection", "modify"))
        self.assertNotIn("802-11-wireless-security.key-mgmt", modify)

    def test_failed_activation_deletes_only_target_profile(self):
        def fake_run(*args, **kwargs):
            if args[:2] == ("connection", "up"):
                raise RuntimeError("activation failed")
            return ""
        with mock.patch.object(self.portal, "run_nmcli", side_effect=fake_run) as run:
            with self.assertRaises(RuntimeError):
                self.portal.configure_wifi_connection("Le Vert & Bois", "secret123", "WPA2")
        delete_calls = [call.args for call in run.call_args_list if call.args[:2] == ("connection", "delete")]
        self.assertTrue(delete_calls)
        self.assertTrue(all("Le Vert & Bois" in args for args in delete_calls))
        self.assertTrue(all("EtR-manual-test" not in args for args in delete_calls))

    def test_wait_ignores_fallback_then_accepts_target(self):
        states = [
            {"wifi": "EtR-manual-test", "hotspot": False},
            {"wifi": "Le Vert & Bois", "hotspot": False},
        ]
        with mock.patch.object(self.portal, "active_connection", side_effect=states), \
             mock.patch.object(self.portal.time, "sleep"):
            self.assertTrue(self.portal.wait_for_wifi_connection("Le Vert & Bois", timeout=2))

    def test_enterprise_network_is_rejected_before_configuration(self):
        client = self.portal.APP.test_client()
        with mock.patch.object(self.portal, "configure_wifi_connection") as configure:
            response = client.post("/api/connect", json={
                "ssid": "Entreprise", "password": "secret123", "security": "WPA2 802.1X",
            }, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(response.status_code, 400)
        configure.assert_not_called()

    def test_endpoint_does_not_commission_unconfirmed_target(self):
        client = self.portal.APP.test_client()
        with mock.patch.object(self.portal, "configure_wifi_connection"), \
             mock.patch.object(self.portal, "wait_for_wifi_connection", return_value=False), \
             mock.patch.object(self.portal, "run_nmcli") as run, \
             mock.patch.object(self.portal.threading, "Thread"):
            response = client.post("/api/connect", json={
                "ssid": "Le Vert & Bois", "password": "secret123", "security": "WPA2",
            }, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.portal.COMMISSIONED_FILE.exists())
        run.assert_called_once_with("connection", "delete", "id", "Le Vert & Bois", check=False)


if __name__ == "__main__":
    unittest.main()
