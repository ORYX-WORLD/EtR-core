#!/usr/bin/env python3
"""Passerelle sortante EtR vers Firebase Realtime Database.

Les secrets sont lus exclusivement depuis l'environnement. Aucun port entrant
n'est nécessaire sur la box Internet et aucun secret ne doit être commité.
"""

import logging
import os
import socket
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

LOG = logging.getLogger("etr-firebase-bridge")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variable obligatoire absente: {name}")
    return value


API_KEY = required("FIREBASE_API_KEY")
AUTH_EMAIL = required("FIREBASE_AUTH_EMAIL")
AUTH_PASSWORD = required("FIREBASE_AUTH_PASSWORD")
DATABASE_URL = required("FIREBASE_DATABASE_URL").rstrip("/")
INSTALLATION_ID = os.getenv("ETR_INSTALLATION_ID", "etr-core").strip() or "etr-core"
LOCAL_API_URL = os.getenv("ETR_LOCAL_API_URL", "http://127.0.0.1:8080/").strip()
INTERVAL_SECONDS = max(5, int(os.getenv("ETR_BRIDGE_INTERVAL", "15")))
TIMEOUT_SECONDS = max(2, int(os.getenv("ETR_HTTP_TIMEOUT", "8")))

session = requests.Session()
session.headers.update({"User-Agent": "ORYX-EtR-Bridge/1.0"})


def authenticate() -> str:
    response = session.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        params={"key": API_KEY},
        json={
            "email": AUTH_EMAIL,
            "password": AUTH_PASSWORD,
            "returnSecureToken": True,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["idToken"]


def read_local_state() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "installation_id": INSTALLATION_ID,
        "hostname": socket.gethostname(),
        "updated_at": now,
        "bridge_online": True,
    }
    try:
        response = session.get(LOCAL_API_URL, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            base.update(payload)
        base["local_api_online"] = True
    except Exception as exc:  # la passerelle reste visible même si la source locale est indisponible
        base["local_api_online"] = False
        base["local_api_error"] = type(exc).__name__
    return base


def publish(token: str, payload: dict) -> None:
    path = quote(INSTALLATION_ID, safe="")
    response = session.put(
        f"{DATABASE_URL}/installations/{path}/latest.json",
        params={"auth": token},
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def run() -> None:
    token = authenticate()
    LOG.info("Passerelle EtR démarrée pour %s", INSTALLATION_ID)
    while True:
        payload = read_local_state()
        try:
            publish(token, payload)
            LOG.info("Données publiées à %s", payload["updated_at"])
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                token = authenticate()
                publish(token, payload)
                LOG.info("Jeton renouvelé et données publiées")
            else:
                LOG.exception("Publication Firebase impossible")
        except Exception:
            LOG.exception("Cycle de passerelle en échec")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("ETR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run()
