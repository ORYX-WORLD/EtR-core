import unittest

from src.deploy.raspi.etr_sd_factory_state import (
    initial_state,
    progress_from_message,
    terminal_state,
)


class SdFactoryStateTests(unittest.TestCase):
    def test_root_copy_progress_is_mapped_to_global_progress(self):
        update = progress_from_message(
            "Copie système EtR : 40 % — 8.4MB/s — reste 00:03:12"
        )
        self.assertEqual(update["status"], "copying_root")
        self.assertEqual(update["speed"], "8.4MB/s")
        self.assertEqual(update["eta"], "00:03:12")
        self.assertGreater(update["progress_percent"], 40)
        self.assertLess(update["progress_percent"], 82)

    def test_boot_copy_progress_stays_in_final_copy_range(self):
        update = progress_from_message(
            "Copie démarrage : 50 % — 4.1MB/s — reste 00:00:20"
        )
        self.assertEqual(update["status"], "copying_boot")
        self.assertGreaterEqual(update["progress_percent"], 82)
        self.assertLessEqual(update["progress_percent"], 88)

    def test_ready_state_is_terminal_and_explicit(self):
        state = initial_state(
            job_id="job-1",
            device="/dev/sda",
            disk_label="Carte 32 Go",
            copy_wifi=True,
        )
        ready = terminal_state(
            state,
            status="ready",
            message="Carte vérifiée et démontée",
        )
        self.assertFalse(ready["active"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["progress_percent"], 100)
        self.assertIsNotNone(ready["finished_at"])


if __name__ == "__main__":
    unittest.main()
