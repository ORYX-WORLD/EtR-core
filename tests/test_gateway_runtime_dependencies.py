import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GatewayRuntimeDependencyTests(unittest.TestCase):
    def test_firebase_realtime_database_runtime_peers_are_pinned(self):
        package = json.loads((ROOT / "gateway/package.json").read_text(encoding="utf-8"))
        dependencies = package.get("dependencies", {})
        self.assertEqual(package.get("version"), "1.1.1")
        self.assertEqual(dependencies.get("firebase-admin"), "13.0.2")
        self.assertEqual(dependencies.get("@firebase/database-compat"), "2.0.11")
        self.assertEqual(dependencies.get("@firebase/app"), "0.13.2")
        for name in ("firebase-admin", "@firebase/database-compat", "@firebase/app"):
            self.assertNotIn(name, package.get("devDependencies", {}))

    def test_docker_image_proves_firebase_modules_survive_production_prune(self):
        dockerfile = (ROOT / "gateway/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("npm prune --omit=dev", dockerfile)
        self.assertIn("require('@firebase/app')", dockerfile)
        self.assertIn("require('@firebase/database-compat/standalone')", dockerfile)
        self.assertIn("require('firebase-admin')", dockerfile)
        self.assertLess(
            dockerfile.index("npm prune --omit=dev"),
            dockerfile.index("require('@firebase/app')"),
        )


if __name__ == "__main__":
    unittest.main()
