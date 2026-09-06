#!/usr/bin/env python3
"""Affiche l'etat d'une intervention distante directement sur l'ecran EtR."""
from __future__ import annotations

import json
from pathlib import Path
from tkinter import BOTH, StringVar, Tk
from tkinter import ttk

STATE = Path("/run/etr-maintenance.json")


def read_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    root = Tk()
    root.title("Maintenance EtR")
    root.attributes("-topmost", True)
    root.geometry("470x245+5+5")
    root.resizable(False, False)

    title = StringVar(value="Maintenance EtR")
    detail = StringVar(value="Preparation de l'intervention...")
    percent = StringVar(value="0 %")

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill=BOTH, expand=True)
    ttk.Label(frame, textvariable=title, font=("DejaVu Sans", 17, "bold")).pack(pady=(8, 14))
    ttk.Label(frame, textvariable=detail, wraplength=430, justify="center", font=("DejaVu Sans", 11)).pack(pady=(0, 14))
    bar = ttk.Progressbar(frame, mode="determinate", maximum=100, value=0)
    bar.pack(fill="x", pady=(0, 8))
    ttk.Label(frame, textvariable=percent, font=("DejaVu Sans", 10, "bold")).pack()

    def refresh() -> None:
        state = read_state()
        if state:
            title.set(str(state.get("title") or "Maintenance EtR"))
            detail.set(str(state.get("message") or "Intervention en cours..."))
            try:
                value = max(0, min(100, int(state.get("progress", 0))))
            except (TypeError, ValueError):
                value = 0
            bar.configure(value=value)
            percent.set(f"{value} %")
            if bool(state.get("done")):
                root.after(2500, root.destroy)
                return
        root.after(400, refresh)

    refresh()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
