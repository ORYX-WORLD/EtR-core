import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProvisioningContractTests(unittest.TestCase):
    def test_factory_provisioning_files_are_versioned(self):
        required = [
            "provisioning/README.md",
            "provisioning/windows/New-EtrMicroSD.ps1",
            "provisioning/raspi/etr-firstboot.sh",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Provisioning contract incomplete: {missing}")

    def test_provisioning_keeps_secrets_out_of_the_image(self):
        powershell = (ROOT / "provisioning/windows/New-EtrMicroSD.ps1").read_text(encoding="utf-8")
        firstboot = (ROOT / "provisioning/raspi/etr-firstboot.sh").read_text(encoding="utf-8")
        self.assertIn("SshPublicKeyPath", powershell)
        self.assertIn("Refus de toucher au disque système", powershell)
        self.assertIn("ETR_INSTALLATION_ID", firstboot)
        self.assertNotIn("Password=", powershell + firstboot)
        self.assertNotIn("ETR_HARDWARE_SERIAL=00000000", firstboot)

    def test_runtime_reads_factory_identity(self):
        unit = (ROOT / "src/deploy/etr.service").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-/etc/etr-core/device.env", unit)


if __name__ == "__main__":
    unittest.main()
