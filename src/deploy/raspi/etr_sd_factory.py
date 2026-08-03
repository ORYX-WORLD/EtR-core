#!/usr/bin/env python3
"""Interface tactile de la fabrique microSD, réservée au bureau Linux EtR."""

from __future__ import annotations

import os
import re
import threading
from tkinter import BOTH, LEFT, RIGHT, StringVar, Tk, Toplevel, messagebox
from tkinter import ttk

try:
    from src.deploy.raspi.etr_sd_factory_core import (
        Disk,
        MOUNT_ROOT,
        active_wifi_profile,
        candidate_disks,
        lsblk_data,
        prepare_card,
        source_disk,
    )
    from src.deploy.raspi.etr_sd_factory_diagnostics import explain_creation_error
except ModuleNotFoundError:
    from etr_sd_factory_core import (
        Disk,
        MOUNT_ROOT,
        active_wifi_profile,
        candidate_disks,
        lsblk_data,
        prepare_card,
        source_disk,
    )
    from etr_sd_factory_diagnostics import explain_creation_error

_PERCENT_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*%")


def fit_small_screen(window: Tk | Toplevel, preferred_width: int, preferred_height: int) -> None:
    """Garde la fenêtre au-dessus de la barre des tâches de l'écran SPI 480x320."""
    screen_width = max(1, window.winfo_screenwidth())
    screen_height = max(1, window.winfo_screenheight())
    width = max(360, min(preferred_width, screen_width - 10))
    height = max(180, min(preferred_height, screen_height - 70))
    x = max(0, (screen_width - width) // 2)
    window.geometry(f"{width}x{height}+{x}+2")


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
        ttk.Label(
            frame,
            text="ATTENTION : EFFACEMENT COMPLET",
            font=("DejaVu Sans", 13, "bold"),
        ).pack(pady=(0, 5))
        ttk.Label(
            frame,
            text=f"Toutes les données de cette carte seront supprimées :\n{disk.label}",
            justify="center",
            wraplength=420,
        ).pack(fill="x", expand=True)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(7, 0))
        ttk.Button(buttons, text="Annuler", command=self.destroy).pack(
            side=LEFT, expand=True, fill="x", padx=(0, 5)
        )
        ttk.Button(buttons, text="EFFACER ET CRÉER", command=self.confirm).pack(
            side=RIGHT, expand=True, fill="x", padx=(5, 0)
        )
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

        style = ttk.Style()
        style.configure("TButton", font=("DejaVu Sans", 10), padding=(7, 4))
        style.configure("Title.TLabel", font=("DejaVu Sans", 14, "bold"))

        frame = ttk.Frame(root, padding=(10, 7))
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text="Créer une carte microSD EtR", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Effacement, clonage et préparation automatique.",
            wraplength=445,
        ).pack(anchor="w", pady=(1, 5))

        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.combo = ttk.Combobox(
            row,
            textvariable=self.selected,
            state="readonly",
            font=("DejaVu Sans", 9),
        )
        self.combo.pack(side=LEFT, fill="x", expand=True)
        ttk.Button(row, text="Actualiser", command=self.refresh).pack(side=RIGHT, padx=(6, 0))

        wifi_text = (
            f"Copier le Wi-Fi actif : {self.wifi_name}"
            if self.wifi_name
            else "Aucun Wi-Fi actif : Ethernet conseillé"
        )
        self.wifi_check = ttk.Checkbutton(
            frame,
            text=wifi_text,
            variable=self.copy_wifi,
            onvalue="1",
            offvalue="0",
        )
        self.wifi_check.pack(anchor="w", pady=(4, 3))
        if not self.wifi_name:
            self.wifi_check.state(["disabled"])

        self.progress = ttk.Progressbar(frame, mode="indeterminate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 3))
        ttk.Label(frame, textvariable=self.status, wraplength=445, justify="left").pack(
            anchor="w", fill="x"
        )

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(5, 0))
        self.close_button = ttk.Button(buttons, text="Fermer", command=root.destroy)
        self.close_button.pack(side=LEFT, fill="x", expand=True, padx=(0, 4))
        self.create_button = ttk.Button(buttons, text="PRÉPARER LA CARTE", command=self.start)
        self.create_button.pack(side=RIGHT, fill="x", expand=True, padx=(4, 0))
        self.refresh()

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
            self.status.set(
                f"{len(labels)} carte amovible détectée."
                if len(labels) == 1
                else f"{len(labels)} cartes amovibles détectées."
            )
        except Exception as exc:
            self.status.set(f"Détection impossible : {exc}")

    def set_status(self, message: str) -> None:
        def apply() -> None:
            self.status.set(message)
            match = _PERCENT_PATTERN.search(message)
            if match:
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=100)
                self.progress["value"] = min(100, int(match.group(1)))
            elif self.busy and str(self.progress.cget("mode")) != "indeterminate":
                self.progress.configure(mode="indeterminate", maximum=100, value=0)
                self.progress.start(12)

        self.root.after(0, apply)

    def start(self) -> None:
        if self.busy:
            return
        disk = self.disks.get(self.selected.get())
        if disk is None:
            messagebox.showwarning("Carte absente", "Insérez une microSD dans le lecteur USB.")
            return
        dialog = ConfirmDialog(self.root, disk)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        self.busy = True
        self.create_button.state(["disabled"])
        self.close_button.state(["disabled"])
        self.progress.configure(mode="indeterminate", maximum=100, value=0)
        self.progress.start(12)
        thread = threading.Thread(target=self.worker, args=(disk,), daemon=True)
        thread.start()

    def worker(self, disk: Disk) -> None:
        try:
            prepare_card(
                disk,
                copy_wifi=self.copy_wifi.get() == "1",
                progress=self.set_status,
            )
        except Exception as exc:
            message = explain_creation_error(exc, disk.path)
            self.root.after(0, messagebox.showerror, "Création impossible", message)
            self.set_status(f"Échec : {message}")
        else:
            self.root.after(
                0,
                messagebox.showinfo,
                "Carte EtR prête",
                "La microSD est prête. Insérez-la dans le nouvel EtR puis mettez-le sous tension.",
            )
        finally:
            self.root.after(0, self.finish)

    def finish(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.create_button.state(["!disabled"])
        self.close_button.state(["!disabled"])
        self.busy = False
        self.refresh()


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
