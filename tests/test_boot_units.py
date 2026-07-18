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
    def test_kiosk_and_vnc_do_not_hard_require_spi_desktop(self):
        for unit_name in ("etr-kiosk.service", "etr-vnc.service"):
            with self.subTest(unit=unit_name):
                unit = unit_section(RASPI_DEPLOY_DIR / unit_name, "Unit")
                requires = " ".join(unit.get("Requires", []))
                wants = " ".join(unit.get("Wants", []))
                self.assertNotIn(
                    "spi-desktop.service",
                    requires,
                    f"{unit_name} must not hard-Requires= spi-desktop.service: "
                    "a transient failure of spi-desktop.service at boot fails "
                    f"{unit_name}'s start job with no automatic retry, since "
                    "Restart= only covers a process exiting after ExecStart, "
                    "not a job that failed before reaching it.",
                )
                self.assertIn("spi-desktop.service", wants)
                after = " ".join(unit.get("After", []))
                self.assertIn("spi-desktop.service", after)


if __name__ == "__main__":
    unittest.main()
