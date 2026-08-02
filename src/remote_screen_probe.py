#!/usr/bin/env python3
"""Diagnostic sûr de la session appareil et du WebSocket écran EtR.

Le script ne journalise jamais les jetons. Il vérifie d'abord que le même ID
token permet de lire la liaison `deviceAccess/<uid>` dans Realtime Database,
puis tente exactement la négociation WebSocket utilisée par l'agent.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
import websockets

from firebase_bridge import atomic_json_write, load_json, refresh_tokens
from remote_screen_agent import installation_id_from_local_device
from remote_screen_identity import resolve_remote_installation_id


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_payload(id_token: str) -> dict[str, Any]:
    parts = str(id_token or "").split(".")
    if len(parts) != 3:
        raise RuntimeError("id_token_invalid")
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("id_token_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("id_token_invalid")
    return payload


def refresh_device_session(token_file: Path, *, database_url: str) -> tuple[str, str, str]:
    cached = load_json(token_file)
    refresh_token = str(cached.get("refreshToken") or "").strip()
    if not refresh_token:
        raise RuntimeError("device_session_missing")
    data = refresh_tokens(refresh_token)
    id_token = str(data.get("idToken") or "").strip()
    next_refresh = str(data.get("refreshToken") or refresh_token).strip()
    if not id_token or not next_refresh:
        raise RuntimeError("device_session_refresh_incomplete")
    installation_id = resolve_remote_installation_id(
        id_token,
        database_url=database_url,
        local_fallback=installation_id_from_local_device(),
    )
    atomic_json_write(token_file, {"idToken": id_token, "refreshToken": next_refresh})
    payload = decode_payload(id_token)
    uid = str(payload.get("sub") or payload.get("user_id") or "").strip()
    if not uid or len(uid) > 128:
        raise RuntimeError("device_uid_missing")
    return id_token, installation_id, uid


def check_device_access(
    *,
    database_url: str,
    uid: str,
    id_token: str,
    expected_installation_id: str,
    request_get=requests.get,
) -> dict[str, Any]:
    url = f"{database_url.rstrip('/')}/deviceAccess/{requests.utils.quote(uid, safe='')}.json"
    try:
        response = request_get(
            url,
            params={"auth": id_token},
            headers={"Accept": "application/json", "User-Agent": "EtR-Remote-Probe/2.0"},
            timeout=12,
        )
        try:
            value = response.json()
        except ValueError:
            value = None
        return {
            "status": int(response.status_code),
            "value": value if isinstance(value, (str, int, float, bool)) or value is None else "structured",
            "matches": response.status_code == 200 and value == expected_installation_id,
        }
    except requests.RequestException as exc:
        return {"status": 0, "value": None, "matches": False, "error": type(exc).__name__}


async def check_websocket(*, gateway: str, id_token: str, installation_id: str) -> dict[str, Any]:
    query = urlencode({"installationId": installation_id})
    url = f"{gateway}{'&' if '?' in gateway else '?'}{query}"
    try:
        async with websockets.connect(
            url,
            extra_headers={"Authorization": f"Bearer {id_token}"},
            open_timeout=15,
            close_timeout=3,
            ping_interval=None,
            max_size=2 * 1024 * 1024,
        ) as ws:
            await ws.close(code=1000, reason="diagnostic complete")
            return {"connected": True, "status": 101, "error": None}
    except Exception as exc:  # type varies between websockets releases
        status = int(getattr(exc, "status_code", 0) or 0)
        return {
            "connected": False,
            "status": status,
            "error": type(exc).__name__,
            "message": str(exc)[:500],
        }


async def run_probe(token_file: Path) -> dict[str, Any]:
    gateway = os.getenv("ETR_REMOTE_GATEWAY_WSS", "").strip()
    database_url = os.getenv("FIREBASE_DATABASE_URL", "").strip()
    if not gateway.startswith("wss://"):
        raise RuntimeError("gateway_missing")
    if not database_url.startswith("https://"):
        raise RuntimeError("database_url_missing")

    id_token, installation_id, uid = refresh_device_session(
        token_file,
        database_url=database_url,
    )
    access = check_device_access(
        database_url=database_url,
        uid=uid,
        id_token=id_token,
        expected_installation_id=installation_id,
    )
    websocket = await check_websocket(
        gateway=gateway,
        id_token=id_token,
        installation_id=installation_id,
    )
    return {
        "checkedAt": utc_now(),
        "installationId": installation_id,
        "uidPresent": bool(uid),
        "deviceAccess": access,
        "websocket": websocket,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe EtR remote screen device session")
    parser.add_argument("--token-file", type=Path, default=Path("/var/lib/etr-core/firebase-auth.json"))
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = asyncio.run(run_probe(args.token_file))
        exit_code = 0 if report["deviceAccess"]["matches"] and report["websocket"]["connected"] else 4
    except Exception as exc:
        report = {
            "checkedAt": utc_now(),
            "installationId": None,
            "uidPresent": False,
            "deviceAccess": {"status": 0, "value": None, "matches": False},
            "websocket": {"connected": False, "status": 0, "error": type(exc).__name__, "message": str(exc)[:500]},
        }
        exit_code = 5
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
