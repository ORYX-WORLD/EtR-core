#!/usr/bin/env python3
"""Interface tactile de la fabrique microSD, indépendante du moteur de copie."""

from __future__ import annotations

import os
import subprocess
import uuid
from tkinter import BOTH, LEFT, RIGHT, StringVar, Tk, Toplevel, Text, messagebox
from tkinter import ttk

try:
    from src.deploy.raspi.etr_sd_factory_core import (
        Disk,
        MOUNT_ROOT,
        active_wifi_profile,
        candidate_disks,
        lsblk_data,
        source_disk,
    )
    from src.deploy.raspi.etr_sd_factory_state import (
        RUNNING_STATUSES,
        TERMINAL_STATUSES,
        read_state,
        terminal_state,
        write_request,
        write_state,
    )
except ModuleNotFoundError:
    from etr_sd_factory_core import (
        Disk,
        MOUNT_ROOT,
        active_wifi_profile,
        candidate_disks,
        lsblk_data,
        source_disk,
    )
    from etr_sd_factory_state import (
        RUNNING_STATUSES,
        TERMINAL_STATUSES,
        read_state,
        terminal_state,
        write_request,
        write_state,
    )

WORKER_SERVICE = "etr-sd-factory-worker.service"
WORKER_BUSY_STATES = {"active", "activating", "reloading", "deactivating"}


def fit_small_screen(window: Tk | Toplevel, preferred_width: int, preferred_height: int) -> None:
    screen_width = max(1, window.winfo_screenwidth())
    screen_height = max(1, window.winfo_screenheight())
    width = max(360, min(preferred_width, screen_width - 10))
    height = max(180, min(preferred_height, screen_height - 70))
    x = max(0, (screen_width - width) // 2)
    window.geometry(f"{width}x{height}+{x}+2")


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/systemctl", *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=15,
    )


def worker_state() -> str:
    result = systemctl("show", "-p", "ActiveState", "--value", WORKER_SERVICE)
    return result.stdout.strip().lower() if result.returncode == 0 else "unknown"


def worker_active() -> bool:
    return worker_state() in WORKER_BUSY_STATES


class ScrollMessageDialog(Toplevel):
    def __init__(self, parent: Tk, title: str, message: str):
        super().__init__(parent)
        self.title(title)
        fit_small_screen(self, 465, 245)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=(8, 7))
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text=title, font=("DejaVu Sans", 12, "bold")).pack(anchor="w", pady=(0, 4))

        body = ttk.Frame(frame)
        body.pack(fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(body, orient="vertical")
        text = Text(
            body,
            wrap="word",
            yscrollcommand=scrollbar.set,
            font=("DejaVu Sans", 9),
            relief="sunken",
            borderwidth=1,
        )
        scrollbar.configure(command=text.yview)
        text.insert("1.0", message)
        text.configure(state="disabled")
        text.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill="y")

        ttk.Button(frame, text="FERMER", command=self.destroy).pack(fill="x", pady=(6, 0))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after_idle(self.focus_force)


class ConfirmDialog(Toplevel):
    def __init__(self, parent: Tk, disk: Disk):
        super().__init__(parent)
        self.result = False
        self.title("Confirmation")
        fit_small_screen(self, 450, 205)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=(12, 9))
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text="ATTENTION : EFFACEMENT COMPLET", font=("DejaVu Sans", 13, "bold")).pack(pady=(0, 5))
        ttk.Label(
            frame,
            text=f"Toutes les données de cette carte seront supprimées :\n{disk.label}",
            justify="center",
            wraplength=420,
        ).pack(fill="x", expand=True)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(7, 0))
        ttk.Button(buttons, text="Annuler", command=self.destroy).pack(side=LEFT, expand=True, fill="x", padx=(0, 5))
        ttk.Button(buttons, text="EFFACER ET CRÉER", command=self.confirm).pack(side=RIGHT, expand=True, fill="x", padx=(5, 0))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after_idle(self.focus_force)

    def confirm(self) -> None:
        self.result = True
        self.destroy()


class FactoryApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Fabrique de cartes EtR")
        fit_small_screen(self.root, 470, 250)
        self.root.resizable(False, False)
        self.disks: dict[str, Disk] = {}
        self.selected = StringVar()
        self.status = StringVar(value="Insérez une microSD puis actualisez.")
        self.wifi_name = active_wifi_profile()
        self.copy_wifi = StringVar(value="1" if self.wifi_name else "0")
        self.busy = False
        self.last_notified_job: str | None = None
        self.inactive_polls = 0

        previous = read_state()
        if previous and str(previous.get("status") or "") in TERMINAL_STATUSES:
            self.last_notified_job = str(previous.get("job_id") or "") or None

        style = ttk.Style()
        style.configure("TButton", font=("DejaVu Sans", 9), padding=(6, 4))
        style.configure("Title.TLabel", font=("DejaVu Sans", 14, "bold"))

        frame = ttk.Frame(root, padding=(10, 7))
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text="Créer une carte microSD EtR", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="La fabrication continue même si cette fenêtre est fermée.", wraplength=445).pack(anchor="w", pady=(1, 5))

        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.combo = ttk.Combobox(row, textvariable=self.selected, state="readonly", font=("DejaVu Sans", 9))
        self.combo.pack(side=LEFT, fill="x", expand=True)
        self.refresh_button = ttk.Button(row, text="Actualiser", command=self.refresh)
        self.refresh_button.pack(side=RIGHT, padx=(6, 0))

        wifi_text = f"Copier le Wi-Fi actif : {self.wifi_name}" if self.wifi_name else "Aucun Wi-Fi actif : Ethernet conseillé"
        self.wifi_check = ttk.Checkbutton(frame, text=wifi_text, variable=self.copy_wifi, onvalue="1", offvalue="0")
        self.wifi_check.pack(anchor="w", pady=(4, 3))
        if not self.wifi_name:
            self.wifi_check.state(["disabled"])

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", pady=(0, 3))
        ttk.Label(frame, textvariable=self.status, wraplength=445, justify="left").pack(anchor="w", fill="x")

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(5, 0))
        self.close_button = ttk.Button(buttons, text="Fermer", command=self.close)
        self.close_button.pack(side=LEFT, fill="x", expand=True, padx=(0, 3))
        self.cancel_button = ttk.Button(buttons, text="ANNULER", command=self.cancel)
        self.cancel_button.pack(side=LEFT, fill="x", expand=True, padx=3)
        self.create_button = ttk.Button(buttons, text="PRÉPARER", command=self.start)
        self.create_button.pack(side=RIGHT, fill="x", expand=True, padx=(3, 0))
        self.cancel_button.state(["disabled"])

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.poll_state()

    def refresh(self) -> None:
        if self.busy:
            return
        try:
            devices = lsblk_data()
            source = source_disk(devices)
            found = candidate_disks(devices, source)
            self.disks = {disk.label: disk for disk in found}
            labels = list(self.disks)
            self.combo["values"] = labels
            self.selected.set(labels[0] if labels else "")
            state = read_state()
            if not state:
                self.status.set(f"{len(labels)} carte amovible détectée." if len(labels) == 1 else f"{len(labels)} cartes amovibles détectées.")
            elif str(state.get("status") or "") in TERMINAL_STATUSES and not worker_active():
                self.status.set("Dernière tentative terminée. Vous pouvez relancer PRÉPARER.")
        except Exception as exc:
            self.status.set(f"Détection impossible : {exc}")

    def start(self) -> None:
        if self.busy or worker_active():
            self.status.set("Une fabrication est déjà en cours. Le suivi est rétabli automatiquement.")
            return
        disk = self.disks.get(self.selected.get())
        if disk is None:
            messagebox.showwarning("Carte absente", "Insérez une microSD dans le lecteur USB.")
            return
        dialog = ConfirmDialog(self.root, disk)
        self.root.wait_window(dialog)
        if not dialog.result:
            return

        job_id = str(uuid.uuid4())
        write_request({
            "job_id": job_id,
            "device": disk.path,
            "disk_label": disk.label,
            "size_bytes": disk.size,
            "copy_wifi": self.copy_wifi.get() == "1",
        })
        self.last_notified_job = None
        systemctl("reset-failed", WORKER_SERVICE)
        result = systemctl("start", "--no-block", WORKER_SERVICE)
        if result.returncode != 0:
            message = result.stderr.strip() or "Impossible de démarrer le moteur de fabrication"
            ScrollMessageDialog(self.root, "Démarrage impossible", message)
            self.status.set(message)
            return
        self.status.set("Démarrage du moteur de fabrication…")
        self.set_busy(True)

    def cancel(self) -> None:
        if not worker_active():
            return
        if not messagebox.askyesno("Annuler la fabrication", "La carte sera démontée et devra être recréée depuis le début. Continuer ?"):
            return
        self.status.set("Annulation et démontage en cours…")
        systemctl("kill", "--signal=SIGINT", WORKER_SERVICE)

    def close(self) -> None:
        if self.busy and not messagebox.askyesno("Fermer le suivi", "La fabrication continuera en arrière-plan. Fermer seulement cette fenêtre ?"):
            return
        self.root.destroy()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.create_button.state(["disabled"])
            self.refresh_button.state(["disabled"])
            self.combo.state(["disabled"])
            self.wifi_check.state(["disabled"])
            self.cancel_button.state(["!disabled"])
        else:
            self.create_button.state(["!disabled"])
            self.refresh_button.state(["!disabled"])
            self.combo.state(["readonly"])
            if self.wifi_name:
                self.wifi_check.state(["!disabled"])
            self.cancel_button.state(["disabled"])

    def poll_state(self) -> None:
        try:
            state = read_state()
            status = str(state.get("status") or "")
            active = status in RUNNING_STATUSES or bool(state.get("active"))
            service_state = worker_state()
            service_active = service_state in WORKER_BUSY_STATES

            if active and service_state in {"inactive", "failed"}:
                self.inactive_polls += 1
                if self.inactive_polls >= 10:
                    state = terminal_state(
                        state,
                        status="interrupted",
                        message="Le moteur s'est arrêté avant la validation finale. Relancez la fabrication depuis le début.",
                        error=f"worker_service_{service_state}",
                    )
                    write_state(state)
                    status = "interrupted"
                    active = False
            else:
                self.inactive_polls = 0

            if state:
                value = float(state.get("progress_percent") or 0)
                self.progress.configure(value=max(0, min(100, value)))
                message = str(state.get("message") or state.get("stage") or "")
                if status not in TERMINAL_STATUSES or active or service_active:
                    self.status.set(message)
                self.set_busy(active or service_active)

                job_id = str(state.get("job_id") or "")
                if status in TERMINAL_STATUSES and job_id and job_id != self.last_notified_job:
                    self.last_notified_job = job_id
                    if status == "ready":
                        messagebox.showinfo("Carte EtR prête", message)
                    elif status == "cancelled":
                        messagebox.showwarning("Fabrication annulée", message)
                    else:
                        ScrollMessageDialog(self.root, "Fabrication incomplète", message)
                    self.refresh()
            else:
                self.set_busy(service_active)
        except Exception as exc:
            self.status.set(f"Suivi indisponible : {exc}")
        finally:
            self.root.after(600, self.poll_state)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("etr_sd_factory.py doit être lancé par etr-sd-factory.service")
    MOUNT_ROOT.mkdir(parents=True, exist_ok=True)
    root = Tk()
    FactoryApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
