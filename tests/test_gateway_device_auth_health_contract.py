import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GatewayDeviceAuthorizationAndHealthContractTests(unittest.TestCase):
    def test_delivery_contract_is_complete(self):
        required = [
            "gateway/health.mjs",
            "gateway/health.test.mjs",
            "gateway/device-authorization.mjs",
            "gateway/device-authorization.test.mjs",
            "gateway/server.mjs",
            "gateway/Dockerfile",
            ".github/workflows/etr-gateway-cloudrun.yml",
            ".github/workflows/etr-gateway-public-probe.yml",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Gateway device/health contract incomplete: {missing}")

    def test_gateway_version_and_public_health_are_explicit(self):
        package = json.loads((ROOT / "gateway/package.json").read_text(encoding="utf-8"))
        health = (ROOT / "gateway/health.mjs").read_text(encoding="utf-8")
        self.assertEqual(package.get("version"), "1.1.1")
        for marker in [
            'GATEWAY_VERSION = "1.1.1"',
            'service: "etr-remote-gateway"',
            'revision = process.env.K_REVISION || null',
            'app.get("/healthz", handler)',
            'app.get("/api/health", handler)',
        ]:
            self.assertIn(marker, health)

    def test_server_uses_token_backed_device_access_authorization(self):
        server = (ROOT / "gateway/server.mjs").read_text(encoding="utf-8")
        authorization = (ROOT / "gateway/device-authorization.mjs").read_text(encoding="utf-8")
        for marker in [
            'import { createDeviceConnectionAuthorizer } from "./device-authorization.mjs"',
            "const authorizeDeviceConnection = createDeviceConnectionAuthorizer",
            "const authorization = await authorizeDeviceConnection",
            "authorization.linkedInstallationId",
            'code: "device-access/mismatch"',
            'console.log("Remote device connected"',
        ]:
            self.assertIn(marker, server)
        self.assertNotIn("async function deviceCanConnect", server)
        for marker in [
            "/deviceAccess/",
            'url.searchParams.set("auth", token)',
            "verifyIdToken(token)",
            "linkedInstallationId === installationId",
            "device-access/firebase-401",
            "device-access/network-error",
        ]:
            self.assertIn(marker, authorization)

    def test_docker_and_deployment_verify_the_new_modules_and_revision(self):
        dockerfile = (ROOT / "gateway/Dockerfile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/etr-gateway-cloudrun.yml").read_text(encoding="utf-8")
        probe = (ROOT / ".github/workflows/etr-gateway-public-probe.yml").read_text(encoding="utf-8")
        for marker in ["COPY health.mjs ./", "COPY device-authorization.mjs ./"]:
            self.assertIn(marker, dockerfile)
        for marker in [
            "EXPECTED_GATEWAY_VERSION: 1.1.1",
            "node --check health.mjs",
            "node --check device-authorization.mjs",
            "127.0.0.1:18080/api/health",
            "$GATEWAY_URL/api/health",
            "data.get('version') == os.environ['EXPECTED_GATEWAY_VERSION']",
            "data.get('revision') == os.environ['GATEWAY_REVISION']",
            "deviceAuthorization",
            "etr-remote-screen-repair-fast.yml",
        ]:
            self.assertIn(marker, workflow)
        for marker in [
            "EXPECTED_GATEWAY_VERSION: 1.1.1",
            "$GATEWAY_URL/api/health",
            "etr-remote-screen-repair-fast.yml",
        ]:
            self.assertIn(marker, probe)


if __name__ == "__main__":
    unittest.main()
