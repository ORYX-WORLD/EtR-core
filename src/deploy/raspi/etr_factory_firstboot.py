#!/usr/bin/env python3
"""Enregistre une carte fabriquée lors du premier démarrage du nouvel EtR."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any

import requests

try:
    from src.device_identity import ensure_device_keypair
except ModuleNotFoundError:
    from device_identity import ensure_device_keypair

STATE_DIR = Path("/var/lib/etr-core")
TICKET_FILE = STATE_DIR / "factory-ticket.json"
PRIVATE_KEY = STATE_DIR / "bootstrap-private.pem"
PUBLIC_KEY = STATE_DIR / "bootstrap-public.pem"
ENV_FILE = Path("/etc/etr-core/firebase-bridge.env")


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


def redeem(ticket_data: dict[str, Any], serial: str, public_key: str) -> dict[str, Any]:
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
            "hostname": socket.gethostname(),
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
    return value


def main() -> int:
    if not TICKET_FILE.exists():
        return 0
    if os.geteuid() != 0:
        raise SystemExit("Le premier démarrage EtR doit s'exécuter en root")
    initialize_machine_identity()
    serial = raspberry_serial()
    set_hostname(serial)
    ensure_device_keypair(PRIVATE_KEY, PUBLIC_KEY)
    os.chown(PRIVATE_KEY, 1000, 1000)
    os.chown(PUBLIC_KEY, 1000, 1000)
    os.chmod(PRIVATE_KEY, 0o600)
    os.chmod(PUBLIC_KEY, 0o644)
    result = redeem(load_json(TICKET_FILE), serial, PUBLIC_KEY.read_text(encoding="ascii"))
    (STATE_DIR / "factory-bootstrap-result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    os.chown(STATE_DIR / "factory-bootstrap-result.json", 1000, 1000)
    os.chmod(STATE_DIR / "factory-bootstrap-result.json", 0o600)
    TICKET_FILE.unlink()
    subprocess.run(["/usr/bin/systemctl", "restart", "etr-firebase-bridge.service"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
