#!/usr/bin/env python3
"""Récupération bornée du lecteur avant la phase de copie de la fabrique EtR.

Contrairement à la reprise rsync, une perte USB pendant l'effacement, le
partitionnement ou le formatage ne peut pas reprendre au dernier octet : la
préparation doit repartir depuis l'effacement. Ce module conserve néanmoins le
même travail de fabrication, attend le retour du lecteur sur le même port USB,
réduit les délais SCSI et relance proprement la préparation depuis le début.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class PreparationRecoveryError(RuntimeError):
    """Le support n'a pas pu être récupéré avant la copie."""


@dataclass(frozen=True)
class PhysicalTargetIdentity:
    disk_path: str
    disk_size: int
    usb_node: str
    vendor_id: str
    product_id: str
    serial: str
    model: str


Progress = Callable[[str], None]


_USB_LOSS_PATTERNS = re.compile(
    r"device offlined|not ready after error recovery|rejecting i/o to offline device|"
    r"i/o error|input/output error|buffer i/o error|capacity change from .* to 0|"
    r"access beyond end of device|reset (?:high|super)-speed usb device",
    re.IGNORECASE,
)
_USB_DEVICE_NODE_RE = re.compile(r"^\d+-\d+(?:\.\d+)*$")


def _run(command: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _output(command: list[str], *, timeout: int = 10) -> str:
    try:
        result = _run(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _disk_size(path: str) -> int:
    value = _output(["/usr/sbin/blockdev", "--getsize64", path], timeout=5)
    return int(value) if value.isdigit() else 0


def _is_usb_device_node(path: Path) -> bool:
    return (
        _USB_DEVICE_NODE_RE.fullmatch(path.name) is not None
        and (path / "idVendor").is_file()
        and (path / "idProduct").is_file()
    )


def _usb_node_name_from_devpath(devpath: str) -> str:
    """Extrait le dernier nœud USB physique d'un chemin udev de bloc."""
    for component in reversed(Path(str(devpath or "")).parts):
        if _USB_DEVICE_NODE_RE.fullmatch(component):
            return component
    return ""


def _node_from_ancestors(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if _is_usb_device_node(candidate):
            return candidate
    return None


def _usb_device_node(disk_path: str) -> Path | None:
    """Retrouve le nœud USB même lorsque le lien SCSI masque ses ancêtres."""
    name = os.path.basename(os.path.realpath(disk_path))
    start = Path("/sys/class/block") / name / "device"
    try:
        resolved = start.resolve(strict=True)
    except OSError:
        resolved = None
    if resolved is not None:
        direct = _node_from_ancestors(resolved)
        if direct is not None:
            return direct

    # Sur certains lecteurs usb-storage, /sys/class/block/.../device se résout
    # seulement jusqu'au périphérique SCSI. Le DEVPATH udev conserve alors le
    # chemin complet, par exemple .../usb1/1-1/1-1.1/1-1.1:1.0/.../block/sda.
    devpath = _output(
        ["/usr/bin/udevadm", "info", "--query=path", "--name", disk_path],
        timeout=8,
    )
    if devpath:
        sys_path = Path("/sys") / devpath.lstrip("/")
        try:
            resolved_udev = sys_path.resolve(strict=True)
        except OSError:
            resolved_udev = sys_path
        from_udev = _node_from_ancestors(resolved_udev)
        if from_udev is not None:
            return from_udev

        node_name = _usb_node_name_from_devpath(devpath)
        if node_name:
            candidate = Path("/sys/bus/usb/devices") / node_name
            if _is_usb_device_node(candidate):
                return candidate

    # Dernier repli : compare le chemin canonique de chaque nœud USB avec le
    # DEVPATH du disque. Cela reste borné aux périphériques déjà présents.
    for candidate in Path("/sys/bus/usb/devices").glob("*"):
        if not _is_usb_device_node(candidate):
            continue
        try:
            candidate_real = str(candidate.resolve(strict=True))
        except OSError:
            continue
        if resolved is not None and str(resolved).startswith(candidate_real + os.sep):
            return candidate
        if devpath and str((Path("/sys") / devpath.lstrip("/"))).startswith(candidate_real + os.sep):
            return candidate
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii", errors="ignore").strip()
    except OSError:
        return ""


def inspect_physical_target(disk_path: str) -> PhysicalTargetIdentity:
    """Capture l'identité physique avant toute écriture destructive."""
    real = os.path.realpath(disk_path)
    size = _disk_size(real)
    node = _usb_device_node(real)
    if not real.startswith("/dev/") or size <= 0 or node is None:
        devpath = _output(
            ["/usr/bin/udevadm", "info", "--query=path", "--name", real],
            timeout=8,
        )
        raise PreparationRecoveryError(
            "Le lecteur USB cible n'est pas identifiable "
            f"(disque={real or 'absent'}, capacité={size}, devpath={devpath or 'absent'})"
        )
    model = _output(["/usr/bin/lsblk", "-ndo", "MODEL", real]).strip()
    identity = PhysicalTargetIdentity(
        disk_path=real,
        disk_size=size,
        usb_node=node.name,
        vendor_id=_read(node / "idVendor").lower(),
        product_id=_read(node / "idProduct").lower(),
        serial=_read(node / "serial"),
        model=model,
    )
    if not identity.usb_node or not identity.vendor_id or not identity.product_id:
        raise PreparationRecoveryError("Identité USB du lecteur incomplète")
    return identity


def configure_conservative_transport(disk_path: str) -> None:
    """Réduit la taille des requêtes et le délai d'attente du lecteur instable."""
    name = os.path.basename(os.path.realpath(disk_path))
    settings = (
        (Path("/sys/block") / name / "queue/max_sectors_kb", "64\n"),
        (Path("/sys/block") / name / "queue/read_ahead_kb", "128\n"),
        (Path("/sys/class/block") / name / "device/timeout", "15\n"),
        (Path("/sys/class/block") / name / "device/queue_depth", "1\n"),
    )
    for path, value in settings:
        try:
            path.write_text(value, encoding="ascii")
        except OSError:
            # Certains lecteurs usb-storage n'exposent pas toutes les options.
            continue


def kernel_indicates_transport_loss(disk_path: str, *, since_epoch: int) -> bool:
    """Distingue un décrochage USB d'une erreur logique ordinaire."""
    name = os.path.basename(os.path.realpath(disk_path))
    try:
        result = _run(
            [
                "/usr/bin/journalctl",
                "-k",
                "--since",
                f"@{max(0, int(since_epoch))}",
                "--no-pager",
                "-o",
                "cat",
            ],
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    lines = [line for line in (result.stdout + "\n" + result.stderr).splitlines() if name in line]
    return bool(lines and _USB_LOSS_PATTERNS.search("\n".join(lines)))


def _reset_usb_node(identity: PhysicalTargetIdentity) -> None:
    node = Path("/sys/bus/usb/devices") / identity.usb_node
    authorized = node / "authorized"
    try:
        authorized.write_text("0\n", encoding="ascii")
        time.sleep(2)
        authorized.write_text("1\n", encoding="ascii")
        time.sleep(3)
        return
    except OSError:
        pass

    unbind = Path("/sys/bus/usb/drivers/usb/unbind")
    bind = Path("/sys/bus/usb/drivers/usb/bind")
    try:
        unbind.write_text(identity.usb_node, encoding="ascii")
        time.sleep(2)
        bind.write_text(identity.usb_node, encoding="ascii")
        time.sleep(3)
    except OSError:
        return


def _trigger_scan() -> None:
    try:
        _run(["/usr/bin/udevadm", "settle"], timeout=8)
    except (OSError, subprocess.SubprocessError):
        pass
    for host in Path("/sys/class/scsi_host").glob("host*"):
        scan = host / "scan"
        try:
            scan.write_text("- - -\n", encoding="ascii")
        except OSError:
            continue


def _candidate_disks() -> list[str]:
    result: list[str] = []
    for entry in sorted(Path("/sys/class/block").iterdir()):
        name = entry.name
        if not re.fullmatch(r"sd[a-z]+", name):
            continue
        path = f"/dev/{name}"
        if _output(["/usr/bin/lsblk", "-ndo", "TYPE", path]) != "disk":
            continue
        if _output(["/usr/bin/lsblk", "-ndo", "TRAN", path]) != "usb":
            continue
        result.append(path)
    return result


def _matches(identity: PhysicalTargetIdentity, disk_path: str) -> bool:
    if _disk_size(disk_path) != identity.disk_size:
        return False
    try:
        candidate = inspect_physical_target(disk_path)
    except PreparationRecoveryError:
        return False
    # Le lecteur testé s'est déjà présenté sous deux couples VID:PID après un
    # reset. Le port USB physique et la capacité sont donc les identifiants
    # principaux. Le numéro de série reste une barrière supplémentaire lorsqu'il
    # est réellement exposé par les deux énumérations.
    if candidate.usb_node != identity.usb_node:
        return False
    if identity.serial and candidate.serial and candidate.serial != identity.serial:
        return False
    return True


def recover_physical_target(
    identity: PhysicalTargetIdentity,
    *,
    progress: Progress,
    attempt: int,
    maximum: int,
    timeout_seconds: int = 90,
    stable_seconds: int = 5,
) -> str:
    """Attend le même lecteur sur le même port puis retourne son nouveau /dev/sdX."""
    progress(
        f"Pause USB avant copie : communication perdue — tentative {attempt}/{maximum}, "
        f"récupération du lecteur pendant {timeout_seconds} s…"
    )
    _reset_usb_node(identity)
    deadline = time.monotonic() + timeout_seconds
    stable_path = ""
    stable_count = 0
    last_bucket = -1

    while time.monotonic() < deadline:
        _trigger_scan()
        matches = [path for path in _candidate_disks() if _matches(identity, path)]
        if len(matches) == 1:
            current = matches[0]
            if current == stable_path:
                stable_count += 1
            else:
                stable_path = current
                stable_count = 1
            if stable_count >= stable_seconds:
                configure_conservative_transport(current)
                progress(
                    f"Reprise de la préparation depuis l'effacement — tentative {attempt}/{maximum}…"
                )
                return current
        else:
            stable_path = ""
            stable_count = 0

        remaining = max(0, int(deadline - time.monotonic()))
        bucket = remaining // 5
        if bucket != last_bucket:
            last_bucket = bucket
            progress(
                f"Pause USB avant copie : attente du lecteur sur le même port — "
                f"tentative {attempt}/{maximum}, reste {remaining} s…"
            )
        time.sleep(1)

    raise PreparationRecoveryError(
        f"Le lecteur n'est pas revenu de façon stable dans les {timeout_seconds} secondes"
    )
