#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ORYX-WORLD/EtR-core.git"
INSTALL_DIR="/home/oryx/EtR-core"
ENV_FILE="/etc/etr-core/firebase-bridge.env"

sudo apt update
sudo apt install -y \
  git curl python3-venv python3-pip python3-tk network-manager psmisc \
  xserver-xorg xserver-xorg-video-fbdev xinit xvfb lxde-core dbus-x11 \
  chromium netcat-openbsd x11vnc rsync dosfstools parted fdisk e2fsprogs

# Cloner ou actualiser le dépôt officiel.
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  git -C "$INSTALL_DIR" fetch origin main
  git -C "$INSTALL_DIR" pull --ff-only origin main
fi
cd "$INSTALL_DIR"

# Environnement virtuel unique, avec dépendances du cœur et du dashboard versionné.
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install -r dashboard/requirements.txt

# Répertoires d'état locaux avant activation des sandbox systemd.
sudo install -d -m 700 -o oryx -g oryx /var/lib/etr-core
sudo install -d -m 750 -o root -g oryx /etc/etr-core
sudo touch "$ENV_FILE"
sudo chown root:oryx "$ENV_FILE"
sudo chmod 640 "$ENV_FILE"
for state_file in \
  /var/lib/etr-core/firebase-auth.json \
  /var/lib/etr-core/enrollment.json \
  /var/lib/etr-core/telemetry.json \
  /var/lib/etr-core/bootstrap-private.pem; do
  if [ -f "$state_file" ]; then
    sudo chown oryx:oryx "$state_file"
    sudo chmod 600 "$state_file"
  fi
done
sudo rm -f /var/lib/etr-core/remote-screen-auth.json /var/lib/etr-core/remote-screen-auth.tmp

# Identité asymétrique persistante du Raspberry.
sudo -u oryx -H "$INSTALL_DIR/.venv/bin/python" - <<'PY'
from pathlib import Path
from src.device_identity import ensure_device_keypair
ensure_device_keypair(
    Path('/var/lib/etr-core/bootstrap-private.pem'),
    Path('/var/lib/etr-core/bootstrap-public.pem'),
)
PY
sudo chown oryx:oryx /var/lib/etr-core/bootstrap-private.pem /var/lib/etr-core/bootstrap-public.pem
sudo chmod 600 /var/lib/etr-core/bootstrap-private.pem
sudo chmod 644 /var/lib/etr-core/bootstrap-public.pem

