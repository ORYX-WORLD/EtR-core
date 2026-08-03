import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_PATH = ROOT / "src/deploy/raspi/etr_sd_factory_preparation_recovery.py"
WORKER = (ROOT / "src/deploy/raspi/etr_sd_factory_worker.py").read_text(encoding="utf-8")
STATE_PATH = ROOT / "src/deploy/raspi/etr_sd_factory_state.py"
RECOVERY = RECOVERY_PATH.read_text(encoding="utf-8")

state_spec = importlib.util.spec_from_file_location("etr_sd_factory_state_precopy_test", STATE_PATH)
assert state_spec is not None and state_spec.loader is not None
state_module = importlib.util.module_from_spec(state_spec)
state_spec.loader.exec_module(state_module)

recovery_spec = importlib.util.spec_from_file_location(
    "etr_sd_factory_preparation_recovery_test",
    RECOVERY_PATH,
)
assert recovery_spec is not None and recovery_spec.loader is not None
recovery_module = importlib.util.module_from_spec(recovery_spec)
sys.modules[recovery_spec.name] = recovery_module
recovery_spec.loader.exec_module(recovery_module)


class SdFactoryPreparationRecoveryTests(unittest.TestCase):
    def test_recovery_module_is_versioned_and_bounded(self):
        self.assertTrue(RECOVERY_PATH.is_file())
        for marker in [
            "class PhysicalTargetIdentity",
            "inspect_physical_target",
            "target_capacity_matches",
            "configure_conservative_transport",
            "kernel_indicates_transport_loss",
            "recover_physical_target",
            'device/timeout", "15',
            'queue/max_sectors_kb", "64',
            "Pause USB avant copie",
            "Reprise de la préparation depuis l'effacement",
            "candidate.usb_node != identity.usb_node",
        ]:
            self.assertIn(marker, RECOVERY)

    def test_usb_node_is_extracted_from_realistic_udev_devpath(self):
        devpath = (
            "/devices/platform/scb/fd500000.pcie/pci0000:00/0000:00:00.0/"
            "0000:01:00.0/usb1/1-1/1-1.1/1-1.1:1.0/host0/target0:0:0/"
            "0:0:0:0/block/sda"
        )
        self.assertEqual(
            recovery_module._usb_node_name_from_devpath(devpath),
            "1-1.1",
        )
        self.assertEqual(
            recovery_module._usb_node_name_from_devpath(
                "/devices/platform/x/usb2/2-1/2-1:1.0/host1/block/sdb"
            ),
            "2-1",
        )
        self.assertEqual(recovery_module._usb_node_name_from_devpath(""), "")

    def test_udev_fallback_is_present_for_scsi_symlink_layouts(self):
        for marker in [
            '"/usr/bin/udevadm"',
            '"--query=path"',
            '"--name"',
            "_usb_node_name_from_devpath",
            'Path("/sys/bus/usb/devices") / node_name',
            "Le lecteur USB cible n'est pas identifiable",
            "devpath=",
        ]:
            self.assertIn(marker, RECOVERY)

    def test_expected_size_preserves_identity_when_live_capacity_is_zero(self):
        node = Path("/sys/bus/usb/devices/1-1.1")

        def fake_read(path: Path) -> str:
            return {
                "idVendor": "1908",
                "idProduct": "0226",
                "serial": "reader-1",
            }.get(path.name, "")

        with (
            patch.object(recovery_module, "_disk_size", return_value=0),
            patch.object(recovery_module, "_usb_device_node", return_value=node),
            patch.object(recovery_module, "_read", side_effect=fake_read),
            patch.object(recovery_module, "_output", return_value="Mass-Storage"),
        ):
            identity = recovery_module.inspect_physical_target(
                "/dev/sda",
                expected_size=31268536320,
            )
        self.assertEqual(identity.disk_size, 31268536320)
        self.assertEqual(identity.usb_node, "1-1.1")
        self.assertEqual(identity.vendor_id, "1908")
        self.assertEqual(identity.product_id, "0226")

    def test_worker_only_restarts_during_pre_copy_stages(self):
        for marker in [
            "MAX_PRECOPY_USB_RECOVERIES",
            "PRECOPY_STATUSES",
            "kernel_indicates_transport_loss",
            "recover_physical_target",
            "configure_conservative_transport",
            "target_capacity_matches",
            "expected_size=disk.size",
            "capacity_unavailable_before_preparation",
            "precopy_recoveries < MAX_PRECOPY_USB_RECOVERIES",
            "status in PRECOPY_STATUSES",
            "La préparation est volontairement relancée depuis l'effacement",
        ]:
            self.assertIn(marker, WORKER)
        self.assertNotIn("while True:\n            core.prepare_card", WORKER)

    def test_pause_and_restart_states_preserve_progress(self):
        pause = state_module.progress_from_message(
            "Pause USB avant copie : communication perdue — tentative 1/2, récupération du lecteur pendant 90 s…"
        )
        waiting = state_module.progress_from_message(
            "Pause USB avant copie : attente du lecteur sur le même port — tentative 1/2, reste 51 s…"
        )
        restart = state_module.progress_from_message(
            "Reprise de la préparation depuis l'effacement — tentative 1/2…"
        )
        self.assertEqual(pause["status"], "paused_usb_setup")
        self.assertEqual(waiting["eta"], "51 s")
        self.assertEqual(restart["status"], "restarting_preparation")
        self.assertTrue(pause["_preserve_progress"])
        self.assertTrue(restart["_preserve_progress"])
        self.assertEqual(restart["precopy_recovery_attempt"], 1)
        self.assertEqual(restart["precopy_recovery_max"], 2)
        self.assertIn("paused_usb_setup", state_module.RUNNING_STATUSES)
        self.assertIn("restarting_preparation", state_module.RUNNING_STATUSES)


if __name__ == "__main__":
    unittest.main()
