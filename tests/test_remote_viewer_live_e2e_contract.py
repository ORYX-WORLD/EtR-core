import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RemoteViewerLiveE2EContractTests(unittest.TestCase):
    def test_delivery_contract_is_complete(self):
        required = [
            "gateway/remote-screen-diagnostic.mjs",
            "gateway/remote-screen-diagnostic.test.mjs",
            "gateway/remote-screen-live-e2e.mjs",
            "gateway/server.mjs",
            "gateway/Dockerfile",
            ".github/workflows/etr-remote-viewer-live-e2e.yml",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Remote viewer live E2E contract incomplete: {missing}")

    def test_diagnostic_ticket_is_limited_to_github_oidc(self):
        diagnostic = (ROOT / "gateway/remote-screen-diagnostic.mjs").read_text(encoding="utf-8")
        server = (ROOT / "gateway/server.mjs").read_text(encoding="utf-8")
        for marker in [
            "deviceBootstrap.verifyWorkflowToken",
            "/api/diagnostics/remote-screen-ticket",
            "ticketTtlMs = 45_000",
            "remote-screen-diagnostic/device-offline",
            "Cache-Control",
        ]:
            self.assertIn(marker, diagnostic)
        for marker in [
            "createDeviceBootstrapService",
            "installRemoteScreenDiagnosticRoute",
            "issueViewerTicket",
            "deviceBootstrap",
        ]:
            self.assertIn(marker, server)

    def test_live_workflow_proves_html_bundle_websocket_and_rfb(self):
        workflow = (ROOT / ".github/workflows/etr-remote-viewer-live-e2e.yml").read_text(encoding="utf-8")
        script = (ROOT / "gateway/remote-screen-live-e2e.mjs").read_text(encoding="utf-8")
        dockerfile = (ROOT / "gateway/Dockerfile").read_text(encoding="utf-8")
        for marker in [
            "id-token: write",
            "audience=etr-bootstrap",
            "remote-screen-live-e2e.mjs",
            "etr-remote-viewer-live-e2e-last.json",
            "liveViewerDiagnostic",
            "int(data.get('viewers', 0)) == 0",
        ]:
            self.assertIn(marker, workflow)
        for marker in [
            "/api/diagnostics/remote-screen-ticket",
            "/novnc/rfb-browser-v2.js",
            'websocketUrl.pathname = "/client"',
            'prefix.startsWith("RFB ")',
            "websocketStatus: 101",
            "GITHUB_OIDC_TOKEN",
        ]:
            self.assertIn(marker, script)
        self.assertNotIn("GITHUB_OIDC_TOKEN:", script)
        self.assertIn("COPY remote-screen-diagnostic.mjs ./", dockerfile)


if __name__ == "__main__":
    unittest.main()
