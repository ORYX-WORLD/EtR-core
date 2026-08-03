#!/usr/bin/env python3
"""Moteur sécurisé de fabrication de cartes microSD EtR."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

STATE_DIR = Path("/var/lib/etr-core")
AUTH_FILE = STATE_DIR / "firebase-auth.json"
ENV_FILE = Path("/etc/etr-core/firebase-bridge.env")
MOUNT_ROOT = Path("/run/etr-sd-factory")
MIN_TARGET_BYTES = 8 * 1024**3
BOOT_BYTES = 512 * 1024**2
FACTORY_TICKET_TTL = 7 * 24 * 60 * 60


class FactoryError(RuntimeError):
    """Erreur contrôlée affichable dans l'interface tactile."""


@dataclass(frozen=True)
class Disk:
    path: str
    size: int
    model: str
    transport: str
    removable: bool

    @property
    def label(self) -> str:
        model = self.model or "Lecteur microSD"
        return f"{model} — {human_size(self.size)} — {self.path}"


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if size < 1024 or unit == "Tio":
            return f"{size:.1f} {unit}" if unit != "o" else f"{int(size)} {unit}"
        size /= 1024
    return f"{value} o"


def run(
    command: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    capture: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        check=check,
        capture_output=capture,
        timeout=timeout,
    )


def flatten_lsblk(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], parent: str = "") -> None:
        item = dict(node)
        item.setdefault("pkname", parent)
        children = item.pop("children", []) or []
        result.append(item)
        for child in children:
            visit(child, str(item.get("name") or ""))

    for node in nodes:
        visit(node)
    return result


def lsblk_data() -> list[dict[str, Any]]:
    completed = run(
        [
            "/usr/bin/lsblk",
            "-J",
            "-b",
            "-o",
            "NAME,PATH,TYPE,SIZE,MODEL,TRAN,RM,MOUNTPOINTS,PKNAME,FSTYPE",
        ]
    )
    return flatten_lsblk(json.loads(completed.stdout).get("blockdevices", []))


def parent_disk_for(path: str, devices: list[dict[str, Any]]) -> str:
    by_name = {str(item.get("name") or ""): item for item in devices}
    by_path = {str(item.get("path") or ""): item for item in devices}
    current = by_path.get(path)
    if current is None:
        resolved = os.path.realpath(path)
        current = by_path.get(resolved)
    if current is None:
        raise FactoryError(f"Périphérique introuvable : {path}")
    seen: set[str] = set()
    while str(current.get("type")) != "disk":
        name = str(current.get("name") or "")
        if name in seen:
            raise FactoryError("Boucle détectée dans la topologie des disques")
        seen.add(name)
        parent_name = str(current.get("pkname") or "")
        current = by_name.get(parent_name)
        if current is None:
            raise FactoryError(f"Disque parent introuvable pour {path}")
    return str(current.get("path") or "")


def source_disk(devices: list[dict[str, Any]] | None = None) -> str:
    devices = devices or lsblk_data()
    root_source = run(["/usr/bin/findmnt", "-n", "-o", "SOURCE", "/"]).stdout.strip()
    return parent_disk_for(root_source, devices)


def candidate_disks(
    devices: list[dict[str, Any]], source: str
) -> list[Disk]:
    candidates: list[Disk] = []
    for item in devices:
        if str(item.get("type")) != "disk":
            continue
        path = str(item.get("path") or "")
        if not path or path == source or not path.startswith("/dev/"):
            continue
        transport = str(item.get("tran") or "").lower()
        removable = str(item.get("rm") or "0") in {"1", "true", "True"}
        size = int(item.get("size") or 0)
        if size < MIN_TARGET_BYTES:
            continue
        if transport != "usb" and not removable:
            continue
        candidates.append(
            Disk(
                path=path,
                size=size,
                model=str(item.get("model") or "").strip(),
                transport=transport,
                removable=removable,
            )
        )
    return sorted(candidates, key=lambda disk: disk.path)


def partition_path(disk: str, number: int) -> str:
    return f"{disk}p{number}" if disk[-1:].isdigit() else f"{disk}{number}"


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def gateway_origin(env: dict[str, str]) -> str:
    enrollment = env.get("FIREBASE_ENROLLMENT_URL", "").rstrip("/")
    if enrollment.endswith("/api/enrollment"):
        return enrollment[: -len("/api/enrollment")]
    remote = env.get("ETR_REMOTE_GATEWAY_WSS", "").strip()
    if remote.startswith("wss://"):
        return ("https://" + remote[6:].split("?", 1)[0]).removesuffix("/device").rstrip("/")
    raise FactoryError("Passerelle EtR non configurée dans /etc/etr-core/firebase-bridge.env")


def atomic_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, mode)
    temp.replace(path)


