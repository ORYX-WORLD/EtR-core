#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ORYX-WORLD/EtR-core.git"
INSTALL_DIR="/home/oryx/EtR-core"

sudo apt update
sudo apt install -y \
  git python3-venv python3-pip network-manager psmisc \
  xserver-xorg xserver-xorg-video-fbdev xinit lxde-core dbus-x11 \
  chromium netcat-openbsd x11vnc

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
sudo install -m 755 src/deploy/raspi/etr-disable-blanking.sh /usr/local/bin/etr-disable-blanking.sh
sudo install -d -m 755 /etc/systemd/system/spi-desktop.service.d
sudo install -m 644 src/deploy/raspi/spi-desktop.service.d/blanking.conf /etc/systemd/system/spi-desktop.service.d/blanking.conf
sudo install -m 644 src/deploy/raspi/etr-kiosk.service /etc/systemd/system/etr-kiosk.service

# Écran distant : VNC reste local au Raspberry et le relais est uniquement sortant.
sudo install -m 644 src/deploy/raspi/etr-vnc.service /etc/systemd/system/etr-vnc.service
sudo install -m 644 src/deploy/raspi/etr-remote-screen.service /etc/systemd/system/etr-remote-screen.service

# Protection de l'espace disque et jeton distinct du relais écran.
sudo systemctl stop etr-remote-screen.service 2>/dev/null || true
sudo install -d -m 755 -o oryx -g oryx /home/oryx/.local/bin
sudo install -d -m 700 -o oryx -g oryx /var/lib/etr-core
sudo rm -f /var/lib/etr-core/remote-screen-auth.tmp
if [ -f /var/lib/etr-core/remote-screen-auth.json ]; then
  sudo chown oryx:oryx /var/lib/etr-core/remote-screen-auth.json
  sudo chmod 600 /var/lib/etr-core/remote-screen-auth.json
fi
sudo install -m 755 src/deploy/raspi/etr-storage-maintenance.sh /home/oryx/.local/bin/etr-storage-maintenance.sh
echo '*/15 * * * * oryx /home/oryx/.local/bin/etr-storage-maintenance.sh' | \
  sudo tee /etc/cron.d/etr-storage-maintenance >/dev/null
sudo chmod 644 /etc/cron.d/etr-storage-maintenance

# Une ancienne exécution manuelle peut conserver le port 8090 ou le profil
# Chromium. On arrête les services puis les seuls processus concernés.
sudo systemctl stop etr-kiosk.service etr-wifi-portal.service 2>/dev/null || true
sudo pkill -f 'wifi_portal.py' 2>/dev/null || true
sudo fuser -k 8090/tcp 2>/dev/null || true
sudo pkill -TERM -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sleep 2
sudo pkill -KILL -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sudo rm -f /home/oryx/.cache/etr-kiosk-chromium/SingletonCookie \
            /home/oryx/.cache/etr-kiosk-chromium/SingletonLock \
            /home/oryx/.cache/etr-kiosk-chromium/SingletonSocket
sudo systemctl reset-failed etr-wifi-portal.service etr-kiosk.service 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable etr.service etr-wifi-portal.service spi-desktop.service etr-kiosk.service
sudo systemctl restart etr.service
sudo systemctl restart spi-desktop.service

# La passerelle distante est activée uniquement lorsqu'une URL WSS a été
# configurée. Le port VNC EtR 5901 reste limité à la boucle locale.
remote_gateway=""
if [ -f /etc/etr-core/firebase-bridge.env ]; then
  remote_gateway=$(sudo sed -n 's/^ETR_REMOTE_GATEWAY_WSS=//p' /etc/etr-core/firebase-bridge.env | tail -n 1)
fi
if [ -n "$remote_gateway" ]; then
  sudo systemctl stop etr-vnc.service 2>/dev/null || true
  sudo pkill -f '[x]11vnc.*rfbport 5901' 2>/dev/null || true
  sudo systemctl enable etr-vnc.service etr-remote-screen.service
  sudo systemctl restart etr-vnc.service
  sudo systemctl restart etr-remote-screen.service
else
  sudo systemctl disable --now etr-remote-screen.service etr-vnc.service 2>/dev/null || true
  echo "Passerelle distante non activée : définir ETR_REMOTE_GATEWAY_WSS dans /etc/etr-core/firebase-bridge.env"
fi

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

echo "OK. EtR, le portail Wi-Fi tactile, l'écran SPI, le kiosque et la configuration d'écran distant sont installés."
echo "Un redémarrage est recommandé pour valider le parcours hors connexion."
