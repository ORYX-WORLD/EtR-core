import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "src/deploy/raspi/etr_sd_factory_core.py"
SPEC = importlib.util.spec_from_file_location("etr_sd_factory_core", MODULE_PATH)
factory = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["etr_sd_factory_core"] = factory
SPEC.loader.exec_module(factory)


class SdFactoryTests(unittest.TestCase):
    def test_candidates_only_keep_external_removable_disks(self):
        devices = factory.flatten_lsblk(
            [
                {
                    "name": "mmcblk0",
                    "path": "/dev/mmcblk0",
                    "type": "disk",
                    "size": 32 * 1024**3,
                    "rm": 0,
                    "tran": "mmc",
                    "children": [
                        {
                            "name": "mmcblk0p2",
                            "path": "/dev/mmcblk0p2",
                            "type": "part",
                            "size": 31 * 1024**3,
                        }
                    ],
                },
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "type": "disk",
                    "size": 64 * 1024**3,
                    "rm": 1,
                    "tran": "usb",
                    "model": "USB SD Reader",
                },
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "type": "disk",
                    "size": 4 * 1024**3,
                    "rm": 1,
                    "tran": "usb",
                },
            ]
        )
        self.assertEqual(factory.parent_disk_for("/dev/mmcblk0p2", devices), "/dev/mmcblk0")
        candidates = factory.candidate_disks(devices, "/dev/mmcblk0")
        self.assertEqual([disk.path for disk in candidates], ["/dev/sda"])

    def test_partition_path_handles_sd_and_mmc_names(self):
        self.assertEqual(factory.partition_path("/dev/sda", 2), "/dev/sda2")
        self.assertEqual(factory.partition_path("/dev/mmcblk1", 2), "/dev/mmcblk1p2")
        self.assertEqual(factory.partition_path("/dev/nvme0n1", 2), "/dev/nvme0n1p2")

    def test_fstab_and_cmdline_use_target_identifiers(self):
        fstab = factory.build_fstab("ROOT-UUID", "BOOT-UUID", "/boot/firmware")
        self.assertIn("UUID=ROOT-UUID / ext4", fstab)
        self.assertIn("UUID=BOOT-UUID /boot/firmware vfat", fstab)
        cmdline = factory.replace_cmdline_root(
            "console=serial0,115200 root=PARTUUID=OLD-02 rootwait quiet\n", "NEW-02"
        )
        self.assertIn("root=PARTUUID=NEW-02", cmdline)
        self.assertNotIn("OLD-02", cmdline)
        self.assertEqual(cmdline.count("root="), 1)

    def test_wifi_profile_drops_hardware_binding_but_keeps_credentials(self):
        original = "[connection]\nid=Site\ninterface-name=wlan0\n[wifi]\nmac-address=AA:BB:CC:DD:EE:FF\ncloned-mac-address=stable\nssid=Site\n[wifi-security]\npsk=secret\n"
        cleaned = factory.sanitize_wifi_keyfile(original)
        self.assertNotIn("interface-name=", cleaned)
        self.assertNotIn("mac-address=", cleaned)
        self.assertNotIn("cloned-mac-address=", cleaned)
        self.assertIn("ssid=Site", cleaned)
        self.assertIn("psk=secret", cleaned)

    def test_target_environment_drops_source_identity_credentials(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "etc/etr-core/firebase-bridge.env"
            env_path.parent.mkdir(parents=True)
            env_path.write_text(
                "FIREBASE_API_KEY=public-key\n"
                "FIREBASE_DATABASE_URL=https://db.example\n"
                "ETR_REMOTE_GATEWAY_WSS=wss://gateway.example/device\n"
                "ETR_INSTALLATION_ID=etr-source\n"
                "ETR_DEVICE_SERIAL=SOURCE1234\n"
                "FIREBASE_AUTH_EMAIL=legacy@example.test\n"
                "FIREBASE_AUTH_PASSWORD=secret\n",
                encoding="utf-8",
            )
            factory.sanitize_target_environment(root)
            cleaned = env_path.read_text(encoding="utf-8")
            self.assertIn("FIREBASE_API_KEY=public-key", cleaned)
            self.assertIn("ETR_REMOTE_GATEWAY_WSS=", cleaned)
            self.assertNotIn("ETR_INSTALLATION_ID", cleaned)
            self.assertNotIn("ETR_DEVICE_SERIAL", cleaned)
            self.assertNotIn("FIREBASE_AUTH_EMAIL", cleaned)
            self.assertNotIn("FIREBASE_AUTH_PASSWORD", cleaned)


if __name__ == "__main__":
    unittest.main()
