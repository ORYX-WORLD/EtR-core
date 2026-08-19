from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT_PATH = ROOT / "product" / "edge-component.json"


class ProductComponentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.component = json.loads(COMPONENT_PATH.read_text(encoding="utf-8"))

    def test_component_identifies_the_complete_etr_product(self) -> None:
        self.assertEqual(self.component["schemaVersion"], 1)
        self.assertEqual(self.component["productId"], "oryx-etr")
        self.assertEqual(self.component["componentId"], "edge")
        self.assertEqual(self.component["repository"], "ORYX-WORLD/EtR-core")

    def test_canonical_manifest_is_declared(self) -> None:
        canonical = self.component["canonicalProductManifest"]
        self.assertEqual(canonical["repository"], "ORYX-WORLD/ORYX-PROJETS")
        self.assertEqual(canonical["path"], "projets/etr-product/product-manifest.json")
        self.assertEqual(canonical["contextPath"], "projets/etr-product/ETR_CONTEXT.md")

    def test_versioned_local_services_are_loopback_only(self) -> None:
        interfaces = self.component["versionedLocalInterfaces"]
        self.assertGreaterEqual(len(interfaces), 3)
        for interface in interfaces:
            self.assertTrue(interface["url"].startswith("http://127.0.0.1:"))
            self.assertTrue(interface["sourcePath"])
            self.assertTrue(interface["service"])

    def test_view_store_cannot_be_claimed_as_deployable_without_source(self) -> None:
        surface = self.component["requiredProductSurface"]
        self.assertEqual(surface["id"], "local-view-store")
        self.assertEqual(surface["observedUrl"], "http://127.0.0.1:3000/?view=view-store")
        if surface["sourceStatus"] == "unresolved":
            self.assertTrue(surface["blocksFullProductRelease"])
            self.assertIsNone(surface["repository"])
            self.assertIsNone(surface["path"])
            self.assertIsNone(surface["service"])
            self.assertIsNone(surface["launchCommand"])
        else:
            self.assertEqual(surface["sourceStatus"], "resolved")
            self.assertFalse(surface["blocksFullProductRelease"])
            self.assertTrue(surface["repository"])
            self.assertTrue(surface["path"])
            self.assertTrue(surface["service"] or surface["launchCommand"])

    def test_shared_contract_versions_are_explicit(self) -> None:
        contracts = self.component["contracts"]
        self.assertEqual(contracts["installationIdentity"], "v1")
        self.assertEqual(contracts["telemetry"], "v1")
        self.assertEqual(contracts["enrollment"], "v1")
        self.assertEqual(contracts["remoteScreen"], "v1")
        self.assertIn(contracts["commands"], {"not-qualified", "v1"})


if __name__ == "__main__":
    unittest.main()
