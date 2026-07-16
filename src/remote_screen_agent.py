#!/usr/bin/env python3
"""Secure outbound screen relay for an EtR Raspberry Pi."""

import asyncio
import json
import logging
import os
from contextlib import suppress
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from firebase_bridge import INSTALLATION_ID, authenticate

LOG = logging.getLogger("etr.remote-screen")
LOCAL_VNC_HOST = os.getenv("ETR_LOCAL_VNC_HOST", "127.0.0.1")
LOCAL_VNC_PORT = int(os.getenv("ETR_LOCAL_VNC_PORT", "5900"))
GATEWAY = os.getenv("ETR_REMOTE_GATEWAY_WSS", "").strip()


async def relay_vnc(ws):
    reader = writer = None
    reader_task = None

    async def close_local():
        nonlocal reader, writer, reader_task
        if reader_task:
            reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await reader_task
        if writer:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
        reader = writer = reader_task = None

    async def local_to_gateway():
        assert reader is not None
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                raise ConnectionError("local VNC disconnected")
            await ws.send(chunk)

    try:
        async for payload in ws:
            if isinstance(payload, str):
                try:
                    command = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if command.get("type") == "open":
                    await close_local()
                    try:
                        reader, writer = await asyncio.open_connection(LOCAL_VNC_HOST, LOCAL_VNC_PORT)
                        reader_task = asyncio.create_task(local_to_gateway())
                        await ws.send(json.dumps({"type": "ready"}))
                    except OSError as exc:
                        LOG.warning("VNC local indisponible: %s", exc)
                        await ws.send(json.dumps({"type": "error", "message": "VNC local indisponible"}))
                elif command.get("type") == "close":
                    await close_local()
            elif writer is not None:
                writer.write(payload)
                await writer.drain()
    finally:
        await close_local()


async def run_forever():
    if not GATEWAY:
        raise SystemExit("ETR_REMOTE_GATEWAY_WSS is not configured")

    delay = 2
    while True:
        try:
            token = authenticate()
            query = urlencode({"installationId": INSTALLATION_ID})
            url = f"{GATEWAY}{'&' if '?' in GATEWAY else '?'}{query}"
            LOG.info("Connecting installation %s to the remote gateway", INSTALLATION_ID)
            async with websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {token}"},
                max_size=2 * 1024 * 1024,
                ping_interval=25,
                ping_timeout=20,
                close_timeout=5,
            ) as ws:
                delay = 2
                await relay_vnc(ws)
        except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            LOG.warning("Remote gateway disconnected: %s", exc)
        except Exception:
            LOG.exception("Remote screen agent error")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("ETR_REMOTE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_forever())
