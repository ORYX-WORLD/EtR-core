#!/usr/bin/env python3
"""Reprise contrôlée d'une copie microSD après une perte USB transitoire.

La reprise ne tente jamais de continuer aveuglément sur un montage devenu
incohérent. Elle arrête rsync, attend le retour du même support, contrôle les
identifiants de partition, exécute fsck hors montage, remonte la partition puis
laisse rsync comparer et reprendre les fichiers déjà présents.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable


class UsbTargetLost(RuntimeError):
    """Le support cible a disparu pendant une écriture."""

    def __init__(self, identity: "TargetIdentity", detail: str):
        super().__init__(detail)
        self.identity = identity
        self.detail = detail


class UsbRecoveryError(RuntimeError):
    """Le support n'a pas pu être remis dans un état sûr pour la reprise."""


@dataclass(frozen=True)
class TargetIdentity:
    mountpoint: str
    partition_path: str
    disk_path: str
    disk_size: int
    filesystem: str
    uuid: str
    partuuid: str
    ptuuid: str
    partition_number: int
    label: str


Progress = Callable[[str], None]


def _run(
    command: list[str],
    *,
    timeout: int = 10,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _output(command: list[str], *, timeout: int = 10) -> str:
    try:
        completed = _run(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _blkid(device: str, field: str) -> str:
    return _output(["/usr/sbin/blkid", "-s", field, "-o", "value", device])


def _parent_disk(partition: str) -> str:
    parent = _output(["/usr/bin/lsblk", "-ndo", "PKNAME", partition])
    if parent:
        return f"/dev/{parent}"
    kind = _output(["/usr/bin/lsblk", "-ndo", "TYPE", partition])
    return partition if kind == "disk" else ""


def _partition_number(partition: str) -> int:
    value = _output(["/usr/bin/lsblk", "-ndo", "PARTN", partition])
    if value.isdigit():
        return int(value)
    match = re.search(r"(?:p)?(\d+)$", partition)
    return int(match.group(1)) if match else 0


def _disk_size(disk: str) -> int:
    value = _output(["/usr/sbin/blockdev", "--getsize64", disk], timeout=5)
    return int(value) if value.isdigit() else 0


def inspect_target(destination: str) -> TargetIdentity:
    """Capture les identifiants stables et le vrai point de montage cible."""
    requested_path = str(Path(destination).resolve())
    mountpoint = _output(
        ["/usr/bin/findmnt", "-n", "-o", "TARGET", "--target", requested_path]
    )
    partition = _output(
        ["/usr/bin/findmnt", "-n", "-o", "SOURCE", "--target", requested_path]
    )
    filesystem = _output(
        ["/usr/bin/findmnt", "-n", "-o", "FSTYPE", "--target", requested_path]
    )
    if not mountpoint.startswith("/"):
        raise UsbRecoveryError(f"Point de montage cible introuvable pour {requested_path}")
    mountpoint = str(Path(mountpoint).resolve())
    if not partition.startswith("/dev/"):
        raise UsbRecoveryError(f"Partition cible introuvable pour {mountpoint}")
    partition = os.path.realpath(partition)
    disk = _parent_disk(partition)
    size = _disk_size(disk)
    identity = TargetIdentity(
        mountpoint=mountpoint,
        partition_path=partition,
        disk_path=disk,
        disk_size=size,
        filesystem=filesystem,
        uuid=_blkid(partition, "UUID"),
        partuuid=_blkid(partition, "PARTUUID"),
        ptuuid=_blkid(disk, "PTUUID"),
        partition_number=_partition_number(partition),
        label=_blkid(partition, "LABEL"),
    )
    if not identity.disk_path or identity.disk_size <= 0:
        raise UsbRecoveryError("Disque parent ou capacité de la microSD introuvable")
    if not identity.uuid or not identity.partuuid or not identity.ptuuid:
        raise UsbRecoveryError(
            "Identité de partition incomplète : UUID, PARTUUID et PTUUID sont requis pour une reprise sûre"
        )
    if identity.partition_number <= 0:
        raise UsbRecoveryError("Numéro de partition cible introuvable")
    if identity.filesystem not in {"ext2", "ext3", "ext4", "vfat", "fat", "msdos"}:
        raise UsbRecoveryError(f"Système de fichiers non pris en charge : {identity.filesystem}")
    return identity


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    deadline = time.monotonic() + 3
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def monitor_target(
    process: subprocess.Popen[bytes],
    identity: TargetIdentity,
    stop_event: threading.Event,
    failure: dict[str, str],
    *,
    check_seconds: float = 1.0,
) -> None:
    """Surveille la capacité et arrête le groupe rsync au premier décrochage."""
    while process.poll() is None and not stop_event.wait(check_seconds):
        size = _disk_size(identity.disk_path)
        if size == identity.disk_size:
            continue
        failure["reason"] = (
            "Communication USB perdue pendant l'écriture "
            f"(capacité attendue {identity.disk_size}, capacité lue {size or 'indisponible'})."
        )
        _terminate_process_group(process)
        return


def _flatten_lsblk(nodes: list[dict]) -> list[dict]:
    result: list[dict] = []

    def visit(node: dict, parent: str = "") -> None:
        current = dict(node)
        children = current.pop("children", []) or []
        current.setdefault("pkname", parent)
        result.append(current)
        for child in children:
            visit(child, str(current.get("name") or ""))

    for node in nodes:
        visit(node)
    return result


def _partition_from_disk(disk: str, number: int) -> str:
    try:
        completed = _run(
            ["/usr/bin/lsblk", "-J", "-o", "NAME,PATH,TYPE,PKNAME,PARTN", disk],
            timeout=8,
        )
        nodes = _flatten_lsblk(json.loads(completed.stdout).get("blockdevices", []))
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    for node in nodes:
        if str(node.get("type") or "") != "part":
            continue
        if int(node.get("partn") or 0) == number:
            return str(node.get("path") or "")
    return ""


def _candidate_matches(identity: TargetIdentity, partition: str) -> bool:
    if not partition.startswith("/dev/") or not Path(partition).exists():
        return False
    disk = _parent_disk(partition)
    if not disk or _disk_size(disk) != identity.disk_size:
        return False
    if _blkid(disk, "PTUUID") != identity.ptuuid:
        return False
    candidate_uuid = _blkid(partition, "UUID")
    candidate_partuuid = _blkid(partition, "PARTUUID")
    return candidate_uuid == identity.uuid or candidate_partuuid == identity.partuuid


def resolve_reconnected_partition(identity: TargetIdentity) -> str:
    """Retrouve le même support même si Linux lui attribue un autre /dev/sdX."""
    by_uuid = _output(["/usr/sbin/blkid", "-U", identity.uuid], timeout=6)
    if _candidate_matches(identity, by_uuid):
        return os.path.realpath(by_uuid)

    by_partuuid = _output(
        ["/usr/sbin/blkid", "-t", f"PARTUUID={identity.partuuid}", "-o", "device"],
        timeout=6,
    ).splitlines()
    for partition in by_partuuid:
        if _candidate_matches(identity, partition.strip()):
            return os.path.realpath(partition.strip())

    disks = _output(
        ["/usr/sbin/blkid", "-t", f"PTUUID={identity.ptuuid}", "-o", "device"],
        timeout=6,
    ).splitlines()
    for disk in disks:
        disk = os.path.realpath(disk.strip())
        if _disk_size(disk) != identity.disk_size:
            continue
        partition = _partition_from_disk(disk, identity.partition_number)
        if _candidate_matches(identity, partition):
            return os.path.realpath(partition)
    return ""


def _trigger_storage_scan() -> None:
    try:
        _run(["/usr/bin/udevadm", "settle"], timeout=8)
    except (OSError, subprocess.SubprocessError):
        pass
    for host in Path("/sys/class/scsi_host").glob("host*"):
        scan = host / "scan"
        if not scan.exists():
            continue
        try:
            scan.write_text("- - -\n", encoding="ascii")
        except OSError:
            pass


def _unmount_source(partition: str) -> None:
    targets = _output(
        ["/usr/bin/findmnt", "-rn", "-S", partition, "-o", "TARGET"],
        timeout=8,
    ).splitlines()
    for target in sorted((item.strip() for item in targets if item.strip()), reverse=True):
        try:
            _run(["/usr/bin/umount", target], timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        if _output(["/usr/bin/findmnt", "-rn", "-M", target], timeout=5):
            try:
                _run(["/usr/bin/umount", "-l", target], timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass


def _detach_stale_mount(mountpoint: str) -> None:
    if not _output(["/usr/bin/findmnt", "-rn", "-M", mountpoint], timeout=5):
        return
    try:
        _run(["/usr/bin/umount", "-l", mountpoint], timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def _filesystem_check(partition: str, filesystem: str) -> None:
    if filesystem in {"ext2", "ext3", "ext4"}:
        command = ["/usr/sbin/e2fsck", "-p", "-f", partition]
        accepted_mask = 1 | 2
    else:
        command = ["/usr/sbin/fsck.vfat", "-a", partition]
        accepted_mask = 1
    try:
        completed = _run(command, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise UsbRecoveryError("Le contrôle du système de fichiers a dépassé 10 minutes") from exc
    except OSError as exc:
        raise UsbRecoveryError(f"Outil de contrôle absent : {command[0]}") from exc

    # fsck utilise des codes binaires : 1/2 indiquent des corrections appliquées.
    fatal_bits = completed.returncode & ~(accepted_mask)
    if fatal_bits:
        detail = " ".join(
            line.strip()
            for line in (completed.stdout + "\n" + completed.stderr).splitlines()[-8:]
            if line.strip()
        )
        raise UsbRecoveryError(
            f"Système de fichiers non récupérable automatiquement (code {completed.returncode})"
            + (f" : {detail[:400]}" if detail else "")
        )


def recover_target_mount(
    identity: TargetIdentity,
    *,
    progress: Progress,
    attempt: int,
    maximum: int,
    timeout_seconds: int = 90,
    stable_seconds: int = 5,
) -> TargetIdentity:
    """Attend le retour du support, contrôle son intégrité et le remonte."""
    progress(
        f"Pause USB : communication perdue — tentative {attempt}/{maximum}, "
        f"attente du retour du lecteur pendant {timeout_seconds} s…"
    )
    _detach_stale_mount(identity.mountpoint)
    deadline = time.monotonic() + timeout_seconds
    stable = 0
    candidate = ""
    last_announcement = -1

    while time.monotonic() < deadline:
        if int(deadline - time.monotonic()) % 10 == 0:
            _trigger_storage_scan()
        resolved = resolve_reconnected_partition(identity)
        if resolved:
            disk = _parent_disk(resolved)
            if _disk_size(disk) == identity.disk_size:
                if candidate == resolved:
                    stable += 1
                else:
                    candidate = resolved
                    stable = 1
                if stable >= stable_seconds:
                    break
            else:
                candidate = ""
                stable = 0
        else:
            candidate = ""
            stable = 0

        remaining = max(0, int(deadline - time.monotonic()))
        bucket = remaining // 5
        if bucket != last_announcement:
            last_announcement = bucket
            progress(
                f"Pause USB : attente du même support — tentative {attempt}/{maximum}, "
                f"reste {remaining} s…"
            )
        time.sleep(1)
    else:
        raise UsbRecoveryError(
            f"Le même lecteur et la même microSD ne sont pas revenus dans les {timeout_seconds} secondes"
        )

    _unmount_source(candidate)
    progress(
        f"Contrôle du système de fichiers après reconnexion USB — tentative {attempt}/{maximum}…"
    )
    _filesystem_check(candidate, identity.filesystem)

    mountpoint = Path(identity.mountpoint)
    mountpoint.mkdir(parents=True, exist_ok=True)
    mounted = _run(
        ["/usr/bin/mount", "-o", "noatime", candidate, str(mountpoint)],
        timeout=30,
    )
    if mounted.returncode != 0:
        detail = (mounted.stderr or mounted.stdout or "").strip()
        raise UsbRecoveryError(
            "Remontage de la partition impossible"
            + (f" : {detail[:300]}" if detail else "")
        )

    refreshed = inspect_target(str(mountpoint))
    if (
        refreshed.uuid != identity.uuid
        or refreshed.partuuid != identity.partuuid
        or refreshed.ptuuid != identity.ptuuid
        or refreshed.disk_size != identity.disk_size
    ):
        _detach_stale_mount(identity.mountpoint)
        raise UsbRecoveryError("Le support revenu ne correspond pas à la microSD initiale")

    progress(
        f"Reprise de la copie après reconnexion USB — tentative {attempt}/{maximum}…"
    )
    return replace(refreshed, mountpoint=identity.mountpoint)
