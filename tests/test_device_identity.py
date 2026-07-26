import base64
import os
import stat
import tempfile
import unittest
from pathlib import Path

from src.device_identity import (
    canonical_device_request,
    ensure_device_keypair,
    export_public_key,
    load_public_key,
    public_key_fingerprint,
    sign_device_request,
)


class DeviceIdentityTests(unittest.TestCase):
    def test_generates_persistent_ed25519_keypair_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private.pem"
            public = Path(directory) / "public.pem"
            ensure_device_keypair(private, public)
            first_private = private.read_bytes()
            first_public = public.read_bytes()
            ensure_device_keypair(private, public)
            self.assertEqual(private.read_bytes(), first_private)
            self.assertEqual(public.read_bytes(), first_public)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o644)
            self.assertEqual(load_public_key(first_public).__class__.__name__, "Ed25519PublicKey")
            self.assertRegex(public_key_fingerprint(first_public), r"^[a-f0-9]{64}$")

    def test_signs_the_canonical_request_and_verifies_with_public_key(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private.pem"
            public = Path(directory) / "public.pem"
            ensure_device_keypair(private, public)
            headers = sign_device_request(
                action="request",
                serial="0000ABCD1234EF56",
                hostname="etr-core",
                rotation_token="rotation-token",
                private_path=private,
                now=lambda: 1_722_000_000,
                nonce_factory=lambda size: bytes(range(size)),
            )
            payload = canonical_device_request(
                action="request",
                serial="0000ABCD1234EF56",
                hostname="etr-core",
                rotation_token="rotation-token",
                timestamp=headers["X-EtR-Timestamp"],
                nonce=headers["X-EtR-Nonce"],
            )
            signature = base64.urlsafe_b64decode(headers["X-EtR-Signature"] + "==")
            load_public_key(export_public_key(public)).verify(signature, payload)
            self.assertEqual(headers["X-EtR-Timestamp"], "1722000000")
            self.assertEqual(len(signature), 64)

    def test_request_and_exchange_payloads_are_distinct(self):
        common = {
            "serial": "0000ABCD1234EF56",
            "timestamp": "1722000000",
            "nonce": "AAAAAAAAAAAAAAAAAAAAAAAA",
        }
        request = canonical_device_request(action="request", hostname="etr-core", **common)
        exchange = canonical_device_request(action="exchange", activation_code="0" * 20, **common)
        self.assertNotEqual(request, exchange)
        self.assertIn(b'"action":"request"', request)
        self.assertIn(b'"activationCode":"00000000000000000000"', exchange)


if __name__ == "__main__":
    unittest.main()
