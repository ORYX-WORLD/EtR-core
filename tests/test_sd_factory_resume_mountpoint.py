import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = (ROOT / "src/deploy/raspi/etr_sd_factory_usb_resume.py").read_text(encoding="utf-8")


class SdFactoryResumeMountpointTests(unittest.TestCase):
    def test_recovery_uses_filesystem_mount_root_not_deep_destination(self):
        self.assertIn(
            '["/usr/bin/findmnt", "-n", "-o", "TARGET", "--target", requested_path]',
            RECOVERY,
        )
        self.assertIn("mountpoint = str(Path(mountpoint).resolve())", RECOVERY)
        self.assertIn('["/usr/bin/findmnt", "-rn", "-M", mountpoint]', RECOVERY)
        self.assertIn('["/usr/bin/findmnt", "-rn", "-M", target]', RECOVERY)


if __name__ == "__main__":
    unittest.main()
