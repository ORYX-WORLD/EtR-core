#!/usr/bin/env python3
"""Diagnostic utilisateur des erreurs de fabrication de carte EtR."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any


def _command_text(error: BaseException) -> str:
    parts: list[str] = []
    command = getattr(error, "cmd", None)
    if command:
        if isinstance(command, (list, tuple)):
            parts.append(" ".join(str(item) for item in command))
        else:
            parts.append(str(command))
    for attribute in ("stderr", "stdout", "output"):
        value = getattr(error, attribute, None)
        if value:
            parts.append(str(value).strip())
    parts.append(str(error))
    return "\n".join(part for part in parts if part).strip()


def recent_kernel_messages(disk: str) -> str:
    """Retourne les messages noyau récents relatifs au disque, sans écrire dessus."""
    device = os.path.basename(os.path.realpath(disk))
    commands = [
        ["/usr/bin/journalctl", "-k", "--since", "-5 minutes", "--no-pager", "-o", "cat"],
        ["/usr/bin/dmesg", "--ctime"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        matching = [line for line in text.splitlines() if device and device in line]
        if matching:
            return "\n".join(matching[-80:])
    return ""


def explain_creation_error(
    error: BaseException,
    disk: str,
    *,
    kernel_text: str | None = None,
) -> str:
    """Transforme une erreur système en consigne courte et exploitable."""
    if error.__class__.__name__ == "FactoryError":
        return str(error)

    command_text = _command_text(error)
    kernel = recent_kernel_messages(disk) if kernel_text is None else kernel_text
    combined = f"{command_text}\n{kernel}".lower()

    medium_markers = (
        "critical medium error",
        "medium error",
        "input/output error",
        "i/o error",
        "buffer i/o error",
        "uncorrectable error",
    )
    if any(marker in combined for marker in medium_markers):
        sector = re.search(r"sector\s+(\d+)", combined)
        location = f" au secteur {sector.group(1)}" if sector else ""
        return (
            "La microSD est défectueuse : une erreur matérielle de lecture/écriture "
            f"a été détectée{location}. Remplacez cette carte ; ne la réutilisez pas "
            "pour un EtR."
        )

    if any(marker in combined for marker in ("read-only", "write protected", "write-protected")):
        return (
            "La microSD ou son adaptateur est en lecture seule. Vérifiez le verrou "
            "de l’adaptateur, retirez puis réinsérez la carte et recommencez."
        )

    if any(marker in combined for marker in ("is mounted", "device or resource busy", "apparently in use")):
        return (
            "La microSD est utilisée ou montée automatiquement par Linux. Fermez les "
            "fenêtres du gestionnaire de fichiers, retirez puis réinsérez la carte et recommencez."
        )

    detail = " ".join(command_text.split())
    if len(detail) > 320:
        detail = detail[:317] + "…"
    return f"La préparation de la microSD a échoué. Détail technique : {detail or 'erreur inconnue'}"
