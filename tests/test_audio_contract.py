import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src/app.py").read_text(encoding="utf-8")
MONITOR = (ROOT / "src/audio_monitor.py").read_text(encoding="utf-8")
JBL_SCRIPT = (ROOT / "src/deploy/raspi/etr-jbl-connect.sh").read_text(encoding="utf-8")
JBL_SERVICE = (ROOT / "src/deploy/raspi/etr-jbl-connect.service").read_text(encoding="utf-8")
MIC_SERVICE = (ROOT / "src/deploy/raspi/etr-audio-monitor.service").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/etr-audio-setup.yml").read_text(encoding="utf-8")


class AudioContractTests(unittest.TestCase):
    def test_jbl_identity_and_autoconnect_are_versioned(self):
        self.assertIn("40:C1:F6:70:C0:1A", JBL_SCRIPT)
        self.assertIn("JBL Go 3", JBL_SCRIPT)
        self.assertIn("bluetoothctl", JBL_SCRIPT)
        self.assertIn("pactl set-default-sink", JBL_SCRIPT)
        self.assertIn("Restart=always", JBL_SERVICE)

    def test_microphone_is_captured_without_permanent_audio_retention(self):
        self.assertIn("/usr/bin/parec", MONITOR)
        self.assertIn("/run/etr-core/microphone-latest.wav", MONITOR)
        self.assertIn("ROLLING_SECONDS = 5", MONITOR)
        self.assertIn("rms_dbfs", MONITOR)
        self.assertIn("speech_active", MONITOR)
        self.assertIn("User=oryx", MIC_SERVICE)
        self.assertIn("ProtectSystem=strict", MIC_SERVICE)

    def test_api_exposes_audio_status_and_local_sample(self):
        self.assertIn('"audio_input": True', APP)
        self.assertIn('"bluetooth_audio": True', APP)
        self.assertIn('@app.get("/api/v1/audio")', APP)
        self.assertIn('@app.get("/api/v1/audio/sample")', APP)
        self.assertIn("send_file(sample", APP)

    def test_physical_workflow_installs_and_checks_audio(self):
        for marker in [
            "libspa-0.2-bluetooth",
            "pipewire-pulse",
            "wireplumber",
            "etr-jbl-connect.service",
            "etr-audio-monitor.service",
            "pactl list short sources",
            "api/v1/audio",
            "etr-audio-last.json",
        ]:
            self.assertIn(marker, WORKFLOW)


if __name__ == "__main__":
    unittest.main()
