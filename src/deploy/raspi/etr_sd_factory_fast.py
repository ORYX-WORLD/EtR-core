#!/usr/bin/env python3
"""Copie microSD optimisée, mesurable et cohérente malgré les mises à jour du banc."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

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
REPOSITORY = Path("/home/oryx/EtR-core")
SNAPSHOT_ROOT = Path("/run/etr-sd-factory")

# Ces chemins sont supprimés après la copie par scrub_clone. Les exclure dès le
# départ évite plusieurs gigaoctets d'écritures inutiles et réduit l'usure SD.
ROOT_COPY_EXCLUDES = (
    "/home/oryx/actions-runner/***",
    "/home/oryx/.cache/***",
    "/home/oryx/.config/chromium/***",
    "/home/oryx/.config/etr-kiosk-chromium/***",
    "/home/oryx/EtR-core/.git/***",
    "/home/oryx/EtR-core/**/__pycache__/***",
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


def _stream_rsync(command: list[str]) -> None:
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


def _repository_snapshot() -> Path:
    """Clone le commit installé afin qu'un git pull concurrent ne mélange pas les versions."""
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="repo-snapshot-", dir=SNAPSHOT_ROOT))
    snapshot = directory / "EtR-core"
    try:
        revision = subprocess.run(
            ["/usr/bin/git", "-C", str(REPOSITORY), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=20,
        ).stdout.strip()
        subprocess.run(
            [
                "/usr/bin/git",
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                str(REPOSITORY),
                str(snapshot),
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            ["/usr/bin/git", "-C", str(snapshot), "checkout", "--force", revision],
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        return snapshot
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _overlay_repository(snapshot: Path, destination: str) -> None:
    if _CURRENT_CALLBACK is not None:
        _CURRENT_CALLBACK("Configuration : installation de la version EtR figée pour cette carte…")
    target = Path(destination) / "home/oryx/EtR-core"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "/usr/bin/rsync",
            "-aHAX",
            "--numeric-ids",
            "--whole-file",
            f"{snapshot}/",
            f"{target}/",
        ],
        check=True,
        timeout=300,
    )


def optimized_rsync_copy(
    source: str,
    destination: str,
    excludes: list[str] | None = None,
) -> None:
    patterns = list(excludes or [])
    snapshot: Path | None = None
    if source == "/":
        for pattern in ROOT_COPY_EXCLUDES:
            if pattern not in patterns:
                patterns.append(pattern)
        snapshot = _repository_snapshot()

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

    try:
        _stream_rsync(command)
        if snapshot is not None:
            _overlay_repository(snapshot, destination)
    finally:
        if snapshot is not None:
            shutil.rmtree(snapshot.parent, ignore_errors=True)


# prepare_card résout ces fonctions dans le module core au moment de l'appel.
core.update_progress = tracked_progress
core.rsync_copy = optimized_rsync_copy


if __name__ == "__main__":
    raise SystemExit(interface.main())
