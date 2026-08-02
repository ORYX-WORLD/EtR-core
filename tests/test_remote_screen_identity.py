import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from remote_screen_identity import (  # noqa: E402
    installation_id_from_device_access,
    resolve_remote_installation_id,
)
import remote_screen_runtime  # noqa: E402


def unsigned_token(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"e30.{encoded}.signature"


class FakeResponse:
    def __init__(self, status_code, value):
        self.status_code = status_code
        self._value = value

    def json(self):
        return self._value


class RemoteScreenIdentityTests(unittest.TestCase):
    def test_signed_claim_has_priority_without_database_lookup(self):
        token = unsigned_token({"sub": "uid-1", "installationId": "etr-signed"})
        getter = mock.Mock(side_effect=AssertionError("RTDB must not be queried"))
        self.assertEqual(
            resolve_remote_installation_id(
                token,
                database_url="https://example.firebasedatabase.app",
                local_fallback="etr-local",
                request_get=getter,
            ),
            "etr-signed",
        )
        getter.assert_not_called()

    def test_legacy_session_uses_authoritative_device_access_mapping(self):
        token = unsigned_token({"sub": "technical-user-1"})
        getter = mock.Mock(return_value=FakeResponse(200, "etr-core"))
        self.assertEqual(
            resolve_remote_installation_id(
                token,
                database_url="https://example.firebasedatabase.app",
                local_fallback="etr-0000dd7429c2",
                request_get=getter,
            ),
            "etr-core",
        )
        args, kwargs = getter.call_args
        self.assertTrue(args[0].endswith("/deviceAccess/technical-user-1.json"))
        self.assertEqual(kwargs["params"], {"auth": token})
        self.assertEqual(kwargs["timeout"], 12)

    def test_missing_device_access_falls_back_to_local_identity(self):
        token = unsigned_token({"sub": "technical-user-2"})
        getter = mock.Mock(return_value=FakeResponse(200, None))
        self.assertEqual(
            resolve_remote_installation_id(
                token,
                database_url="https://example.firebasedatabase.app",
                local_fallback="etr-0000dd7429c2",
                request_get=getter,
            ),
            "etr-0000dd7429c2",
        )

    def test_invalid_device_access_mapping_is_rejected(self):
        token = unsigned_token({"sub": "technical-user-3"})
        getter = mock.Mock(return_value=FakeResponse(200, "not valid !"))
        with self.assertRaisesRegex(RuntimeError, "device_access_installation_invalid"):
            installation_id_from_device_access(
                token,
                database_url="https://example.firebasedatabase.app",
                request_get=getter,
            )

    def test_database_http_failure_is_not_silently_replaced(self):
        token = unsigned_token({"sub": "technical-user-4"})
        getter = mock.Mock(return_value=FakeResponse(403, {"error": "Permission denied"}))
        with self.assertRaisesRegex(RuntimeError, "device_access_http_403"):
            resolve_remote_installation_id(
                token,
                database_url="https://example.firebasedatabase.app",
                local_fallback="etr-0000dd7429c2",
                request_get=getter,
            )


class RemoteScreenRuntimeTests(unittest.TestCase):
    def test_runtime_refreshes_shared_token_and_uses_linked_installation(self):
        writes = []
        with (
            mock.patch.object(
                remote_screen_runtime.agent,
                "load_json",
                return_value={"refreshToken": "old-refresh-token"},
            ),
            mock.patch.object(
                remote_screen_runtime.agent,
                "refresh_tokens",
                return_value={
                    "idToken": "fresh-id-token",
                    "refreshToken": "fresh-refresh-token",
                },
            ),
            mock.patch.object(
                remote_screen_runtime.agent,
                "installation_id_from_local_device",
                return_value="etr-0000dd7429c2",
            ),
            mock.patch.object(
                remote_screen_runtime,
                "resolve_remote_installation_id",
                return_value="etr-core",
            ) as resolve,
            mock.patch.object(
                remote_screen_runtime.agent,
                "atomic_json_write",
                side_effect=lambda path, value: writes.append((path, value)),
            ),
            mock.patch.dict(
                os.environ,
                {"FIREBASE_DATABASE_URL": "https://example.firebasedatabase.app"},
            ),
        ):
            token, installation_id = remote_screen_runtime.authenticate_linked_device_session()

        self.assertEqual(token, "fresh-id-token")
        self.assertEqual(installation_id, "etr-core")
        resolve.assert_called_once_with(
            "fresh-id-token",
            database_url="https://example.firebasedatabase.app",
            local_fallback="etr-0000dd7429c2",
        )
        self.assertEqual(
            writes,
            [
                (
                    remote_screen_runtime.agent.PRIMARY_TOKEN_FILE,
                    {
                        "idToken": "fresh-id-token",
                        "refreshToken": "fresh-refresh-token",
                    },
                )
            ],
        )


class RemoteScreenIdentityRepositoryContractTests(unittest.TestCase):
    def test_service_uses_linked_identity_runtime(self):
        service = (ROOT / "src/deploy/raspi/etr-remote-screen.service").read_text(encoding="utf-8")
        runtime = (ROOT / "src/remote_screen_runtime.py").read_text(encoding="utf-8")
        identity = (ROOT / "src/remote_screen_identity.py").read_text(encoding="utf-8")
        for marker in [
            "remote_screen_runtime.py",
            "ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json",
            "etr-firebase-bridge.service",
        ]:
            self.assertIn(marker, service)
        for marker in [
            "resolve_remote_installation_id",
            "FIREBASE_DATABASE_URL",
            "authenticate_existing_device_session = authenticate_linked_device_session",
        ]:
            self.assertIn(marker, runtime)
        for marker in [
            "deviceAccess/",
            "installation_id_from_device_access",
            "signed claim",
            "local_fallback",
        ]:
            self.assertIn(marker, identity)


if __name__ == "__main__":
    unittest.main()
