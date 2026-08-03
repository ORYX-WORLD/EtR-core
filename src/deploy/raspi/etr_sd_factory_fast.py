#!/usr/bin/env python3
"""Copie microSD optimisée, mesurable et reprenable après une perte USB."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

try:
    from src.deploy.raspi import etr_sd_factory as interface
    from src.deploy.raspi import etr_sd_factory_core as core
    from src.deploy.raspi.etr_sd_factory_usb_resume import (
        TargetIdentity,
        UsbRecoveryError,
        UsbTargetLost,
        inspect_target,
        monitor_target,
        recover_target_mount,
    )
except ModuleNotFoundError:
    import etr_sd_factory as interface
    import etr_sd_factory_core as core
    from etr_sd_factory_usb_resume import (
        TargetIdentity,
        UsbRecoveryError,
        UsbTargetLost,
        inspect_target,
        monitor_target,
        recover_target_mount,
    )

_PROGRESS_PATTERN = re.compile(
    r"(?P<bytes>[0-9,]+)\s+(?P<percent>[0-9]{1,3})%\s+"
    r"(?P<speed>\S+/s)\s+(?P<remaining>[0-9]+:[0-9]{2}:[0-9]{2})"
)
_ERROR_PATTERN = re.compile(
    r"rsync:|permission denied|operation not permitted|operation not supported|"
    r"no such file|vanished|input/output|i/o error|no space left",
    re.IGNORECASE,
)
_IO_ERROR_PATTERN = re.compile(
    r"input/output error|i/o error|buffer i/o error|device or resource busy|"
    r"connection unexpectedly closed",
    re.IGNORECASE,
)
_CURRENT_CALLBACK: Callable[[str], None] | None = None
_CURRENT_STAGE = "système EtR"
REPOSITORY = Path("/home/oryx/EtR-core")
SNAPSHOT_ROOT = Path("/run/etr-sd-factory")
RSYNC_LOG = core.STATE_DIR / "sd-factory-rsync.log"
RETRYABLE_CODES = {23, 24}
USB_IO_ERROR_CODES = {10, 11, 12}
MAX_RSYNC_ATTEMPTS = 3
MAX_USB_RECOVERIES = max(0, min(4, int(os.getenv("ETR_SD_MAX_USB_RECOVERIES", "2"))))
USB_RECONNECT_TIMEOUT_SECONDS = max(
    20, min(300, int(os.getenv("ETR_SD_USB_RECONNECT_TIMEOUT_SECONDS", "90")))
)
USB_STABLE_SECONDS = max(3, min(15, int(os.getenv("ETR_SD_USB_STABLE_SECONDS", "5"))))
RSYNC_BWLIMIT_KB = max(512, min(8192, int(os.getenv("ETR_SD_RSYNC_BWLIMIT_KB", "2048"))))
_USB_RECOVERY_COUNT = 0

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
    "/usr/share/doc/***",
    "/usr/share/man/***",
    "/usr/share/info/***",
    "/var/lib/apt/lists/***",
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


def _stream_rsync(
    command: list[str],
    *,
    sequence: int,
    destination: str,
    identity: TargetIdentity,
) -> tuple[int, list[str]]:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    _append_log(f"\n=== lancement rsync {sequence} ===")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
        bufsize=0,
        start_new_session=True,
    )
    assert process.stdout is not None

    stop_event = threading.Event()
    failure: dict[str, str] = {}
    monitor = threading.Thread(
        target=monitor_target,
        args=(process, identity, stop_event, failure),
        name="etr-sd-target-monitor",
        daemon=True,
    )
    monitor.start()

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
            del error_lines[:-40]

    try:
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
    finally:
        stop_event.set()
        monitor.join(timeout=5)

    if failure.get("reason"):
        _append_log("=== support USB perdu : arrêt contrôlé de rsync ===")
        raise UsbTargetLost(identity, failure["reason"])

    _append_log(f"=== fin lancement rsync {sequence}: code {code} ===")
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


def _has_storage_io_error(lines: list[str]) -> bool:
    return any(_IO_ERROR_PATTERN.search(line) for line in lines)


def _recover_or_fail(identity: TargetIdentity) -> TargetIdentity:
    global _USB_RECOVERY_COUNT
    if MAX_USB_RECOVERIES <= 0 or _USB_RECOVERY_COUNT >= MAX_USB_RECOVERIES:
        raise core.FactoryError(
            f"Communication USB perdue : {MAX_USB_RECOVERIES} reprise(s) maximum déjà utilisée(s)."
        )
    _USB_RECOVERY_COUNT += 1
    if _CURRENT_CALLBACK is None:
        raise core.FactoryError("Callback de progression absent pendant la reprise USB")
    try:
        recovered = recover_target_mount(
            identity,
            progress=_CURRENT_CALLBACK,
            attempt=_USB_RECOVERY_COUNT,
            maximum=MAX_USB_RECOVERIES,
            timeout_seconds=USB_RECONNECT_TIMEOUT_SECONDS,
            stable_seconds=USB_STABLE_SECONDS,
        )
    except UsbRecoveryError as exc:
        raise core.FactoryError(f"Reprise USB impossible : {exc}") from exc
    _append_log(
        "REPRISE USB RÉUSSIE "
        f"{_USB_RECOVERY_COUNT}/{MAX_USB_RECOVERIES}: {recovered.partition_path}"
    )
    return recovered


def _run_rsync_with_recovery(command: list[str], destination: str) -> None:
    identity = inspect_target(destination)
    partial_attempt = 1
    sequence = 0

    while True:
        sequence += 1
        try:
            code, errors = _stream_rsync(
                command,
                sequence=sequence,
                destination=destination,
                identity=identity,
            )
        except UsbTargetLost as lost:
            _append_log("ERREUR SUPPORT: " + lost.detail)
            identity = _recover_or_fail(lost.identity)
            continue

        if code == 0:
            return

        if code in USB_IO_ERROR_CODES and _has_storage_io_error(errors):
            _append_log(
                f"Code rsync {code} associé à une erreur E/S : lancement de la reprise contrôlée."
            )
            identity = _recover_or_fail(identity)
            continue

        if code in RETRYABLE_CODES and partial_attempt < MAX_RSYNC_ATTEMPTS:
            partial_attempt += 1
            if _CURRENT_CALLBACK is not None:
                _CURRENT_CALLBACK(
                    f"Synchronisation finale : reprise automatique {partial_attempt}/{MAX_RSYNC_ATTEMPTS}…"
                )
            time.sleep(2)
            continue

        raise core.FactoryError(_concise_rsync_error(code, errors))


def _git_from_repository(*arguments: str) -> list[str]:
    """Exécute Git en root avec une autorisation limitée à ce dépôt précis."""
    return [
        "/usr/bin/git",
        "-c",
        f"safe.directory={REPOSITORY}",
        "-C",
        str(REPOSITORY),
        *arguments,
    ]


def _repository_snapshot() -> Path:
    """Extrait le commit installé sans setuid, clone local ni accès réseau."""
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="repo-snapshot-", dir=SNAPSHOT_ROOT))
    snapshot = directory / "EtR-core"
    archive = directory / "EtR-core.tar"
    try:
        snapshot.mkdir(mode=0o700)
        revision = subprocess.run(
            _git_from_repository("rev-parse", "HEAD"),
            check=True,
            text=True,
            capture_output=True,
            timeout=20,
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise core.FactoryError("Révision Git du banc invalide")

        with archive.open("wb") as output:
            completed = subprocess.run(
                _git_from_repository("archive", "--format=tar", revision),
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise core.FactoryError(
                "Création de l'archive EtR impossible : "
                + (detail[0] if detail else f"code {completed.returncode}")
            )

        extracted = subprocess.run(
            [
                "/usr/bin/tar",
                "--extract",
                "--file",
                str(archive),
                "--directory",
                str(snapshot),
                "--no-same-owner",
            ],
            text=True,
            capture_output=True,
            timeout=120,
        )
        if extracted.returncode:
            detail = (extracted.stderr or extracted.stdout or "").strip().splitlines()
            raise core.FactoryError(
                "Extraction de la version EtR impossible : "
                + (detail[0] if detail else f"code {extracted.returncode}")
            )

        archive.unlink(missing_ok=True)
        (snapshot / ".etr-source-revision").write_text(revision + "\n", encoding="ascii")
        return snapshot
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _base_rsync_command(source: str, destination: str) -> list[str]:
    return [
        "/usr/bin/rsync",
        "-aHAXx",
        "--numeric-ids",
        "--no-inc-recursive",
        "--no-whole-file",
        "--partial",
        "--partial-dir=.etr-rsync-partial",
        "--delete-delay",
        "--outbuf=L",
        f"--bwlimit={RSYNC_BWLIMIT_KB}",
        "--info=progress2",
        source,
        destination,
    ]


def _overlay_repository(snapshot: Path, destination: str) -> None:
    if _CURRENT_CALLBACK is not None:
        _CURRENT_CALLBACK("Configuration : installation de la version EtR figée pour cette carte…")
    target = Path(destination) / "home/oryx/EtR-core"
    target.mkdir(parents=True, exist_ok=True)
    command = _base_rsync_command(f"{snapshot}/", f"{target}/")
    _run_rsync_with_recovery(command, str(target))


def optimized_rsync_copy(
    source: str,
    destination: str,
    excludes: list[str] | None = None,
) -> None:
    global _USB_RECOVERY_COUNT
    patterns = list(excludes or [])
    snapshot: Path | None = None
    if source == "/":
        _USB_RECOVERY_COUNT = 0
        for pattern in ROOT_COPY_EXCLUDES:
            if pattern not in patterns:
                patterns.append(pattern)
        snapshot = _repository_snapshot()
        RSYNC_LOG.unlink(missing_ok=True)
        if _CURRENT_CALLBACK is not None:
            _CURRENT_CALLBACK(
                "Copie résiliente activée : débit limité à "
                f"{RSYNC_BWLIMIT_KB // 1024 or 1} Mio/s, "
                f"{MAX_USB_RECOVERIES} reprise(s) USB maximum."
            )

    command = _base_rsync_command(source, destination)
    insert_at = len(command) - 2
    for pattern in patterns:
        command[insert_at:insert_at] = ["--exclude", pattern]
        insert_at += 2

    try:
        _run_rsync_with_recovery(command, destination)
        partial = Path(destination) / ".etr-rsync-partial"
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)

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
