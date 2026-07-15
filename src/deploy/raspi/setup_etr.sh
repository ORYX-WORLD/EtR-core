#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ORYX-WORLD/EtR-core.git"
INSTALL_DIR="/home/oryx/EtR-core"

sudo apt update
sudo apt install -y \
  git python3-venv python3-pip network-manager \
  xserver-xorg xserver-xorg-video-fbdev xinit lxde-core dbus-x11 \
  chromium netcat-openbsd

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

# NetworkManager gère Ethernet, les profils Wi-Fi et le hotspot temporaire.
sudo systemctl enable NetworkManager.service

# Services principaux
sudo install -m 644 src/deploy/etr.service /etc/systemd/system/etr.service
sudo install -m 644 src/deploy/raspi/etr-wifi-portal.service /etc/systemd/system/etr-wifi-portal.service

# Affichage SPI et kiosque
sudo install -m 755 src/deploy/raspi/start_spi_desktop.sh /usr/local/bin/start_spi_desktop.sh
sudo install -m 755 src/deploy/raspi/etr-kiosk.sh /usr/local/bin/etr-kiosk.sh
sudo install -m 644 src/deploy/raspi/spi-desktop.service /etc/systemd/system/spi-desktop.service
sudo install -m 644 src/deploy/raspi/etr-kiosk.service /etc/systemd/system/etr-kiosk.service

# Protection de l'espace disque
sudo install -m 755 src/deploy/raspi/etr-storage-maintenance.sh /home/oryx/.local/bin/etr-storage-maintenance.sh
echo '*/15 * * * * oryx /home/oryx/.local/bin/etr-storage-maintenance.sh' | \
  sudo tee /etc/cron.d/etr-storage-maintenance >/dev/null
sudo chmod 644 /etc/cron.d/etr-storage-maintenance

sudo systemctl daemon-reload
sudo systemctl enable etr.service etr-wifi-portal.service spi-desktop.service etr-kiosk.service
sudo systemctl restart etr.service
sudo systemctl restart etr-wifi-portal.service
sudo systemctl restart spi-desktop.service
sudo systemctl restart etr-kiosk.service

echo "OK. EtR, le portail Wi-Fi tactile, l'écran SPI et le kiosque sont actifs."
echo "Un redémarrage est recommandé pour valider le parcours hors connexion."
