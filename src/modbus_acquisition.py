"""Read-only RTU collection. Configuration is separate from hardware selection."""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

try:
    from .hardware_profile import profile_lock, ProfileConflict, read_profile
    from .sensor_acquisition import atomic_write_json
except ImportError:
    from hardware_profile import profile_lock, ProfileConflict, read_profile
    from sensor_acquisition import atomic_write_json


def config_path():
    return Path(os.getenv("ETR_MODBUS_CONFIG_FILE", "/var/lib/etr-core/modbus-config.json"))


def default_config():
    return {"schema_version": 1, "revision": 0, "enabled": False,
            "bus_exclusive_confirmed": False, "installation_id": "", "networks": [], "points": []}


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Nombre entier Modbus hors limites.")
    return value


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) != set(default_config()):
        raise ValueError("Configuration de collecte invalide.")
    if raw["schema_version"] != 1 or type(raw["schema_version"]) is not int:
        raise ValueError("Version de collecte inconnue.")
    integer(raw["revision"], 0, 2**53-2)
    for field in ("enabled", "bus_exclusive_confirmed"):
        if type(raw[field]) is not bool:
            raise ValueError("Activation explicite attendue.")
    if not isinstance(raw["installation_id"], str) or len(raw["installation_id"]) > 100:
        raise ValueError("Installation invalide.")
    if not isinstance(raw["networks"], list) or len(raw["networks"]) > 1:
        raise ValueError("Cette collecte utilise un seul convertisseur RTU sélectionné.")
    if not isinstance(raw["points"], list) or len(raw["points"]) > 512:
        raise ValueError("512 points maximum.")
    networks = set()
    for n in raw["networks"]:
        if not isinstance(n, dict) or set(n) != {"id", "baud_rate", "parity", "stop_bits"}:
            raise ValueError("Paramètres RTU invalides.")
        if not isinstance(n["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", n["id"]):
            raise ValueError("Identifiant réseau invalide.")
        if n["baud_rate"] not in (1200,2400,4800,9600,19200,38400,57600,115200) or type(n["baud_rate"]) is not int:
            raise ValueError("Vitesse RTU invalide.")
        if n["parity"] not in ("N", "E", "O"):
            raise ValueError("Parité invalide.")
        integer(n["stop_bits"], 1, 2)
        networks.add(n["id"])
    seen = set()
    for p in raw["points"]:
        fields = {"id", "network_id", "slave", "address", "function", "data_type", "gain", "offset", "mask", "active_value", "name", "unit", "definition"}
        if not isinstance(p, dict) or set(p) != fields:
            raise ValueError("Définition de registre incomplète.")
        for key in ("id", "name", "unit", "definition"):
            if not isinstance(p[key], str) or len(p[key]) > 600:
                raise ValueError("Libellé de registre invalide.")
        if not p["id"].startswith("modbus:" + quote(raw["installation_id"], safe="-_.!~*'()") + ":") or p["id"] in seen:
            raise ValueError("Source dupliquée ou installation différente.")
        seen.add(p["id"])
        if p["network_id"] not in networks:
            raise ValueError("Réseau du registre absent.")
        integer(p["slave"], 1, 247)
        integer(p["address"], 0, 65535)
        if type(p["function"]) is not int or p["function"] not in (3, 4):
            raise ValueError("Seules les lectures 03 et 04 sont autorisées.")
        if p["data_type"] not in ("int16", "uint16", "bit"):
            raise ValueError("Type de registre non pris en charge.")
        for key in ("gain", "offset"):
            if type(p[key]) not in (int, float) or not math.isfinite(p[key]) or abs(p[key]) > 1e9:
                raise ValueError("Conversion invalide.")
        integer(p["mask"], 0, 65535)
        integer(p["active_value"], 0, 65535)
        if p["data_type"] == "bit" and (not p["mask"] or p["active_value"] & ~p["mask"]):
            raise ValueError("Masque ou valeur active invalide.")
    if raw["enabled"] and (not raw["bus_exclusive_confirmed"] or not raw["points"] or not raw["installation_id"]):
        raise ValueError("Configurer les registres et confirmer que le Raspberry est le seul maître du bus.")
    return json.loads(json.dumps(raw))


def read_config():
    try:
        return validate_config(json.loads(config_path().read_text(encoding="utf-8")))
    except FileNotFoundError:
        return default_config()


def save_config(raw):
    candidate = validate_config(raw)
    path = config_path()
    with profile_lock(path):
        if candidate["revision"] != read_config()["revision"]:
            raise ProfileConflict("La collecte a changé. Rechargez avant d’enregistrer.")
        candidate["revision"] += 1
        atomic_write_json(path, candidate)
    return candidate


def client_factory(port, network):
    from pymodbus.client import ModbusSerialClient
    return ModbusSerialClient(port, baudrate=network["baud_rate"], bytesize=8,
                              parity=network["parity"], stopbits=network["stop_bits"],
                              timeout=0.5, retries=0)


def decode(word, point):
    if point["data_type"] == "bit":
        return int((word & point["mask"]) == point["active_value"])
    signed = word - 65536 if point["data_type"] == "int16" and word >= 32768 else word
    return signed * point["gain"] + point["offset"]


def collect(payload, profile, factory=client_factory, sleep=time.sleep, monotonic=time.monotonic):
    payload["modbus"] = {"revision": None, "points": []}
    if not profile["modbus"]["enabled"]:
        return payload
    hardware = payload["hardware"]["modbus"]
    def state(status, message):
        hardware.update(status=status, message=message)
        payload["alerts"] = [a for a in payload["alerts"] if not a.get("code", "").startswith("MODBUS_")]
        if status != "online":
            payload["alerts"].append({"code": "MODBUS_" + status.upper(), "severity": "warning", "message": message})
    try:
        config = read_config()
        payload["modbus"]["revision"] = config["revision"]
    except (OSError, ValueError, TypeError):
        state("config_invalid", "Configuration de collecte illisible.")
        return payload
    if hardware["status"] in ("unavailable", "not_configured"):
        return payload
    if not config["enabled"]:
        state("not_configured", "Collecte arrêtée : configurer le réseau et les régulateurs, puis activer la lecture.")
        return payload
    if hardware["status"] in ("unavailable", "not_configured"):
        return payload
    start = monotonic()
    cache = {}
    failed_slaves = set()
    points = payload["modbus"]["points"]
    client = None
    try:
        client = factory(profile["modbus"]["serial_port"], config["networks"][0])
        if not client.connect():
            raise OSError("Port série inaccessible ou occupé.")
        for p in config["points"]:
            if read_config() != config or read_profile() != profile:
                payload["modbus"]["points"] = []
                state("applying", "Configuration modifiée ; nouveau cycle en préparation.")
                return payload
            row = {"id": p["id"], "definition": p["definition"], "unit": p["unit"], "name": p["name"], "status": "unavailable", "message": "Régulateur sans réponse."}
            key = (p["slave"], p["function"], p["address"])
            if key not in cache and p["slave"] not in failed_slaves and monotonic()-start < 8:
                # IC208 requires at least 60 ms; serialize all requests on the port.
                sleep(0.065)
                try:
                    read = client.read_holding_registers if p["function"] == 3 else client.read_input_registers
                    response = read(p["address"], count=1, slave=p["slave"])
                    if response.isError():
                        cache[key] = None
                        # Protocol exceptions are register-specific; missing replies stop this slave for the cycle.
                        if not getattr(response, "exception_code", None):
                            failed_slaves.add(p["slave"])
                    elif len(response.registers) == 1 and type(response.registers[0]) is int and 0 <= response.registers[0] <= 65535:
                        cache[key] = response.registers[0]
                    else:
                        cache[key] = None
                except Exception:
                    failed_slaves.add(p["slave"])
                    cache[key] = None
            word = cache.get(key)
            if word is not None:
                row.update(value=decode(word, p), raw=word, status="ok", message="Lecture Modbus", updated_at=datetime.now(timezone.utc).isoformat())
            elif key not in cache and p["slave"] not in failed_slaves:
                row["message"] = "Cycle de lecture trop long ; réduire les registres interrogés."
            points.append(row)
    except Exception:
        state("unavailable", "Port série inaccessible, occupé ou pilote indisponible.")
        return payload
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    valid = sum(p["status"] == "ok" for p in points)
    state("online" if valid == len(points) and valid else "partial" if valid else "unavailable",
          f"{valid}/{len(points)} valeurs lues" if valid else "Aucune réponse valide des régulateurs.")
    return payload
