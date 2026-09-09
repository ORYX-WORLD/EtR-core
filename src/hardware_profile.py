"""Persistent selection of expected hardware, independent of observed measurements."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile

DEFAULT_PROFILE_FILE = "/var/lib/etr-core/hardware-profile.json"


class ProfileConflict(ValueError):
    pass


def profile_path() -> Path:
    return Path(os.getenv("ETR_HARDWARE_PROFILE_FILE", DEFAULT_PROFILE_FILE))


def default_profile() -> dict:
    # Preserve installed ADC systems until an operator explicitly changes them.
    return {"schema_version": 1, "revision": 0,
            "ads1263": {"enabled": True}, "modbus": {"enabled": False, "serial_port": ""}}


def validate_profile(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "revision", "ads1263", "modbus"}:
        raise ValueError("Configuration matérielle invalide.")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("Version de configuration non prise en charge.")
    if type(raw["revision"]) is not int or not 0 <= raw["revision"] < 2**53 - 1:
        raise ValueError("Révision invalide.")
    for key, fields in (("ads1263", {"enabled"}), ("modbus", {"enabled", "serial_port"})):
        if not isinstance(raw[key], dict) or set(raw[key]) != fields or type(raw[key]["enabled"]) is not bool:
            raise ValueError("Chaque matériel doit être explicitement activé ou désactivé.")
    port = raw["modbus"]["serial_port"]
    if not isinstance(port, str) or len(port) > 240 or (port and not re.fullmatch(
        r"/dev/(?:tty(?:USB|ACM)[0-9]+|serial/by-id/[A-Za-z0-9_:+.\-]+)", port
    )):
        raise ValueError("Port attendu : /dev/serial/by-id/… ou /dev/ttyUSB0.")
    return json.loads(json.dumps(raw))


def read_profile(path: Path | None = None) -> dict:
    try:
        raw = json.loads((path or profile_path()).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_profile()
    return validate_profile(raw)


@contextmanager
def profile_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def save_profile(raw: object, path: Path | None = None) -> dict:
    candidate = validate_profile(raw)
    path = path or profile_path()
    with profile_lock(path):
        current = read_profile(path)
        if candidate["revision"] != current["revision"]:
            raise ProfileConflict("La configuration a changé. Rechargez avant d’enregistrer.")
        candidate["revision"] += 1
        fd, name = tempfile.mkstemp(prefix=".hardware-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(candidate, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return candidate


def empty_payload(profile: dict, status: str = "disabled") -> dict:
    return {"schema_version": "1.1", "source": "hardware-profile", "updated_at": None,
            "hardware": {"adc": "ADS1263", "status": status, "chip_id": None},
            "hardware_profile": profile, "sensors": [], "measurements": {}, "states": {}, "alerts": []}


def attach_profile(payload: dict, profile: dict) -> dict:
    payload["hardware_profile"] = profile
    modbus = profile["modbus"]
    status, message = "disabled", "Modbus désactivé"
    if modbus["enabled"]:
        port = modbus["serial_port"]
        if not port:
            status, message = "not_configured", "Modbus activé : sélectionner le port du convertisseur."
        elif not Path(port).exists():
            status, message = "unavailable", "Convertisseur Modbus attendu mais absent sur le port configuré."
        else:
            status, message = "pending_validation", "Port Modbus présent ; collecte et réponse des régulateurs non validées."
        payload["alerts"].append({"code": "MODBUS_" + status.upper(), "severity": "warning", "message": message})
    payload["hardware"]["modbus"] = {"enabled": modbus["enabled"], "status": status, "message": message}
    return payload


if __name__ == "__main__":
    print("true" if read_profile()["ads1263"]["enabled"] else "false")