remote_gateway=$(sudo sed -n 's/^ETR_REMOTE_GATEWAY_WSS=//p' "$ENV_FILE" | tail -n 1)
if [ -n "$remote_gateway" ] && ! sudo grep -q '^FIREBASE_ENROLLMENT_URL=.' "$ENV_FILE"; then
  enrollment_origin=${remote_gateway/wss:\/\//https:\/\/}
  enrollment_origin=${enrollment_origin%%\?*}
  enrollment_origin=${enrollment_origin%/device}
  enrollment_origin=${enrollment_origin%/}
  printf '%s\n' "FIREBASE_ENROLLMENT_URL=${enrollment_origin}/api/enrollment" | sudo tee -a "$ENV_FILE" >/dev/null
fi

sudo systemctl enable NetworkManager.service

sudo systemctl stop etr-dashboard.service etr-firebase-bridge.service 2>/dev/null || true
sudo install -m 644 src/deploy/etr.service /etc/systemd/system/etr.service
sudo install -m 644 src/deploy/raspi/etr-dashboard.service /etc/systemd/system/etr-dashboard.service
sudo install -m 644 src/deploy/raspi/etr-firebase-bridge.service /etc/systemd/system/etr-firebase-bridge.service
sudo install -m 644 src/deploy/raspi/etr-wifi-portal.service /etc/systemd/system/etr-wifi-portal.service

# Affichage SPI 480x320 local.
sudo install -m 755 src/deploy/raspi/start_spi_desktop.sh /usr/local/bin/start_spi_desktop.sh
sudo install -m 755 src/deploy/raspi/etr-kiosk.sh /usr/local/bin/etr-kiosk.sh
sudo install -m 644 src/deploy/raspi/spi-desktop.service /etc/systemd/system/spi-desktop.service
sudo install -m 755 src/deploy/raspi/etr-disable-blanking.sh /usr/local/bin/etr-disable-blanking.sh
sudo install -d -m 755 /etc/systemd/system/spi-desktop.service.d
sudo install -m 644 src/deploy/raspi/spi-desktop.service.d/blanking.conf /etc/systemd/system/spi-desktop.service.d/blanking.conf
sudo install -m 644 src/deploy/raspi/etr-kiosk.service /etc/systemd/system/etr-kiosk.service

# Bureau distant indépendant du framebuffer SPI : 1280x720 sur :2.
sudo install -m 755 src/deploy/raspi/start_remote_desktop.sh /usr/local/bin/start_remote_desktop.sh
sudo install -m 644 src/deploy/raspi/etr-remote-desktop.service /etc/systemd/system/etr-remote-desktop.service

# Fabrique de cartes microSD.
sudo install -m 755 src/deploy/raspi/etr-sd-factory-launch.sh /usr/local/bin/etr-sd-factory-launch.sh
sudo install -m 644 src/deploy/raspi/etr-sd-factory.service /etc/systemd/system/etr-sd-factory.service
sudo install -m 644 src/deploy/raspi/etr-factory-firstboot.service /etc/systemd/system/etr-factory-firstboot.service
sudo install -m 644 src/deploy/raspi/etr-sd-factory.desktop /usr/share/applications/etr-sd-factory.desktop
sudo install -m 440 src/deploy/raspi/etr-sd-factory.sudoers /etc/sudoers.d/etr-sd-factory
sudo /usr/sbin/visudo -cf /etc/sudoers.d/etr-sd-factory >/dev/null

# Écran distant : VNC reste local au Raspberry et publie uniquement le bureau virtuel :2.
sudo install -m 644 src/deploy/raspi/etr-vnc.service /etc/systemd/system/etr-vnc.service
sudo install -m 644 src/deploy/raspi/etr-remote-screen.service /etc/systemd/system/etr-remote-screen.service

sudo systemctl stop etr-remote-screen.service etr-vnc.service etr-remote-desktop.service 2>/dev/null || true
sudo install -d -m 755 -o oryx -g oryx /home/oryx/.local/bin
sudo rm -f /var/lib/etr-core/remote-screen-auth.json /var/lib/etr-core/remote-screen-auth.tmp
if [ -f /var/lib/etr-core/firebase-auth.json ]; then
  sudo chown oryx:oryx /var/lib/etr-core/firebase-auth.json
  sudo chmod 600 /var/lib/etr-core/firebase-auth.json
fi
sudo install -m 755 src/deploy/raspi/etr-storage-maintenance.sh /home/oryx/.local/bin/etr-storage-maintenance.sh
echo '*/15 * * * * oryx /home/oryx/.local/bin/etr-storage-maintenance.sh' | sudo tee /etc/cron.d/etr-storage-maintenance >/dev/null
sudo chmod 644 /etc/cron.d/etr-storage-maintenance

sudo systemctl stop etr-kiosk.service etr-wifi-portal.service 2>/dev/null || true
sudo pkill -f 'wifi_portal.py' 2>/dev/null || true
sudo fuser -k 8090/tcp 2>/dev/null || true
sudo pkill -TERM -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sleep 2
sudo pkill -KILL -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sudo rm -f /home/oryx/.cache/etr-kiosk-chromium/SingletonCookie \
            /home/oryx/.cache/etr-kiosk-chromium/SingletonLock \
            /home/oryx/.cache/etr-kiosk-chromium/SingletonSocket
sudo systemctl reset-failed etr-dashboard.service etr-firebase-bridge.service etr-wifi-portal.service etr-kiosk.service etr-remote-desktop.service etr-vnc.service 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable etr.service etr-dashboard.service etr-wifi-portal.service spi-desktop.service etr-kiosk.service
sudo systemctl restart etr.service

api_ready=false
for attempt in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
if [ "$api_ready" != true ]; then
  sudo systemctl status etr.service --no-pager || true
  sudo journalctl -u etr.service -n 80 --no-pager || true
  exit 1
fi

if sudo grep -q '^FIREBASE_API_KEY=.' "$ENV_FILE" && sudo grep -q '^FIREBASE_DATABASE_URL=.' "$ENV_FILE"; then
  sudo systemctl enable etr-firebase-bridge.service
  sudo systemctl restart etr-firebase-bridge.service
else
  sudo systemctl disable --now etr-firebase-bridge.service 2>/dev/null || true
  echo "Bridge Firebase non activé : FIREBASE_API_KEY et FIREBASE_DATABASE_URL sont requis dans $ENV_FILE"
fi

sudo systemctl restart etr-dashboard.service
dashboard_ready=false
for attempt in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    dashboard_ready=true
    break
  fi
  sleep 2
done
if [ "$dashboard_ready" != true ]; then
  sudo systemctl status etr-dashboard.service --no-pager || true
  sudo journalctl -u etr-dashboard.service -n 80 --no-pager || true
  exit 1
fi

sudo systemctl restart spi-desktop.service

remote_gateway=$(sudo sed -n 's/^ETR_REMOTE_GATEWAY_WSS=//p' "$ENV_FILE" | tail -n 1)
if [ -n "$remote_gateway" ]; then
  sudo pkill -f '[x]11vnc.*rfbport 5901' 2>/dev/null || true
  sudo systemctl enable etr-remote-desktop.service etr-vnc.service etr-remote-screen.service
  sudo systemctl restart etr-remote-desktop.service
  remote_desktop_ready=false
  for attempt in $(seq 1 30); do
    if DISPLAY=:2 xdpyinfo 2>/dev/null | grep -q 'dimensions:.*1280x720'; then
      remote_desktop_ready=true
      break
    fi
    sleep 1
  done
  if [ "$remote_desktop_ready" != true ]; then
    sudo systemctl status etr-remote-desktop.service --no-pager -l || true
    sudo journalctl -u etr-remote-desktop.service -n 120 --no-pager || true
    exit 1
  fi
  sudo systemctl restart etr-vnc.service
  sudo systemctl restart etr-remote-screen.service
else
  sudo systemctl disable --now etr-remote-screen.service etr-vnc.service etr-remote-desktop.service 2>/dev/null || true
  echo "Passerelle distante non activée : définir ETR_REMOTE_GATEWAY_WSS dans $ENV_FILE"
fi

sudo systemctl start etr-wifi-portal.service
portal_ready=false
for attempt in $(seq 1 60); do
  if sudo systemctl is-active --quiet etr-wifi-portal.service && curl -fsS http://127.0.0.1:8090/api/status >/dev/null; then
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

echo "OK. API EtR, écran SPI 480x320 et bureau distant virtuel 1280x720 sont installés."
echo "Un redémarrage est recommandé pour valider le parcours hors connexion."