def load_auth() -> dict[str, Any]:
    try:
        value = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh_id_token(auth: dict[str, Any], env: dict[str, str]) -> str:
    refresh_token = str(auth.get("refreshToken") or "")
    api_key = env.get("FIREBASE_API_KEY", "")
    if len(refresh_token) < 40 or len(api_key) < 20:
        raise FactoryError("Session Firebase de l'EtR absente ou incomplète")
    response = requests.post(
        "https://securetoken.googleapis.com/v1/token",
        params={"key": api_key},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if not response.ok:
        raise FactoryError(f"Renouvellement de la session usine refusé (HTTP {response.status_code})")
    data = response.json()
    updated = {
        **auth,
        "idToken": str(data.get("id_token") or ""),
        "refreshToken": str(data.get("refresh_token") or refresh_token),
    }
    if updated["idToken"].count(".") != 2:
        raise FactoryError("Jeton Firebase renouvelé invalide")
    atomic_json(AUTH_FILE, updated)
    return updated["idToken"]


def request_factory_ticket() -> dict[str, Any]:
    env = read_env()
    origin = gateway_origin(env)
    auth = load_auth()
    token = str(auth.get("idToken") or "")
    if token.count(".") != 2:
        token = refresh_id_token(auth, env)

    def send(id_token: str) -> requests.Response:
        return requests.post(
            f"{origin}/api/enrollment/factory-ticket",
            headers={"Authorization": f"Bearer {id_token}", "Accept": "application/json"},
            json={"expiresIn": FACTORY_TICKET_TTL},
            timeout=20,
        )

    response = send(token)
    if response.status_code == 401:
        response = send(refresh_id_token(load_auth(), env))
    if not response.ok:
        try:
            detail = response.json().get("error") or response.json().get("code")
        except ValueError:
            detail = response.text[:180]
        raise FactoryError(f"Ticket de fabrication refusé (HTTP {response.status_code}) : {detail}")
    data = response.json()
    ticket = str(data.get("ticket") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,120}", ticket):
        raise FactoryError("Ticket de fabrication invalide")
    return {
        "version": 1,
        "ticket": ticket,
        "gatewayOrigin": origin,
        "expiresAt": str(data.get("expiresAt") or ""),
        "issuedAt": str(data.get("issuedAt") or ""),
    }


def active_wifi_profile() -> str:
    try:
        completed = run(
            ["/usr/bin/nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"]
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    for line in completed.stdout.splitlines():
        fields = line.split(":")
        if len(fields) >= 3 and fields[1] in {"wifi", "802-11-wireless"} and fields[2]:
            return fields[0].replace("\\:", ":")
    return ""


def wifi_profile_path(name: str) -> Path | None:
    if not name:
        return None
    try:
        filename = run(
            ["/usr/bin/nmcli", "-g", "GENERAL.FILENAME", "connection", "show", name]
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    path = Path(filename)
    return path if path.is_file() else None


def sanitize_wifi_keyfile(text: str) -> str:
    lines = []
    portable_keys = {"interface-name", "mac-address", "cloned-mac-address"}
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in portable_keys:
            continue
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def update_progress(callback: Callable[[str], None], message: str) -> None:
    callback(message)


def unmount_target(disk: str) -> None:
    completed = run(["/usr/bin/lsblk", "-nrpo", "NAME,MOUNTPOINTS", disk])
    for line in reversed(completed.stdout.splitlines()):
        fields = line.split(maxsplit=1)
        if len(fields) == 2 and fields[1].strip():
            run(["/usr/bin/umount", "-l", fields[0]], check=False)


def wait_for(path: str, timeout: int = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return
        time.sleep(0.25)
    raise FactoryError(f"Partition non détectée : {path}")


def partition_target(disk: str) -> tuple[str, str]:
    unmount_target(disk)
    run(["/usr/sbin/wipefs", "-a", disk])
    sectors = BOOT_BYTES // 512
    layout = (
        "label: dos\n"
        "unit: sectors\n\n"
        f"start=8192, size={sectors}, type=c, bootable\n"
        f"start={8192 + sectors}, type=83\n"
    )
    run(["/usr/sbin/sfdisk", "--wipe", "always", disk], input_text=layout)
    run(["/usr/sbin/partprobe", disk], check=False)
    run(["/usr/bin/udevadm", "settle"], check=False)
    boot = partition_path(disk, 1)
    root = partition_path(disk, 2)
    wait_for(boot)
    wait_for(root)
    run(["/usr/sbin/mkfs.vfat", "-F", "32", "-n", "bootfs", boot])
    run(["/usr/sbin/mkfs.ext4", "-F", "-L", "rootfs", root])
    return boot, root


def rsync_copy(source: str, destination: str, excludes: list[str] | None = None) -> None:
    command = [
        "/usr/bin/rsync",
        "-aHAXx",
        "--numeric-ids",
        "--delete-delay",
        "--info=progress2",
    ]
    for pattern in excludes or []:
        command.extend(["--exclude", pattern])
    command.extend([source, destination])
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    tail: list[str] = []
    for line in process.stdout:
        tail.append(line.rstrip())
        tail = tail[-20:]
    code = process.wait()
    if code:
        raise FactoryError("Copie du système interrompue : " + " | ".join(tail[-3:]))


def source_boot_mount() -> Path:
    for candidate in (Path("/boot/firmware"), Path("/boot")):
        if candidate.is_dir() and (candidate / "cmdline.txt").exists():
            return candidate
    raise FactoryError("Partition de démarrage Raspberry introuvable")


def blkid_value(device: str, field: str) -> str:
    value = run(["/usr/sbin/blkid", "-s", field, "-o", "value", device]).stdout.strip()
    if not value:
        raise FactoryError(f"{field} introuvable pour {device}")
    return value


def build_fstab(root_uuid: str, boot_uuid: str, boot_mount: str) -> str:
    return (
        "proc /proc proc defaults 0 0\n"
        f"UUID={boot_uuid} {boot_mount} vfat defaults 0 2\n"
        f"UUID={root_uuid} / ext4 defaults,noatime 0 1\n"
    )


def replace_cmdline_root(text: str, root_partuuid: str) -> str:
    replacement = f"root=PARTUUID={root_partuuid}"
    if re.search(r"(?:^|\s)root=\S+", text):
        return re.sub(r"(?<!\S)root=\S+", replacement, text, count=1).strip() + "\n"
    return (text.strip() + " " + replacement).strip() + "\n"


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def scrub_clone(root: Path) -> None:
    for relative in [
        "var/lib/etr-core",
        "home/oryx/actions-runner",
        "home/oryx/.cache",
        "home/oryx/.config/chromium",
        "home/oryx/.config/etr-kiosk-chromium",
        "home/oryx/.Xauthority",
        "home/oryx/.bash_history",
        "root/.bash_history",
        "var/lib/systemd/random-seed",
        "var/lib/dbus/machine-id",
    ]:
        remove_path(root / relative)

    for pattern in (
        "etc/ssh/ssh_host_*",
        "etc/systemd/system/actions.runner*.service",
        "etc/systemd/system/multi-user.target.wants/actions.runner*.service",
        "var/log/*.log",
        "var/log/*/*.log",
    ):
        for path in root.glob(pattern):
            if path.is_file() and pattern.startswith("var/log"):
                path.write_bytes(b"")
            else:
                remove_path(path)

    machine_id = root / "etc/machine-id"
    machine_id.parent.mkdir(parents=True, exist_ok=True)
    machine_id.write_text("", encoding="ascii")
    (root / "etc/hostname").write_text("etr-new\n", encoding="ascii")
    hosts = root / "etc/hosts"
    if hosts.exists():
        text = hosts.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"(?m)^(127\.0\.1\.1\s+)\S+", r"\1etr-new", text)
        hosts.write_text(text, encoding="utf-8")

    state = root / "var/lib/etr-core"
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    os.chown(state, 1000, 1000)

    profile_dir = root / "etc/NetworkManager/system-connections"
    profile_dir.mkdir(parents=True, exist_ok=True)
    for child in profile_dir.iterdir():
        remove_path(child)


def sanitize_target_environment(root: Path) -> None:
    env_path = root / "etc/etr-core/firebase-bridge.env"
    if not env_path.exists():
        raise FactoryError("Configuration Firebase EtR absente de l'image clonée")
    forbidden = {
        "ETR_ACTIVATION_CODE",
        "ETR_DEVICE_SERIAL",
        "ETR_INSTALLATION_ID",
        "FIREBASE_AUTH_EMAIL",
        "FIREBASE_AUTH_PASSWORD",
    }
    clean = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in forbidden:
            continue
        clean.append(line)
    env_path.write_text("\n".join(clean).rstrip() + "\n", encoding="utf-8")
    os.chmod(env_path, 0o640)


def install_wifi_profile(root: Path, profile: Path | None) -> None:
    if profile is None:
        return
    target_dir = root / "etc/NetworkManager/system-connections"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / profile.name
    target.write_text(sanitize_wifi_keyfile(profile.read_text(encoding="utf-8")), encoding="utf-8")
    os.chmod(target, 0o600)


def enable_firstboot(root: Path) -> None:
    unit = root / "etc/systemd/system/etr-factory-firstboot.service"
    if not unit.exists():
        raise FactoryError("Service de premier démarrage absent de l'image clonée")
    wants = root / "etc/systemd/system/multi-user.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    link = wants / unit.name
    link.unlink(missing_ok=True)
    link.symlink_to("../etr-factory-firstboot.service")


def write_ticket(root: Path, ticket: dict[str, Any]) -> None:
    path = root / "var/lib/etr-core/factory-ticket.json"
    atomic_json(path, ticket, 0o600)
    os.chown(path, 1000, 1000)


def verify_clone(root: Path, boot: Path) -> None:
    required = [
        root / "home/oryx/EtR-core/src/deploy/raspi/setup_etr.sh",
        root / "home/oryx/EtR-core/src/deploy/raspi/etr_factory_firstboot.py",
        root / "etc/systemd/system/etr-factory-firstboot.service",
        root / "var/lib/etr-core/factory-ticket.json",
        root / "etc/fstab",
        boot / "cmdline.txt",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FactoryError("Carte incomplète : " + ", ".join(missing))
    forbidden = [
        root / "var/lib/etr-core/firebase-auth.json",
        root / "var/lib/etr-core/bootstrap-private.pem",
        root / "home/oryx/actions-runner/.credentials",
    ]
    present = [str(path) for path in forbidden if path.exists()]
    if present:
        raise FactoryError("Données uniques encore présentes : " + ", ".join(present))


def prepare_card(
    disk: Disk,
    *,
    copy_wifi: bool,
    progress: Callable[[str], None],
) -> None:
    if os.geteuid() != 0:
        raise FactoryError("La fabrique de cartes doit être lancée avec les droits système")

    devices = lsblk_data()
    current_source = source_disk(devices)
    allowed = {item.path: item for item in candidate_disks(devices, current_source)}
    if disk.path not in allowed:
        raise FactoryError("Le lecteur sélectionné n'est plus disponible ou n'est pas amovible")

    root_used = shutil.disk_usage("/").used
    boot_source = source_boot_mount()
    boot_used = shutil.disk_usage(boot_source).used
    required = int((root_used + boot_used) * 1.20 + 1024**3)
    if disk.size < required:
        raise FactoryError(
            f"Carte trop petite : {human_size(disk.size)} disponible, {human_size(required)} requis"
        )
    if boot_used > BOOT_BYTES * 0.88:
        raise FactoryError("La partition de démarrage source dépasse la capacité prévue de 512 Mio")

    update_progress(progress, "Autorisation sécurisée de fabrication…")
    ticket = request_factory_ticket()
    profile = wifi_profile_path(active_wifi_profile()) if copy_wifi else None

    update_progress(progress, "Effacement et partitionnement de la microSD…")
    boot_device, root_device = partition_target(disk.path)
    session = MOUNT_ROOT / f"job-{os.getpid()}-{int(time.time())}"
    root_mount = session / "root"
    boot_mount = session / "boot"
    root_mount.mkdir(parents=True, exist_ok=True)
    boot_mount.mkdir(parents=True, exist_ok=True)

    try:
        run(["/usr/bin/mount", root_device, str(root_mount)])
        run(["/usr/bin/mount", boot_device, str(boot_mount)])

        update_progress(progress, "Copie du système EtR de référence…")
        rsync_copy(
            "/",
            str(root_mount) + "/",
            excludes=[
                "/boot/***",
                "/dev/***",
                "/proc/***",
                "/sys/***",
                "/run/***",
                "/tmp/***",
                "/mnt/***",
                "/media/***",
                "/lost+found",
                "/swapfile",
            ],
        )
        update_progress(progress, "Copie de la partition de démarrage…")
        rsync_copy(str(boot_source) + "/", str(boot_mount) + "/")

        update_progress(progress, "Suppression des identités et secrets du banc…")
        scrub_clone(root_mount)
        sanitize_target_environment(root_mount)
        install_wifi_profile(root_mount, profile)
        write_ticket(root_mount, ticket)
        enable_firstboot(root_mount)

        root_uuid = blkid_value(root_device, "UUID")
        boot_uuid = blkid_value(boot_device, "UUID")
        root_partuuid = blkid_value(root_device, "PARTUUID")
        boot_target_path = "/boot/firmware" if boot_source == Path("/boot/firmware") else "/boot"
        (root_mount / "etc/fstab").write_text(
            build_fstab(root_uuid, boot_uuid, boot_target_path), encoding="utf-8"
        )
        cmdline = boot_mount / "cmdline.txt"
        cmdline.write_text(
            replace_cmdline_root(cmdline.read_text(encoding="utf-8"), root_partuuid),
            encoding="utf-8",
        )

        update_progress(progress, "Vérification de la carte EtR…")
        verify_clone(root_mount, boot_mount)
        run(["/usr/bin/sync"])
    finally:
        run(["/usr/bin/umount", str(boot_mount)], check=False)
        run(["/usr/bin/umount", str(root_mount)], check=False)
        shutil.rmtree(session, ignore_errors=True)

    update_progress(progress, "Carte prête. Retirez-la et insérez-la dans le nouvel EtR.")
