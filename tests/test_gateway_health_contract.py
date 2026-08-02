import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GatewayHealthContractTests(unittest.TestCase):
    def test_public_health_module_is_versioned_and_embedded(self):
        required = [
            "gateway/health.mjs",
            "gateway/health.test.mjs",
            "gateway/server.mjs",
            "gateway/Dockerfile",
            ".github/workflows/etr-gateway-public-probe.yml",
            "docs/PROJECT_TRACKER.md",
        ]
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], f"Gateway health contract incomplete: {missing}")

        server = (ROOT / "gateway/server.mjs").read_text(encoding="utf-8")
        health = (ROOT / "gateway/health.mjs").read_text(encoding="utf-8")
        dockerfile = (ROOT / "gateway/Dockerfile").read_text(encoding="utf-8")
        probe = (ROOT / ".github/workflows/etr-gateway-public-probe.yml").read_text(encoding="utf-8")

        self.assertIn("installGatewayHealthRoutes", server)
        for marker in [
            'app.get("/healthz", handler)',
            'app.get("/api/health", handler)',
            'service: "etr-remote-gateway"',
            'version = GATEWAY_VERSION',
            'revision = process.env.K_REVISION || null',
        ]:
            self.assertIn(marker, health)
        self.assertIn("COPY health.mjs ./", dockerfile)
        for marker in [
            "$GATEWAY_URL/api/health",
            "data.get('version') == '1.1.0'",
            "etr-remote-screen-repair-fast.yml",
            "api_health",
        ]:
            self.assertIn(marker, probe)

    def test_health_probe_does_not_depend_on_the_intercepted_public_path(self):
        probe = (ROOT / ".github/workflows/etr-gateway-public-probe.yml").read_text(encoding="utf-8")
        wait_section = probe.split("Attendre la route de santé publique versionnée", 1)[1].split("Sonder les autres routes publiques", 1)[0]
        self.assertIn("/api/health", wait_section)
        self.assertNotIn("$GATEWAY_URL/healthz", wait_section)


if __name__ == "__main__":
    unittest.main()
