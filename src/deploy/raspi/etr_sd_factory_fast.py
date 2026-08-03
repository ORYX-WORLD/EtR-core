#!/usr/bin/env python3
"""Lance la fabrique EtR avec copie rsync optimisée et progression exploitable."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable

try:
    from src.deploy.raspi import etr_sd_factory as interface
    from src.deploy.raspi import etr_sd_factory_core as core
except ModuleNotFoundError:
    import etr_sd_factory as interface
    import etr_sd_factory_core as core

_PROGRESS_PATTERN = re.compile(
    r"(?P<bytes>[0-9,]+)\s+(?P<percent>[0-9]{1,3})%\s+"
    r"(?P<speed>\S+/s)\s+(?P<remaining>[0-9]+:[0-9]{2}:[0-9]{2})"
)
_CURRENT_CALLBACK: Callable[[str], None] | None = None
_CURRENT_STAGE = "système EtR"

# Ces chemins sont supprimés après la copie par scrub_clone. Les exclure dès le
# départ évite plusieurs gigaoctets d'écritures inutiles et réduit l'usure SD.
ROOT_COPY_EXCLUDES = (
    "/home/oryx/actions-runner/***",
    "/home/oryx/.cache/***",
    "/home/oryx/.config/chromium/***",
    "/home/oryx/.config/etr-kiosk-chromium/***",
    "/var/lib/etr-core/***",
    "/var/cache/apt/archives/***",
    "/var/cache/man/***",
    "/var/log/journal/***",
    "/var/log/*.log",
    "/var/log/*/*.log",
)


def tracked_progress(callback: Callable[[str], None], message: str) -> None:
    global _CURRENT_CALLBACK, _CURRENT_STAGE
    _CURRENT_CALLBACK = callback
    if message.startswith("Copie du système"):
        _CURRENT_STAGE = "système EtR"
    elif message.startswith("Copie de la partition"):
        _CURRENT_STAGE = "démarrage"
    callback(message)


def _publish_rsync_progress(line: str) -> None:
    match = _PROGRESS_PATTERN.search(line)
    if match is None or _CURRENT_CALLBACK is None:
        return
    percent = min(100, int(match.group("percent")))
    speed = match.group("speed")
    remaining = match.group("remaining")
    _CURRENT_CALLBACK(
        f"Copie {_CURRENT_STAGE} : {percent} % — {speed} — reste {remaining}"
    )


def optimized_rsync_copy(
    source: str,
    destination: str,
    excludes: list[str] | None = None,
) -> None:
    patterns = list(excludes or [])
    if source == "/":
        for pattern in ROOT_COPY_EXCLUDES:
            if pattern not in patterns:
                patterns.append(pattern)

    command = [
        "/usr/bin/rsync",
        "-aHAXx",
        "--numeric-ids",
        "--no-inc-recursive",
        "--whole-file",
        "--info=progress2",
    ]
    for pattern in patterns:
        command.extend(["--exclude", pattern])
    command.extend([source, destination])

    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
        bufsize=0,
    )
    assert process.stdout is not None

    tail: list[str] = []
    pending = bytearray()
    while True:
        chunk = process.stdout.read(4096)
        if not chunk:
            break
        for value in chunk:
            if value in (10, 13):
                if not pending:
                    continue
                line = pending.decode("utf-8", errors="replace").strip()
                pending.clear()
                if line:
                    tail.append(line)
                    tail = tail[-20:]
                    _publish_rsync_progress(line)
            else:
                pending.append(value)
    if pending:
        line = pending.decode("utf-8", errors="replace").strip()
        if line:
            tail.append(line)
            _publish_rsync_progress(line)

    code = process.wait()
    if code:
        detail = " | ".join(tail[-3:]) or f"rsync a retourné le code {code}"
        raise core.FactoryError("Copie du système interrompue : " + detail)


# prepare_card résout ces fonctions dans le module core au moment de l'appel.
core.update_progress = tracked_progress
core.rsync_copy = optimized_rsync_copy


if __name__ == "__main__":
    raise SystemExit(interface.main())
