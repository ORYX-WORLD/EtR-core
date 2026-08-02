import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteScreenOverrideCleanupTests(unittest.TestCase):
    def test_repair_removes_obsolete_dropins_and_proves_loaded_token_path(self):
        script = (ROOT / "src/deploy/raspi/repair_remote_screen.sh").read_text(encoding="utf-8")
        for marker in [
            "DROPIN_DIR=/etc/systemd/system/${UNIT_NAME}.d",
            'rm -rf "$DROPIN_DIR"',
            'rm -f "$STATE_DIR/remote-screen-auth.json"',
            'systemctl cat "$UNIT_NAME"',
            "ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json",
            "ETR_TOKEN_FILE=/var/lib/etr-core/remote-screen-auth.json",
            "Ancien fichier de jetons encore chargé par systemd",
            "ETR_INSTALLATION_ID=${installation_id}",
            "ETR_TOKEN_FILE=${TOKEN_FILE}",
        ]:
            self.assertIn(marker, script)

    def test_repair_reinstalls_versioned_units_before_restart(self):
        script = (ROOT / "src/deploy/raspi/repair_remote_screen.sh").read_text(encoding="utf-8")
        install_remote = script.index('etr-remote-screen.service" "$UNIT_FILE"')
        daemon_reload = script.index("systemctl daemon-reload")
        restart_remote = script.index('systemctl restart "$UNIT_NAME"')
        self.assertLess(install_remote, daemon_reload)
        self.assertLess(daemon_reload, restart_remote)


if __name__ == "__main__":
    unittest.main()
