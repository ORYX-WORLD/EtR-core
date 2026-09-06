#!/usr/bin/env python3
"""Affiche le diagnostic d'architecture EtR dans la session graphique locale.

Ce programme est volontairement lance par l'utilisateur oryx depuis LXDE. Il ne
s'appuie pas sur un terminal ni sur une session logind. Le diagnostic systeme
reste dans le script shell et est execute via sudo -n.
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from pathlib import Path

REPO = Path("/home/oryx/EtR-core")
DIAG = REPO / "src/deploy/raspi/etr-architecture-diagnostic.sh"
RESULT = Path("/tmp/etr-architecture-diagnostic.txt")
READY = Path("/tmp/etr-architecture-diagnostic-gui-ready")


def run_diagnostic() -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("HOME", "/home/oryx")
    proc = subprocess.run(
        ["sudo", "-n", "bash", str(DIAG)],
        cwd=str(REPO),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )
    text = proc.stdout or "(aucune sortie)"
    RESULT.write_text(text, encoding="utf-8")
    return proc.returncode, text


root = tk.Tk()
root.title("Diagnostic architecture EtR")
root.geometry("470x300+5+5")
root.attributes("-topmost", True)
root.configure(padx=10, pady=10)

status = tk.Label(root, text="Diagnostic EtR en cours...", anchor="w", font=("Sans", 12, "bold"))
status.pack(fill="x", pady=(0, 8))

box = tk.Text(root, wrap="word", font=("Monospace", 9))
box.pack(fill="both", expand=True)
box.insert("end", "Initialisation...\n")
box.configure(state="disabled")


def mapped(_event=None):
    READY.write_text("mapped\n", encoding="utf-8")


root.bind("<Map>", mapped)


def execute():
    try:
        rc, text = run_diagnostic()
        status.configure(text=f"Diagnostic termine - code {rc}")
    except Exception as exc:  # preuve visible en cas d'echec du diagnostic lui-meme
        rc = 99
        text = f"DIAGNOSTIC_GUI_ERROR: {exc}\n"
        RESULT.write_text(text, encoding="utf-8")
        status.configure(text="Diagnostic impossible")
    box.configure(state="normal")
    box.delete("1.0", "end")
    box.insert("end", text)
    box.configure(state="disabled")


root.after(500, execute)
root.mainloop()
