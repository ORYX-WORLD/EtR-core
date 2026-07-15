#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ORYX-WORLD/EtR-core.git"
INSTALL_DIR="/home/oryx/EtR-core"

sudo apt update
sudo apt install -y \
  git python3-venv python3-pip network-manager psmisc \
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
sudo install -d -m 755 -o oryx -g oryx /home/oryx/.local/bin
sudo install -d -m 755 -o root -g root /var/lib/etr-core
sudo install -m 755 src/deploy/raspi/etr-storage-maintenance.sh /home/oryx/.local/bin/etr-storage-maintenance.sh
echo '*/15 * * * * oryx /home/oryx/.local/bin/etr-storage-maintenance.sh' | \
  sudo tee /etc/cron.d/etr-storage-maintenance >/dev/null
sudo chmod 644 /etc/cron.d/etr-storage-maintenance

# Une ancienne exécution manuelle peut conserver le port 8090 ou le profil
# Chromium. On arrête les services puis les seuls processus concernés.
sudo systemctl stop etr-kiosk.service etr-wifi-portal.service 2>/dev/null || true
sudo pkill -f 'wifi_portal.py' 2>/dev/null || true
sudo fuser -k 8090/tcp 2>/dev/null || true
sudo pkill -u oryx -f 'chromium.*127.0.0.1:8090|chromium.*localhost:8090' 2>/dev/null || true
sudo systemctl reset-failed etr-wifi-portal.service etr-kiosk.service 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable etr.service etr-wifi-portal.service spi-desktop.service etr-kiosk.service
sudo systemctl restart etr.service
sudo systemctl restart spi-desktop.service

# Le portail doit être réellement joignable avant de démarrer le kiosque.
sudo systemctl start etr-wifi-portal.service
portal_ready=false
for attempt in $(seq 1 60); do
  if sudo systemctl is-active --quiet etr-wifi-portal.service && \
     curl -fsS http://127.0.0.1:8090/api/status >/dev/null; then
    portal_ready=true
    break
  fi
  sleep 2
done
if [ "$portal_ready" != true ]; then
  sudo systemctl status etr-wifi-portal.service --no-pager || true
  sudo journalctl -u etr-wifi-portal.service -n 80 --no-pager || true
  exit 1
fi

sudo systemctl reset-failed etr-kiosk.service 2>/dev/null || true
sudo systemctl start etr-kiosk.service

echo "OK. EtR, le portail Wi-Fi tactile, l'écran SPI et le kiosque sont actifs."
echo "Un redémarrage est recommandé pour valider le parcours hors connexion."
