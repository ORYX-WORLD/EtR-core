#!/usr/bin/env python3
"""Lance la Fabrique EtR avec un preflight reseau auto-reparable.

Ce wrapper ne depend d'aucun SSID. Il teste la resolution DNS de la passerelle,
tente une reparation NetworkManager si necessaire, puis relance proprement la
demande de ticket avant de laisser la fabrique poursuivre.
"""
from __future__ import annotations

import socket
import subprocess
import time
from urllib.parse import urlparse

import requests

try:
    from src.deploy.raspi import etr_sd_factory_core as core
    from src.deploy.raspi import etr_sd_factory_fast as fast
except ModuleNotFoundError:
    import etr_sd_factory_core as core
    import etr_sd_factory_fast as fast

_ORIGINAL_REQUEST_FACTORY_TICKET = core.request_factory_ticket


def _host_from_origin(origin: str) -> str:
    host = urlparse(origin).hostname
    if not host:
        raise core.FactoryError("Adresse de passerelle EtR invalide")
    return host


def _dns_ok(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return True
    except socket.gaierror:
        return False


def _run_quiet(command: list[str]) -> None:
    try:
        subprocess.run(command, check=False, text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def _repair_network() -> None:
    # Utilise en priorite le service EtR commun, s'il est installe.
    if subprocess.run(
        ["/usr/bin/test", "-x", "/usr/local/bin/etr-network-resilience.sh"],
        check=False,
    ).returncode == 0:
        _run_quiet(["/usr/local/bin/etr-network-resilience.sh"])
        return

    # Repli generique : connexion active quelle qu'elle soit, sans SSID fige.
    try:
        active = subprocess.run(
            ["/usr/bin/nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return

    for line in active:
        if ":" not in line:
            continue
        name, device = line.rsplit(":", 1)
        if not device or device == "lo":
            continue
        _run_quiet([
            "/usr/bin/nmcli", "connection", "modify", name,
            "ipv4.ignore-auto-dns", "no",
        ])
        _run_quiet(["/usr/bin/nmcli", "device", "reapply", device])


def resilient_request_factory_ticket() -> dict:
    env = core.read_env()
    origin = core.gateway_origin(env)
    host = _host_from_origin(origin)

    last_error: Exception | None = None
    for attempt in range(1, 4):
        if not _dns_ok(host):
            _repair_network()
            time.sleep(2 * attempt)
        try:
            return _ORIGINAL_REQUEST_FACTORY_TICKET()
        except (requests.RequestException, socket.gaierror) as exc:
            last_error = exc
            _repair_network()
            time.sleep(2 * attempt)

    raise core.FactoryError(
        "Connexion Internet/DNS indisponible. EtR a tente une reparation automatique "
        "sans succes. Verifiez que le Raspberry est bien connecte a un reseau avec acces Internet."
    ) from last_error


core.request_factory_ticket = resilient_request_factory_ticket


if __name__ == "__main__":
    raise SystemExit(fast.interface.main())
