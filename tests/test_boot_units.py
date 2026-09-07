import unittest
from pathlib import Path

RASPI_DEPLOY_DIR = Path(__file__).resolve().parents[1] / "src" / "deploy" / "raspi"


def unit_section(unit_file: Path, section: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    current_section = None
    for line in unit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue
        if current_section != section or not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), []).append(value.strip())
    return values


class BootUnitDependencyTests(unittest.TestCase):
    def test_kiosk_and_vnc_soft_depend_on_their_actual_desktop(self):
        expected_desktops = {
            "etr-kiosk.service": "spi-desktop.service",
            "etr-vnc.service": "etr-remote-desktop.service",
        }
        for unit_name, desktop_service in expected_desktops.items():
            with self.subTest(unit=unit_name):
                unit = unit_section(RASPI_DEPLOY_DIR / unit_name, "Unit")
                requires = " ".join(unit.get("Requires", []))
                wants = " ".join(unit.get("Wants", []))
                self.assertNotIn(
                    desktop_service,
                    requires,
                    f"{unit_name} must not hard-Requires= {desktop_service}: "
                    "a transient desktop failure at boot must not permanently "
                    "fail the consumer's start job.",
                )
                self.assertIn(desktop_service, wants)
                after = " ".join(unit.get("After", []))
                self.assertIn(desktop_service, after)

    def test_blanking_dropin_cannot_fail_spi_desktop(self):
        dropin = RASPI_DEPLOY_DIR / "spi-desktop.service.d" / "blanking.conf"
        service = unit_section(dropin, "Service")
        exec_start_post = [v for v in service.get("ExecStartPost", []) if v]
        self.assertTrue(
            exec_start_post,
            "blanking.conf must still run the anti-blanking script after a reset",
        )
        self.assertTrue(
            exec_start_post[-1].startswith("-"),
            "ExecStartPost for etr-disable-blanking.sh must be prefixed with '-' "
            "so systemd ignores its exit status: it previously made "
            "spi-desktop.service (and etr-kiosk through it) fail whenever the "
            "anti-blanking script raced X11 on cold boot.",
        )

    def test_disable_blanking_exhausts_retries_without_failing(self):
        script = (RASPI_DEPLOY_DIR / "etr-disable-blanking.sh").read_text(encoding="utf-8")
        self.assertNotIn(
            "exit 1",
            script,
            "etr-disable-blanking.sh must not exit 1 after exhausting its retries: "
            "as an ExecStartPost, that failure used to be enough to mark "
            "spi-desktop.service failed on cold boot even though the desktop "
            "itself was still starting normally.",
        )
        self.assertIn("exit 0", script)

    def test_runner_recovery_enables_and_hardens_official_service(self):
        script = (RASPI_DEPLOY_DIR / "etr-recover-github-runner.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"$RUNNER_BASE/actions-runner" "$RUNNER_BASE"', script)
        self.assertIn('systemctl enable "$unit"', script)
        self.assertIn("Restart=always", script)
        self.assertIn("RestartSec=10s", script)
        self.assertIn('"${1:-}" = "--no-restart"', script)

    def test_spi_desktop_identifies_display_without_unstable_fb_index(self):
        unit = (RASPI_DEPLOY_DIR / "spi-desktop.service").read_text(encoding="utf-8")
        launcher = (RASPI_DEPLOY_DIR / "start_spi_desktop.sh").read_text(
            encoding="utf-8"
        )
        setup = (RASPI_DEPLOY_DIR / "setup_etr.sh").read_text(encoding="utf-8")

        self.assertNotIn("ConditionPathExists=/dev/fb1", unit)
        self.assertIn("fb_ili9486", launcher)
        self.assertIn("480,320", launcher)
        self.assertIn("export FRAMEBUFFER", launcher)
        self.assertIn('Option "fbdev" "$FRAMEBUFFER"', launcher)
        self.assertIn('-config "$XORG_CONFIG"', launcher)
        self.assertNotIn("FRAMEBUFFER=/dev/fb1", launcher)
        self.assertIn("dtoverlay=tft35a:rotate=90", setup)

    def test_runner_watchdog_detects_stale_physical_queue(self):
        watchdog = (
            Path(__file__).resolve().parents[1]
            / ".github/scripts/etr-runner-watchdog.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("?status=queued&per_page=30", watchdog)
        self.assertIn("total_seconds() >= 300", watchdog)
        self.assertIn("pgrep -f '/bin/Runner.Worker '", watchdog)


if __name__ == "__main__":
    unittest.main()
