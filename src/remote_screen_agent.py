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
LOCAL_VNC_PORT = int(os.getenv("ETR_LOCAL_VNC_PORT", "5901"))
GATEWAY = os.getenv("ETR_REMOTE_GATEWAY_WSS", "").strip()


async def relay_vnc(ws):
    reader = writer = None
    reader_task = None
    active_session_id = None

    async def close_local(reason="requested"):
        nonlocal reader, writer, reader_task, active_session_id
        had_connection = writer is not None
        if reader_task:
            reader_task.cancel()
            with suppress(asyncio.CancelledError, ConnectionError, OSError):
                await reader_task
        if writer:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
        reader = writer = reader_task = None
        if had_connection:
            LOG.info("VNC local fermé (%s), session %s", reason, active_session_id or "sans identifiant")
        active_session_id = None

    async def local_to_gateway(session_id):
        assert reader is not None
        first_payload = True
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                raise ConnectionError("local VNC disconnected")
            if first_payload:
                LOG.info(
                    "Premier flux VNC local transmis (%d octets), session %s",
                    len(chunk),
                    session_id or "sans identifiant",
                )
                first_payload = False
            await ws.send(chunk)

    try:
        async for payload in ws:
            if isinstance(payload, str):
                try:
                    command = json.loads(payload)
                except json.JSONDecodeError:
                    LOG.warning("Commande distante JSON invalide ignorée")
                    continue
                if command.get("type") == "open":
                    session_id = str(command.get("sessionId") or "")
                    LOG.info(
                        "Commande d'ouverture VNC reçue pour %s:%d, session %s",
                        LOCAL_VNC_HOST,
                        LOCAL_VNC_PORT,
                        session_id or "sans identifiant",
                    )
                    await close_local("nouvelle session")
                    try:
                        reader, writer = await asyncio.open_connection(LOCAL_VNC_HOST, LOCAL_VNC_PORT)
                        active_session_id = session_id
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "ready",
                                    "sessionId": session_id,
                                    "host": LOCAL_VNC_HOST,
                                    "port": LOCAL_VNC_PORT,
                                }
                            )
                        )
                        reader_task = asyncio.create_task(local_to_gateway(session_id))
                        LOG.info(
                            "VNC local connecté à %s:%d, session %s",
                            LOCAL_VNC_HOST,
                            LOCAL_VNC_PORT,
                            session_id or "sans identifiant",
                        )
                    except OSError as exc:
                        LOG.warning("VNC local %s:%d indisponible: %s", LOCAL_VNC_HOST, LOCAL_VNC_PORT, exc)
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "error",
                                    "sessionId": session_id,
                                    "message": f"VNC local {LOCAL_VNC_HOST}:{LOCAL_VNC_PORT} indisponible",
                                }
                            )
                        )
                elif command.get("type") == "close":
                    LOG.info(
                        "Commande de fermeture VNC reçue, session %s",
                        command.get("sessionId") or "sans identifiant",
                    )
                    await close_local("viewer fermé")
            elif writer is not None:
                writer.write(payload)
                await writer.drain()
    finally:
        await close_local("passerelle déconnectée")


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
                extra_headers={"Authorization": f"Bearer {token}"},
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
