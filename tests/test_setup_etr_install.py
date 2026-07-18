import re
import unittest
from pathlib import Path

SETUP_SCRIPT = (
    Path(__file__).resolve().parents[1] / "src" / "deploy" / "raspi" / "setup_etr.sh"
).read_text(encoding="utf-8")


def install_calls(script: str) -> list[dict[str, str]]:
    calls = []
    for match in re.finditer(r"^sudo install (?P<args>.+)$", script, re.MULTILINE):
        tokens = match.group("args").split()
        paths = [t for t in tokens if not t.startswith("-") and t not in ("644", "755", "700")]
        mode = next((tokens[i + 1] for i, t in enumerate(tokens) if t == "-m"), None)
        directory = "-d" in tokens
        calls.append({"mode": mode, "directory": directory, "paths": paths})
    return calls


class SetupEtrInstallsBlankingFixTests(unittest.TestCase):
    def setUp(self):
        self.calls = install_calls(SETUP_SCRIPT)

    def test_installs_disable_blanking_script_executable(self):
        matches = [
            c
            for c in self.calls
            if c["paths"]
            == [
                "src/deploy/raspi/etr-disable-blanking.sh",
                "/usr/local/bin/etr-disable-blanking.sh",
            ]
        ]
        self.assertTrue(matches, "setup_etr.sh must install etr-disable-blanking.sh")
        self.assertEqual(matches[0]["mode"], "755")

    def test_creates_spi_desktop_dropin_directory(self):
        matches = [
            c
            for c in self.calls
            if c["directory"] and c["paths"] == ["/etc/systemd/system/spi-desktop.service.d"]
        ]
        self.assertTrue(
            matches, "setup_etr.sh must create /etc/systemd/system/spi-desktop.service.d"
        )

    def test_installs_blanking_dropin_with_correct_mode(self):
        matches = [
            c
            for c in self.calls
            if c["paths"]
            == [
                "src/deploy/raspi/spi-desktop.service.d/blanking.conf",
                "/etc/systemd/system/spi-desktop.service.d/blanking.conf",
            ]
        ]
        self.assertTrue(matches, "setup_etr.sh must install spi-desktop.service.d/blanking.conf")
        self.assertEqual(matches[0]["mode"], "644")

    def test_daemon_reload_runs_after_the_new_installs(self):
        blanking_dropin_index = SETUP_SCRIPT.index(
            "spi-desktop.service.d/blanking.conf /etc/systemd/system"
        )
        daemon_reload_index = SETUP_SCRIPT.index("systemctl daemon-reload")
        self.assertLess(blanking_dropin_index, daemon_reload_index)


if __name__ == "__main__":
    unittest.main()
