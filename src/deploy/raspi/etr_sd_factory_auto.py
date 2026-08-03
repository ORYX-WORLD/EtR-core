#!/usr/bin/env python3
"""Interface EtR qui relance automatiquement la fabrication sur l'unique carte USB."""

from __future__ import annotations

import os
import threading
from tkinter import messagebox

try:
    # L'import applique la copie rsync optimisée au moteur partagé avant le lancement.
    from src.deploy.raspi import etr_sd_factory_fast as _fast  # noqa: F401
    from src.deploy.raspi.etr_sd_factory import FactoryApp, MOUNT_ROOT, Tk
except ModuleNotFoundError:
    import etr_sd_factory_fast as _fast  # noqa: F401
    from etr_sd_factory import FactoryApp, MOUNT_ROOT, Tk


class AutoFactoryApp(FactoryApp):
    def __init__(self, root: Tk):
        super().__init__(root)
        self.status.set("Relance optimisée de la fabrication en préparation…")
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
        self.progress.configure(mode="indeterminate", maximum=100, value=0)
        self.progress.start(12)
        self.status.set(
            "Relance optimisée : effacement, formatage et copie sans caches inutiles…"
        )
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
