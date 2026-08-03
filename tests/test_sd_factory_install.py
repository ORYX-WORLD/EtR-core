import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = (ROOT / "src/deploy/raspi/setup_etr.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "src/deploy/raspi/start_spi_desktop.sh").read_text(encoding="utf-8")


class SdFactoryInstallContractTests(unittest.TestCase):
    def test_installer_contains_factory_runtime_and_dependencies(self):
        for marker in [
            "python3-tk",
            "rsync",
            "dosfstools",
            "parted",
            "fdisk",
            "e2fsprogs",
            "etr-sd-factory.service",
            "etr-factory-firstboot.service",
            "etr-sd-factory-launch.sh",
            "etr-sd-factory.desktop",
            "etr-sd-factory.sudoers",
            "/usr/sbin/visudo -cf",
        ]:
            self.assertIn(marker, SETUP)

    def test_factory_is_only_exposed_on_linux_desktop(self):
        self.assertIn("Creer-une-carte-EtR.desktop", DESKTOP)
        self.assertIn("etr-sd-factory.desktop", DESKTOP)
        touch_shell = (ROOT / "src/touch_shell.py").read_text(encoding="utf-8") if (ROOT / "src/touch_shell.py").exists() else ""
        self.assertNotIn("sd-factory", touch_shell)
        self.assertNotIn("Créer une carte EtR", touch_shell)

    def test_factory_files_are_versioned(self):
        required = [
            "src/deploy/raspi/etr_sd_factory.py",
            "src/deploy/raspi/etr_sd_factory_core.py",
            "src/deploy/raspi/etr_factory_firstboot.py",
            "src/deploy/raspi/etr-sd-factory.service",
            "src/deploy/raspi/etr-factory-firstboot.service",
            "src/deploy/raspi/etr-sd-factory-launch.sh",
            "src/deploy/raspi/etr-sd-factory.desktop",
            "src/deploy/raspi/etr-sd-factory.sudoers",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [])

    def test_gateway_exposes_one_time_factory_provisioning_contract(self):
        bootstrap = (ROOT / "gateway/device-bootstrap.mjs").read_text(encoding="utf-8")
        routes = (ROOT / "gateway/enrollment-http.mjs").read_text(encoding="utf-8")
        for marker in [
            "FACTORY_PROVISIONING_POLICY",
            "ticketEntropyBits: 256",
            "factoryBootstrapTickets/",
            "issueFactoryTicket",
            "redeemFactoryTicket",
            "defaultFactoryInstallation: DEFAULT_FACTORY_INSTALLATION",
        ]:
            self.assertIn(marker, bootstrap)
        for marker in [
            "/api/enrollment/factory-ticket",
            "/api/enrollment/factory-bootstrap",
            "verifyIdToken(bearer(req))",
        ]:
            self.assertIn(marker, routes)


if __name__ == "__main__":
    unittest.main()
