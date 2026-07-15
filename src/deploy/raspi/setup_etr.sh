#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ORYX-WORLD/EtR-core.git"
INSTALL_DIR="/home/oryx/EtR-core"

sudo apt update
sudo apt install -y git python3-venv python3-pip

# Cloner ou actualiser le dépôt officiel
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  git -C "$INSTALL_DIR" fetch origin main
  git -C "$INSTALL_DIR" pull --ff-only origin main
fi
cd "$INSTALL_DIR"

# Environnement virtuel + dépendances
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# Service systemd
sudo install -m 644 src/deploy/etr.service /etc/systemd/system/etr.service
sudo systemctl daemon-reload
sudo systemctl enable etr.service
sudo systemctl restart etr.service

echo "OK. Service EtR actif sur le port 8080."
