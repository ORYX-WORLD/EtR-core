#!/usr/bin/env python3
"""Moteur persistant de fabrication microSD, indépendant de l'interface graphique."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

try:
    # Applique au moteur partagé la copie rsync optimisée et ses exclusions.
    from src.deploy.raspi import etr_sd_factory_fast as _fast  # noqa: F401
    from src.deploy.raspi import etr_sd_factory_core as core
    from src.deploy.raspi.etr_sd_factory_diagnostics import explain_creation_error
    from src.deploy.raspi.etr_sd_factory_preparation_recovery import (
        PreparationRecoveryError,
        configure_conservative_transport,
        inspect_physical_target,
        kernel_indicates_transport_loss,
        recover_physical_target,
    )
    from src.deploy.raspi.etr_sd_factory_state import (
        LOCK_PATH,
        REQUEST_PATH,
        initial_state,
        progress_from_message,
        read_request,
        terminal_state,
        write_state,
    )
except ModuleNotFoundError:
    import etr_sd_factory_fast as _fast  # noqa: F401
    import etr_sd_factory_core as core
    from etr_sd_factory_diagnostics import explain_creation_error
    from etr_sd_factory_preparation_recovery import (
        PreparationRecoveryError,
        configure_conservative_transport,
        inspect_physical_target,
        kernel_indicates_transport_loss,
        recover_physical_target,
    )
    from etr_sd_factory_state import (
        LOCK_PATH,
        REQUEST_PATH,
        initial_state,
        progress_from_message,
        read_request,
        terminal_state,
        write_state,
    )


class FactoryCancelled(RuntimeError):
    pass


_cancel_requested = False
MAX_PRECOPY_USB_RECOVERIES = max(
    0,
    min(4, int(os.getenv("ETR_SD_MAX_PRECOPY_USB_RECOVERIES", "2"))),
)
PRECOPY_RECOVERY_TIMEOUT_SECONDS = max(
    20,
    min(300, int(os.getenv("ETR_SD_PRECOPY_RECOVERY_TIMEOUT_SECONDS", "90"))),
)
PRECOPY_STABLE_SECONDS = max(
    3,
    min(15, int(os.getenv("ETR_SD_PRECOPY_STABLE_SECONDS", "5"))),
)
PRECOPY_STATUSES = {
    "validating",
    "ticket",
    "unmounting",
    "partitioning",
    "formatting",
    "mounting",
    "paused_usb_setup",
    "restarting_preparation",
}


def _handle_cancel(_signum: int, _frame: object) -> None:
    global _cancel_requested
    _cancel_requested = True
    raise FactoryCancelled("Annulation demandée par l'utilisateur")


def _candidate_disks() -> list[core.Disk]:
    devices = core.lsblk_data()
    source = core.source_disk(devices)
    return core.candidate_disks(devices, source)


def _find_requested_disk(request: dict[str, Any]) -> core.Disk:
    device = str(request.get("device") or "").strip()
    if not device.startswith("/dev/"):
        raise core.FactoryError("Le périphérique demandé est invalide")
    expected_size = int(request.get("size_bytes") or 0)
    for disk in _candidate_disks():
        if disk.path != device:
            continue
        if expected_size and disk.size != expected_size:
            raise core.FactoryError("La carte détectée ne correspond plus à la carte sélectionnée")
        return disk
    raise core.FactoryError("La microSD sélectionnée n'est plus présente dans le lecteur USB")


def _find_recovered_disk(path: str, expected_size: int) -> core.Disk:
    real = os.path.realpath(path)
    matches = [
        disk
        for disk in _candidate_disks()
        if os.path.realpath(disk.path) == real and disk.size == expected_size
    ]
    if len(matches) != 1:
        raise core.FactoryError(
            "Le lecteur est revenu, mais la microSD ne peut pas être identifiée de façon sûre"
        )
    return matches[0]


def _remaining_mounts(device: str) -> list[str]:
    if not device.startswith("/dev/"):
        return []
    remaining: list[str] = []
    for number in (1, 2):
        partition = core.partition_path(device, number)
        completed = subprocess.run(
            ["/usr/bin/findmnt", "-rn", "-S", partition, "-o", "TARGET"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        remaining.extend(line.strip() for line in completed.stdout.splitlines() if line.strip())
    return sorted(set(remaining))


def _cleanup_mounts(device: str) -> list[str]:
    """Synchronise et démonte uniquement le support cible de la fabrique."""
    try:
        subprocess.run(["/usr/bin/sync"], check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass

    if device.startswith("/dev/"):
        try:
            core.unmount_target(device)
        except Exception:
            pass

    mount_root = Path(core.MOUNT_ROOT)
    if mount_root.exists():
        paths = sorted(
            (path for path in mount_root.glob("job-*/*") if path.name in {"root", "boot"}),
            key=lambda path: len(str(path)),
            reverse=True,
        )
        for path in paths:
            try:
                subprocess.run(["/usr/bin/umount", str(path)], check=False, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
        for path in sorted(mount_root.glob("job-*"), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass

    # Deuxième passage après le démontage des chemins connus, puis contrôle réel.
    if device.startswith("/dev/"):
        try:
            core.unmount_target(device)
        except Exception:
            pass
    try:
        subprocess.run(["/usr/bin/sync"], check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    return _remaining_mounts(device)


def _cleanup_warning(remaining: list[str]) -> str:
    return "Points de montage encore actifs : " + ", ".join(remaining)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("Le moteur de fabrication doit être lancé par systemd avec les droits root")

    signal.signal(signal.SIGINT, _handle_cancel)
    signal.signal(signal.SIGTERM, _handle_cancel)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_stream = LOCK_PATH.open("a+")
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Une fabrication microSD est déjà en cours")

    request = read_request()
    if not request:
        raise SystemExit(f"Demande de fabrication absente: {REQUEST_PATH}")

    device = str(request.get("device") or "")
    label = str(request.get("disk_label") or device)
    copy_wifi = bool(request.get("copy_wifi"))
    job_id = str(request.get("job_id") or uuid.uuid4())
    state = initial_state(job_id=job_id, device=device, disk_label=label, copy_wifi=copy_wifi)
    write_state(state)
    cleanup_completed = False

    def publish(message: str) -> None:
        nonlocal state
        if _cancel_requested:
            raise FactoryCancelled("Annulation demandée par l'utilisateur")
        update = progress_from_message(message)
        preserve_progress = bool(update.pop("_preserve_progress", False))
        current_progress = float(state.get("progress_percent") or 0)
        if preserve_progress:
            update["progress_percent"] = current_progress
        elif "progress_percent" in update:
            # Une relance rsync ou une reprise de préparation repart de zéro au
            # niveau local. La progression globale affichée reste monotone.
            update["progress_percent"] = max(
                current_progress,
                float(update.get("progress_percent") or 0),
            )
        state = {**state, **update, "active": True}
        write_state(state)

    try:
        disk = _find_requested_disk(request)
        physical_identity = inspect_physical_target(disk.path)
        state.update(
            {
                "device": disk.path,
                "disk_label": disk.label,
                "size_bytes": disk.size,
                "precopy_recovery_max": MAX_PRECOPY_USB_RECOVERIES,
            }
        )
        write_state(state)

        precopy_recoveries = 0
        while True:
            configure_conservative_transport(disk.path)
            attempt_started = max(0, int(time.time()) - 1)
            try:
                core.prepare_card(disk, copy_wifi=copy_wifi, progress=publish)
                break
            except FactoryCancelled:
                raise
            except Exception as exc:
                status = str(state.get("status") or "")
                recoverable = (
                    status in PRECOPY_STATUSES
                    and precopy_recoveries < MAX_PRECOPY_USB_RECOVERIES
                    and kernel_indicates_transport_loss(
                        disk.path,
                        since_epoch=attempt_started,
                    )
                )
                if not recoverable:
                    raise

                precopy_recoveries += 1
                try:
                    recovered_path = recover_physical_target(
                        physical_identity,
                        progress=publish,
                        attempt=precopy_recoveries,
                        maximum=MAX_PRECOPY_USB_RECOVERIES,
                        timeout_seconds=PRECOPY_RECOVERY_TIMEOUT_SECONDS,
                        stable_seconds=PRECOPY_STABLE_SECONDS,
                    )
                except PreparationRecoveryError as recovery_error:
                    raise core.FactoryError(
                        f"Récupération USB avant copie impossible : {recovery_error}"
                    ) from exc

                disk = _find_recovered_disk(
                    recovered_path,
                    physical_identity.disk_size,
                )
                device = disk.path
                state.update(
                    {
                        "device": disk.path,
                        "disk_label": disk.label,
                        "size_bytes": disk.size,
                        "precopy_recovery_attempt": precopy_recoveries,
                        "precopy_recovery_max": MAX_PRECOPY_USB_RECOVERIES,
                        "last_preparation_error": f"{type(exc).__name__}: {exc}",
                    }
                )
                write_state(state)
                # La préparation est volontairement relancée depuis l'effacement :
                # aucune partition n'est considérée fiable avant la phase rsync.
                continue

    except FactoryCancelled as exc:
        remaining = _cleanup_mounts(device)
        cleanup_completed = True
        message = "Fabrication annulée. La carte a été démontée et doit être relancée depuis le début."
        if remaining:
            message += " " + _cleanup_warning(remaining)
        state = terminal_state(
            state,
            status="cancelled" if not remaining else "failed",
            message=message,
            error=str(exc) if not remaining else _cleanup_warning(remaining),
        )
        state.update(
            {
                "ready_to_remove": False,
                "safe_to_remove": not remaining,
                "verification": "cancelled" if not remaining else "mounts_remaining",
            }
        )
        write_state(state)
        return 0 if not remaining else 1
    except Exception as exc:  # le détail est conservé dans l'état persistant
        remaining = _cleanup_mounts(device)
        cleanup_completed = True
        message = explain_creation_error(exc, device)
        if remaining:
            message += " " + _cleanup_warning(remaining)
        state = terminal_state(
            state,
            status="failed",
            message=message,
            error=f"{type(exc).__name__}: {exc}",
        )
        state.update(
            {
                "ready_to_remove": False,
                "safe_to_remove": not remaining,
                "verification": "failed" if not remaining else "mounts_remaining",
            }
        )
        write_state(state)
        return 1
    else:
        remaining = _cleanup_mounts(device)
        cleanup_completed = True
        if remaining:
            state = terminal_state(
                state,
                status="failed",
                message=(
                    "La copie est terminée, mais la carte n'a pas pu être démontée complètement. "
                    + _cleanup_warning(remaining)
                    + ". Ne la retirez pas."
                ),
                error=_cleanup_warning(remaining),
            )
            state.update(
                {
                    "ready_to_remove": False,
                    "safe_to_remove": False,
                    "verification": "mounts_remaining",
                }
            )
            write_state(state)
            return 1

        state = terminal_state(
            state,
            status="ready",
            message="Carte EtR vérifiée, synchronisée et démontée. Vous pouvez la retirer puis l'insérer dans le nouvel EtR.",
        )
        state.update(
            {
                "ready_to_remove": True,
                "safe_to_remove": True,
                "verification": "passed",
            }
        )
        write_state(state)
        return 0
    finally:
        if not cleanup_completed:
            _cleanup_mounts(device)
        try:
            REQUEST_PATH.unlink()
        except FileNotFoundError:
            pass
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
