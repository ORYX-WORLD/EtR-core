"""Résolution sûre de l'identité d'installation du relais écran EtR."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable
from urllib.parse import quote

import requests

INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,80}$")


def decode_id_token_payload(id_token: str) -> dict[str, Any]:
    parts = str(id_token or "").split(".")
    if len(parts) != 3:
        raise RuntimeError("device_session_token_invalid")
    try:
        payload_part = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("device_session_token_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("device_session_token_invalid")
    return payload


def installation_id_from_device_access(
    id_token: str,
    *,
    database_url: str,
    request_get: Callable[..., Any] = requests.get,
) -> str | None:
    """Lit l'installation liée à l'UID technique avec le jeton de l'appareil.

    Cette valeur est déjà utilisée par les règles Firebase et par la passerelle
    Cloud. Elle permet aux premiers EtR, liés sous un identifiant lisible tel
    que ``etr-core``, de rester compatibles sans créer un second enrôlement.
    """

    payload = decode_id_token_payload(id_token)
    uid = str(payload.get("sub") or payload.get("user_id") or "").strip()
    if not uid or len(uid) > 128:
        raise RuntimeError("device_session_uid_missing")
    origin = str(database_url or "").strip().rstrip("/")
    if not origin.startswith("https://"):
        raise RuntimeError("firebase_database_url_missing")
    try:
        response = request_get(
            f"{origin}/deviceAccess/{quote(uid, safe='')}.json",
            params={"auth": id_token},
            headers={"Accept": "application/json", "User-Agent": "EtR-Remote-Screen/2.0"},
            timeout=12,
        )
    except requests.RequestException as exc:
        raise RuntimeError("device_access_lookup_failed") from exc
    if response.status_code != 200:
        raise RuntimeError(f"device_access_http_{response.status_code}")
    try:
        value = response.json()
    except ValueError as exc:
        raise RuntimeError("device_access_payload_invalid") from exc
    if value is None:
        return None
    installation_id = str(value).strip()
    if not INSTALLATION_ID_PATTERN.fullmatch(installation_id):
        raise RuntimeError("device_access_installation_invalid")
    return installation_id


def resolve_remote_installation_id(
    id_token: str,
    *,
    database_url: str,
    local_fallback: str,
    request_get: Callable[..., Any] = requests.get,
) -> str:
    """Priorité : claim signé, liaison deviceAccess, identité matérielle."""

    payload = decode_id_token_payload(id_token)
    signed = str(payload.get("installationId") or "").strip()
    if signed:
        if not INSTALLATION_ID_PATTERN.fullmatch(signed):
            raise RuntimeError("device_session_installation_invalid")
        return signed

    linked = installation_id_from_device_access(
        id_token,
        database_url=database_url,
        request_get=request_get,
    )
    if linked:
        return linked

    fallback = str(local_fallback or "").strip()
    if not INSTALLATION_ID_PATTERN.fullmatch(fallback):
        raise RuntimeError("device_local_installation_missing")
    return fallback
