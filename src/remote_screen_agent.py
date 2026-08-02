#!/usr/bin/env python3
"""Secure outbound screen relay for an EtR Raspberry Pi."""

import asyncio
import base64
import json
import logging
import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from firebase_bridge import atomic_json_write, load_json, refresh_tokens

LOG = logging.getLogger("etr.remote-screen")
LOCAL_VNC_HOST = os.getenv("ETR_LOCAL_VNC_HOST", "127.0.0.1")
LOCAL_VNC_PORT = int(os.getenv("ETR_LOCAL_VNC_PORT", "5901"))
GATEWAY = os.getenv("ETR_REMOTE_GATEWAY_WSS", "").strip()
PRIMARY_TOKEN_FILE = Path("/var/lib/etr-core/firebase-auth.json")
INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,80}$")
SERIAL_PATHS = (
    Path("/sys/firmware/devicetree/base/serial-number"),
    Path("/proc/device-tree/serial-number"),
)
CPUINFO_PATH = Path("/proc/cpuinfo")


def _normalize_serial(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def installation_id_from_local_device(
    *,
    configured: str | None = None,
    serial_paths: tuple[Path, ...] = SERIAL_PATHS,
    cpuinfo_path: Path = CPUINFO_PATH,
) -> str:
    """Derive the same stable installation ID used during secure enrollment.

    The Raspberry serial is preferred over configuration so an obsolete local
    hostname such as ``etr-core`` cannot shadow the canonical enrolled identity.
    The environment value remains a controlled fallback for tests and hardware
    where the firmware serial files are unavailable.
    """

    serial = ""
    for path in serial_paths:
        try:
            serial = _normalize_serial(
                path.read_bytes().replace(b"\x00", b"").decode("ascii").strip()
            )
        except (OSError, UnicodeDecodeError):
            continue
        if serial:
            break

    if not serial:
        try:
            for line in cpuinfo_path.read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("serial") and ":" in line:
                    serial = _normalize_serial(line.split(":", 1)[1])
                    if serial:
                        break
        except OSError:
            pass

    if len(serial) >= 8:
        installation_id = f"etr-{serial[-12:].lower()}"
        if INSTALLATION_ID_PATTERN.fullmatch(installation_id):
            return installation_id

    fallback = str(
        configured if configured is not None else os.getenv("ETR_INSTALLATION_ID", "")
    ).strip()
    if INSTALLATION_ID_PATTERN.fullmatch(fallback):
        return fallback
    raise RuntimeError("device_local_installation_missing")


def installation_id_from_id_token(
    id_token: str,
    *,
    fallback_installation_id: str | None = None,
) -> str:
    """Select the signed installation claim or the canonical local identity.

    Cloud Run validates the complete JWT and separately checks
    ``deviceAccess/<uid>`` against the installation ID sent in the query string.
    A deterministic local fallback is therefore safe for sessions issued by an
    older gateway that contain ``etrDevice=true`` but no ``installationId``
    custom claim.
    """

    parts = str(id_token or "").split(".")
    if len(parts) != 3:
        raise RuntimeError("device_session_token_invalid")
    try:
        payload_part = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("device_session_token_invalid") from exc

    if payload.get("etrDevice") is not True:
        raise RuntimeError("device_session_claim_missing")

    signed_installation_id = str(payload.get("installationId") or "").strip()
    if signed_installation_id:
        if not INSTALLATION_ID_PATTERN.fullmatch(signed_installation_id):
            raise RuntimeError("device_session_installation_invalid")
        return signed_installation_id

    return installation_id_from_local_device(configured=fallback_installation_id)


def authenticate_existing_device_session() -> tuple[str, str]:
    """Refresh and identify the device session created by the main bridge.

    The primary token path is fixed deliberately. Historical systemd drop-ins
    used a second token file and could silently restart another enrollment. The
    screen relay now ignores any legacy ETR_TOKEN_FILE override.
    """

    cached = load_json(PRIMARY_TOKEN_FILE)
    refresh_token = str(cached.get("refreshToken") or "").strip()
    if not refresh_token:
        raise RuntimeError("device_session_missing")
    data = refresh_tokens(refresh_token)
    id_token = str(data.get("idToken") or "").strip()
    next_refresh_token = str(data.get("refreshToken") or refresh_token).strip()
    if not id_token or not next_refresh_token:
        raise RuntimeError("device_session_refresh_incomplete")
    installation_id = installation_id_from_id_token(id_token)
    atomic_json_write(
        PRIMARY_TOKEN_FILE,
        {"idToken": id_token, "refreshToken": next_refresh_token},
    )
    return id_token, installation_id


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
    session_wait_logged = False
    while True:
        try:
            token, installation_id = authenticate_existing_device_session()
            session_wait_logged = False
            query = urlencode({"installationId": installation_id})
            url = f"{GATEWAY}{'&' if '?' in GATEWAY else '?'}{query}"
            LOG.info("Connecting installation %s to the remote gateway", installation_id)
            async with websockets.connect(
                url,
                extra_headers={"Authorization": f"Bearer {token}"},
                max_size=2 * 1024 * 1024,
                ping_interval=25,
                ping_timeout=20,
                close_timeout=5,
            ) as ws:
                LOG.info("Installation %s connected to the remote gateway", installation_id)
                delay = 2
                await relay_vnc(ws)
        except RuntimeError as exc:
            if str(exc) == "device_session_missing":
                if not session_wait_logged:
                    LOG.info("Session appareil absente; attente de l'enrôlement Firebase principal")
                    session_wait_logged = True
            else:
                LOG.warning("Remote device session unavailable: %s", exc)
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
