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
            "src/deploy/raspi/etr_sd_factory_fast.py",
            "src/deploy/raspi/etr_sd_factory_state.py",
            "src/deploy/raspi/etr_sd_factory_worker.py",
            "src/deploy/raspi/etr_factory_firstboot.py",
            "src/deploy/raspi/etr-sd-factory.service",
            "src/deploy/raspi/etr-sd-factory-worker.service",
            "src/deploy/raspi/etr-sd-factory-cleanup.sh",
            "src/deploy/raspi/etr-factory-firstboot.service",
            "src/deploy/raspi/etr-sd-factory-launch.sh",
            "src/deploy/raspi/etr-sd-factory.desktop",
            "src/deploy/raspi/etr-sd-factory.sudoers",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [])

    def test_ui_is_decoupled_from_destructive_worker(self):
        interface = (ROOT / "src/deploy/raspi/etr_sd_factory.py").read_text(encoding="utf-8")
        worker = (ROOT / "src/deploy/raspi/etr_sd_factory_worker.py").read_text(encoding="utf-8")
        launcher = (ROOT / "src/deploy/raspi/etr-sd-factory-launch.sh").read_text(encoding="utf-8")
        self.assertNotIn("prepare_card(", interface)
        self.assertIn("etr-sd-factory-worker.service", interface)
        self.assertIn("prepare_card(", worker)
        self.assertIn("etr-sd-factory-worker.service", launcher)
        self.assertIn("La fabrication continue même si cette fenêtre est fermée", interface)

    def test_copy_excludes_session_mounts_and_handles_partial_rsync(self):
        copy_engine = (ROOT / "src/deploy/raspi/etr_sd_factory_fast.py").read_text(encoding="utf-8")
        for marker in [
            '"/home/oryx/.gvfs"',
            '"/home/oryx/.gvfs/***"',
            '"/home/oryx/.local/share/gvfs-metadata/***"',
            '"/usr/share/doc/***"',
            '"/usr/share/man/***"',
            '"/usr/share/info/***"',
            '"/var/lib/apt/lists/***"',
            "RSYNC_LOG",
            "RETRYABLE_CODES = {23, 24}",
            "MAX_RSYNC_ATTEMPTS = 3",
            "Synchronisation finale : reprise automatique",
            "sd-factory-rsync.log",
        ]:
            self.assertIn(marker, copy_engine)
        self.assertIn("--delete-delay", copy_engine)
        self.assertIn("_concise_rsync_error", copy_engine)

    def test_conservative_reader_profile_limits_and_monitors_writes(self):
        copy_engine = (ROOT / "src/deploy/raspi/etr_sd_factory_fast.py").read_text(encoding="utf-8")
        for marker in [
            'ETR_SD_RSYNC_BWLIMIT_KB", "2048"',
            "--bwlimit=",
            "_target_disk",
            "_monitor_target",
            "blockdev",
            "Le lecteur USB ou la microSD a disparu pendant l'écriture",
            "Copie prudente activée",
        ]:
            self.assertIn(marker, copy_engine)

    def test_repository_snapshot_uses_archive_without_identity_switch(self):
        copy_engine = (ROOT / "src/deploy/raspi/etr_sd_factory_fast.py").read_text(encoding="utf-8")
        for marker in [
            "_git_from_repository",
            "safe.directory=",
            '"archive", "--format=tar"',
            '"--no-same-owner"',
            '".etr-source-revision"',
            "sans setuid, clone local ni accès réseau",
        ]:
            self.assertIn(marker, copy_engine)
        self.assertNotIn("runuser", copy_engine.lower())
        self.assertNotIn("setuid(", copy_engine)
        self.assertNotIn("git config --global", copy_engine)
        self.assertNotIn('"clone", "--local"', copy_engine)

    def test_ready_state_requires_confirmed_unmount(self):
        worker = (ROOT / "src/deploy/raspi/etr_sd_factory_worker.py").read_text(encoding="utf-8")
        for marker in [
            "_remaining_mounts",
            "core.unmount_target(device)",
            '"ready_to_remove": False',
            '"ready_to_remove": True',
            '"verification": "passed"',
            "Carte EtR vérifiée, synchronisée et démontée",
        ]:
            self.assertIn(marker, worker)

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
