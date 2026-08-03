import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_PATH = ROOT / "src/deploy/raspi/etr_sd_factory_preparation_recovery.py"
WORKER = (ROOT / "src/deploy/raspi/etr_sd_factory_worker.py").read_text(encoding="utf-8")
STATE_PATH = ROOT / "src/deploy/raspi/etr_sd_factory_state.py"
RECOVERY = RECOVERY_PATH.read_text(encoding="utf-8")

spec = importlib.util.spec_from_file_location("etr_sd_factory_state_precopy_test", STATE_PATH)
assert spec is not None and spec.loader is not None
state_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_module)


class SdFactoryPreparationRecoveryTests(unittest.TestCase):
    def test_recovery_module_is_versioned_and_bounded(self):
        self.assertTrue(RECOVERY_PATH.is_file())
        for marker in [
            "class PhysicalTargetIdentity",
            "inspect_physical_target",
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

    def test_worker_only_restarts_during_pre_copy_stages(self):
        for marker in [
            "MAX_PRECOPY_USB_RECOVERIES",
            "PRECOPY_STATUSES",
            "kernel_indicates_transport_loss",
            "recover_physical_target",
            "configure_conservative_transport",
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
