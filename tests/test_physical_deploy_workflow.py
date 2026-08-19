from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "etr-deploy.yml"
DEPLOY_SCRIPT = ROOT / "src" / "deploy" / "raspi" / "etr_physical_deploy.sh"
PUBLISH_SCRIPT = ROOT / "src" / "deploy" / "raspi" / "publish_physical_deploy_report.sh"
SETUP_SCRIPT = ROOT / "src" / "deploy" / "raspi" / "setup_etr.sh"


class PhysicalDeployWorkflowTests(unittest.TestCase):
    def test_runner_does_not_depend_on_actions_checkout(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("uses: actions/checkout", content)
        self.assertIn("sans workspace Actions", content)
        self.assertIn('checkout --detach "$GITHUB_SHA"', content)
        self.assertIn('rev-parse HEAD)" = "$GITHUB_SHA"', content)

    def test_workflow_runs_versioned_deploy_and_report_scripts(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("etr_physical_deploy.sh", content)
        self.assertIn("publish_physical_deploy_report.sh", content)
        self.assertIn("if: always()", content)
        self.assertIn("runs-on: [self-hosted, Linux, ARM64]", content)
        self.assertIn("id-token: write", content)

    def test_main_installer_requires_the_ads1263_acquisition(self):
        content = SETUP_SCRIPT.read_text(encoding="utf-8")
        for marker in [
            "install_sensor_acquisition.sh",
            "etr-sensor-acquisition.service",
            "/var/lib/etr-core/telemetry.json",
            "acquisition ADS1263",
        ]:
            self.assertIn(marker, content)

    def test_physical_proof_covers_exact_revision_services_cloud_and_sensors(self):
        content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        for marker in [
            'installed_commit" = "$GITHUB_SHA',
            "etr-firebase-bridge.service",
            "etr-remote-screen.service",
            "etr-sensor-acquisition.service",
            "connected to the remote gateway",
            "/api/enrollment/session-health",
            "firebase_session_health=true",
            "remote_screen_connected=true",
            "sensor_acquisition=true",
            "sensor_adc_online=true",
            "pressure_signals_valid=true",
            "temperature_inputs_diagnosed=true",
            "telemetry_fresh=true",
            "hardware.get('adc') == 'ADS1263'",
            "hardware.get('chip_id') == 1",
            "{'pressure_1', 'pressure_2', 'temperature_1', 'temperature_2'}",
            "gateway_devices=",
            "enrollment_status=",
            "commit_match=",
        ]:
            self.assertIn(marker, content)

    def test_report_is_published_from_an_isolated_temporary_clone(self):
        content = PUBLISH_SCRIPT.read_text(encoding="utf-8")
        for marker in [
            "RUNNER_TEMP",
            "git clone --filter=blob:none",
            "x-access-token:${GH_TOKEN}",
            ".github/deployment/etr-last-deploy.txt",
            "[skip ci]",
            "checkout -B proof-edge origin/main",
        ]:
            self.assertIn(marker, content)

    def test_versioned_shell_scripts_are_syntactically_valid(self):
        for script in (DEPLOY_SCRIPT, PUBLISH_SCRIPT, SETUP_SCRIPT):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_workflow_change_triggers_the_physical_deployment(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- .github/workflows/etr-deploy.yml", content)
        self.assertIn("- src/**", content)
        self.assertIn("- dashboard/**", content)


if __name__ == "__main__":
    unittest.main()
