#!/usr/bin/env python3
"""Moteur persistant de fabrication microSD, indépendant de l'interface graphique."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    # Applique au moteur partagé la copie rsync optimisée et ses exclusions.
    from src.deploy.raspi import etr_sd_factory_fast as _fast  # noqa: F401
    from src.deploy.raspi import etr_sd_factory_core as core
    from src.deploy.raspi.etr_sd_factory_diagnostics import explain_creation_error
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


def _handle_cancel(_signum: int, _frame: object) -> None:
    global _cancel_requested
    _cancel_requested = True
    raise FactoryCancelled("Annulation demandée par l'utilisateur")


def _find_requested_disk(request: dict[str, Any]) -> core.Disk:
    device = str(request.get("device") or "").strip()
    if not device.startswith("/dev/"):
        raise core.FactoryError("Le périphérique demandé est invalide")
    devices = core.lsblk_data()
    source = core.source_disk(devices)
    candidates = core.candidate_disks(devices, source)
    for disk in candidates:
        if disk.path == device:
            expected_size = int(request.get("size_bytes") or 0)
            if expected_size and disk.size != expected_size:
                raise core.FactoryError("La carte détectée ne correspond plus à la carte sélectionnée")
            return disk
    raise core.FactoryError("La microSD sélectionnée n'est plus présente dans le lecteur USB")


def _cleanup_mounts(device: str) -> None:
    try:
        core.unmount_disk(device)
    except Exception:
        pass
    try:
        subprocess.run(["/usr/bin/sync"], check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    mount_root = Path(core.MOUNT_ROOT)
    if mount_root.exists():
        for path in sorted(mount_root.glob("job-*/*"), reverse=True):
            try:
                subprocess.run(["/usr/bin/umount", str(path)], check=False, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
        for path in sorted(mount_root.glob("job-*"), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass


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

    def publish(message: str) -> None:
        nonlocal state
        if _cancel_requested:
            raise FactoryCancelled("Annulation demandée par l'utilisateur")
        update = progress_from_message(message)
        state = {**state, **update, "active": True}
        write_state(state)

    try:
        disk = _find_requested_disk(request)
        state.update({"device": disk.path, "disk_label": disk.label, "size_bytes": disk.size})
        write_state(state)
        core.prepare_card(disk, copy_wifi=copy_wifi, progress=publish)
    except FactoryCancelled as exc:
        state = terminal_state(
            state,
            status="cancelled",
            message="Fabrication annulée. La carte a été démontée et doit être relancée depuis le début.",
            error=str(exc),
        )
        write_state(state)
        return 0
    except Exception as exc:  # le détail est conservé dans l'état persistant
        message = explain_creation_error(exc, device)
        state = terminal_state(
            state,
            status="failed",
            message=message,
            error=f"{type(exc).__name__}: {exc}",
        )
        write_state(state)
        return 1
    else:
        state = terminal_state(
            state,
            status="ready",
            message="Carte EtR vérifiée et démontée. Vous pouvez la retirer puis l'insérer dans le nouvel EtR.",
        )
        state.update({"ready_to_remove": True, "verification": "passed"})
        write_state(state)
        return 0
    finally:
        _cleanup_mounts(device)
        try:
            REQUEST_PATH.unlink()
        except FileNotFoundError:
            pass
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
