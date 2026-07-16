import asyncio
import importlib
import json
import os
import socket
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("ETR_REMOTE_GATEWAY_WSS", "wss://example.invalid/device")

firebase_bridge = types.ModuleType("firebase_bridge")
firebase_bridge.INSTALLATION_ID = "etr-test"
firebase_bridge.authenticate = lambda: "test-token"
sys.modules["firebase_bridge"] = firebase_bridge

agent = importlib.import_module("remote_screen_agent")


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
