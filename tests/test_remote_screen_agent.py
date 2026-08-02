import asyncio
import base64
import importlib
import json
import os
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("ETR_REMOTE_GATEWAY_WSS", "wss://example.invalid/device")

firebase_bridge = types.ModuleType("firebase_bridge")
firebase_bridge.load_json = lambda _path: {"refreshToken": "test-refresh-token"}
firebase_bridge.refresh_tokens = lambda _token: {
    "idToken": "header.payload.signature",
    "refreshToken": "test-next-refresh-token",
}
firebase_bridge.atomic_json_write = lambda _path, _tokens: None
sys.modules["firebase_bridge"] = firebase_bridge

agent = importlib.import_module("remote_screen_agent")


def unsigned_test_token(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"e30.{encoded}.signature"


class RemoteScreenIdentityTests(unittest.TestCase):
    def test_signed_installation_claim_has_priority(self):
        token = unsigned_test_token(
            {"etrDevice": True, "installationId": "etr-signed-device"}
        )
        self.assertEqual(
            agent.installation_id_from_id_token(
                token, fallback_installation_id="etr-local-device"
            ),
            "etr-signed-device",
        )

    def test_legacy_device_token_uses_controlled_local_fallback(self):
        token = unsigned_test_token({"etrDevice": True})
        self.assertEqual(
            agent.installation_id_from_id_token(
                token, fallback_installation_id="etr-0000dd7429c2"
            ),
            "etr-0000dd7429c2",
        )

    def test_legacy_session_without_custom_claims_uses_local_identity(self):
        token = unsigned_test_token({"sub": "etrdev_legacy"})
        self.assertEqual(
            agent.installation_id_from_id_token(
                token, fallback_installation_id="etr-0000dd7429c2"
            ),
            "etr-0000dd7429c2",
        )

    def test_local_raspberry_serial_derives_canonical_enrollment_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            serial_path = Path(directory) / "serial-number"
            serial_path.write_bytes(b"00000000DD7429C2\x00")
            missing_cpuinfo = Path(directory) / "cpuinfo-missing"
            self.assertEqual(
                agent.installation_id_from_local_device(
                    serial_paths=(serial_path,),
                    cpuinfo_path=missing_cpuinfo,
                    configured="etr-obsolete-hostname",
                ),
                "etr-0000dd7429c2",
            )

    def test_local_configuration_is_used_only_when_serial_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            self.assertEqual(
                agent.installation_id_from_local_device(
                    serial_paths=(missing,),
                    cpuinfo_path=missing,
                    configured="etr-configured-device",
                ),
                "etr-configured-device",
            )

    def test_invalid_signed_installation_is_never_replaced_silently(self):
        token = unsigned_test_token(
            {"etrDevice": True, "installationId": "not valid !"}
        )
        with self.assertRaisesRegex(RuntimeError, "device_session_installation_invalid"):
            agent.installation_id_from_id_token(
                token, fallback_installation_id="etr-0000dd7429c2"
            )

    def test_authentication_refreshes_shared_legacy_session_and_uses_local_fallback(self):
        token = unsigned_test_token({"sub": "etrdev_legacy"})
        writes = []
        with (
            mock.patch.object(agent, "load_json", return_value={"refreshToken": "refresh-old"}),
            mock.patch.object(
                agent,
                "refresh_tokens",
                return_value={"idToken": token, "refreshToken": "refresh-new"},
            ),
            mock.patch.object(
                agent,
                "installation_id_from_local_device",
                return_value="etr-0000dd7429c2",
            ),
            mock.patch.object(
                agent,
                "atomic_json_write",
                side_effect=lambda path, value: writes.append((path, value)),
            ),
        ):
            id_token, installation_id = agent.authenticate_existing_device_session()
        self.assertEqual(id_token, token)
        self.assertEqual(installation_id, "etr-0000dd7429c2")
        self.assertEqual(
            writes,
            [
                (
                    agent.PRIMARY_TOKEN_FILE,
                    {"idToken": token, "refreshToken": "refresh-new"},
                )
            ],
        )


class FakeWebSocket:
    def __init__(self, commands, stop_after_sends):
        self.commands = list(commands)
        self.sent = []
        self.stop_after_sends = stop_after_sends
        self.sent_event = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.commands:
            return self.commands.pop(0)
        if len(self.sent) < self.stop_after_sends:
            await asyncio.wait_for(self.sent_event.wait(), timeout=1)
            self.sent_event.clear()
            return await self.__anext__()
        raise StopAsyncIteration

    async def send(self, payload):
        self.sent.append(payload)
        self.sent_event.set()


class RemoteScreenRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_acknowledges_session_and_relays_vnc_banner(self):
        client_closed = asyncio.Event()

        async def vnc_server(reader, writer):
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            await reader.read()
            writer.close()
            await writer.wait_closed()
            client_closed.set()

        server = await asyncio.start_server(vnc_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        old_port = agent.LOCAL_VNC_PORT
        agent.LOCAL_VNC_PORT = port
        ws = FakeWebSocket(
            [json.dumps({"type": "open", "sessionId": "session-1"})],
            stop_after_sends=2,
        )

        try:
            await asyncio.wait_for(agent.relay_vnc(ws), timeout=2)
            ready = json.loads(ws.sent[0])
            self.assertEqual(ready["type"], "ready")
            self.assertEqual(ready["sessionId"], "session-1")
            self.assertEqual(ready["port"], port)
            self.assertEqual(ws.sent[1], b"RFB 003.008\n")
            await asyncio.wait_for(client_closed.wait(), timeout=1)
        finally:
            agent.LOCAL_VNC_PORT = old_port
            server.close()
            await server.wait_closed()

    async def test_open_reports_unavailable_local_vnc(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
        probe.close()

        old_port = agent.LOCAL_VNC_PORT
        agent.LOCAL_VNC_PORT = unused_port
        ws = FakeWebSocket(
            [json.dumps({"type": "open", "sessionId": "session-2"})],
            stop_after_sends=1,
        )

        try:
            await asyncio.wait_for(agent.relay_vnc(ws), timeout=2)
            error = json.loads(ws.sent[0])
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["sessionId"], "session-2")
            self.assertIn(str(unused_port), error["message"])
        finally:
            agent.LOCAL_VNC_PORT = old_port


if __name__ == "__main__":
    unittest.main()
