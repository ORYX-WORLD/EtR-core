#!/usr/bin/env python3
"""Point d'entrée du relais écran avec compatibilité des installations historiques."""

from __future__ import annotations

import asyncio
import logging
import os

import remote_screen_agent as agent
from remote_screen_identity import resolve_remote_installation_id


def authenticate_linked_device_session() -> tuple[str, str]:
    """Rafraîchit la session principale puis résout son installation réelle."""

    cached = agent.load_json(agent.PRIMARY_TOKEN_FILE)
    refresh_token = str(cached.get("refreshToken") or "").strip()
    if not refresh_token:
        raise RuntimeError("device_session_missing")
    data = agent.refresh_tokens(refresh_token)
    id_token = str(data.get("idToken") or "").strip()
    next_refresh_token = str(data.get("refreshToken") or refresh_token).strip()
    if not id_token or not next_refresh_token:
        raise RuntimeError("device_session_refresh_incomplete")

    local_installation_id = agent.installation_id_from_local_device()
    installation_id = resolve_remote_installation_id(
        id_token,
        database_url=os.getenv("FIREBASE_DATABASE_URL", ""),
        local_fallback=local_installation_id,
    )
    agent.atomic_json_write(
        agent.PRIMARY_TOKEN_FILE,
        {"idToken": id_token, "refreshToken": next_refresh_token},
    )
    return id_token, installation_id


agent.authenticate_existing_device_session = authenticate_linked_device_session


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("ETR_REMOTE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(agent.run_forever())
