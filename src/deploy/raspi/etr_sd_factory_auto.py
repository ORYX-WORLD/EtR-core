#!/usr/bin/env python3
"""Interface EtR qui relance automatiquement la fabrication sur l'unique carte USB."""

from __future__ import annotations

import os
import threading
from tkinter import messagebox

try:
    from src.deploy.raspi.etr_sd_factory import FactoryApp, MOUNT_ROOT, Tk
except ModuleNotFoundError:
    from etr_sd_factory import FactoryApp, MOUNT_ROOT, Tk


class AutoFactoryApp(FactoryApp):
    def __init__(self, root: Tk):
        super().__init__(root)
        self.status.set("Relance automatique de la fabrication en préparation…")
        self.root.after(1200, self.auto_start)

    def auto_start(self) -> None:
        if self.busy:
            return
        if len(self.disks) != 1:
            messagebox.showerror(
                "Relance impossible",
                "La relance automatique exige exactement une microSD USB détectée.",
            )
            self.status.set("Relance annulée : vérifiez le lecteur microSD.")
            return
        disk = next(iter(self.disks.values()))
        self.selected.set(disk.label)
        self.busy = True
        self.create_button.state(["disabled"])
        self.close_button.state(["disabled"])
        self.progress.start(12)
        self.status.set("Relance complète : effacement, formatage et copie du système EtR…")
        threading.Thread(target=self.worker, args=(disk,), daemon=True).start()


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("etr_sd_factory_auto.py doit être lancé avec les droits système")
    MOUNT_ROOT.mkdir(parents=True, exist_ok=True)
    root = Tk()
    AutoFactoryApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
