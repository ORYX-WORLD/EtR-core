#!/usr/bin/env python3
"""Passerelle sortante EtR vers Firebase Realtime Database.

Modes d'authentification :
- enrôlement automatique par numéro de série + code physique à usage unique ;
- compte technique e-mail/mot de passe conservé uniquement pour la transition.

Les secrets restent dans /etc/etr-core et les jetons/états locaux dans
/var/lib/etr-core avec des permissions 0600. Aucun port entrant n'est requis.
"""

import hashlib
import json
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

LOG = logging.getLogger("etr-firebase-bridge")
BRIDGE_VERSION = "3.0"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variable obligatoire absente: {name}")
    return value


def normalize_serial(value: str) -> str:
    serial = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if len(serial) < 8:
        raise RuntimeError("Numéro de série Raspberry invalide")
    return serial[-64:]


def normalize_activation_code(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def raspberry_serial() -> str:
    override = os.getenv("ETR_DEVICE_SERIAL", "").strip()
    if override:
        return normalize_serial(override)
    for name in ("/sys/firmware/devicetree/base/serial-number", "/proc/device-tree/serial-number"):
        try:
            value = Path(name).read_bytes().replace(b"\x00", b"").decode("ascii").strip()
            if value:
                return normalize_serial(value)
        except (OSError, UnicodeDecodeError):
            pass
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("serial"):
                return normalize_serial(line.split(":", 1)[1])
    except OSError:
        pass
    raise RuntimeError("Numéro de série Raspberry introuvable; définir ETR_DEVICE_SERIAL")


API_KEY = required("FIREBASE_API_KEY")
DATABASE_URL = required("FIREBASE_DATABASE_URL").rstrip("/")
DEVICE_SERIAL = raspberry_serial()
DEVICE_FINGERPRINT = hashlib.sha256(DEVICE_SERIAL.encode("ascii")).hexdigest()
INSTALLATION_ID = os.getenv("ETR_INSTALLATION_ID", "").strip() or f"etr-{DEVICE_SERIAL[-12:].lower()}"
LOCAL_API_URL = os.getenv("ETR_LOCAL_API_URL", "http://127.0.0.1:8080/api/v1/status").strip()
ENROLLMENT_URL = os.getenv("FIREBASE_ENROLLMENT_URL", "").strip()
ACTIVATION_CODE = normalize_activation_code(os.getenv("ETR_ACTIVATION_CODE", ""))
AUTH_EMAIL = os.getenv("FIREBASE_AUTH_EMAIL", "").strip()
AUTH_PASSWORD = os.getenv("FIREBASE_AUTH_PASSWORD", "").strip()
TOKEN_FILE = Path(os.getenv("ETR_TOKEN_FILE", "/var/lib/etr-core/firebase-auth.json"))
ENROLLMENT_FILE = Path(os.getenv("ETR_ENROLLMENT_FILE", "/var/lib/etr-core/enrollment.json"))
INTERVAL_SECONDS = max(5, int(os.getenv("ETR_BRIDGE_INTERVAL", "15")))
ENROLLMENT_RETRY_SECONDS = max(5, int(os.getenv("ETR_ENROLLMENT_RETRY", "15")))
TIMEOUT_SECONDS = max(2, int(os.getenv("ETR_HTTP_TIMEOUT", "8")))

session = requests.Session()
session.headers.update({"User-Agent": f"ORYX-EtR-Bridge/{BRIDGE_VERSION}"})


def atomic_json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_tokens(data: dict) -> None:
    atomic_json_write(TOKEN_FILE, data)


def load_tokens() -> dict:
    return load_json(TOKEN_FILE)


def save_enrollment(data: dict) -> None:
    safe = {
        "installationId": str(data.get("installationId") or INSTALLATION_ID),
        "activationCode": str(data.get("activationCode") or ""),
        "rotationToken": str(data.get("rotationToken") or ""),
        "expiresAt": str(data.get("expiresAt") or ""),
        "expiresEpoch": float(data.get("expiresEpoch") or 0),
        "status": str(data.get("status") or "pending"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json_write(ENROLLMENT_FILE, safe)


def load_enrollment() -> dict:
    return load_json(ENROLLMENT_FILE)


def clear_enrollment() -> None:
    try:
        ENROLLMENT_FILE.unlink()
    except FileNotFoundError:
        pass


def sign_in_password() -> dict:
    response = session.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        params={"key": API_KEY},
        json={"email": AUTH_EMAIL, "password": AUTH_PASSWORD, "returnSecureToken": True},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def sign_in_custom_token(custom_token: str) -> dict:
    response = session.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
        params={"key": API_KEY},
        json={"token": custom_token, "returnSecureToken": True},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def refresh_tokens(refresh_token: str) -> dict:
    response = session.post(
        "https://securetoken.googleapis.com/v1/token",
        params={"key": API_KEY},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return {"idToken": data["id_token"], "refreshToken": data["refresh_token"]}


def request_enrollment(existing: dict | None = None) -> dict:
    if not ENROLLMENT_URL:
        raise RuntimeError("URL d'enrôlement absente")
    existing = existing or {}
    payload = {
        "action": "request",
        "serial": DEVICE_SERIAL,
        "hostname": socket.gethostname(),
        "installationId": INSTALLATION_ID,
    }
    if existing.get("rotationToken"):
        payload["rotationToken"] = existing["rotationToken"]
    response = session.post(ENROLLMENT_URL, json=payload, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    code = str(data.get("activationCode") or "").strip()
    rotation = str(data.get("rotationToken") or "").strip()
    if not code or not rotation:
        raise RuntimeError("Réponse d'enrôlement incomplète")
    data["expiresEpoch"] = time.time() + max(60, int(data.get("expiresIn") or 86400))
    data["status"] = "pending"
    save_enrollment(data)
    LOG.warning("Code d'activation EtR disponible sur l'écran local pour %s", INSTALLATION_ID)
    return data


def exchange_activation_code(code: str) -> dict:
    response = session.post(
        required("FIREBASE_ENROLLMENT_URL"),
        json={"action": "exchange", "serial": DEVICE_SERIAL, "code": code},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code == 409:
        try:
            reason = response.json().get("code", "awaiting_claim")
        except ValueError:
            reason = "awaiting_claim"
        raise RuntimeError(reason)
    if response.status_code in (401, 410, 429):
        clear_enrollment()
    response.raise_for_status()
    custom_token = response.json()["customToken"]
    return sign_in_custom_token(custom_token)


def enrollment_expired(state: dict) -> bool:
    return bool(state) and float(state.get("expiresEpoch") or 0) <= time.time()


def authenticate(force: bool = False) -> str:
    cached = load_tokens()
    if cached.get("refreshToken"):
        try:
            data = refresh_tokens(cached["refreshToken"])
            save_tokens(data)
            return data["idToken"]
        except requests.RequestException:
            if not force:
                LOG.warning("Jeton Firebase à renouveler par enrôlement ou compte technique")

    enrollment = load_enrollment()
    if enrollment_expired(enrollment):
        clear_enrollment()
        enrollment = {}

    code = ACTIVATION_CODE or normalize_activation_code(enrollment.get("activationCode", ""))
    if code and ENROLLMENT_URL:
        data = exchange_activation_code(code)
        clear_enrollment()
    elif AUTH_EMAIL and AUTH_PASSWORD:
        data = sign_in_password()
    else:
        request_enrollment(enrollment)
        raise RuntimeError("awaiting_claim")

    save_tokens({"idToken": data["idToken"], "refreshToken": data["refreshToken"]})
    return data["idToken"]


def read_local_state() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "installation_id": INSTALLATION_ID,
        "device_fingerprint": DEVICE_FINGERPRINT,
        "hostname": socket.gethostname(),
        "updated_at": now,
        "bridge_online": True,
        "gateway_version": BRIDGE_VERSION,
    }
    try:
        response = session.get(LOCAL_API_URL, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            base.update(payload)
        base["local_api_online"] = True
    except Exception as exc:
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
    token = None
    while token is None:
        try:
            token = authenticate()
        except Exception as exc:
            LOG.warning(
                "Passerelle non enrôlée (%s); nouvel essai dans %s s",
                str(exc)[:80],
                ENROLLMENT_RETRY_SECONDS,
            )
            time.sleep(ENROLLMENT_RETRY_SECONDS)
    LOG.info("Passerelle EtR %s démarrée pour %s", BRIDGE_VERSION, INSTALLATION_ID)
    while True:
        payload = read_local_state()
        try:
            publish(token, payload)
            LOG.info("Données publiées à %s", payload["updated_at"])
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                token = authenticate(force=True)
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
