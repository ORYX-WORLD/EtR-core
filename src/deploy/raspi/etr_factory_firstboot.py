#!/usr/bin/env python3
"""Enregistre une carte fabriquée lors du premier démarrage du nouvel EtR."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

try:
    from src.device_identity import ensure_device_keypair
except ModuleNotFoundError:
    from device_identity import ensure_device_keypair

STATE_DIR = Path("/var/lib/etr-core")
TICKET_FILE = STATE_DIR / "factory-ticket.json"
AUTH_FILE = STATE_DIR / "firebase-auth.json"
RESULT_FILE = STATE_DIR / "factory-bootstrap-result.json"
DISPLAY_RESULT_FILE = STATE_DIR / "headless-display-result.json"
PRIVATE_KEY = STATE_DIR / "bootstrap-private.pem"
PUBLIC_KEY = STATE_DIR / "bootstrap-public.pem"


def normalize_serial(value: str) -> str:
    serial = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if len(serial) < 8:
        raise RuntimeError("Numéro de série Raspberry invalide")
    return serial[-64:]


def raspberry_serial() -> str:
    for name in ("/sys/firmware/devicetree/base/serial-number", "/proc/device-tree/serial-number"):
        try:
            value = Path(name).read_bytes().replace(b"\x00", b"").decode("ascii").strip()
            if value:
                return normalize_serial(value)
        except (OSError, UnicodeDecodeError):
            pass
    for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("serial"):
            return normalize_serial(line.split(":", 1)[1])
    raise RuntimeError("Numéro de série Raspberry introuvable")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Ticket de fabrication invalide")
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chown(temporary, 1000, 1000)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def set_hostname(serial: str) -> str:
    hostname = f"etr-{serial[-8:].lower()}"
    Path("/etc/hostname").write_text(hostname + "\n", encoding="ascii")
    hosts = Path("/etc/hosts")
    if hosts.exists():
        text = hosts.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^127\.0\.1\.1\s+", text):
            text = re.sub(r"(?m)^(127\.0\.1\.1\s+)\S+", rf"\1{hostname}", text)
        else:
            text += f"\n127.0.1.1\t{hostname}\n"
        hosts.write_text(text, encoding="utf-8")
    subprocess.run(["/usr/bin/hostnamectl", "set-hostname", hostname], check=False)
    return hostname


def initialize_machine_identity() -> None:
    machine_id = Path("/etc/machine-id")
    if not machine_id.exists() or not machine_id.read_text(encoding="ascii", errors="ignore").strip():
        subprocess.run(["/usr/bin/systemd-machine-id-setup"], check=True)
    subprocess.run(["/usr/bin/ssh-keygen", "-A"], check=False)


def ensure_headless_display_runtime() -> dict[str, Any]:
    """Garantit un écran :1 même quand aucun écran physique n'est raccordé."""
    physical_screen = Path("/dev/fb1").exists()
    result: dict[str, Any] = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "physicalScreen": physical_screen,
        "mode": "spi" if physical_screen else "headless",
        "xvfbInstalled": Path("/usr/bin/Xvfb").exists(),
        "packageInstallAttempted": False,
        "packageInstallSucceeded": False,
        "error": None,
    }
    if physical_screen or result["xvfbInstalled"]:
        result["packageInstallSucceeded"] = True
        atomic_json(DISPLAY_RESULT_FILE, result)
        return result

    apt_get = Path("/usr/bin/apt-get")
    if not apt_get.exists():
        result["error"] = "apt-get absent et Xvfb non installé"
        atomic_json(DISPLAY_RESULT_FILE, result)
        return result

    result["packageInstallAttempted"] = True
    try:
        subprocess.run(
            [str(apt_get), "update"],
            check=True,
            timeout=300,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        subprocess.run(
            [str(apt_get), "install", "-y", "--no-install-recommends", "xvfb"],
            check=True,
            timeout=300,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        result["xvfbInstalled"] = Path("/usr/bin/Xvfb").exists()
        result["packageInstallSucceeded"] = bool(result["xvfbInstalled"])
        if not result["xvfbInstalled"]:
            result["error"] = "Le paquet xvfb a été installé sans fournir /usr/bin/Xvfb"
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)[:300]
    atomic_json(DISPLAY_RESULT_FILE, result)
    return result


def redeem(ticket_data: dict[str, Any], serial: str, public_key: str, hostname: str) -> dict[str, Any]:
    origin = str(ticket_data.get("gatewayOrigin") or "").rstrip("/")
    ticket = str(ticket_data.get("ticket") or "")
    if not origin.startswith("https://") or not re.fullmatch(r"[A-Za-z0-9_-]{40,120}", ticket):
        raise RuntimeError("Ticket de fabrication incomplet")
    response = requests.post(
        f"{origin}/api/enrollment/factory-bootstrap",
        json={
            "ticket": ticket,
            "serial": serial,
            "installationId": f"etr-{serial[-12:].lower()}",
            "publicKey": public_key,
            "hostname": hostname,
        },
        timeout=20,
    )
    if not response.ok:
        try:
            detail = response.json().get("error") or response.json().get("code")
        except ValueError:
            detail = response.text[:180]
        raise RuntimeError(f"Enregistrement usine refusé (HTTP {response.status_code}) : {detail}")
    value = response.json()
    if value.get("status") not in {"registered", "already_registered"}:
        raise RuntimeError("Réponse d'enregistrement usine invalide")
    if str(value.get("idToken") or "").count(".") != 2 or len(str(value.get("refreshToken") or "")) < 40:
        raise RuntimeError("Session Firebase usine absente ou invalide")
    return value


def save_factory_session(result: dict[str, Any]) -> None:
    atomic_json(
        AUTH_FILE,
        {
            "idToken": str(result["idToken"]),
            "refreshToken": str(result["refreshToken"]),
        },
    )
    safe_result = {
        key: value
        for key, value in result.items()
        if key not in {"idToken", "refreshToken"}
    }
    atomic_json(RESULT_FILE, safe_result)


def restart_etr_runtime() -> None:
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=False)
    for unit in (
        "etr-firebase-bridge.service",
        "spi-desktop.service",
        "etr-wifi-portal.service",
        "etr-kiosk.service",
        "etr-vnc.service",
        "etr-remote-screen.service",
    ):
        subprocess.run(["/usr/bin/systemctl", "enable", unit], check=False)
        subprocess.run(["/usr/bin/systemctl", "restart", unit], check=False)


def main() -> int:
    if not TICKET_FILE.exists():
        return 0
    if os.geteuid() != 0:
        raise SystemExit("Le premier démarrage EtR doit s'exécuter en root")
    initialize_machine_identity()
    serial = raspberry_serial()
    hostname = set_hostname(serial)
    ensure_device_keypair(PRIVATE_KEY, PUBLIC_KEY)
    os.chown(PRIVATE_KEY, 1000, 1000)
    os.chown(PUBLIC_KEY, 1000, 1000)
    os.chmod(PRIVATE_KEY, 0o600)
    os.chmod(PUBLIC_KEY, 0o644)
    result = redeem(
        load_json(TICKET_FILE),
        serial,
        PUBLIC_KEY.read_text(encoding="ascii"),
        hostname,
    )
    save_factory_session(result)
    ensure_headless_display_runtime()
    TICKET_FILE.unlink()
    restart_etr_runtime()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
