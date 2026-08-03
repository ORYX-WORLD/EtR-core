import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_MODULE = ROOT / "src/deploy/raspi/etr_sd_factory_state.py"

spec = importlib.util.spec_from_file_location("etr_sd_factory_state_test", STATE_MODULE)
assert spec is not None and spec.loader is not None
state_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_module)


class SdFactoryResumeStateTests(unittest.TestCase):
    def test_pause_state_preserves_progress_and_exposes_attempt(self):
        value = state_module.progress_from_message(
            "Pause USB : communication perdue — tentative 1/2, attente du retour du lecteur pendant 90 s…"
        )
        self.assertEqual(value["status"], "paused_usb")
        self.assertTrue(value["_preserve_progress"])
        self.assertEqual(value["usb_recovery_attempt"], 1)
        self.assertEqual(value["usb_recovery_max"], 2)

    def test_waiting_pause_exposes_remaining_time(self):
        value = state_module.progress_from_message(
            "Pause USB : attente du même support — tentative 1/2, reste 47 s…"
        )
        self.assertEqual(value["status"], "paused_usb")
        self.assertEqual(value["eta"], "47 s")

    def test_filesystem_check_and_resume_are_running_states(self):
        check = state_module.progress_from_message(
            "Contrôle du système de fichiers après reconnexion USB — tentative 1/2…"
        )
        resume = state_module.progress_from_message(
            "Reprise de la copie après reconnexion USB — tentative 1/2…"
        )
        self.assertEqual(check["status"], "checking_filesystem")
        self.assertEqual(resume["status"], "resuming_copy")
        self.assertEqual(resume["resume_count"], 1)
        self.assertIn("paused_usb", state_module.RUNNING_STATUSES)
        self.assertIn("checking_filesystem", state_module.RUNNING_STATUSES)
        self.assertIn("resuming_copy", state_module.RUNNING_STATUSES)


if __name__ == "__main__":
    unittest.main()
