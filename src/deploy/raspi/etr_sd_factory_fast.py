#!/usr/bin/env python3
"""Copie microSD optimisée, mesurable et cohérente malgré les mises à jour du banc."""

from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
import tempfile
import time
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
_ERROR_PATTERN = re.compile(
    r"rsync:|permission denied|operation not permitted|operation not supported|"
    r"no such file|vanished|input/output|no space left",
    re.IGNORECASE,
)
_CURRENT_CALLBACK: Callable[[str], None] | None = None
_CURRENT_STAGE = "système EtR"
REPOSITORY = Path("/home/oryx/EtR-core")
SNAPSHOT_ROOT = Path("/run/etr-sd-factory")
RSYNC_LOG = core.STATE_DIR / "sd-factory-rsync.log"
RETRYABLE_CODES = {23, 24}
MAX_RSYNC_ATTEMPTS = 3
GIT_OWNER = "oryx"
RUNUSER = Path("/usr/sbin/runuser")

# Ces chemins sont supprimés après la copie par scrub_clone, sont des montages
# de session ou changent en permanence. Les exclure dès le départ évite les
# écritures inutiles et les faux échecs d'une copie du système vivant.
ROOT_COPY_EXCLUDES = (
    "/home/oryx/actions-runner/***",
    "/home/oryx/.cache/***",
    "/home/oryx/.config/chromium/***",
    "/home/oryx/.config/etr-kiosk-chromium/***",
    "/home/oryx/.gvfs",
    "/home/oryx/.gvfs/***",
    "/home/oryx/.local/share/gvfs-metadata/***",
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


def _append_log(text: str) -> None:
    RSYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RSYNC_LOG.open("a", encoding="utf-8") as stream:
        stream.write(text.rstrip("\n") + "\n")
    os.chmod(RSYNC_LOG, 0o600)


def _stream_rsync(command: list[str], *, attempt: int) -> tuple[int, list[str]]:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    _append_log(f"\n=== tentative rsync {attempt}/{MAX_RSYNC_ATTEMPTS} ===")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
        bufsize=0,
    )
    assert process.stdout is not None

    error_lines: list[str] = []
    pending = bytearray()

    def consume(raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            return
        _append_log(line)
        _publish_rsync_progress(line)
        if _ERROR_PATTERN.search(line):
            error_lines.append(line)
            del error_lines[:-30]

    while True:
        chunk = process.stdout.read(4096)
        if not chunk:
            break
        for value in chunk:
            if value in (10, 13):
                if pending:
                    consume(bytes(pending))
                    pending.clear()
            else:
                pending.append(value)
    if pending:
        consume(bytes(pending))

    code = process.wait()
    _append_log(f"=== fin tentative {attempt}: code {code} ===")
    return code, error_lines


def _concise_rsync_error(code: int, lines: list[str]) -> str:
    relevant = [
        line
        for line in lines
        if not line.lower().startswith("rsync error:")
        and "some files/attrs were not transferred" not in line.lower()
    ]
    detail = relevant[0] if relevant else f"rsync a retourné le code {code}"
    detail = detail.replace("rsync: [sender] ", "").replace("rsync: [receiver] ", "")
    return (
        f"Copie incomplète (code rsync {code}) : {detail}. "
        f"Le diagnostic complet est conservé dans {RSYNC_LOG}."
    )


def _git_as_owner(*arguments: str) -> list[str]:
    if not RUNUSER.is_file():
        raise core.FactoryError(f"Commande système absente : {RUNUSER}")
    return [str(RUNUSER), "-u", GIT_OWNER, "--", "/usr/bin/git", *arguments]


def _repository_snapshot() -> Path:
    """Clone le commit installé sous l'identité qui possède réellement le dépôt."""
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="repo-snapshot-", dir=SNAPSHOT_ROOT))
    snapshot = directory / "EtR-core"
    try:
        owner = pwd.getpwnam(GIT_OWNER)
        os.chown(directory, owner.pw_uid, owner.pw_gid)
        os.chmod(directory, 0o700)
        revision = subprocess.run(
            _git_as_owner("-C", str(REPOSITORY), "rev-parse", "HEAD"),
            check=True,
            text=True,
            capture_output=True,
            timeout=20,
        ).stdout.strip()
        subprocess.run(
            _git_as_owner(
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                str(REPOSITORY),
                str(snapshot),
            ),
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            _git_as_owner("-C", str(snapshot), "checkout", "--force", revision),
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
    completed = subprocess.run(
        [
            "/usr/bin/rsync",
            "-aHAX",
            "--numeric-ids",
            "--whole-file",
            f"{snapshot}/",
            f"{target}/",
        ],
        text=True,
        capture_output=True,
        timeout=300,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise core.FactoryError(
            "Installation de la version EtR figée impossible : "
            + (detail[0] if detail else f"code {completed.returncode}")
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
        RSYNC_LOG.unlink(missing_ok=True)

    command = [
        "/usr/bin/rsync",
        "-aHAXx",
        "--numeric-ids",
        "--no-inc-recursive",
        "--whole-file",
        "--delete-delay",
        "--info=progress2",
    ]
    for pattern in patterns:
        command.extend(["--exclude", pattern])
    command.extend([source, destination])

    try:
        last_code = 0
        last_errors: list[str] = []
        for attempt in range(1, MAX_RSYNC_ATTEMPTS + 1):
            if attempt > 1 and _CURRENT_CALLBACK is not None:
                _CURRENT_CALLBACK(
                    f"Synchronisation finale : reprise automatique {attempt}/{MAX_RSYNC_ATTEMPTS}…"
                )
            last_code, last_errors = _stream_rsync(command, attempt=attempt)
            if last_code == 0:
                break
            if last_code not in RETRYABLE_CODES or attempt == MAX_RSYNC_ATTEMPTS:
                raise core.FactoryError(_concise_rsync_error(last_code, last_errors))
            time.sleep(2)

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
