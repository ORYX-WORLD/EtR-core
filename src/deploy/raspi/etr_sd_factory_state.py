#!/usr/bin/env python3
"""État persistant et atomique de la fabrique microSD EtR."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_PATH = Path(os.getenv("ETR_SD_FACTORY_STATE", "/var/lib/etr-core/sd-factory-state.json"))
REQUEST_PATH = Path(os.getenv("ETR_SD_FACTORY_REQUEST", "/var/lib/etr-core/sd-factory-request.json"))
LOCK_PATH = Path(os.getenv("ETR_SD_FACTORY_LOCK", "/run/lock/etr-sd-factory.lock"))

TERMINAL_STATUSES = {"ready", "failed", "cancelled", "interrupted"}
RUNNING_STATUSES = {
    "validating",
    "ticket",
    "unmounting",
    "partitioning",
    "formatting",
    "mounting",
    "copying_root",
    "copying_boot",
    "paused_usb",
    "checking_filesystem",
    "resuming_copy",
    "configuring",
    "verifying",
    "syncing",
    "finalizing",
}

_STAGE_PROGRESS = {
    "validating": 2.0,
    "ticket": 5.0,
    "unmounting": 8.0,
    "partitioning": 12.0,
    "formatting": 18.0,
    "mounting": 22.0,
    "copying_root": 25.0,
    "copying_boot": 82.0,
    "paused_usb": 25.0,
    "checking_filesystem": 25.0,
    "resuming_copy": 25.0,
    "configuring": 90.0,
    "verifying": 95.0,
    "syncing": 98.0,
    "finalizing": 99.0,
    "ready": 100.0,
    "failed": 0.0,
    "cancelled": 0.0,
    "interrupted": 0.0,
}

_COPY_RE = re.compile(
    r"Copie (?P<part>système EtR|démarrage)\s*:\s*(?P<percent>\d{1,3})\s*%\s*[—-]\s*"
    r"(?P<speed>\S+)\s*[—-]\s*reste\s*(?P<eta>\S+)",
    re.IGNORECASE,
)
_USB_ATTEMPT_RE = re.compile(r"tentative\s+(?P<attempt>\d+)\s*/\s*(?P<maximum>\d+)", re.IGNORECASE)
_USB_REMAINING_RE = re.compile(r"reste\s+(?P<seconds>\d+)\s*s", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_state() -> dict[str, Any]:
    return read_json(STATE_PATH)


def write_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = dict(payload)
    state["updated_at"] = utc_now()
    _atomic_json(STATE_PATH, state)
    return state


def write_request(payload: dict[str, Any]) -> None:
    request = dict(payload)
    request["requested_at"] = utc_now()
    _atomic_json(REQUEST_PATH, request)


def read_request() -> dict[str, Any]:
    return read_json(REQUEST_PATH)


def initial_state(*, job_id: str, device: str, disk_label: str, copy_wifi: bool) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": "1.0",
        "job_id": job_id,
        "status": "validating",
        "active": True,
        "stage": "Validation de la carte",
        "message": "Validation du support USB avant effacement…",
        "progress_percent": _STAGE_PROGRESS["validating"],
        "device": device,
        "disk_label": disk_label,
        "copy_wifi": bool(copy_wifi),
        "speed": None,
        "eta": None,
        "usb_recovery_attempt": 0,
        "usb_recovery_max": None,
        "resume_count": 0,
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "error": None,
    }


def _usb_attempt_fields(text: str) -> dict[str, Any]:
    match = _USB_ATTEMPT_RE.search(text)
    if not match:
        return {}
    return {
        "usb_recovery_attempt": int(match.group("attempt")),
        "usb_recovery_max": int(match.group("maximum")),
    }


def progress_from_message(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    match = _COPY_RE.search(text)
    if match:
        percent = max(0, min(100, int(match.group("percent"))))
        is_boot = match.group("part").lower() == "démarrage"
        if is_boot:
            status = "copying_boot"
            overall = 82.0 + percent * 0.06
            stage = "Copie de la partition de démarrage"
        else:
            status = "copying_root"
            overall = 25.0 + percent * 0.55
            stage = "Copie du système EtR"
        return {
            "status": status,
            "stage": stage,
            "message": text,
            "progress_percent": round(min(88.0, overall), 1),
            "speed": match.group("speed"),
            "eta": match.group("eta"),
        }

    lowered = text.lower()
    if lowered.startswith("pause usb"):
        remaining = _USB_REMAINING_RE.search(text)
        return {
            "status": "paused_usb",
            "stage": "Pause USB — attente de reconnexion",
            "message": text,
            "speed": None,
            "eta": f"{remaining.group('seconds')} s" if remaining else "reconnexion",
            "_preserve_progress": True,
            **_usb_attempt_fields(text),
        }
    if lowered.startswith("contrôle du système de fichiers"):
        return {
            "status": "checking_filesystem",
            "stage": "Contrôle d'intégrité après reconnexion",
            "message": text,
            "speed": None,
            "eta": "contrôle en cours",
            "_preserve_progress": True,
            **_usb_attempt_fields(text),
        }
    if lowered.startswith("reprise de la copie"):
        fields = _usb_attempt_fields(text)
        return {
            "status": "resuming_copy",
            "stage": "Reprise progressive de la copie",
            "message": text,
            "speed": None,
            "eta": "recalcul en cours",
            "resume_count": fields.get("usb_recovery_attempt", 0),
            "_preserve_progress": True,
            **fields,
        }

    mapping = (
        (("ticket", "autorisation de fabrication"), "ticket", "Autorisation de fabrication"),
        (("démontage",), "unmounting", "Démontage du support"),
        (("effacement", "partitionnement"), "partitioning", "Effacement et partitionnement"),
        (("formatage", "création du système de fichiers"), "formatting", "Formatage de la microSD"),
        (("montage",), "mounting", "Montage des partitions"),
        (("copie résiliente", "copie du système"), "copying_root", "Copie du système EtR"),
        (("copie de la partition", "copie du démarrage"), "copying_boot", "Copie du démarrage"),
        (("configuration", "nettoyage", "identité", "wi-fi"), "configuring", "Configuration du nouvel EtR"),
        (("vérification",), "verifying", "Vérification de la carte"),
        (("synchronisation", "sync"), "syncing", "Synchronisation des écritures"),
        (("démontage final", "carte prête"), "finalizing", "Finalisation et démontage"),
    )
    for needles, status, stage in mapping:
        if any(needle in lowered for needle in needles):
            return {
                "status": status,
                "stage": stage,
                "message": text,
                "progress_percent": _STAGE_PROGRESS[status],
                "speed": None,
                "eta": None,
            }
    return {"message": text}


def terminal_state(
    current: dict[str, Any],
    *,
    status: str,
    message: str,
    error: str | None = None,
) -> dict[str, Any]:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"Statut terminal invalide: {status}")
    result = dict(current)
    result.update(
        {
            "status": status,
            "active": False,
            "stage": {
                "ready": "Carte EtR prête",
                "failed": "Fabrication en échec",
                "cancelled": "Fabrication annulée",
                "interrupted": "Fabrication interrompue",
            }[status],
            "message": message,
            "progress_percent": _STAGE_PROGRESS[status],
            "speed": None,
            "eta": None,
            "finished_at": utc_now(),
            "error": error,
        }
    )
    return result
